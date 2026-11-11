//! map: list files with line counts, without shell ls/find.
//!
//! WHAT IT IS
//!   `map(path, include, max_files, offset)` lists the files under one path or several, with the
//!   line count of each, paged by `max_files` (default 120; 0 returns every row).
//!   `walk`/`include_list`/`matches` are the shared directory walk search uses as well.
//!
//! INVARIANTS
//!   ignored trees never appear (IGNORED_DIRS), so `map()` is a stable view of the source tree.
//!   a literal include is an exact relative path; a glob matches the full path or the basename
//!   (`*.md` any depth, `bench/*.md` one level).
//!   a file path is one row; a missing path is a structured refusal (not_found).

use crate::envelope::{Env, Page};
use crate::tools::{Ctx, IGNORED_DIRS};
use crate::util;
use serde_json::Value;
use std::path::{Path, PathBuf};

/// The default page size in rows.
pub const MAX_FILES: usize = 120;

/// `map`: rows of `path (N lines)` for the requested paths.
pub fn map(ctx: &Ctx, args: &Value) -> Env {
    let includes = include_list(&args["include"]);
    let max_files = match args["max_files"].as_u64() {
        Some(0) => 0,
        Some(n) => n as usize,
        None => MAX_FILES,
    };
    let offset = args["offset"].as_u64().unwrap_or(0) as usize;
    let mut rows: Vec<String> = Vec::new();
    let mut files: Vec<PathBuf> = Vec::new();
    let roots: Vec<String> = match &args["path"] {
        Value::Array(a) => a
            .iter()
            .filter_map(|v| v.as_str())
            .map(|s| s.to_string())
            .collect(),
        Value::String(s) => vec![s.clone()],
        _ => vec![".".to_string()],
    };
    for root in &roots {
        let full = util::resolve(&ctx.root, root);
        if full.is_file() {
            files.push(full);
        } else if full.is_dir() {
            files.extend(walk(&ctx.root, &full, &includes));
        } else {
            return Env::fail(
                "map",
                "not_found",
                format!("{root}: no such file or directory"),
                "pass path=\".\" for the workspace root",
            );
        }
    }
    files.sort();
    files.dedup();
    let total = files.len();
    let end = if max_files == 0 {
        total
    } else {
        (offset + max_files).min(total)
    };
    for file in files.iter().take(end).skip(offset.min(total)) {
        let shown = util::relative(&ctx.root, file);
        let count = std::fs::read(file)
            .map(|b| {
                if util::looks_binary(&b) {
                    0
                } else {
                    String::from_utf8_lossy(&b).lines().count()
                }
            })
            .unwrap_or(0);
        rows.push(format!("{shown} ({count} lines)"));
    }
    let mut env = Env::ok("map")
        .field("files", total)
        .paged(Page::window(
            end.saturating_sub(offset.min(total)),
            total,
            offset.min(total),
            "file",
        ))
        .body(rows.join("\n"));
    if end < total {
        env = env.note(format!("next: offset={end}"));
    }
    env
}

/// Normalise the `include` argument: one string or a list of them.
pub fn include_list(value: &Value) -> Vec<String> {
    match value {
        Value::String(s) => vec![s.clone()],
        Value::Array(a) => a
            .iter()
            .filter_map(|v| v.as_str())
            .map(|s| s.to_string())
            .collect(),
        _ => Vec::new(),
    }
}

/// Every file under `dir` that passes `includes`, skipping the ignored trees.
pub fn walk(root: &Path, dir: &Path, includes: &[String]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(current) = stack.pop() {
        let entries = match std::fs::read_dir(&current) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
                if IGNORED_DIRS.contains(&name.as_str())
                    || name.starts_with('.') && name != ".agent"
                {
                    continue;
                }
                stack.push(path);
            } else if includes.is_empty()
                || includes.iter().any(|pattern| matches(root, &path, pattern))
            {
                out.push(path);
            }
        }
    }
    out
}

/// True when `path` matches one include entry (literal relative path, or a glob).
pub fn matches(root: &Path, path: &Path, pattern: &str) -> bool {
    let rel = util::relative(root, path);
    if !pattern.contains('*') {
        return rel == pattern || rel.ends_with(&format!("/{pattern}"));
    }
    glob(pattern, &rel)
        || glob(
            pattern,
            path.file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default()
                .as_str(),
        )
}

/// A small glob matcher: `*` matches any run within one path segment, `**` across segments,
/// `?` one character. A bare `*.md` therefore matches a basename at any depth because callers
/// compare it against the basename as well as the full relative path.
pub fn glob(pattern: &str, text: &str) -> bool {
    let p: Vec<char> = pattern.chars().collect();
    let t: Vec<char> = text.chars().collect();
    glob_at(&p, &t)
}

/// The recursive matcher behind `glob`: `*` stops at `/`, `**` does not.
fn glob_at(p: &[char], t: &[char]) -> bool {
    if p.is_empty() {
        return t.is_empty();
    }
    match p[0] {
        '*' if p.get(1) == Some(&'*') => {
            // `**`: try every split, crossing separators.
            (0..=t.len()).any(|n| glob_at(&p[2..], &t[n..]))
        }
        '*' => {
            // `*`: any run without a separator.
            let mut n = 0;
            loop {
                if glob_at(&p[1..], &t[n..]) {
                    return true;
                }
                if n >= t.len() || t[n] == '/' {
                    return false;
                }
                n += 1;
            }
        }
        '?' => !t.is_empty() && t[0] != '/' && glob_at(&p[1..], &t[1..]),
        c => !t.is_empty() && t[0] == c && glob_at(&p[1..], &t[1..]),
    }
}
