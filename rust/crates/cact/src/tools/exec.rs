//! exec: the one process tool: argv, several commands, or an explicit shell string.
//!
//! WHAT IT IS
//!   `exec(argv=[...])` runs one program; `commands=[[...], [...]]` runs several in order;
//!   `shell="..."` is the explicit shell escape hatch. cwd/env/stdin/timeout are explicit.
//!
//! INVARIANTS
//!   a command that would mutate a tracked file inside the workspace is refused before it runs
//!   (wrong_call): the journal owns mutation, and a process write would bypass diff()/undo().
//!   scratch (/tmp, /var/tmp, /dev/shm, $TMPDIR) and paths outside the workspace are not gated.
//!   output is bounded and the full text is on an _OBS handle, never silently dropped.

use crate::envelope::Env;
use crate::tools::{inside, Ctx};
use crate::util;
use serde_json::Value;
use std::collections::BTreeMap;

/// Default per-command timeout in seconds.
pub const TIMEOUT: f64 = 60.0;

/// `exec`: run the requested program(s) or shell string.
pub fn exec(ctx: &Ctx, args: &Value) -> Env {
    if !ctx.allow_exec {
        return Env::fail("exec", "exec_not_allowed", "this run does not grant process spawn", "use the typed tools; a task that needs exec grants it with CODE_TUI_ALLOW_RUN=1");
    }
    let cwd = args["cwd"].as_str().map(|c| util::resolve(&ctx.root, c)).unwrap_or_else(|| ctx.root.clone());
    let mut env_map: BTreeMap<String, String> = BTreeMap::new();
    if let Some(obj) = args["env"].as_object() {
        for (k, v) in obj {
            if let Some(text) = v.as_str() {
                env_map.insert(k.clone(), text.to_string());
            }
        }
    }
    let timeout = args["timeout"].as_f64().unwrap_or(TIMEOUT);
    let stdin = args["stdin"].as_str();
    let mut runs: Vec<Vec<String>> = Vec::new();
    let mut shell: Option<String> = None;
    if let Some(list) = args["commands"].as_array() {
        for item in list {
            if let Some(argv) = item.as_array() {
                runs.push(argv.iter().filter_map(|v| v.as_str()).map(|s| s.to_string()).collect());
            }
        }
    }
    if runs.is_empty() {
        if let Some(argv) = args["argv"].as_array() {
            runs.push(argv.iter().filter_map(|v| v.as_str()).map(|s| s.to_string()).collect());
        } else if let Some(s) = args["argv"].as_str() {
            shell = Some(s.to_string());
        }
    }
    if let Some(s) = args["shell"].as_str() {
        if !runs.is_empty() {
            return Env::fail("exec", "bad_arguments", "exec takes argv/commands or shell, not both", "pass argv=[...], or shell=\"...\"");
        }
        shell = Some(s.to_string());
    }
    if let Some(command) = &shell {
        if let Some(reason) = gate_shell(&ctx.root, command) {
            return wrong_call(reason);
        }
    }
    for argv in &runs {
        if let Some(reason) = gate_argv(&ctx.root, argv) {
            return wrong_call(reason);
        }
    }
    if runs.is_empty() && shell.is_none() {
        return Env::fail("exec", "bad_arguments", "exec needs argv, commands or shell", "pass argv=[\"git\", \"status\", \"--short\"]");
    }
    let mut blocks: Vec<String> = Vec::new();
    let mut failed = false;
    let mut rows: Vec<Value> = Vec::new();
    let all: Vec<Result<util::Output, String>> = if let Some(command) = &shell {
        vec![util::run_shell(command, &cwd, &env_map, timeout)]
    } else {
        runs.iter().map(|argv| util::run_argv(argv, &cwd, &env_map, stdin, timeout)).collect()
    };
    for out in all {
        match out {
            Err(e) => {
                failed = true;
                blocks.push(format!("[error] {e}"));
                rows.push(serde_json::json!({"error": e}));
            }
            Ok(out) => {
                let code = out.exit.unwrap_or(-1);
                if code != 0 || out.timed_out {
                    failed = true;
                }
                let mut text = String::new();
                if !out.stdout.is_empty() {
                    text.push_str(out.stdout.trim_end());
                }
                if !out.stderr.is_empty() {
                    if !text.is_empty() {
                        text.push('\n');
                    }
                    text.push_str(out.stderr.trim_end());
                }
                if text.is_empty() {
                    text.push_str("(no output)");
                }
                blocks.push(text);
                rows.push(serde_json::json!({"exit": code, "duration_ms": out.duration_ms, "timeout": out.timed_out}));
            }
        }
    }
    let body = blocks.join("\n---\n");
    let clipped = util::clip(&body, ctx.max_output);
    let mut env = Env::ok("exec").field("runs", rows);
    if clipped.len() < body.len() {
        let handle = ctx.obs.borrow_mut().push(crate::obs::Entry { stdout: body, ..Default::default() });
        env = env.field("obs", format!("_OBS[{handle}]")).note(format!("full output: _OBS[{handle}]"));
    }
    env.body = Some(clipped);
    if failed {
        let first = env.body.clone().unwrap_or_default();
        env.ok = false;
        env.error = Some(crate::envelope::Refusal {
            code: "nonzero_exit".into(),
            message: format!("a command exited non-zero: {}", util::clip(first.lines().next().unwrap_or(""), 200)),
            use_hint: "read the output, fix the cause, then run the gate again".into(),
        });
    }
    env
}

/// A refusal for a process that would bypass the journal.
fn wrong_call(reason: String) -> Env {
    Env::fail(
        "exec",
        "wrong_call",
        reason,
        "use write/edit/splice/delete/mkdir/apply so the change lands in the journal, or target a scratch path",
    )
}

/// Programs that move files around; their path arguments decide whether they are gated.
const MUTATORS: &[&str] = &["tee", "touch", "cp", "mv", "rm", "rmdir", "mkdir", "ln", "truncate", "dd", "install"];

/// Refuse an argv that would write inside the workspace.
fn gate_argv(root: &std::path::Path, argv: &[String]) -> Option<String> {
    if argv.is_empty() {
        return None;
    }
    let program = baseline(&argv[0]);
    if program == "sed" && argv.iter().any(|a| a == "-i" || a.starts_with("--in-place") || (a.starts_with("-i") && a.len() > 2)) {
        return Some("sed -i writes in place".into());
    }
    if !MUTATORS.contains(&program.as_str()) {
        return None;
    }
    let targets: Vec<&String> = argv[1..].iter().filter(|a| !a.starts_with('-')).collect();
    if targets.is_empty() {
        return None;
    }
    let hit = targets.iter().find(|t| writes_inside(root, t));
    hit.map(|t| format!("`{program} {t}` would mutate a tracked file"))
}

/// Refuse a shell string that redirects into the workspace or calls a mutating program.
fn gate_shell(root: &std::path::Path, command: &str) -> Option<String> {
    for token in command.split([';', '|', '&', '\n']) {
        let words: Vec<String> = token.split_whitespace().map(|w| w.to_string()).collect();
        if words.is_empty() {
            continue;
        }
        if let Some(reason) = gate_argv(root, &words) {
            return Some(reason);
        }
    }
    // `>` and `>>` targets: a redirect into the workspace bypasses the journal.
    let mut rest = command;
    while let Some(pos) = rest.find('>') {
        let after = rest[pos..].trim_start_matches('>').trim_start();
        let target: String = after
            .chars()
            .take_while(|c| !c.is_whitespace() && *c != ';' && *c != '|')
            .collect();
        if !target.is_empty() && !target.contains('$') && writes_inside(root, &target) {
            return Some(format!("redirect into `{target}` would mutate a tracked file"));
        }
        rest = &rest[pos + 1..];
    }
    None
}

/// True when `target` resolves inside the workspace and is not an explicit scratch path.
fn writes_inside(root: &std::path::Path, target: &str) -> bool {
    let trimmed = target.trim().trim_matches('"').trim_matches('\'');
    if trimmed.is_empty() || trimmed == "/dev/null" || trimmed == "-" {
        return false;
    }
    if trimmed.starts_with("/tmp") || trimmed.starts_with("/var/tmp") || trimmed.starts_with("/dev/shm") {
        return false;
    }
    if let Ok(tmp) = std::env::var("TMPDIR") {
        if !tmp.is_empty() && trimmed.starts_with(&tmp) {
            return false;
        }
    }
    inside(root, std::path::Path::new(trimmed))
}

/// The program name without a directory prefix.
fn baseline(program: &str) -> String {
    program.rsplit('/').next().unwrap_or(program).to_string()
}
