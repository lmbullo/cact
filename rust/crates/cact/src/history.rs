//! history: the cross-task memory: state dir, prompt prefix, one history.jsonl row.
//!
//! WHAT IT IS
//!   `state_dir(workspace)` is where a workspace's memory lives (history, logs, docs);
//!   `brief()` renders the `[workspace history]` prefix the worker prepends to the next prompt;
//!   `record()` appends one row (last 50 kept) from the structured handoff a run produced.
//!
//! INVARIANTS
//!   the same state dir decides where history and logs live, so memory and log never disagree.
//!   a malformed line is skipped, never fatal: history is memory, not a contract file.
//!   the prefix is bounded (rows, open items, clipped fields) so it cannot crowd out the task.

use serde_json::Value;
use std::path::{Path, PathBuf};

/// The per-workspace state directory: `$CODE_TUI_STATE`, else `$XDG_STATE_HOME/code-tui/<hash>`.
pub fn state_dir(workspace: &Path) -> PathBuf {
    if let Ok(explicit) = std::env::var("CODE_TUI_STATE") {
        if !explicit.trim().is_empty() {
            return PathBuf::from(explicit);
        }
    }
    let base = match std::env::var("CODE_TUI_STATE_ROOT") {
        Ok(root) if !root.trim().is_empty() => PathBuf::from(root),
        _ => {
            let xdg = std::env::var("XDG_STATE_HOME").unwrap_or_else(|_| {
                let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
                format!("{home}/.local/state")
            });
            PathBuf::from(xdg).join("code-tui")
        }
    };
    let digest = crate::util::digest(&workspace.to_string_lossy());
    base.join("workspaces").join(&digest[..16])
}

/// The prompt prefix: the last few tasks, then their open deferred/untested items.
pub fn brief(workspace: &Path) -> String {
    let path = state_dir(workspace).join("history.jsonl");
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(_) => return String::new(),
    };
    let rows: Vec<Value> = text
        .lines()
        .rev()
        .take(4)
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    if rows.is_empty() {
        return String::new();
    }
    let mut lines: Vec<String> = Vec::new();
    for row in &rows {
        let files = row["files"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            })
            .unwrap_or_default();
        lines.push(format!(
            "- {}: {} [{}]",
            row["when"].as_str().unwrap_or("?"),
            crate::util::clip(row["summary"].as_str().unwrap_or(""), 180),
            crate::util::clip(&files, 120)
        ));
    }
    let mut open: Vec<String> = Vec::new();
    for row in rows.iter().rev() {
        for label in ["deferred", "untested"] {
            if let Some(items) = row[label].as_array() {
                for item in items {
                    open.push(format!(
                        "- {} {label}: {}",
                        row["when"].as_str().unwrap_or("?"),
                        crate::util::clip(item.as_str().unwrap_or(&item.to_string()), 200)
                    ));
                    if open.len() >= 6 {
                        break;
                    }
                }
            }
            if open.len() >= 6 {
                break;
            }
        }
        if open.len() >= 6 {
            break;
        }
    }
    if !open.is_empty() {
        lines.push("open items from earlier tasks (newest first):".into());
        lines.extend(open);
    }
    format!(
        "[workspace history] previous tasks in this workspace and what they changed:\n{}",
        lines.join("\n")
    )
}

/// Append one history row (the last 50 are kept) so the next task sees what changed.
pub fn record(workspace: &Path, prompt: &str, result: &crate::engine::RunResult) {
    let path = state_dir(workspace).join("history.jsonl");
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let handoff = result.final_answer.as_ref();
    let summary = handoff
        .and_then(|v| {
            v.get("summary")
                .and_then(|s| s.as_str())
                .map(|s| s.to_string())
        })
        .or_else(|| handoff.and_then(|v| v.as_str().map(|s| s.to_string())))
        .unwrap_or_else(|| result.final_text.clone());
    let list = |key: &str| -> Vec<Value> {
        handoff
            .and_then(|v| v.get(key).and_then(|f| f.as_array()).cloned())
            .unwrap_or_default()
    };
    let now = now_hhmm();
    let row = serde_json::json!({
        "when": now,
        "prompt": crate::util::clip(prompt, 200),
        "summary": crate::util::clip(&summary, 400),
        "files": result.changed.iter().take(12).cloned().collect::<Vec<_>>(),
        "stop": result.stop,
        "changed": list("changed"),
        "gates": list("gates"),
        "deferred": list("deferred"),
        "untested": list("untested"),
    });
    let mut rows: Vec<String> = std::fs::read_to_string(&path)
        .map(|text| {
            text.lines()
                .filter(|l| !l.trim().is_empty())
                .map(|l| l.to_string())
                .collect()
        })
        .unwrap_or_default();
    if rows.len() >= 49 {
        rows = rows.split_off(rows.len() - 49);
    }
    rows.push(row.to_string());
    let _ = std::fs::write(&path, rows.join("\n") + "\n");
}

/// The local wall clock as `HH:MM`, for the history row.
fn now_hhmm() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let offset = std::env::var("CACT_TZ_OFFSET")
        .ok()
        .and_then(|v| v.parse::<i64>().ok())
        .unwrap_or(0);
    let local = secs as i64 + offset;
    let minutes = (local % 86_400 + 86_400) % 86_400 / 60;
    format!("{:02}:{:02}", minutes / 60, minutes % 60)
}
