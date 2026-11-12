//! main: the `cact` binary: worker (the TUI seam), run (one headless task), selfcheck, version.
//!
//! WHAT IT IS
//!   `cact worker ...` is what `tui/src/server-config.ts` spawns: same argv, same `@@TUI@@` lines.
//!   `cact run` is the same engine without the TUI protocol, for a terminal or a script.
//!   `cact selfcheck` is the offline contract gate (no key, no network).
//!
//! INVARIANTS
//!   an unknown subcommand prints the usage and exits 2; it never starts a run.
//!   `run` reads the key from `--key` or `DEEPSEEK_API_KEY`, and refuses with 2 when neither exists.
//!   every mode shares one engine: worker/run differ only in the event sink and the output.

mod card;
mod checks;
mod contract;
mod engine;
mod env;
mod envelope;
mod history;
mod journal;
mod kernel;
mod llm;
mod obs;
mod selfcheck;
mod shape;
mod spec;
mod tools;
mod util;
mod worker;

use serde_json::json;
use std::io::Write;

/// The usage text one call prints; it names every mode and the key it needs.
const USAGE: &str = "cact: an unattended coding agent

  cact worker --workspace DIR --prompt-file FILE [--model deepseek-flash] [--effort low|none|high]
              [--max-steps N] [--max-wall SECONDS] [--cell-timeout SECONDS] [--max-output N]
              [--allow-run 0|1] [--check CMD] [--checks JSON]
  cact run    --workspace DIR (--prompt TEXT | --prompt-file FILE) [--model M] [--effort E]
              [--max-steps N] [--max-wall S] [--check CMD] [--checks JSON] [--key SK]
  cact selfcheck
  cact version

Environment: DEEPSEEK_API_KEY (or --key), DEEPSEEK_BASE_URL, CODE_TUI_ALLOWED_TOOLS,
CODE_TUI_STATE, BENCH_LLM_CACHE, PYTHON (kernel interpreter), CODE_TUI_CARD_BUDGET,
CODE_TUI_MAX_TOTAL_TOKENS, RUST_LOG=1 (worker events on stderr).
";

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let code = match argv.first().map(|s| s.as_str()) {
        Some("worker") => worker::main(&argv[1..]),
        Some("run") => run(argv[1..].to_vec()),
        Some("selfcheck") => selfcheck::run(),
        Some("version") | Some("--version") | Some("-V") => {
            println!("cact {}", env!("CARGO_PKG_VERSION"));
            0
        }
        Some("help") | Some("--help") | Some("-h") | None => {
            print!("{USAGE}");
            0
        }
        Some(other) => {
            eprintln!("cact: unknown subcommand `{other}`\n\n{USAGE}");
            2
        }
    };
    std::process::exit(code);
}

/// `cact run`: one task, no protocol framing; prints the cards and the final report.
fn run(argv: Vec<String>) -> i32 {
    let mut args = worker::Args::default();
    let mut prompt = String::new();
    let mut key = String::new();
    let mut verbose = false;
    let mut i = 0;
    while i < argv.len() {
        let flag = argv[i].clone();
        let mut value = || -> Option<String> {
            i += 1;
            argv.get(i).cloned()
        };
        match flag.as_str() {
            "--workspace" => args.workspace = std::path::PathBuf::from(value().unwrap_or_default()),
            "--prompt" => prompt = value().unwrap_or_default(),
            "--prompt-file" => {
                let path = value().unwrap_or_default();
                prompt = std::fs::read_to_string(path).unwrap_or_default();
            }
            "--model" => args.model = value().unwrap_or_else(|| args.model.clone()),
            "--effort" => {
                let level = value().unwrap_or_default();
                args.effort = if level == "none" { None } else { Some(level) };
            }
            "--max-steps" => {
                args.max_steps = value()
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(args.max_steps)
            }
            "--max-wall" => {
                args.max_wall = value()
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(args.max_wall)
            }
            "--cell-timeout" => {
                args.cell_timeout = value()
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(args.cell_timeout)
            }
            "--max-output" => {
                args.max_output = value()
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(args.max_output)
            }
            "--check" => args.check = value().unwrap_or_default(),
            "--checks" => args.checks = value().unwrap_or_default(),
            "--key" => key = value().unwrap_or_default(),
            "--verbose" => verbose = true,
            other => {
                eprintln!("cact run: unknown flag {other}");
                return 2;
            }
        }
        i += 1;
    }
    if prompt.trim().is_empty() {
        eprintln!("cact run: a --prompt or --prompt-file is required");
        return 2;
    }
    if key.is_empty() {
        key = std::env::var("DEEPSEEK_API_KEY").unwrap_or_default();
    }
    if key.trim().is_empty() {
        eprintln!("cact run: DEEPSEEK_API_KEY (or --key) is required");
        return 2;
    }
    let workspace = args
        .workspace
        .canonicalize()
        .unwrap_or_else(|_| args.workspace.clone());
    let specs = if args.checks.trim().is_empty() {
        checks::parse(&args.check)
    } else {
        checks::parse(&args.checks)
    };
    let ctx = tools::Ctx::new(&workspace, specs.clone(), args.allow_run, args.max_output);
    let base =
        std::env::var("DEEPSEEK_BASE_URL").unwrap_or_else(|_| "https://api.deepseek.com".into());
    let client = llm::Client::new(
        &key,
        &args.model,
        args.effort.as_deref(),
        &base,
        180.0,
        Some(llm::Client::default_cache(&workspace)),
    );
    let budget = engine::Budget::new(args.max_steps, args.max_wall.max(10.0), args.max_output);
    let tools_env = std::env::var("CODE_TUI_ALLOWED_TOOLS").unwrap_or_default();
    let scope = if tools_env.trim().is_empty() {
        spec::Scope::production()
    } else {
        spec::Scope::from_names(&tools_env)
    };
    let rules = format!(
        "{}{}{}{}",
        spec::TOOL_RULES,
        spec::STATE_RULES,
        spec::AUTONOMOUS,
        spec::RUN_NOTE
    );
    let sink: Box<dyn FnMut(serde_json::Value)> = if verbose {
        Box::new(|event| {
            let kind = event["kind"].as_str().unwrap_or("event").to_string();
            eprintln!("[{}] {}", kind, event);
        })
    } else {
        Box::new(|event| {
            if event["kind"] == json!("check") {
                eprintln!(
                    "[check] {} {}",
                    if event["ok"] == json!(true) {
                        "pass"
                    } else {
                        "fail"
                    },
                    event["line"].as_str().unwrap_or("")
                );
            }
        })
    };
    let mut kernel = kernel::Kernel::start(&workspace, args.cell_timeout.max(5.0));
    let result = {
        let mut engine =
            engine::Engine::new(&client, &ctx, &mut kernel, budget, scope, rules, sink);
        engine.run("run", &prompt)
    };
    kernel.stop();
    for path in &result.changed {
        println!("chg: {path}");
    }
    println!("\n[stop] {}", result.stop);
    println!(
        "[cost] steps={} calls={} llm={} prompt={} completion={} reasoning={}",
        result.steps,
        result.calls,
        result.llm_calls,
        result.usage.prompt,
        result.usage.completion,
        result.usage.reasoning
    );
    let text = result
        .final_answer
        .as_ref()
        .map(|v| match v {
            serde_json::Value::String(s) => s.clone(),
            other => other.to_string(),
        })
        .unwrap_or_else(|| result.final_text.clone());
    println!("\n{}", text.trim());
    let _ = std::io::stdout().flush();
    if result.stop == engine::stops::DONE || result.stop == engine::stops::VERIFIED {
        0
    } else if result.stop == engine::stops::STEP_LIMIT || result.stop == engine::stops::WALL_LIMIT {
        3
    } else {
        1
    }
}
