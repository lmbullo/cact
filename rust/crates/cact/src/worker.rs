//! worker: the TUI seam: one task = one process = one engine run, streaming `@@TUI@@` events.
//!
//! WHAT IT IS
//!   `cact worker --workspace ... --prompt-file ... --checks ...` runs one task and writes one
//!   `@@TUI@@{json}` line per event to stdout: cell.start | cell.end | turn | check | log |
//!   error | final. The exit code is the verdict: 0 only for a done run.
//!
//! ENTRY POINTS
//!   main(argv) -> exit code        argv parsing + the run
//!   emit(payload)                  one protocol line on stdout, flushed
//!   final_payloads(result) -> (status, [payload])   the closing events and the status word
//!
//! INVARIANTS
//!   a missing key or an empty prompt exits 2 with NO_API_KEY / EMPTY_PROMPT and spends no kernel.
//!   status: DONE|VERIFIED -> done; STEP_LIMIT|WALL_LIMIT with a summary -> partial; else failed.
//!   the history brief is prepended (state mode) and the handoff is persisted even after a failure.

use crate::checks;
use crate::engine::{stops, Budget, Engine, RunResult};
use crate::kernel::Kernel;
use crate::llm::Client;
use crate::spec::{Scope, AUTONOMOUS, RUN_NOTE, STATE_RULES, TOOL_RULES};
use crate::tools::Ctx;
use serde_json::{json, Value};
use std::io::Write;

/// The marker every machine-readable line carries.
pub const PREFIX: &str = "@@TUI@@";

/// Write one `@@TUI@@` JSON line to stdout and flush it.
pub fn emit(payload: Value) {
    let stdout = std::io::stdout();
    let mut lock = stdout.lock();
    let _ = writeln!(lock, "{PREFIX}{payload}");
    let _ = lock.flush();
}

/// The worker's own argv, parsed by hand so an unknown flag is a clear refusal.
#[derive(Debug, Clone)]
pub struct Args {
    pub workspace: std::path::PathBuf,
    pub prompt_file: std::path::PathBuf,
    pub model: String,
    pub effort: Option<String>,
    pub max_steps: usize,
    pub max_wall: f64,
    pub cell_timeout: f64,
    pub max_output: usize,
    pub allow_run: bool,
    pub check: String,
    pub checks: String,
}

impl Default for Args {
    fn default() -> Self {
        Args {
            workspace: std::path::PathBuf::from("."),
            prompt_file: std::path::PathBuf::new(),
            model: "deepseek-flash".into(),
            effort: Some("low".into()),
            max_steps: 300,
            max_wall: 1800.0,
            cell_timeout: 120.0,
            max_output: 32_000,
            allow_run: true,
            check: String::new(),
            checks: String::new(),
        }
    }
}

impl Args {
    /// Parse `--name value` pairs; `--allow-run 0/1` switches exec off.
    pub fn parse(argv: &[String]) -> Result<Args, String> {
        let mut args = Args::default();
        let mut i = 0;
        while i < argv.len() {
            let flag = argv[i].as_str();
            let mut value = || -> Result<String, String> {
                i += 1;
                argv.get(i).cloned().ok_or_else(|| format!("{flag} needs a value"))
            };
            match flag {
                "--workspace" => args.workspace = std::path::PathBuf::from(value()?),
                "--prompt-file" => args.prompt_file = std::path::PathBuf::from(value()?),
                "--model" => args.model = value()?,
                "--effort" => {
                    let level = value()?;
                    args.effort = if level == "none" { None } else { Some(level) };
                }
                "--max-steps" => args.max_steps = value()?.parse().unwrap_or(300),
                "--max-wall" => args.max_wall = value()?.parse().unwrap_or(1800.0),
                "--cell-timeout" => args.cell_timeout = value()?.parse().unwrap_or(120.0),
                "--max-output" => args.max_output = value()?.parse().unwrap_or(32_000),
                "--allow-run" => args.allow_run = value()? != "0",
                "--check" => args.check = value()?,
                "--checks" => args.checks = value()?,
                other => return Err(format!("unknown flag {other}")),
            }
            i += 1;
        }
        Ok(args)
    }
}

/// Run one task and return the process exit code (0 only for a done run).
pub fn main(argv: &[String]) -> i32 {
    let args = match Args::parse(argv) {
        Ok(args) => args,
        Err(e) => {
            emit(json!({"kind": "error", "text": e}));
            emit(json!({"kind": "final", "status": "failed", "stop": "BAD_ARGS"}));
            return 2;
        }
    };
    let workspace = args
        .workspace
        .canonicalize()
        .unwrap_or_else(|_| args.workspace.clone());
    let prompt = std::fs::read_to_string(&args.prompt_file)
        .map(|text| text.trim().to_string())
        .unwrap_or_default();
    if prompt.is_empty() {
        emit(json!({"kind": "error", "text": "empty prompt"}));
        emit(json!({"kind": "final", "status": "failed", "stop": "EMPTY_PROMPT"}));
        return 2;
    }
    let key = std::env::var("DEEPSEEK_API_KEY").unwrap_or_default().trim().to_string();
    if key.is_empty() {
        emit(json!({"kind": "error", "text": "DEEPSEEK_API_KEY is not set. Put it in ~/.config/code-tui/env or export it before launching code."}));
        emit(json!({"kind": "final", "status": "failed", "stop": "NO_API_KEY"}));
        return 2;
    }
    // A task may commit; a fresh workspace has no git identity. Supply one for children only.
    let git_name = std::env::var("CODE_TUI_GIT_NAME").unwrap_or_else(|_| "code-tui agent".into());
    let git_mail = std::env::var("CODE_TUI_GIT_EMAIL").unwrap_or_else(|_| "agent@code-tui.local".into());
    for var in ["GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"] {
        if std::env::var_os(var).is_none() {
            std::env::set_var(var, &git_name);
        }
    }
    for var in ["GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"] {
        if std::env::var_os(var).is_none() {
            std::env::set_var(var, &git_mail);
        }
    }

    let specs = if !args.checks.trim().is_empty() {
        checks::parse(&args.checks)
    } else {
        checks::parse(&args.check)
    };
    let ctx = Ctx::new(&workspace, specs, args.allow_run, args.max_output);
    let base = std::env::var("DEEPSEEK_BASE_URL").unwrap_or_else(|_| "https://api.deepseek.com".into());
    let client = Client::new(&key, &args.model, args.effort.as_deref(), &base, 180.0, None);
    let budget = Budget::new(args.max_steps, args.max_wall.max(10.0), args.max_output);
    let tools_env = std::env::var("CODE_TUI_ALLOWED_TOOLS").unwrap_or_default();
    let scope = if tools_env.trim().is_empty() { Scope::production() } else { Scope::from_names(&tools_env) };
    let mut rules = format!("{TOOL_RULES}{STATE_RULES}{AUTONOMOUS}");
    if args.allow_run {
        rules.push_str(RUN_NOTE);
    }
    let mut prompt = prompt;
    let brief = crate::history::brief(&workspace);
    if !brief.is_empty() {
        prompt = format!("{brief}\n\n[TASK]\n{prompt}");
    }
    let mut kernel = Kernel::start(&workspace, args.cell_timeout.max(5.0));
    let result = {
        let mut engine = Engine::new(&client, &ctx, &mut kernel, budget, scope, rules, Box::new(emit));
        engine.run("tui-task", &prompt)
    };
    let (status, payloads) = final_payloads(&result);
    for payload in payloads {
        emit(payload);
    }
    crate::history::record(&workspace, &prompt, &result);
    kernel.stop();
    if status == "done" {
        0
    } else {
        1
    }
}

/// The closing events for one finished run, plus its status word.
pub fn final_payloads(result: &RunResult) -> (String, Vec<Value>) {
    let mut payloads: Vec<Value> = Vec::new();
    if result.stop == stops::LLM_FAILED {
        payloads.push(json!({"kind": "error", "text": crate::util::clip(&result.final_text, 400)}));
    }
    let status = if result.stop == stops::DONE || result.stop == stops::VERIFIED {
        "done"
    } else if result.stop == stops::GOAL_UNMET {
        "failed"
    } else if (result.stop == stops::STEP_LIMIT || result.stop == stops::WALL_LIMIT) && !result.final_text.trim().is_empty() {
        "partial"
    } else {
        "failed"
    };
    let final_value = result
        .final_answer
        .clone()
        .unwrap_or_else(|| Value::String(result.final_text.clone()));
    payloads.push(json!({
        "kind": "final",
        "status": status,
        "final": final_value,
        "stop": result.stop,
        "steps": result.steps,
        "calls": result.calls,
        "llmCalls": result.llm_calls,
        "errors": result.errors,
        "usage": {
            "prompt": result.usage.prompt,
            "completion": result.usage.completion,
            "reasoning": result.usage.reasoning,
            "total": result.usage.total,
            "cacheHit": result.usage.cache_hit_tokens,
            "cacheMiss": result.usage.cache_miss_tokens,
        },
        "economy": {"changed": result.changed, "planGreen": result.plan_green},
        "wallMs": result.wall_ms,
    }));
    (status.to_string(), payloads)
}
