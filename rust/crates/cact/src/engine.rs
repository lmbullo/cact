//! engine: the turn machine: one model reply = one turn, one card per call.
//!
//! WHAT IT IS
//!   `Engine::run` drives the loop: send the transcript, read the reply, dispatch each tool call
//!   (typed verb in-process, `code`/`python` in the kernel child), append one state card per call,
//!   and stop on the finish protocol. The verifier (the run's check plan) gates every finish.
//!
//! ENTRY POINTS
//!   Budget { steps, wall_s, output }        the run's limits
//!   Engine::new(client, ctx, kernel, budget, scope, rules, sink)
//!   .run(prompt) -> RunResult               stop, final text, counters, usage, handoff
//!
//! INVARIANTS
//!   a model error is stop=LLM_FAILED with the last text kept, never a panic.
//!   `final_answer` is rejected until the plan passes; a failing check never ends the run, it is a
//!   repair instruction, and a silent final_answer that passes ends the turn as VERIFIED.
//!   the turn is bounded: at most `max_calls_per_turn` calls, steps and wall clock from `Budget`.

use crate::card;
use crate::envelope::Env;
use crate::kernel::{Cell, Kernel};
use crate::llm::{Client, Effort, Reply, Usage};
use crate::spec::Scope;
use crate::tools::{self, RunState};
use serde_json::{json, Value};

/// The run's limits: turns, wall clock, and the per-turn output ceiling.
#[derive(Debug, Clone, Copy)]
pub struct Budget {
    pub steps: usize,
    pub wall_s: f64,
    pub output: usize,
}

impl Budget {
    /// A budget with the given caps; a zero step cap means unlimited (bounded by wall clock).
    pub fn new(steps: usize, wall_s: f64, output: usize) -> Budget {
        Budget { steps, wall_s, output }
    }
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
        Engine { client, ctx, kernel, budget, scope, rules, sink, plan_label: None }
    }

    /// Emit one worker event through the sink.
    fn emit(&mut self, payload: Value) {
        (self.sink)(payload);
    }

    /// Drive the loop until a stop word; never raises on a model error.
    pub fn run(&mut self, task_id: &str, prompt: &str) -> RunResult {
        let started = std::time::Instant::now();
        let mut result = RunResult { plan_green: self.ctx.plan.is_none(), ..RunResult::default() };
        let tools_specs = self.specs();
        let mut messages: Vec<Value> = vec![
            json!({"role": "system", "content": self.rules}),
            json!({"role": "user", "content": format!("[task] {task_id}\n\n{prompt}")}),
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
                let candidate = staged.clone().unwrap_or_else(|| Value::String(reply.content.clone()));
                match self.verify() {
                    Ok(true) => {
                        result.stop = if self.ctx.changed().is_empty() { stops::DONE } else { stops::VERIFIED }.into();
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
            for (index, call) in calls.iter().take(self.scope.max_calls_per_turn).enumerate() {
                if !self.scope.allows(&call.name) {
                    let env = Env::fail(
                        &call.name,
                        "not_granted",
                        format!("this run does not grant `{}`", call.name),
                        "use a granted tool; the tool list is the capability set",
                    );
                    messages.push(tool_message(&call.id, &card::render_body(&env)));
                    continue;
                }
                let args: Value = match serde_json::from_str(&call.args) {
                    Ok(value) => value,
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
                result.calls += 1;
                self.emit(json!({"kind": "cell.start", "cell": index + 1, "tool": call.name, "args": args}));
                let (cell, text) = self.dispatch(&call.name, &args, step);
                result.changed = self.ctx.changed();
                if cell.final_value.is_some() {
                    turn_values.push((call.name.clone(), cell.final_value.clone().unwrap()));
                }
                if let Some(error) = &cell.error {
                    if error.contains("runtime") || error.contains("Error") {
                        // A cell error is a repair instruction, not a failed turn.
                    }
                }
                if let Some(check) = self.ctx.state.borrow().last_check.clone() {
                    self.emit(json!({"kind": "check", "ok": check == "pass", "name": self.plan_label.clone().unwrap_or_else(|| "check".into()), "line": if check == "pass" { String::new() } else { check }}));
                }
                self.emit(json!({
                    "kind": "cell.end",
                    "cell": index + 1,
                    "tool": call.name,
                    "ok": cell.error.is_none() && !cell.timed_out,
                    "code": cell.envelope.as_ref().and_then(|e| e.error.clone()).map(|e| e.code),
                    "chars": cell.stdout.len() + cell.result.len(),
                    "duration_ms": cell.duration_ms,
                    "obs": cell.obs_index.map(|i| format!("_OBS[{i}]")),
                }));
                messages.push(tool_message(&call.id, &text));
                if !turn_values.is_empty() && self.scope.inline_finish {
                    break;
                }
            }
            // A final_answer inside a cell: accept in place when silent and the plan passes.
            if let Some((_, value)) = turn_values.last().cloned() {
                match self.verify() {
                    Ok(true) => {
                        result.stop = if self.ctx.changed().is_empty() { stops::DONE } else { stops::VERIFIED }.into();
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
        specs.push(crate::spec::cell_spec("code"));
        specs.push(crate::spec::plan_spec());
        specs.push(crate::spec::task_spec());
        self.scope.filter(specs)
    }

    /// Run one call and return both the cell record and the card text the model reads.
    fn dispatch(&mut self, name: &str, args: &Value, step: usize) -> (Cell, String) {
        let state: RunState = self.ctx.state.borrow().clone();
        if name == "code" || name == "python" {
            let code = args["code"].as_str().unwrap_or("");
            let mut cell = self.kernel.cell(self.ctx, code, "code");
            self.ctx.state.borrow_mut().steps = step;
            let text = card::render(&cell, step, 0, &state, None);
            if cell.obs_index.is_none() {
                cell.obs_index = None;
            }
            return (cell, text);
        }
        let env = tools::call(self.ctx, name, args);
        let text = card::render_body(&env);
        let cell = Cell {
            tool: name.to_string(),
            envelope: Some(env),
            changed: self.ctx.changed(),
            reads: Vec::new(),
            ..Cell::default()
        };
        self.ctx.state.borrow_mut().steps = step;
        (cell, text)
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
                        if ok {
                            self.ctx.state.borrow_mut().last_check = Some("pass".into());
                        } else {
                            let line = format!("{} : {}", name_of(&check.cmd), check.first_failure());
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
        let line = self.ctx.state.borrow().last_check.clone().unwrap_or_else(|| "the plan failed".into());
        format!(
            "[runtime] final_answer rejected: the check plan has not passed.\n[check] {}\nRead the failing line, repair the cause, and re-run checks() when you believe it passes. Keep working.",
            crate::util::clip(&line, 240)
        )
    }
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
