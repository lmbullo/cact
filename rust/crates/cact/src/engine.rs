//! engine: the turn machine: one model reply = one turn, one card per call.
//!
//! WHAT IT IS
//!   `Engine::run` drives the loop: send the transcript, read the reply, dispatch each tool call
//!   (typed verb in-process, `code`/`python` in the kernel child), append one state card per call,
//!   and stop on the finish protocol. The verifier (the run's check plan) gates every finish.
//!
//! ENTRY POINTS
//!   Budget { steps, wall_s, output, max_total_tokens, card_budget }   the run's limits
//!   Engine::new(client, ctx, kernel, budget, scope, rules, sink)
//!   .run(prompt) -> RunResult               stop, final text, counters, usage, handoff
//!
//! INVARIANTS
//!   a model error is stop=LLM_FAILED with the last text kept, never a panic.
//!   `final_answer` is rejected until the plan and the prompt contract pass; a failing check or
//!   an unsupported prompt constraint never ends the run, it is a repair instruction.
//!   the turn is bounded: at most `max_calls_per_turn` calls, steps, wall clock, total tokens and
//!   the card budget from `Budget`; a clipped card always names the exact resume call.
//!   every assistant `tool_call` gets exactly one `tool` message, including a call skipped by the
//!   per-turn budget or by a terminal `final_answer`; an unpaired call is a provider 400.

use crate::card;
use crate::contract::{self, Evidence};
use crate::env;
use crate::envelope::Env;
use crate::kernel::{Cell, Kernel};
use crate::llm::{Client, Effort, Reply, Usage};
use crate::spec::Scope;
use crate::tools::{self, PlanItem, RunState};
use serde_json::{json, Value};

/// The run's limits: turns, wall clock, output per turn, total tokens and card delivery.
#[derive(Debug, Clone, Copy)]
pub struct Budget {
    pub steps: usize,
    pub wall_s: f64,
    pub output: usize,
    pub max_total_tokens: usize,
    pub card_budget: usize,
}

impl Budget {
    /// A budget with the given caps; `max_total_tokens`/`card_budget` default from the env.
    pub fn new(steps: usize, wall_s: f64, output: usize) -> Budget {
        Budget {
            steps,
            wall_s,
            output,
            max_total_tokens: env_usize("CODE_TUI_MAX_TOTAL_TOKENS", 30_000_000),
            card_budget: env_usize("CODE_TUI_CARD_BUDGET", 5_000),
        }
    }
}

/// One environment integer, with a default when unset or malformed.
fn env_usize(name: &str, default: usize) -> usize {
    std::env::var(name)
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(default)
}

/// One finished run: the stop word, the report, and what it cost.
#[derive(Debug, Clone, Default)]
pub struct RunResult {
    pub stop: String,
    pub final_text: String,
    pub final_answer: Option<Value>,
    pub steps: usize,
    pub calls: usize,
    pub llm_calls: usize,
    pub errors: usize,
    pub usage: Usage,
    pub changed: Vec<String>,
    pub wall_ms: u128,
    pub plan_green: bool,
}

/// The stop vocabulary: one closed set of words the worker maps to a task status.
pub mod stops {
    /// Finished with a passing plan (or no plan).
    pub const DONE: &str = "DONE";
    /// Finished with the plan green after a mutation.
    pub const VERIFIED: &str = "VERIFIED";
    /// The step budget ran out.
    pub const STEP_LIMIT: &str = "STEP_LIMIT";
    /// The wall clock ran out.
    pub const WALL_LIMIT: &str = "WALL_LIMIT";
    /// The model kept trying to finish while the plan failed.
    pub const GOAL_UNMET: &str = "GOAL_UNMET";
    /// The provider truncated the turn.
    pub const TRUNCATED: &str = "TRUNCATED";
    /// The model call itself failed.
    pub const LLM_FAILED: &str = "LLM_FAILED";
    /// The run's total token ceiling was reached: the circuit breaker.
    pub const TOKEN_LIMIT: &str = "TOKEN_LIMIT";
}

/// The turn machine: model, tools, kernel child, verify-on-finish.
pub struct Engine<'a> {
    pub client: &'a Client,
    pub ctx: &'a tools::Ctx,
    pub kernel: &'a mut Kernel,
    pub budget: Budget,
    pub scope: Scope,
    pub rules: String,
    pub sink: Box<dyn FnMut(Value) + 'a>,
    pub plan_label: Option<String>,
    contract: Vec<contract::Item>,
    evidence: Evidence,
    last_prompt_tokens: u64,
    card_step: usize,
    card_used: usize,
    green: bool,
}

impl<'a> Engine<'a> {
    /// Build one run: `rules` is the system prompt, `sink` receives worker events.
    pub fn new(
        client: &'a Client,
        ctx: &'a tools::Ctx,
        kernel: &'a mut Kernel,
        budget: Budget,
        scope: Scope,
        rules: String,
        sink: Box<dyn FnMut(Value) + 'a>,
    ) -> Engine<'a> {
        Engine {
            client,
            ctx,
            kernel,
            budget,
            scope,
            rules,
            sink,
            plan_label: None,
            contract: Vec::new(),
            evidence: Evidence::default(),
            last_prompt_tokens: 0,
            card_step: 0,
            card_used: 0,
            green: false,
        }
    }

    /// Emit one worker event through the sink.
    fn emit(&mut self, payload: Value) {
        (self.sink)(payload);
    }

    /// Drive the loop until a stop word; never raises on a model error.
    pub fn run(&mut self, task_id: &str, prompt: &str) -> RunResult {
        let started = std::time::Instant::now();
        let mut result = RunResult {
            plan_green: self.ctx.plan.is_none(),
            ..RunResult::default()
        };
        self.contract = contract::parse(prompt);
        if !self.contract.is_empty() {
            let mut state = self.ctx.state.borrow_mut();
            if state.plan.is_empty() {
                state.plan = self
                    .contract
                    .iter()
                    .map(|item| PlanItem {
                        text: format!("contract: {}", item.text),
                        done: false,
                    })
                    .collect();
            }
        }
        let tools_specs = self.specs();
        let env_card = env::render(&self.ctx.root, prompt, self.ctx.plan.as_ref());
        let first_user = if env_card.is_empty() {
            format!("[task] {task_id}\n\n{prompt}")
        } else {
            format!("{env_card}\n\n[task] {task_id}\n\n{prompt}")
        };
        let mut messages: Vec<Value> = vec![
            json!({"role": "system", "content": self.rules}),
            json!({"role": "user", "content": first_user}),
        ];
        let mut staged: Option<Value> = None;
        let mut last_text = String::new();
        let mut step = 0usize;
        loop {
            if self.budget.steps > 0 && step >= self.budget.steps {
                result.stop = stops::STEP_LIMIT.into();
                break;
            }
            if started.elapsed().as_secs_f64() > self.budget.wall_s {
                result.stop = stops::WALL_LIMIT.into();
                break;
            }
            step += 1;
            result.steps = step;
            let reply = match self.client.complete(
                &messages,
                Some(&tools_specs),
                self.scope.max_turn_tokens.min(self.budget.output.max(1)),
                match &self.scope.effort {
                    Some(level) => Effort::Level(level.clone()),
                    None => self.client.effort_level(),
                },
            ) {
                Ok(reply) => reply,
                Err(err) => {
                    self.emit(json!({"kind": "log", "text": format!("llm error: {err}")}));
                    result.stop = stops::LLM_FAILED.into();
                    result.final_text = format!("{last_text}\n\n[llm] {err}").trim().to_string();
                    break;
                }
            };
            result.llm_calls += 1;
            usage_add(&mut result.usage, &reply.usage);
            self.last_prompt_tokens = reply.usage.prompt;
            if self.budget.max_total_tokens > 0
                && result.usage.total as usize >= self.budget.max_total_tokens
            {
                result.stop = stops::TOKEN_LIMIT.into();
                result.final_text = staged
                    .as_ref()
                    .map(value_text)
                    .unwrap_or_else(|| last_text.clone());
                break;
            }
            self.emit(json!({"kind": "turn", "turn": step, "data": {
                "usage": {
                    "prompt": reply.usage.prompt, "completion": reply.usage.completion,
                    "reasoning": reply.usage.reasoning, "total": reply.usage.total,
                    "cacheHit": reply.usage.cache_hit_tokens, "cacheMiss": reply.usage.cache_miss_tokens
                },
                "cache": reply.local_cache, "latencyMs": reply.latency_ms, "finish": reply.finish
            }}));
            if !reply.content.trim().is_empty() {
                last_text = reply.content.clone();
            }
            if reply.finish == "length" {
                result.stop = stops::TRUNCATED.into();
                break;
            }
            let calls = reply.tool_calls.clone();
            messages.push(assistant_message(&reply));
            if calls.is_empty() {
                // No tool call: accept the staged answer, or the reply's own text, after verifying.
                let candidate = staged
                    .clone()
                    .unwrap_or_else(|| Value::String(reply.content.clone()));
                match self.verify() {
                    Ok(true) => {
                        if let Some(notice) = self.contract_rejection() {
                            staged = None;
                            messages.push(json!({"role": "user", "content": notice}));
                            continue;
                        }
                        result.stop = if self.ctx.changed().is_empty() {
                            stops::DONE
                        } else {
                            stops::VERIFIED
                        }
                        .into();
                        result.plan_green = true;
                        result.final_text = value_text(&candidate);
                        result.final_answer = Some(candidate);
                        break;
                    }
                    Ok(false) => {
                        staged = None;
                        let rejection = self.reject();
                        messages.push(json!({"role": "user", "content": rejection}));
                        continue;
                    }
                    Err(stop) => {
                        result.stop = stop;
                        result.final_text = value_text(&candidate);
                        break;
                    }
                }
            }
            let mut turn_values: Vec<(String, Value)> = Vec::new();
            let mut terminal = false;
            for (index, call) in calls.iter().enumerate() {
                if let Some(notice) = skip_notice(
                    terminal,
                    index >= self.scope.max_calls_per_turn,
                    self.scope.max_calls_per_turn,
                ) {
                    // Every assistant tool_call must get exactly one tool message, including the
                    // ones we refuse to run; otherwise the next request is a provider 400.
                    messages.push(tool_message(&call.id, &notice));
                    continue;
                }
                let (parsed, json_note) = match crate::shape::parse_args(&call.args) {
                    Ok(pair) => pair,
                    Err(e) => {
                        let env = Env::fail(
                            &call.name,
                            "bad_arguments",
                            format!("the arguments are not valid JSON: {e}"),
                            "send a JSON object matching the tool schema",
                        );
                        messages.push(tool_message(&call.id, &card::render_body(&env)));
                        result.errors += 1;
                        continue;
                    }
                };
                let shape = crate::shape::repair(&call.name, parsed, &self.scope);
                let mut name = shape.name;
                let mut args = shape.args;
                let mut shape_note = String::new();
                if !json_note.is_empty() {
                    shape_note.push_str(&format!("[shape] {json_note}\n"));
                }
                if shape.changed {
                    shape_note.push_str(&format!("[shape] {}\n", shape.note));
                }
                if name == "exec" {
                    if let Some(spec) = plan_spec(&args, self.ctx.plan.as_ref()) {
                        shape_note.push_str(&format!(
                            "[shape] exec(plan command) ran as checks({spec:?})\n"
                        ));
                        name = "checks".into();
                        args = json!({"name": spec});
                    }
                }
                if !self.scope.allows(&name) {
                    let env = Env::fail(
                        &name,
                        "not_granted",
                        format!("this run does not grant `{name}`"),
                        "use a granted tool; the tool list is the capability set",
                    );
                    messages.push(tool_message(&call.id, &card::render_body(&env)));
                    continue;
                }
                result.calls += 1;
                self.emit(
                    json!({"kind": "cell.start", "cell": index + 1, "tool": name, "args": args}),
                );
                let (cell, text) = self.dispatch(&name, &args, step);
                let text = if shape_note.is_empty() {
                    text
                } else {
                    format!("{shape_note}{text}")
                };
                result.changed = self.ctx.changed();
                if cell.final_value.is_some() {
                    turn_values.push((name.clone(), cell.final_value.clone().unwrap()));
                }
                if let Some(error) = &cell.error {
                    if error.contains("runtime") || error.contains("Error") {
                        // A cell error is a repair instruction, not a failed turn.
                    }
                }
                if let Some(check) = self.ctx.state.borrow().last_check.clone() {
                    self.emit(json!({"kind": "check", "ok": check == "pass", "name": self.plan_label.clone().unwrap_or_else(|| "check".into()), "line": if check == "pass" { String::new() } else { check }}));
                }
                let refusal = cell.envelope.as_ref().and_then(|e| e.error.clone());
                let env_ok = cell.envelope.as_ref().map(|e| e.ok).unwrap_or(true);
                let ok = cell.error.is_none() && !cell.timed_out && env_ok;
                let notes: Vec<String> = cell
                    .envelope
                    .as_ref()
                    .map(|e| {
                        e.notes
                            .iter()
                            .map(|note| note.split_whitespace().collect::<Vec<_>>().join(" "))
                            .collect()
                    })
                    .unwrap_or_default();
                let error = refusal
                    .as_ref()
                    .map(|e| format!("{}: {}", e.code, e.message))
                    .or_else(|| cell.error.clone());
                self.emit(json!({
                    "kind": "cell.end",
                    "cell": index + 1,
                    "tool": name,
                    "ok": ok,
                    "verb_kind": cell.envelope.as_ref().map(|e| e.kind.clone()),
                    "code": refusal.as_ref().map(|e| e.code.clone()),
                    "error": error,
                    "page": cell.envelope.as_ref().and_then(|e| e.page.as_ref()).map(|page| json!({
                        "shown": page.shown, "total": page.total, "unit": page.unit,
                        "has_more": page.has_more, "truncated": page.truncated,
                        "complete": page.complete
                    })),
                    "notes": notes,
                    "runs": cell.runs,
                    "chars": cell.stdout.len() + cell.result.len(),
                    "duration_ms": cell.duration_ms,
                    "obs": cell.obs_index.map(|i| format!("_OBS[{i}]")),
                }));
                messages.push(tool_message(&call.id, &text));
                if !turn_values.is_empty() && self.scope.inline_finish {
                    terminal = true;
                }
            }
            // A final_answer inside a cell: accept in place when silent and the plan passes.
            if let Some((_, value)) = turn_values.last().cloned() {
                match self.verify() {
                    Ok(true) => {
                        if let Some(notice) = self.contract_rejection() {
                            messages.push(json!({"role": "user", "content": notice}));
                            staged = None;
                            continue;
                        }
                        result.stop = if self.ctx.changed().is_empty() {
                            stops::DONE
                        } else {
                            stops::VERIFIED
                        }
                        .into();
                        result.plan_green = true;
                        result.final_text = value_text(&value);
                        result.final_answer = Some(value);
                        result.wall_ms = started.elapsed().as_millis();
                        return result;
                    }
                    Ok(false) => {
                        let rejection = self.reject();
                        messages.push(json!({"role": "user", "content": rejection}));
                        staged = None;
                    }
                    Err(stop) => {
                        result.stop = stop;
                        result.final_text = value_text(&value);
                        break;
                    }
                }
            }
        }
        result.wall_ms = started.elapsed().as_millis();
        if result.final_text.is_empty() {
            result.final_text = last_text.clone();
        }
        result.changed = self.ctx.changed();
        result.final_text = crate::util::clip(&result.final_text, self.scope.max_report_chars);
        result
    }

    /// The provider tool list: typed specs plus the cell and plan/task, filtered by scope.
    fn specs(&self) -> Vec<Value> {
        let mut specs = crate::spec::typed_specs();
        let cell = if self.scope.code_mode {
            "code"
        } else {
            "python"
        };
        specs.push(crate::spec::cell_spec(cell));
        specs.push(crate::spec::plan_spec());
        specs.push(crate::spec::task_spec());
        self.scope.filter(specs)
    }

    /// Run one call and return both the cell record and the card text the model reads.
    fn dispatch(&mut self, name: &str, args: &Value, step: usize) -> (Cell, String) {
        let state: RunState = self.ctx.state.borrow().clone();
        if name == "final_answer" {
            let value = if args.get("summary").and_then(|v| v.as_str()).is_some()
                && args.as_object().map(|o| o.len() == 1).unwrap_or(false)
            {
                args["summary"].clone()
            } else {
                args.clone()
            };
            let env = Env::ok("final_answer")
                .body("final answer submitted; the check plan decides whether the run stops.");
            let text = card::render_body(&env);
            let cell = Cell {
                tool: "final_answer".into(),
                envelope: Some(env),
                final_value: Some(value),
                changed: self.ctx.changed(),
                ..Cell::default()
            };
            let text = self.fit_card(name, args, &cell, text, step);
            return (cell, text);
        }
        if self.green && name == "exec" && !is_gate_command(args) {
            // Under a green plan a read-only exec is still useful: the model often checks a
            // real value the test did not cover. Only a re-run of a gate is refused.
        } else if self.green && (name == "exec" || name == "checks") {
            let env = if name == "checks" {
                Env::ok("checks")
                    .field("cached", true)
                    .body("the check plan already passes for this tree (latest run); no new check ran. Reply with the final report, or call final_answer(...) inside a silent code cell.")
            } else {
                Env::fail(
                    name,
                    "green_plan",
                    "the check plan already passes for this tree",
                    "reply with the final report, or call final_answer(...) inside a silent code cell",
                )
            };
            let text = card::render_body(&env);
            let cell = Cell {
                tool: name.to_string(),
                envelope: Some(env),
                changed: self.ctx.changed(),
                ..Cell::default()
            };
            let text = self.fit_card(name, args, &cell, text, step);
            return (cell, text);
        }
        if name == "code" || name == "python" {
            let code = args["code"].as_str().unwrap_or("");
            let cell = self.kernel.cell(self.ctx, code, "code");
            self.ctx.state.borrow_mut().steps = step;
            self.evidence
                .observe_runs(&cell.runs, cell.error.is_none() && !cell.timed_out);
            if !cell.changed.is_empty() {
                self.green = false;
            }
            let text = card::render(&cell, step, self.last_prompt_tokens, &state, None);
            let text = self.fit_card(name, args, &cell, text, step);
            return (cell, text);
        }
        let env = tools::call(self.ctx, name, args);
        if !env.changed.is_empty() {
            self.green = false;
        }
        if name == "checks" && env.ok {
            self.green = self.ctx.state.borrow().last_check.as_deref() == Some("pass");
        }
        let runs = contract::commands_from_args(args);
        self.evidence.record_call(name, args, env.ok);
        self.evidence.observe_runs(&runs, env.ok);
        let text = card::render_body(&env);
        let cell = Cell {
            tool: name.to_string(),
            envelope: Some(env.clone()),
            changed: self.ctx.changed(),
            reads: env.reads.clone(),
            delta: env.delta.clone(),
            runs,
            ..Cell::default()
        };
        self.ctx.state.borrow_mut().steps = step;
        let text = self.fit_card(name, args, &cell, text, step);
        (cell, text)
    }

    /// The exact resume call a clipped card names: the next read window, else the _OBS page.
    fn resume_call(name: &str, cell: &Cell, handle: &str) -> String {
        if name == "read" {
            if let Some((path, _, end)) = cell.reads.iter().max_by_key(|(_, _, end)| *end) {
                return format!("[card] clipped; resume read({path:?}, start={})", end + 1);
            }
        }
        format!("[card] clipped; resume page(handle={handle:?})")
    }

    /// Deliver a card within this turn's budget; a cut names the exact call that continues it.
    fn fit_card(
        &mut self,
        name: &str,
        _args: &Value,
        cell: &Cell,
        text: String,
        step: usize,
    ) -> String {
        if self.budget.card_budget == 0 {
            return text;
        }
        if step != self.card_step {
            self.card_step = step;
            self.card_used = 0;
        }
        let total = text.chars().count();
        let room = self.budget.card_budget.saturating_sub(self.card_used);
        if total <= room {
            self.card_used += total;
            return text;
        }
        let handle = cell
            .obs_index
            .map(|i| format!("_OBS[{i}]"))
            .or_else(|| {
                cell.envelope
                    .as_ref()
                    .and_then(|env| env.data.as_ref())
                    .and_then(|data| data.get("obs"))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            })
            .unwrap_or_else(|| "_OBS[-1]".into());
        let reserve = if room > 180 { 180 } else { room / 4 };
        let (head, _) = card::clip_card(&text, room.saturating_sub(reserve), &handle);
        let out = format!("{}\n{}", head, Self::resume_call(name, cell, &handle));
        self.card_used += out.chars().count();
        out
    }

    /// The rejection a finish earns when a prompt constraint still has no evidence.
    fn contract_rejection(&self) -> Option<String> {
        let evidence = self
            .evidence
            .snapshot(self.ctx.changed().len(), self.evidence.checks_green);
        let missing = contract::missing(&self.contract, &evidence);
        if missing.is_empty() {
            None
        } else {
            Some(contract::rejection(&missing))
        }
    }

    /// Run the goal plan, if any: Ok(true) pass, Ok(false) fail, Err(stop) when the plan is unrunnable.
    fn verify(&mut self) -> Result<bool, String> {
        match &self.ctx.plan {
            None => Ok(true),
            Some(plan) => {
                self.plan_label = Some(plan.label());
                let check = plan.run(Some(&self.ctx.changed()));
                match check {
                    None => Ok(true),
                    Some(check) => {
                        let ok = check.ok();
                        self.evidence.checks_green = ok;
                        if ok {
                            self.ctx.state.borrow_mut().last_check = Some("pass".into());
                        } else {
                            let line =
                                format!("{} : {}", name_of(&check.cmd), check.first_failure());
                            self.ctx.state.borrow_mut().last_check = Some(line);
                        }
                        self.emit(json!({"kind": "check", "ok": ok, "name": name_of(&check.cmd), "line": if ok { String::new() } else { check.first_failure() }}));
                        Ok(ok)
                    }
                }
            }
        }
    }

    /// The rejection message a failed finish earns: the first failing line, then "keep working".
    fn reject(&self) -> String {
        let line = self
            .ctx
            .state
            .borrow()
            .last_check
            .clone()
            .unwrap_or_else(|| "the plan failed".into());
        format!(
            "[runtime] final_answer rejected: the check plan has not passed.\n[check] {}\nRead the failing line, repair the cause, and re-run checks() when you believe it passes. Keep working.",
            crate::util::clip(&line, 240)
        )
    }
}

/// The tool message one skipped call gets; None means the call is allowed to run.
fn skip_notice(terminal: bool, over_budget: bool, max_calls: usize) -> Option<String> {
    if terminal {
        return Some(
            "[observation] skipped: final_answer in this turn is terminal intent, so this call did not run."
                .into(),
        );
    }
    if over_budget {
        return Some(format!(
            "[observation] skipped: this task allows at most {max_calls} tool calls per turn; split the work across turns."
        ));
    }
    None
}

/// The command lines an `exec` argument would run: argv, each commands row, and shell.
fn exec_commands(args: &Value) -> Vec<String> {
    let mut commands = Vec::new();
    if let Some(argv) = args["argv"].as_array() {
        commands.push(
            argv.iter()
                .filter_map(|v| v.as_str())
                .collect::<Vec<_>>()
                .join(" "),
        );
    }
    if let Some(rows) = args["commands"].as_array() {
        for row in rows {
            if let Some(parts) = row.as_array() {
                commands.push(
                    parts
                        .iter()
                        .filter_map(|v| v.as_str())
                        .collect::<Vec<_>>()
                        .join(" "),
                );
            }
        }
    }
    if let Some(shell) = args["shell"].as_str() {
        commands.push(shell.to_string());
    }
    commands
}

/// True when an `exec` argument runs a verification gate the green plan already owns.
fn is_gate_command(args: &Value) -> bool {
    const WORDS: &[&str] = &[
        "pytest",
        "unittest",
        "test",
        "tests",
        "typecheck",
        "tsc",
        "lint",
        "clippy",
        "fmt",
        "format",
        "ruff",
    ];
    exec_commands(args).iter().any(|command| {
        command
            .to_lowercase()
            .split_whitespace()
            .any(|word| WORDS.contains(&word))
    })
}

/// The plan spec an `exec` argument exactly runs, else None.
fn plan_spec(args: &Value, plan: Option<&crate::checks::Plan>) -> Option<String> {
    let plan = plan?;
    let mut commands = exec_commands(args)
        .iter()
        .map(|command| command.split_whitespace().collect::<Vec<_>>().join(" "))
        .collect::<Vec<_>>();
    if commands.len() != 1 {
        return None;
    }
    let command = commands.pop().unwrap();
    plan.specs
        .iter()
        .find(|spec| spec.cmd.split_whitespace().collect::<Vec<_>>().join(" ") == command)
        .map(|spec| spec.name.clone())
}

/// The assistant message that carries content plus tool calls.
fn assistant_message(reply: &Reply) -> Value {
    let calls: Vec<Value> = reply
        .tool_calls
        .iter()
        .map(|c| json!({"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.args}}))
        .collect();
    let mut message = json!({"role": "assistant", "content": reply.content});
    if !calls.is_empty() {
        message["tool_calls"] = json!(calls);
    }
    message
}

/// One tool-result message keyed by the call id.
fn tool_message(id: &str, content: &str) -> Value {
    json!({"role": "tool", "tool_call_id": id, "content": content})
}

/// The plain text of a final value: a string as-is, a dict as compact JSON.
fn value_text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// The spec name of a check: the part before ": ".
fn name_of(cmd: &str) -> String {
    cmd.split(": ").next().unwrap_or(cmd).to_string()
}

/// Add one reply's usage into the run total.
fn usage_add(total: &mut Usage, add: &Usage) {
    total.prompt += add.prompt;
    total.completion += add.completion;
    total.reasoning += add.reasoning;
    total.total += add.total;
    total.cache_hit_tokens += add.cache_hit_tokens;
    total.cache_miss_tokens += add.cache_miss_tokens;
}

#[cfg(test)]
mod tests {
    use super::skip_notice;

    #[test]
    fn every_extra_call_gets_a_pairing_notice() {
        let max = 2;
        let notices: Vec<Option<String>> =
            (0..5).map(|i| skip_notice(false, i >= max, max)).collect();
        assert!(notices[0].is_none() && notices[1].is_none());
        assert!(notices[2..].iter().all(|notice| notice.is_some()));
    }

    #[test]
    fn a_call_after_a_final_answer_is_skipped() {
        assert!(skip_notice(true, false, 8).is_some());
        assert!(skip_notice(false, false, 8).is_none());
    }
}
