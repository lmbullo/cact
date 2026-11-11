//! guide: which module owns this, answered from the source headers themselves.
//!
//! WHAT IT IS
//!   `guide()` lists the modules this tree has a header for; `guide(query=...)` ranks them by
//!   term overlap and returns one-line rows; `guide(path=...)` returns one module's whole header.
//!   `skill()` loads a project procedure from `.agent/skills/<name>.md`.
//!
//! INVARIANTS
//!   only source headers are indexed: a module with no leading header has no row.
//!   the ranking is truthful: total and next.offset page the same list the caller sees.
//!   a missing skill is a structured refusal (not_found), never an empty answer.

use crate::envelope::{Env, Page};
use crate::tools::{map, Ctx};
use crate::util;
use serde_json::Value;

/// One indexed module: its workspace-relative path, one-line summary and full header.
pub struct Module {
    pub path: String,
    pub summary: String,
    pub header: String,
}

/// `guide`: the module map, a ranked query, or one module's header.
pub fn guide(ctx: &Ctx, args: &Value) -> Env {
    let modules = index(ctx);
    if let Some(path) = args["path"].as_str() {
        match modules
            .iter()
            .find(|m| m.path == path || m.path.ends_with(path))
        {
            Some(module) => {
                return Env::ok("guide")
                    .field("path", module.path.clone())
                    .body(module.header.clone())
            }
            None => {
                return Env::fail(
                    "guide",
                    "not_found",
                    format!("no indexed header for {path}"),
                    "call guide() for the module map, or read(path=...) for the file",
                )
            }
        }
    }
    let query = args["query"].as_str().unwrap_or("").trim().to_string();
    let limit = args["limit"].as_u64().unwrap_or(10) as usize;
    let offset = args["offset"].as_u64().unwrap_or(0) as usize;
    let mut rows: Vec<&Module> = modules.iter().collect();
    if !query.is_empty() {
        let terms: Vec<String> = query
            .to_lowercase()
            .split_whitespace()
            .map(|s| s.to_string())
            .collect();
        rows.sort_by_key(|m| {
            let hay = format!("{} {}", m.path, m.header).to_lowercase();
            let hits = terms.iter().filter(|t| hay.contains(t.as_str())).count();
            std::cmp::Reverse(hits)
        });
        rows.retain(|m| {
            let hay = format!("{} {}", m.path, m.header).to_lowercase();
            terms.iter().any(|t| hay.contains(t.as_str()))
        });
    }
    if rows.is_empty() {
        return Env::ok("guide")
            .paged(Page::whole(0, "module"))
            .body(format!(
                "no module header matches {query:?}; guide() lists all {} indexed modules",
                modules.len()
            ));
    }
    let total = rows.len();
    let end = (offset + limit).min(total);
    let window = &rows[offset.min(total)..end];
    let body = window
        .iter()
        .map(|m| format!("{}  {}", m.path, m.summary))
        .collect::<Vec<_>>()
        .join("\n");
    Env::ok("guide")
        .field("modules", total)
        .paged(Page::window(
            window.len(),
            total,
            offset.min(total),
            "module",
        ))
        .body(
            body + &if end < total {
                format!("\nnext: offset={end}")
            } else {
                String::new()
            },
        )
}

/// `skill`: list procedures, or return one whole.
pub fn skill(ctx: &Ctx, args: &Value) -> Env {
    let root = args["root"]
        .as_str()
        .map(|r| util::resolve(&ctx.root, r))
        .unwrap_or_else(|| ctx.root.clone());
    let dir = root.join(".agent").join("skills");
    let name = args["name"].as_str().unwrap_or("").trim();
    if name.is_empty() {
        let mut names: Vec<String> = std::fs::read_dir(&dir)
            .map(|entries| {
                entries
                    .flatten()
                    .filter_map(|e| {
                        e.file_name()
                            .to_str()
                            .map(|n| n.trim_end_matches(".md").to_string())
                    })
                    .collect()
            })
            .unwrap_or_default();
        names.sort();
        return Env::ok("skill")
            .field("skills", names.clone())
            .body(if names.is_empty() {
                "no project skills under .agent/skills".to_string()
            } else {
                names.join("\n")
            });
    }
    let path = dir.join(format!("{name}.md"));
    match std::fs::read_to_string(&path) {
        Ok(text) => Env::ok("skill").field("name", name).body(text),
        Err(_) => Env::fail(
            "skill",
            "not_found",
            format!("no skill `{name}` under .agent/skills"),
            "call skill() for the available names",
        ),
    }
}

/// Scan the tree once and index every file with a leading header block.
fn index(ctx: &Ctx) -> Vec<Module> {
    let files = map::walk(&ctx.root, &ctx.root, &[]);
    let mut out: Vec<Module> = Vec::new();
    for file in files {
        let shown = util::relative(&ctx.root, &file);
        if shown.starts_with(".agent/") {
            continue;
        }
        let ext = file.extension().and_then(|e| e.to_str()).unwrap_or("");
        if !matches!(ext, "rs" | "py" | "ts" | "tsx" | "js" | "md") {
            continue;
        }
        let text = match std::fs::read_to_string(&file) {
            Ok(text) => text,
            Err(_) => continue,
        };
        if let Some(header) = header_of(&text, ext) {
            let summary = header
                .lines()
                .next()
                .unwrap_or("")
                .trim_start_matches("#!")
                .trim_start_matches("///")
                .trim()
                .to_string();
            out.push(Module {
                path: shown,
                summary,
                header,
            });
        }
    }
    out.sort_by(|a, b| a.path.cmp(&b.path));
    out
}

/// The leading header block of a source file, by language comment style.
fn header_of(text: &str, ext: &str) -> Option<String> {
    match ext {
        "rs" => {
            let lines: Vec<&str> = text
                .lines()
                .take_while(|l| l.trim_start().starts_with("//!"))
                .collect();
            if lines.is_empty() {
                return None;
            }
            Some(
                lines
                    .iter()
                    .map(|l| l.trim_start().trim_start_matches("//!").trim_start())
                    .collect::<Vec<_>>()
                    .join("\n"),
            )
        }
        "py" => {
            let mut rest = text.trim_start();
            if !(rest.starts_with("\"\"\"") || rest.starts_with("'''")) {
                let first_const = rest.find("\"\"\"").or_else(|| rest.find("'''"))?;
                if first_const > 6
                    || !rest[..first_const]
                        .lines()
                        .all(|l| l.trim().is_empty() || l.trim().starts_with('#'))
                {
                    return None;
                }
                rest = &rest[first_const..];
            }
            let quote = &rest[..3];
            let body = rest[3..].split(quote).next()?;
            Some(body.trim().to_string())
        }
        "ts" | "tsx" | "js" => {
            let trimmed = text.trim_start();
            if !trimmed.starts_with("/**") {
                return None;
            }
            let body = trimmed[3..].split("*/").next()?;
            Some(
                body.lines()
                    .map(|l| l.trim_start().trim_start_matches('*').trim_start())
                    .collect::<Vec<_>>()
                    .join("\n")
                    .trim()
                    .to_string(),
            )
        }
        "md" => text
            .lines()
            .find(|l| l.starts_with("# "))
            .map(|l| l.trim_start_matches("# ").to_string()),
        _ => None,
    }
}
