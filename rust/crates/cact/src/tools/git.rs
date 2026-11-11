//! git: the typed git working-tree surface, paged like `read`.
//!
//! WHAT IT IS
//!   `git(action, ...)` runs one git subcommand in the workspace (or `cwd`): status, diff, show,
//!   log, files, add, commit, push, remote, branch. `diff`/`show` page with offset/max_chars and
//!   return `next` plus `has_more`, the same shape as `read`.
//!
//! INVARIANTS
//!   a non-zero git exit is a structured refusal (git_failed) carrying git's own first line.
//!   `commit` requires a message; `push` forwards remote/branch when given.
//!   the typed `session_diff` tool is the journal view, not this: git sees everything on disk.

use crate::envelope::{Env, Page};
use crate::tools::Ctx;
use crate::util;
use serde_json::Value;

/// The actions this port implements.
pub const ACTIONS: &[&str] = &["status", "diff", "show", "log", "files", "add", "commit", "push", "remote", "branch"];

/// `git`: one subcommand, one paged answer.
pub fn git(ctx: &Ctx, args: &Value) -> Env {
    let action = args["action"].as_str().unwrap_or("");
    if !ACTIONS.contains(&action) {
        return Env::fail(
            "git",
            "bad_arguments",
            format!("unknown git action `{action}`"),
            format!("use one of: {}", ACTIONS.join(", ")),
        );
    }
    let cwd = args["cwd"].as_str().map(|c| util::resolve(&ctx.root, c)).unwrap_or_else(|| ctx.root.clone());
    let offset = args["offset"].as_u64().unwrap_or(0) as usize;
    let max_chars = args["max_chars"].as_u64().unwrap_or(24_000) as usize;
    let limit = args["limit"].as_u64().unwrap_or(30) as usize;
    let mut argv: Vec<String> = vec!["git".into(), action.into()];
    let paths: Vec<String> = args["paths"].as_array().map(|a| a.iter().filter_map(|v| v.as_str()).map(|s| s.to_string()).collect()).unwrap_or_default();
    match action {
        "status" => {
            argv.push("--short".into());
            argv.push("--branch".into());
        }
        "diff" | "show" => {
            if args["stat"].as_bool().unwrap_or(false) {
                argv.push("--stat".into());
            }
            if args["cached"].as_bool().unwrap_or(false) && action == "diff" {
                argv.push("--cached".into());
            }
            if action == "show" {
                if let Some(rev) = args["rev"].as_str() {
                    argv.push(rev.to_string());
                }
            }
            argv.push("--".into());
            argv.extend(paths.iter().cloned());
        }
        "log" => {
            argv.push(format!("-{limit}"));
            argv.push("--oneline".into());
            argv.extend(paths.iter().cloned());
        }
        "files" => argv.push("ls-files".into()),
        "add" => {
            if paths.is_empty() {
                return Env::fail("git", "bad_arguments", "git add needs paths", "pass paths=[\"src/main.rs\"] or paths=[\".\"]");
            }
            argv.extend(paths.iter().cloned());
        }
        "commit" => {
            let message = args["message"].as_str().unwrap_or("");
            if message.trim().is_empty() {
                return Env::fail("git", "bad_arguments", "git commit needs a message", "pass message=\"what changed and why\"");
            }
            argv.push("-m".into());
            argv.push(message.to_string());
            if !paths.is_empty() {
                argv.push("--".into());
                argv.extend(paths.iter().cloned());
            }
        }
        "push" => {
            if let Some(remote) = args["remote"].as_str() {
                argv.push(remote.to_string());
            }
            if let Some(branch) = args["branch"].as_str() {
                argv.push(branch.to_string());
            }
        }
        "remote" => argv.push("-v".into()),
        "branch" => argv.push("--show-current".into()),
        _ => {}
    }
    // `git files` is `git ls-files`; rebuild the argv for the alias.
    if action == "files" {
        argv = vec!["git".into(), "ls-files".into()];
    }
    match util::run_argv(&argv, &cwd, &Default::default(), None, 120.0) {
        Err(e) => Env::fail("git", "git_failed", e, "is git installed?"),
        Ok(out) => {
            let text = if action == "status" && out.stdout.is_empty() { "working tree clean".to_string() } else { out.stdout.clone() };
            if out.exit != Some(0) {
                let detail = util::first_failure(&out.stdout, &out.stderr);
                return Env::fail(
                    "git",
                    "git_failed",
                    format!("git {action} exited {:?}: {detail}", out.exit),
                    "read the message; commit or push again once the cause is fixed",
                );
            }
            if action == "diff" || action == "show" {
                let chars: Vec<char> = text.chars().collect();
                let total = chars.len();
                let start = offset.min(total);
                let take = if max_chars == 0 { total - start } else { max_chars.min(total - start) };
                let body: String = chars[start..start + take].iter().collect();
                let mut env = Env::ok("git")
                    .field("action", action)
                    .paged(Page::window(take, total, start, "char"))
                    .body(body);
                if start + take < total {
                    env = env.note(format!("next: offset={}", start + take));
                }
                return env;
            }
            Env::ok("git").field("action", action).body(util::clip(&text, 24_000))
        }
    }
}
