//! read: the file reader: one window, several paths, or batched ranges.
//!
//! WHAT IT IS
//!   `read` serves one canonical line window per request and says exactly what it delivered.
//!   start/end are 1-based and inclusive; omitting end serves the reader's page (400 lines).
//!   A range already delivered this session returns a pointer, not the text, unless repeat=true.
//!
//! INVARIANTS
//!   `[read] path a-b/total` is built from what was actually delivered, never from the request.
//!   a missing file is a structured refusal (not_found) naming the workspace root it searched.
//!   a binary body is refused (binary_file), never rendered as mojibake.

use crate::envelope::{Env, Page};
use crate::tools::Ctx;
use crate::util;
use serde_json::Value;

/// The reader's page size in lines when `end` is omitted.
pub const PAGE: usize = 400;

/// `read`: one file, a list of files (one window each), or batched `ranges`.
pub fn read(ctx: &Ctx, args: &Value) -> Env {
    if let Some(list) = args["ranges"].as_array() {
        let mut envs = Vec::new();
        for item in list {
            envs.push(read_one(
                ctx,
                item["path"].as_str().unwrap_or(""),
                item["start"].as_u64().unwrap_or(1) as usize,
                item["end"].as_u64().map(|v| v as usize),
                args,
            ));
        }
        return merge(envs);
    }
    let start = args["start"].as_u64().unwrap_or(1) as usize;
    let end = args["end"].as_u64().map(|v| v as usize);
    match &args["path"] {
        Value::Array(paths) => {
            let envs = paths
                .iter()
                .filter_map(|p| p.as_str())
                .map(|p| read_one(ctx, p, start, end, args))
                .collect();
            merge(envs)
        }
        Value::String(path) => read_one(ctx, path, start, end, args),
        _ => Env::fail("read", "bad_arguments", "read needs a path", "pass path=\"src/main.rs\", or ranges=[{path,start,end}]"),
    }
}

/// Read one path; `args` carries the shared flags (raw, repeat, section).
fn read_one(ctx: &Ctx, path: &str, start: usize, end: Option<usize>, args: &Value) -> Env {
    let raw = args["raw"].as_bool().unwrap_or(false);
    let repeat = args["repeat"].as_bool().unwrap_or(false);
    let full = util::resolve(&ctx.root, path);
    let shown_path = util::relative(&ctx.root, &full);
    let bytes = match std::fs::read(&full) {
        Ok(bytes) => bytes,
        Err(e) => {
            return Env::fail(
                "read",
                if e.kind() == std::io::ErrorKind::NotFound { "not_found" } else { "read_failed" },
                format!("{shown_path}: {e}"),
                format!("map(path=\"{}\") lists what exists under the workspace root {}", ctx.root.display(), ctx.root.display()),
            )
        }
    };
    if util::looks_binary(&bytes) {
        return Env::fail("read", "binary_file", format!("{shown_path} is binary ({} bytes)", bytes.len()), "use exec(argv=[\"file\", path]) or a parser instead");
    }
    let text = String::from_utf8_lossy(&bytes).to_string();
    let (start, end) = match args["section"].as_str().filter(|s| !s.is_empty()) {
        Some(section) => match crate::tools::outline::section_range(&ctx.root, &shown_path, section) {
            Some((s, e)) => (s, Some(e)),
            None => {
                return Env::fail(
                    "read",
                    "no_section",
                    format!("no heading or symbol named `{section}` in {shown_path}"),
                    "call outline(path=...) to see the names and their line ranges",
                )
            }
        },
        None => (start, end),
    };
    let (body, first, last) = util::line_window(&text, start, end, PAGE);
    let total = text.lines().count();
    let key = format!("{shown_path}:{first}-{last}");
    let seen = {
        let mut shown = ctx.shown.borrow_mut();
        let seen = shown.get(&key).is_some_and(|r| r.contains(&(first, last)));
        shown.entry(key.clone()).or_default().push((first, last));
        seen
    };
    if seen && !repeat {
        return Env::ok("read")
            .field("path", shown_path.clone())
            .field("start", first)
            .field("end", last)
            .field("total", total)
            .field("repeat", true)
            .paged(Page::whole(total, "line"))
            .body(format!(
                "{shown_path} {first}-{last}/{total} was already delivered in this session; pass repeat=true to read it again, or start=<n> for a new window"
            ));
    }
    let rendered = if raw { body.clone() } else { util::numbered(&body, first) };
    let mut env = Env::ok("read")
        .field("path", shown_path.clone())
        .field("start", first)
        .field("end", last)
        .field("total", total)
        .paged(Page::window(last.saturating_sub(first) + 1, total, first.saturating_sub(1), "line"))
        .body(rendered);
    env.reads.push((shown_path, first, last));
    if last < total {
        env = env.note(format!("{total} lines total; next: start={}", last + 1));
    }
    env
}

/// Merge several per-file answers into one envelope: bodies in order, reads concatenated.
fn merge(mut envs: Vec<Env>) -> Env {
    if envs.is_empty() {
        return Env::fail("read", "bad_arguments", "no path to read", "pass path=... or ranges=[{path}]");
    }
    if envs.len() == 1 {
        return envs.remove(0);
    }
    let mut out = Env::ok("read");
    let mut bodies: Vec<String> = Vec::new();
    let mut failed: Vec<Env> = Vec::new();
    for env in envs {
        if !env.ok {
            failed.push(env.clone());
        }
        bodies.push(env.body.clone().unwrap_or_default());
        out.reads.extend(env.reads);
        out.notes.extend(env.notes);
    }
    out.body = Some(bodies.join("\n\n"));
    out.page = Some(Page::whole(out.reads.len(), "file"));
    if !failed.is_empty() {
        let first = &failed[0];
        out.notes.push(format!("{} file(s) failed; first: {}", failed.len(), first.error.as_ref().map(|e| e.message.clone()).unwrap_or_default()));
        out.ok = false;
        out.error = first.error.clone();
    }
    out
}
