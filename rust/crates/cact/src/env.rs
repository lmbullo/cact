//! env: the deterministic `[env]` card prepended to the first task message.
//!
//! WHAT IT IS
//!   One workspace probe: resolved root, git root (or none), capped top-level listing, a few
//!   files at depth <= 2, the check plan, and task-mentioned paths that do not exist plus the
//!   closest path that does. `render` returns the `[env]` block; the engine prepends it once.
//!
//! ENTRY POINTS
//!   render(root, task, plan) -> String   the `[env]` card, or "" when the root is unreadable
//!
//! INVARIANTS
//!   no mutation, no network, no model; a failed git probe means `git=none`, not an error.
//!   generated trees are skipped, so node_modules/target cannot bury real entries.
//!   a task path is reported only when it is missing: `tui/src/x.ts` under root `tui` -> `src/x.ts`.

use crate::checks::Plan;
use crate::tools::IGNORED_DIRS;
use crate::util;
use std::path::{Path, PathBuf};

const TOP: usize = 24;
const FILES: usize = 32;

/// The `[env]` card for `root`, the task text, and the run's check plan.
pub fn render(root: &Path, task: &str, plan: Option<&Plan>) -> String {
    let base = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    if !base.is_dir() {
        return String::new();
    }
    let mut lines = vec!["[env]".to_string(), format!("root={}", base.display())];
    match git_root(&base) {
        Some(git) => lines.push(format!("git={git}")),
        None => lines.push("git=none".into()),
    }
    let top = top_entries(&base);
    if !top.is_empty() {
        lines.push(format!("top={}", top.join(" ")));
    }
    let files = files(&base);
    if !files.is_empty() {
        lines.push(format!("files={}", files.join(" ")));
    }
    if let Some(plan) = plan {
        if !plan.specs.is_empty() {
            let rows: Vec<String> = plan
                .specs
                .iter()
                .map(|spec| format!("{}={}", spec.name, spec.cmd))
                .collect();
            lines.push(format!("checks={}", rows.join("; ")));
        }
    }
    for (given, found) in task_paths(&base, task) {
        match found {
            Some(use_path) => lines.push(format!("path {given}: use {use_path}")),
            None => lines.push(format!("path {given}: missing")),
        }
    }
    lines.join("\n")
}

/// The work-tree root from `git rev-parse`, or None when git is absent or this is not a repo.
fn git_root(root: &Path) -> Option<String> {
    let out = util::run_argv(
        &["git".into(), "rev-parse".into(), "--show-toplevel".into()],
        root,
        &Default::default(),
        None,
        5.0,
    )
    .ok()?;
    if out.exit == Some(0) {
        let text = out.stdout.trim();
        if text.is_empty() {
            None
        } else {
            Some(text.to_string())
        }
    } else {
        None
    }
}

/// A capped, sorted top-level listing with directories marked and generated trees skipped.
fn top_entries(root: &Path) -> Vec<String> {
    let entries = match std::fs::read_dir(root) {
        Ok(entries) => entries,
        Err(_) => return Vec::new(),
    };
    let mut rows: Vec<(bool, String)> = entries
        .filter_map(|entry| entry.ok())
        .filter_map(|entry| {
            let name = entry.file_name().to_string_lossy().to_string();
            if IGNORED_DIRS.contains(&name.as_str()) {
                return None;
            }
            let is_dir = entry.file_type().map(|t| t.is_dir()).unwrap_or(false);
            Some((is_dir, format!("{name}{}", if is_dir { "/" } else { "" })))
        })
        .collect();
    rows.sort_by(|a, b| {
        b.0.cmp(&a.0)
            .then_with(|| a.1.to_lowercase().cmp(&b.1.to_lowercase()))
    });
    rows.into_iter().take(TOP).map(|(_, name)| name).collect()
}

/// A capped, sorted path list at depth <= 2, skipping generated and hidden trees.
fn files(root: &Path) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    walk(root, root, 0, &mut out);
    out
}

/// Depth-first walk used by `files`; stops as soon as the cap is reached.
fn walk(root: &Path, dir: &Path, depth: usize, out: &mut Vec<String>) {
    if out.len() >= FILES {
        return;
    }
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut rows: Vec<(bool, PathBuf)> = entries
        .filter_map(|entry| entry.ok())
        .filter_map(|entry| {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || IGNORED_DIRS.contains(&name.as_str()) {
                return None;
            }
            let is_dir = entry.file_type().map(|t| t.is_dir()).unwrap_or(false);
            Some((is_dir, entry.path()))
        })
        .collect();
    rows.sort_by(|a, b| a.1.file_name().cmp(&b.1.file_name()));
    for (is_dir, path) in rows {
        if out.len() >= FILES {
            return;
        }
        if is_dir {
            if depth < 1 {
                walk(root, &path, depth + 1, out);
            }
        } else {
            out.push(util::relative(root, &path));
        }
    }
}

/// The path-like tokens in `task` that do not exist at `root`, with the closest existing suffix.
fn task_paths(root: &Path, task: &str) -> Vec<(String, Option<String>)> {
    let mut out: Vec<(String, Option<String>)> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for raw in task.split_whitespace() {
        let token = raw
            .trim_matches(|c: char| {
                matches!(
                    c,
                    '"' | '\'' | '`' | '(' | ')' | '[' | ']' | ',' | ';' | ':' | '.'
                )
            })
            .to_string();
        if token.is_empty() || !token.contains('/') || token.starts_with("http") {
            continue;
        }
        if seen.iter().any(|s| s == &token) {
            continue;
        }
        seen.push(token.clone());
        if root.join(&token).exists() {
            continue;
        }
        let parts: Vec<&str> = token
            .trim_matches('/')
            .split('/')
            .filter(|part| !part.is_empty() && *part != ".")
            .collect();
        let mut found = None;
        for index in 1..parts.len() {
            let candidate = parts[index..].join("/");
            if root.join(&candidate).exists() {
                found = Some(candidate);
                break;
            }
        }
        out.push((token, found));
    }
    out
}
