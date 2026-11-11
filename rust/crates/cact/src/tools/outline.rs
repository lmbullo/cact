//! outline: a file's structure with line ranges, and section lookup for `read`.
//!
//! WHAT IT IS
//!   `outline(path)` lists Markdown headings, Python classes/functions/methods, TS/JS defs and
//!   tests, and HTML headings, each with its 1-based line range. A Markdown heading covers its
//!   subtree (up to the next same-or-shallower heading).
//!
//! INVARIANTS
//!   a range is exact: it is the block the source's own indentation/heading level defines.
//!   `section_range` answers the same ranges for `read(section=...)`, so the two never disagree.
//!   an unsupported extension is an empty outline, not an error: the caller falls back to read.

use crate::envelope::Env;
use crate::tools::Ctx;
use crate::util;
use serde_json::Value;
use std::path::Path;

/// One outline row: kind, name, first and last line.
pub struct Entry {
    pub kind: String,
    pub name: String,
    pub start: usize,
    pub end: usize,
}

/// `outline`: one row per structural entry, in source order.
pub fn outline(ctx: &Ctx, args: &Value) -> Env {
    let max_entries = args["max_entries"].as_u64().unwrap_or(200) as usize;
    let paths: Vec<String> = match &args["path"] {
        Value::Array(a) => a.iter().filter_map(|v| v.as_str()).map(|s| s.to_string()).collect(),
        Value::String(s) => vec![s.clone()],
        _ => Vec::new(),
    };
    if paths.is_empty() {
        return Env::fail("outline", "bad_arguments", "outline needs a path or a list of paths", "pass path=\"src/main.rs\"");
    }
    let mut blocks: Vec<String> = Vec::new();
    let mut total = 0usize;
    for path in &paths {
        let full = util::resolve(&ctx.root, path);
        let text = match std::fs::read_to_string(&full) {
            Ok(text) => text,
            Err(e) => {
                return Env::fail("outline", "not_found", format!("{path}: {e}"), "call map() to see the tree");
            }
        };
        let shown = util::relative(&ctx.root, &full);
        let entries = entries_for(&shown, &text);
        let mut rows: Vec<String> = Vec::new();
        for entry in entries.iter().take(max_entries) {
            rows.push(format!("{}-{}  {} {}", entry.start, entry.end, entry.kind, entry.name));
        }
        if rows.is_empty() {
            rows.push(format!("no structural entries recognized in {shown}"));
        }
        total += entries.len();
        blocks.push(format!("## {shown}\n{}", rows.join("\n")));
    }
    let body = blocks.join("\n\n");
    Env::ok("outline")
        .field("entries", total)
        .body(util::clip(&body, 24_000))
}

/// The `(start, end)` a section name resolves to, for `read(section=...)`.
pub fn section_range(root: &Path, path: &str, section: &str) -> Option<(usize, usize)> {
    let full = util::resolve(root, path);
    let text = std::fs::read_to_string(full).ok()?;
    let entries = entries_for(path, &text);
    entries
        .iter()
        .find(|e| e.name.eq_ignore_ascii_case(section))
        .or_else(|| entries.iter().find(|e| e.name.contains(section)))
        .map(|e| (e.start, e.end))
}

/// Every structural entry for one file body, by extension.
fn entries_for(path: &str, text: &str) -> Vec<Entry> {
    if path.ends_with(".md") {
        headings(text)
    } else if path.ends_with(".py") {
        python_defs(text)
    } else if path.ends_with(".ts") || path.ends_with(".tsx") || path.ends_with(".js") || path.ends_with(".jsx") {
        ts_defs(text)
    } else if path.ends_with(".html") || path.ends_with(".htm") {
        html_headings(text)
    } else if path.ends_with(".rs") {
        rust_defs(text)
    } else {
        Vec::new()
    }
}

/// Markdown headings; each ends before the next heading of the same or shallower level.
fn headings(text: &str) -> Vec<Entry> {
    let lines: Vec<&str> = text.lines().collect();
    let mut found: Vec<(usize, usize, String)> = Vec::new();
    let mut in_fence = false;
    for (i, line) in lines.iter().enumerate() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("```") {
            in_fence = !in_fence;
            continue;
        }
        if in_fence {
            continue;
        }
        let level = trimmed.chars().take_while(|c| *c == '#').count();
        if level > 0 && level <= 6 && trimmed.chars().nth(level) == Some(' ') {
            found.push((i + 1, level, trimmed[level..].trim().to_string()));
        }
    }
    let mut out = Vec::new();
    for (index, (start, level, name)) in found.iter().enumerate() {
        let mut end = lines.len();
        for (next_start, next_level, _) in found.iter().skip(index + 1) {
            if next_level <= level {
                end = next_start - 1;
                break;
            }
        }
        out.push(Entry { kind: format!("h{level}"), name: name.clone(), start: *start, end });
    }
    out
}

/// Python classes/functions/methods by indentation; a block ends before the next def at a
/// shallower-or-equal indent.
fn python_defs(text: &str) -> Vec<Entry> {
    let lines: Vec<&str> = text.lines().collect();
    let mut found: Vec<(usize, usize, String)> = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        let indent = line.len() - line.trim_start().len();
        let trimmed = line.trim_start();
        if trimmed.starts_with("def ") || trimmed.starts_with("class ") || trimmed.starts_with("async def ") {
            let name: String = trimmed
                .trim_start_matches("async ")
                .trim_start_matches("def ")
                .trim_start_matches("class ")
                .chars()
                .take_while(|c| c.is_alphanumeric() || *c == '_')
                .collect();
            found.push((i + 1, indent, name));
        }
    }
    with_blocks(&lines, &found)
}

/// Rust items: fn/struct/enum/impl/trait/mod, closed by the next item at the same indent.
fn rust_defs(text: &str) -> Vec<Entry> {
    let lines: Vec<&str> = text.lines().collect();
    let mut found: Vec<(usize, usize, String)> = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        let trimmed = line.trim_start();
        let keyword = ["pub fn ", "fn ", "pub struct ", "struct ", "pub enum ", "enum ", "pub trait ", "trait ", "impl ", "pub mod ", "mod "]
            .iter()
            .find(|k| trimmed.starts_with(**k));
        if let Some(k) = keyword {
            let name: String = trimmed[k.len()..].chars().take_while(|c| c.is_alphanumeric() || *c == '_').collect();
            found.push((i + 1, 0, name));
        }
    }
    with_blocks(&lines, &found)
}

/// TS/JS definitions and tests.
fn ts_defs(text: &str) -> Vec<Entry> {
    let lines: Vec<&str> = text.lines().collect();
    let mut found: Vec<(usize, usize, String)> = Vec::new();
    let patterns = [
        ("export function ", "function"),
        ("export default function ", "function"),
        ("function ", "function"),
        ("export class ", "class"),
        ("class ", "class"),
        ("export const ", "const"),
        ("const ", "const"),
        ("export interface ", "interface"),
        ("interface ", "interface"),
    ];
    for (i, line) in lines.iter().enumerate() {
        let trimmed = line.trim_start();
        let is_test = trimmed.starts_with("test(") || trimmed.starts_with("it(") || trimmed.starts_with("describe(");
        if is_test {
            let name: String = trimmed
                .split_once('(')
                .and_then(|(_, rest)| rest.split_once('"').or_else(|| rest.split_once('\'')))
                .map(|(_, rest)| rest.split('"').next().or_else(|| rest.split('\'').next()).unwrap_or("").to_string())
                .unwrap_or_default();
            found.push((i + 1, 0, format!("test {name}")));
            continue;
        }
        for (prefix, kind) in patterns {
            if let Some(rest) = trimmed.strip_prefix(prefix) {
                let name: String = rest
                    .chars()
                    .take_while(|c| c.is_alphanumeric() || *c == '_' || *c == '$')
                    .collect();
                if !name.is_empty() {
                    found.push((i + 1, 0, format!("{kind} {name}")));
                }
                break;
            }
        }
    }
    with_blocks(&lines, &found)
}

/// HTML sections/headings, closed by the next heading of the same or shallower level.
fn html_headings(text: &str) -> Vec<Entry> {
    let lines: Vec<&str> = text.lines().collect();
    let mut out = Vec::new();
    let re = regex::Regex::new(r"(?i)<h([1-6])[^>]*>(.*?)</h[1-6]>").unwrap();
    for (i, line) in lines.iter().enumerate() {
        if let Some(caps) = re.captures(line) {
            let name = caps.get(2).map(|m| m.as_str().trim().to_string()).unwrap_or_default();
            out.push(Entry { kind: format!("h{}", &caps[1]), name, start: i + 1, end: i + 1 });
        }
    }
    out
}

/// Close each found item at the next one with an indentation no deeper than its own.
fn with_blocks(lines: &[&str], found: &[(usize, usize, String)]) -> Vec<Entry> {
    let mut out = Vec::new();
    for (index, (start, indent, name)) in found.iter().enumerate() {
        let mut end = lines.len();
        for (next_start, next_indent, _) in found.iter().skip(index + 1) {
            if next_indent <= indent {
                end = next_start - 1;
                break;
            }
        }
        let kind = if name.contains('(') { "def" } else { "block" };
        out.push(Entry { kind: kind.into(), name: name.clone(), start: *start, end });
    }
    out
}
