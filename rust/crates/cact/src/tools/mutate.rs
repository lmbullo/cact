//! mutate: the journaled write path: write, edit, splice, delete, mkdir, apply.
//!
//! WHAT IT IS
//!   Every mutation reads the before-body, changes the file, and records the pair in the session
//!   journal; the returned `changed`/`delta` come from that record, so a card's `[diff]` is the
//!   truth and `session_diff()`/undo see the same bytes.
//!
//! INVARIANTS
//!   atomic: edit validates every replacement in every file before writing any of them, so a
//!   bad second edit changes nothing and returns the closest matching lines.
//!   a truncated rewrite is refused unless force=true; the note names the size difference.
//!   delete refuses a directory (map the files first) and a missing path (not_found).

use crate::envelope::Env;
use crate::tools::Ctx;
use crate::util;
use serde_json::Value;

/// `write`: a whole file; refuses a shorter body without `force`.
pub fn write(ctx: &Ctx, args: &Value) -> Env {
    let path = match args["path"].as_str() {
        Some(p) if !p.is_empty() => p,
        _ => {
            return Env::fail(
                "write",
                "bad_arguments",
                "write needs a path",
                "pass path=\"file.rs\", content=\"...\"",
            )
        }
    };
    let content = match args["content"].as_str() {
        Some(c) => c.to_string(),
        None => {
            return Env::fail(
                "write",
                "bad_arguments",
                "write needs content",
                "pass content=\"...\" (an empty string is allowed)",
            )
        }
    };
    let force = args["force"].as_bool().unwrap_or(false);
    let full = util::resolve(&ctx.root, path);
    let shown = util::relative(&ctx.root, &full);
    let before = std::fs::read_to_string(&full).ok();
    if let Some(old) = &before {
        if !force && content.len() < old.len() {
            return Env::fail(
                "write",
                "truncated_write",
                format!(
                    "{shown}: {} bytes would replace {} bytes",
                    content.len(),
                    old.len()
                ),
                format!(
                    "use edit/splice for a partial change, or pass force=true to replace {shown}"
                ),
            );
        }
    }
    if let Some(parent) = full.parent() {
        if let Err(e) = std::fs::create_dir_all(parent) {
            return Env::fail(
                "write",
                "write_failed",
                format!("{shown}: {e}"),
                "check that the parent directory is writable",
            );
        }
    }
    if let Err(e) = std::fs::write(&full, &content) {
        return Env::fail(
            "write",
            "write_failed",
            format!("{shown}: {e}"),
            "check permissions and disk space",
        );
    }
    let mut env = Env::ok("write")
        .field("path", shown.clone())
        .field("bytes", content.len())
        .field("created", before.is_none())
        .body(format!(
            "{} {shown} ({} bytes)",
            if before.is_some() { "wrote" } else { "created" },
            content.len()
        ));
    if content.contains("\\n") && !content.contains('\n') {
        env =
            env.note("content has no real line breaks: it contains the two characters backslash-n");
    }
    ctx.commit(env, &shown, before, Some(content))
}

/// `edit`: one atomic batch of old->new replacements across one or several files.
pub fn edit(ctx: &Ctx, args: &Value) -> Env {
    let edits = match args["edits"].as_array() {
        Some(list) if !list.is_empty() => list,
        _ => {
            return Env::fail(
                "edit",
                "bad_arguments",
                "edit needs edits=[{path, old, new}]",
                "pass edits=[{\"path\": \"a.rs\", \"old\": \"x\", \"new\": \"y\"}]",
            )
        }
    };
    // Validate everything first: a bad edit changes no file.
    let mut planned: Vec<(String, String, String, String)> = Vec::new();
    for (index, item) in edits.iter().enumerate() {
        let path = item["path"].as_str().unwrap_or("");
        let old = item["old"].as_str().unwrap_or("");
        let new = item["new"].as_str().unwrap_or("");
        let replace_all = item["all"].as_bool().unwrap_or(false);
        if path.is_empty() || old.is_empty() {
            return Env::fail(
                "edit",
                "bad_arguments",
                format!("edit #{} needs a non-empty path and old text", index + 1),
                "every edit needs {path, old, new}; use write for a new file",
            );
        }
        let full = util::resolve(&ctx.root, path);
        let shown = util::relative(&ctx.root, &full);
        let text = match std::fs::read_to_string(&full) {
            Ok(text) => text,
            Err(e) => {
                return Env::fail(
                    "edit",
                    "not_found",
                    format!("{shown}: {e}"),
                    "use write for a new file, or map() to see what exists",
                );
            }
        };
        let current = planned
            .iter()
            .find(|(p, _, _, _)| *p == shown)
            .map(|(_, _, after, _)| after.clone())
            .unwrap_or(text);
        match replace(&current, old, new, replace_all) {
            Ok(updated) => planned.push((shown, old.to_string(), new.to_string(), updated)),
            Err(hint) => {
                return Env::fail(
                    "edit",
                    "no_match",
                    format!("{shown}: edit #{} did not match", index + 1),
                    hint,
                )
            }
        }
    }
    let mut changed: Vec<String> = Vec::new();
    let mut env = Env::ok("edit");
    for (shown, _old, _new, after) in planned {
        let full = util::resolve(&ctx.root, &shown);
        let before = std::fs::read_to_string(&full).ok();
        if let Err(e) = std::fs::write(&full, &after) {
            return Env::fail(
                "edit",
                "write_failed",
                format!("{shown}: {e}"),
                "check permissions",
            );
        }
        if ctx.journal.borrow_mut().record(&shown, before, Some(after)) {
            changed.push(shown.clone());
        }
    }
    env.changed = changed.clone();
    if let Some(first) = changed.first() {
        env.delta = Some(ctx.journal.borrow().diff(Some(first), 6000));
    }
    env.body = Some(format!(
        "edited {} file(s): {}",
        changed.len(),
        changed.join(", ")
    ));
    env
}

/// Replace `old` with `new`; an indentation-only difference is accepted when unique.
fn replace(text: &str, old: &str, new: &str, replace_all: bool) -> Result<String, String> {
    let count = text.matches(old).count();
    if count > 0 {
        if count > 1 && !replace_all {
            return Err(format!("`old` appears {count} times; add surrounding lines to make it unique, or pass all=true"));
        }
        return Ok(if replace_all {
            text.replace(old, new)
        } else {
            text.replacen(old, new, 1)
        });
    }
    // Try ignoring leading indentation: find a unique block equal after trimming.
    let old_trim: Vec<&str> = old.lines().map(|l| l.trim_start()).collect();
    let lines: Vec<&str> = text.lines().collect();
    let mut matches: Vec<usize> = Vec::new();
    if old_trim.len() <= lines.len() {
        for start in 0..=lines.len() - old_trim.len() {
            if (0..old_trim.len()).all(|i| lines[start + i].trim_start() == old_trim[i]) {
                matches.push(start);
            }
        }
    }
    if matches.len() == 1 {
        let start = matches[0];
        let indent = &lines[start][..lines[start].len() - lines[start].trim_start().len()];
        let replacement: Vec<String> = new
            .lines()
            .map(|line| {
                if line.is_empty() {
                    String::new()
                } else {
                    format!("{indent}{}", line.trim_start())
                }
            })
            .collect();
        let mut out: Vec<String> = lines.iter().map(|l| l.to_string()).collect();
        out.splice(start..start + old_trim.len(), replacement);
        return Ok(out.join("\n") + if text.ends_with('\n') { "\n" } else { "" });
    }
    Err(closest(text, old_trim[0]))
}

/// The closest lines to a failed match, so the caller can see the real text.
fn closest(text: &str, needle: &str) -> String {
    let key: String = needle
        .chars()
        .filter(|c| !c.is_whitespace())
        .take(24)
        .collect();
    for (i, line) in text.lines().enumerate() {
        if line.contains(&key) {
            return format!("closest line {}: {}", i + 1, util::clip(line, 200));
        }
    }
    "read the file and copy the exact text, whitespace included".to_string()
}

/// `splice`: replace 1-based inclusive lines `start..=end` with `text`.
pub fn splice(ctx: &Ctx, args: &Value) -> Env {
    let path = args["path"].as_str().unwrap_or("");
    let start = args["start"].as_u64().unwrap_or(0) as usize;
    if path.is_empty() || start == 0 {
        return Env::fail(
            "splice",
            "bad_arguments",
            "splice needs path and start >= 1",
            "pass path, start, end (end=start-1 inserts)",
        );
    }
    let end = match args["end"].as_u64() {
        Some(end) => end as usize,
        None => {
            return Env::fail(
                "splice",
                "bad_arguments",
                "splice needs end",
                "pass end=start-1 to insert before start, end=start to replace it",
            )
        }
    };
    if end + 1 < start {
        return Env::fail(
            "splice",
            "bad_arguments",
            format!("start={start} end={end} is not a range"),
            "end must be >= start-1",
        );
    }
    let text = args["text"].as_str().unwrap_or("");
    let full = util::resolve(&ctx.root, path);
    let shown = util::relative(&ctx.root, &full);
    let before = match std::fs::read_to_string(&full) {
        Ok(text) => text,
        Err(e) => {
            return Env::fail(
                "splice",
                "not_found",
                format!("{shown}: {e}"),
                "use write for a new file",
            )
        }
    };
    let lines: Vec<&str> = before.lines().collect();
    if start > lines.len() + 1 {
        return Env::fail(
            "splice",
            "out_of_range",
            format!("{shown} has {} lines; start={start}", lines.len()),
            "read the file first, then splice inside its range",
        );
    }
    let last = end.min(lines.len());
    let mut out: Vec<String> = lines.iter().map(|l| l.to_string()).collect();
    let replacement: Vec<String> = if text.is_empty() {
        Vec::new()
    } else {
        text.lines().map(|l| l.to_string()).collect()
    };
    out.splice(start - 1..last, replacement);
    let after = out.join("\n")
        + if before.ends_with('\n') || !out.is_empty() {
            "\n"
        } else {
            ""
        };
    if let Err(e) = std::fs::write(&full, &after) {
        return Env::fail(
            "splice",
            "write_failed",
            format!("{shown}: {e}"),
            "check permissions",
        );
    }
    let env = Env::ok("splice")
        .field("path", shown.clone())
        .field("start", start)
        .field("end", end)
        .body(format!(
            "spliced {shown} {start}-{end} ({} lines before, {} after)",
            lines.len(),
            after.lines().count()
        ));
    ctx.commit(env, &shown, Some(before), Some(after))
}

/// `delete`: remove one file through the journal.
pub fn delete(ctx: &Ctx, args: &Value) -> Env {
    let path = args["path"].as_str().unwrap_or("");
    if path.is_empty() {
        return Env::fail(
            "delete",
            "bad_arguments",
            "delete needs a path",
            "pass the file path, not a directory",
        );
    }
    let full = util::resolve(&ctx.root, path);
    let shown = util::relative(&ctx.root, &full);
    if full.is_dir() {
        return Env::fail(
            "delete",
            "is_directory",
            format!("{shown} is a directory"),
            "map(path) first, then delete the files it lists",
        );
    }
    let before = match std::fs::read_to_string(&full) {
        Ok(text) => text,
        Err(e) => {
            return Env::fail(
                "delete",
                "not_found",
                format!("{shown}: {e}"),
                "call map() to see what exists",
            )
        }
    };
    if let Err(e) = std::fs::remove_file(&full) {
        return Env::fail(
            "delete",
            "delete_failed",
            format!("{shown}: {e}"),
            "check permissions",
        );
    }
    let env = Env::ok("delete")
        .field("path", shown.clone())
        .body(format!("deleted {shown}"));
    ctx.commit(env, &shown, Some(before), None)
}

/// `mkdir`: create one directory (parents included).
pub fn mkdir(ctx: &Ctx, args: &Value) -> Env {
    let path = args["path"].as_str().unwrap_or("");
    if path.is_empty() {
        return Env::fail(
            "mkdir",
            "bad_arguments",
            "mkdir needs a path",
            "pass path=\"src/new\"",
        );
    }
    let full = util::resolve(&ctx.root, path);
    let shown = util::relative(&ctx.root, &full);
    if full.is_dir() {
        return Env::ok("mkdir")
            .field("path", shown.clone())
            .body(format!("{shown} already exists"));
    }
    match std::fs::create_dir_all(&full) {
        Ok(()) => Env::ok("mkdir")
            .field("path", shown.clone())
            .body(format!("created {shown}")),
        Err(e) => Env::fail(
            "mkdir",
            "mkdir_failed",
            format!("{shown}: {e}"),
            "check permissions",
        ),
    }
}

/// `apply`: one unified diff across one or several files, journaled as one call.
pub fn apply(ctx: &Ctx, args: &Value) -> Env {
    let patch = match args["patch"].as_str() {
        Some(p) if !p.trim().is_empty() => p,
        _ => {
            return Env::fail(
                "apply",
                "bad_arguments",
                "apply needs a unified diff",
                "pass patch=\"--- a/file\\n+++ b/file\\n@@ ...\"",
            )
        }
    };
    let mut files: Vec<(String, String)> = Vec::new();
    for line in patch.lines() {
        if let Some(rest) = line.strip_prefix("+++ ") {
            files.push((
                rest.trim().trim_start_matches("b/").to_string(),
                String::new(),
            ));
            continue;
        }
        if line.starts_with("--- ") {
            continue;
        }
        if let Some((_, body)) = files.last_mut() {
            body.push_str(line);
            body.push('\n');
        }
    }
    if files.is_empty() {
        return Env::fail(
            "apply",
            "bad_patch",
            "no `+++ b/path` header found",
            "supply a real unified diff; prefer edit(edits=[...])",
        );
    }
    let mut updates: Vec<(String, String, String)> = Vec::new();
    for (path, hunks) in files {
        let full = util::resolve(&ctx.root, &path);
        let before = match std::fs::read_to_string(&full) {
            Ok(text) => text,
            Err(e) => {
                return Env::fail(
                    "apply",
                    "not_found",
                    format!("{path}: {e}"),
                    "apply edits existing files; use write for a new file",
                )
            }
        };
        match util::apply_patch(&before, &hunks) {
            Ok(after) => updates.push((path, before, after)),
            Err(e) => {
                return Env::fail(
                    "apply",
                    "bad_patch",
                    format!("{path}: {e}"),
                    "read the file and rebuild the diff, or use edit(edits=[...])",
                )
            }
        }
    }
    let mut changed: Vec<String> = Vec::new();
    for (path, before, after) in &updates {
        let full = util::resolve(&ctx.root, path);
        if let Err(e) = std::fs::write(&full, after) {
            return Env::fail(
                "apply",
                "write_failed",
                format!("{path}: {e}"),
                "check permissions",
            );
        }
        if ctx
            .journal
            .borrow_mut()
            .record(path, Some(before.clone()), Some(after.clone()))
        {
            changed.push(path.clone());
        }
    }
    let mut env = Env::ok("apply")
        .field("paths", changed.clone())
        .body(format!(
            "applied {} file(s): {}",
            changed.len(),
            changed.join(", ")
        ));
    if let Some(first) = changed.first() {
        env.delta = Some(ctx.journal.borrow().diff(Some(first), 6000));
    }
    env.changed = changed;
    env
}
