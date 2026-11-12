//! search: the one grep: regex or literal over a file, a directory walk, or the workspace.
//!
//! WHAT IT IS
//!   `search(pattern, path, include, context, ignore_case, max_results, offset, count, within)`
//!   returns `path:line:text` rows for hits and `path:line-text` for context, paged truthfully.
//!
//! INVARIANTS
//!   the envelope always says shown/total/offset/next/unit, so `next` is a replayable cursor.
//!   a directory walk skips the ignored trees and binary files; an explicit file is searched as given.
//!   `count=true` returns `path:count` rows and no hit text.
//!   an invalid regex is a structured refusal (bad_pattern), never an empty result.

use crate::envelope::{Env, Page};
use crate::tools::{map, Ctx};
use crate::util;
use serde_json::Value;

/// One hit: path, 1-based line, the line text, and up to `context` lines either side.
struct Hit {
    path: String,
    line: usize,
    text: String,
    before: Vec<(usize, String)>,
    after: Vec<(usize, String)>,
}

/// `search`: find `pattern` and page the hits.
pub fn search(ctx: &Ctx, args: &Value) -> Env {
    let pattern = args["pattern"].as_str().unwrap_or("");
    if pattern.is_empty() {
        return Env::fail(
            "search",
            "bad_arguments",
            "search needs a pattern",
            "pass pattern=\"TODO\" (regex by default)",
        );
    }
    let regex_mode = args["regex"].as_bool().unwrap_or(true);
    let ignore_case = args["ignore_case"].as_bool().unwrap_or(false);
    let matcher = match build_matcher(pattern, regex_mode, ignore_case) {
        Ok(m) => m,
        Err(e) => {
            return Env::fail(
                "search",
                "bad_pattern",
                e,
                "pass regex=false for a literal search, or fix the regex",
            );
        }
    };
    let context = args["context"].as_u64().unwrap_or(0) as usize;
    let max_results = match args["max_results"].as_u64() {
        Some(0) | None => 50usize,
        Some(n) => n as usize,
    };
    let offset = args["offset"].as_u64().unwrap_or(0) as usize;
    let count_only = args["count"].as_bool().unwrap_or(false);
    let within: Option<(usize, usize)> =
        args["within"].as_array().filter(|a| a.len() == 2).map(|a| {
            (
                a[0].as_u64().unwrap_or(1) as usize,
                a[1].as_u64().unwrap_or(u64::MAX) as usize,
            )
        });

    let files = match collect_files(ctx, args) {
        Ok(files) => files,
        Err(env) => return *env,
    };
    if files.is_empty() {
        return Env::fail(
            "search",
            "no_files",
            "no file matched the path/include filter",
            "call map() to see the tree, or pass an explicit path",
        );
    }
    let mut hits: Vec<Hit> = Vec::new();
    let mut counts: Vec<(String, usize)> = Vec::new();
    for file in &files {
        let text = match std::fs::read(file) {
            Ok(bytes) if !util::looks_binary(&bytes) => String::from_utf8_lossy(&bytes).to_string(),
            _ => continue,
        };
        let shown = util::relative(&ctx.root, file);
        let lines: Vec<&str> = text.lines().collect();
        let mut file_hits = 0usize;
        for (i, line) in lines.iter().enumerate() {
            let number = i + 1;
            if let Some((lo, hi)) = within {
                if number < lo || number > hi {
                    continue;
                }
            }
            if !matcher(line) {
                continue;
            }
            file_hits += 1;
            if count_only {
                continue;
            }
            let from = number.saturating_sub(1 + context).max(1);
            let to = (number + context).min(lines.len());
            hits.push(Hit {
                path: shown.clone(),
                line: number,
                text: line.to_string(),
                before: (from..number)
                    .map(|n| (n, lines[n - 1].to_string()))
                    .collect(),
                after: (number + 1..=to)
                    .map(|n| (n, lines[n - 1].to_string()))
                    .collect(),
            });
        }
        if file_hits > 0 {
            counts.push((shown, file_hits));
        }
    }
    if count_only {
        let total: usize = counts.iter().map(|(_, n)| n).sum();
        let body: Vec<String> = counts.iter().map(|(p, n)| format!("{p}:{n}")).collect();
        return Env::ok("search")
            .field("count", total)
            .paged(Page::whole(counts.len(), "file"))
            .body(body.join("\n"));
    }
    let total = hits.len();
    if total == 0 {
        return Env::ok("search")
            .field("matches", 0)
            .paged(Page::whole(0, "hit"))
            .body(format!(
                "no match for {pattern:?} in {} file(s)",
                files.len()
            ));
    }
    let end = if max_results == 0 {
        total
    } else {
        (offset + max_results).min(total)
    };
    let start = offset.min(total);
    let window = &hits[start..end];
    let mut rows: Vec<String> = Vec::new();
    let mut last_path: Option<&str> = None;
    for hit in window {
        if last_path != Some(hit.path.as_str()) {
            last_path = Some(hit.path.as_str());
        }
        for (n, text) in &hit.before {
            rows.push(format!("{}:{n}-{text}", hit.path));
        }
        rows.push(format!("{}:{}:{}", hit.path, hit.line, hit.text));
        for (n, text) in &hit.after {
            rows.push(format!("{}:{n}-{text}", hit.path));
        }
    }
    let mut env = Env::ok("search")
        .field("matches", total)
        .field("files", counts.len())
        .paged(Page::window(window.len(), total, start, "hit"))
        .body(rows.join("\n"));
    if end < total {
        env = env.note(format!("more hits: offset={end}"));
    }
    env
}

/// The line predicate a search applies to one line.
type Matcher = Box<dyn Fn(&str) -> bool>;

/// Build the line predicate: a regex, or a literal substring.
fn build_matcher(pattern: &str, regex_mode: bool, ignore_case: bool) -> Result<Matcher, String> {
    if regex_mode {
        let source = if ignore_case {
            format!("(?i){pattern}")
        } else {
            pattern.to_string()
        };
        let re = regex::Regex::new(&source).map_err(|e| e.to_string())?;
        Ok(Box::new(move |line: &str| re.is_match(line)))
    } else {
        let needle = if ignore_case {
            pattern.to_lowercase()
        } else {
            pattern.to_string()
        };
        Ok(Box::new(move |line: &str| {
            let hay = if ignore_case {
                line.to_lowercase()
            } else {
                line.to_string()
            };
            hay.contains(&needle)
        }))
    }
}

/// Resolve the request to a concrete file list (an explicit file is taken as given).
fn collect_files(ctx: &Ctx, args: &Value) -> Result<Vec<std::path::PathBuf>, Box<Env>> {
    let includes = map::include_list(&args["include"]);
    let mut out: Vec<std::path::PathBuf> = Vec::new();
    let roots: Vec<String> = match &args["path"] {
        Value::Array(a) => a
            .iter()
            .filter_map(|v| v.as_str())
            .map(|s| s.to_string())
            .collect(),
        Value::String(s) => vec![s.clone()],
        _ => Vec::new(),
    };
    if roots.is_empty() {
        out.extend(map::walk(&ctx.root, &ctx.root, &includes));
        return Ok(out);
    }
    for root in roots {
        let full = util::resolve(&ctx.root, &root);
        if full.is_file() {
            out.push(full);
        } else if full.is_dir() {
            out.extend(map::walk(&ctx.root, &full, &includes));
        } else {
            return Err(Box::new(Env::fail(
                "search",
                "not_found",
                format!(
                    "{root}: no such file or directory under {}",
                    ctx.root.display()
                ),
                "call map() to see the tree",
            )));
        }
    }
    out.sort();
    Ok(out)
}
