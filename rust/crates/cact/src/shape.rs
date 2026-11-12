//! shape: repair the tool-call shapes deepseek-flash actually emits before dispatch.
//!
//! WHAT IT IS
//!   One pure rewrite of a parsed call: `exec(command=...)` -> `shell`, `exec(code=...)` -> the
//!   Python cell, `exec(argv=[shell, -c, ...])` -> `shell`, `read(_OBS[n])` -> `page`, a
//!   truncated `_OBS[n` handle closed, `git add -A` -> `git add`, and `edit(path, old, new)` ->
//!   `edit(edits=[...])`. `parse_args` accepts a JSON object followed by stray trailing text.
//!
//! ENTRY POINTS
//!   parse_args(raw) -> (Value, note)       one argument string, repaired when trailing text exists
//!   repair(name, args, scope) -> Repair    the normalized name/arguments and one-line note
//!
//! INVARIANTS
//!   pure: no I/O and no kernel; the same call always produces the same repair.
//!   only a shape is repaired, never a value: a name is changed only when its target is granted.
//!   `note` is one line and only when something changed; the engine prefixes it to the result.

use crate::spec::Scope;
use serde::Deserialize;
use serde_json::{json, Map, Value};

/// One call after normalization: the name to dispatch, its arguments, and the note to show.
#[derive(Debug, Clone)]
pub struct Repair {
    pub name: String,
    pub args: Value,
    pub note: String,
    pub changed: bool,
}

/// Parse one tool-call argument string; keep the first object and note ignored trailing text.
pub fn parse_args(raw: &str) -> Result<(Value, String), String> {
    let raw = if raw.trim().is_empty() { "{}" } else { raw };
    let mut de = serde_json::Deserializer::from_str(raw);
    let value = Value::deserialize(&mut de).map_err(|e| e.to_string())?;
    if !value.is_object() {
        return Err("arguments must be a JSON object".into());
    }
    let note = if de.end().is_err() {
        "ignored text after the JSON object".to_string()
    } else {
        String::new()
    };
    Ok((value, note))
}

/// True when `text` is a complete `_OBS[n]` handle.
fn is_obs(text: &str) -> bool {
    let Some(rest) = text.strip_prefix("_OBS[") else {
        return false;
    };
    let Some(digits) = rest.strip_suffix(']') else {
        return false;
    };
    !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit())
}

/// `_OBS[23` -> `_OBS[23]`; `None` when it is already closed or not a handle at all.
fn closed_obs(value: Option<&Value>) -> Option<String> {
    let text = value?.as_str()?.trim();
    let rest = text.strip_prefix("_OBS[")?;
    if rest.ends_with(']') || rest.is_empty() || !rest.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    Some(format!("{text}]"))
}

/// The shell command inside `argv=[shell, -c|-lc, string]`, else None.
fn bash_string(argv: Option<&Value>) -> Option<String> {
    const SHELLS: &[&str] = &["bash", "sh", "zsh", "dash", "ksh"];
    let parts = argv?.as_array()?;
    if parts.len() < 3 {
        return None;
    }
    let program = parts[0].as_str()?.rsplit('/').next().unwrap_or("");
    if !SHELLS.contains(&program) {
        return None;
    }
    let has_c = parts[1..].iter().any(|part| {
        let text = part.as_str().unwrap_or("");
        text.starts_with('-') && text[1..].contains('c') && !text.starts_with("--")
    });
    if !has_c {
        return None;
    }
    parts[1..]
        .iter()
        .rev()
        .find_map(|part| part.as_str().filter(|text| !text.starts_with('-')))
        .map(|text| text.to_string())
}

/// Rewrite one parsed call to the typed signature; anything uncertain is left untouched.
pub fn repair(name: &str, args: Value, scope: &Scope) -> Repair {
    let Value::Object(mut call) = args else {
        return Repair {
            name: name.to_string(),
            args: Value::Object(Map::new()),
            note: String::new(),
            changed: false,
        };
    };
    clean_paths(&mut call);
    let original = name;
    match name {
        "page" => {
            if let Some(closed) = closed_obs(call.get("handle")) {
                let note = format!("page handle {:?} ran as {closed:?}", call.get("handle"));
                call.insert("handle".into(), json!(closed));
                return changed(original, &call, note);
            }
        }
        "exec" => {
            if call.contains_key("commands") {
                if call.contains_key("command")
                    && !call.contains_key("argv")
                    && !call.contains_key("shell")
                {
                    call.remove("command");
                    return changed(
                        original,
                        &call,
                        "exec(command=...) was ignored because commands=... was present",
                    );
                }
                return Repair {
                    name: original.into(),
                    args: Value::Object(call),
                    note: String::new(),
                    changed: false,
                };
            }
            if call.contains_key("command")
                && !call.contains_key("argv")
                && !call.contains_key("shell")
                && !call.contains_key("code")
            {
                let command = call.remove("command").unwrap_or(Value::Null);
                call.insert("shell".into(), command);
                return changed(original, &call, "exec(command=...) ran as shell=<string>");
            }
            if call.contains_key("code")
                && !call.contains_key("argv")
                && !call.contains_key("shell")
                && !call.contains_key("commands")
            {
                let code = call.remove("code").unwrap_or(Value::Null);
                if code.is_string() && scope.allows("python") {
                    return Repair {
                        name: "python".into(),
                        args: json!({"code": code}),
                        note: "exec(code=...) ran as the python cell".into(),
                        changed: true,
                    };
                }
                call.insert("code".into(), code);
            }
            if !call.contains_key("shell") {
                if let Some(shell) = bash_string(call.get("argv")) {
                    call.remove("argv");
                    call.insert("shell".into(), json!(shell));
                    return changed(
                        original,
                        &call,
                        "exec(argv=[shell, -c, ...]) ran as shell=<string>",
                    );
                }
            }
        }
        "read" => {
            if let Some(path) = call.get("path").and_then(|v| v.as_str()) {
                let mut note = String::new();
                let mut handle = path.to_string();
                if let Some(closed) = closed_obs(call.get("path")) {
                    note = format!("read handle {path:?} ran as {closed:?}");
                    handle = closed;
                }
                if is_obs(handle.trim()) && scope.allows("page") {
                    let mut page = Map::new();
                    page.insert("handle".into(), json!(handle.trim()));
                    for key in ["offset", "limit", "field"] {
                        if let Some(value) = call.get(key) {
                            page.insert(key.into(), value.clone());
                        }
                    }
                    return Repair {
                        name: "page".into(),
                        args: Value::Object(page),
                        note: if note.is_empty() {
                            "read(_OBS[n]) ran as page(handle)".into()
                        } else {
                            note
                        },
                        changed: true,
                    };
                }
                if !note.is_empty() {
                    call.insert("path".into(), json!(handle));
                    return changed(original, &call, note);
                }
            }
        }
        "git" => {
            if call
                .get("action")
                .and_then(|v| v.as_str())
                .map(|a| a.eq_ignore_ascii_case("add"))
                == Some(true)
            {
                if let Some(paths) = call.get("paths").and_then(|v| v.as_array()) {
                    let mut kept: Vec<Value> = Vec::new();
                    let mut dropped = false;
                    for path in paths {
                        let text = path.as_str().unwrap_or("");
                        if text == "-A" || text == "--all" {
                            dropped = true;
                        } else {
                            kept.push(path.clone());
                        }
                    }
                    if dropped {
                        call.insert("paths".into(), json!(kept));
                        return changed(
                            original,
                            &call,
                            "git add(-A) ran as git add with the flag removed",
                        );
                    }
                }
            }
        }
        "edit" => {
            let single = ["path", "old", "new"].iter().all(|k| call.contains_key(*k));
            if !call.contains_key("edits") && single {
                let one = json!({"path": call["path"].clone(), "old": call["old"].clone(), "new": call["new"].clone()});
                return Repair {
                    name: original.into(),
                    args: json!({"edits": [one]}),
                    note: "edit(path, old, new) ran as edit(edits=[...])".into(),
                    changed: true,
                };
            }
            if call.get("edits").map(|v| v.is_object()) == Some(true) {
                let one = call.get("edits").cloned().unwrap_or(Value::Null);
                call.insert("edits".into(), json!([one]));
                return changed(
                    original,
                    &call,
                    "edit(edits={...}) ran as edit(edits=[{...}])",
                );
            }
        }
        _ => {}
    }
    Repair {
        name: original.to_string(),
        args: Value::Object(call),
        note: String::new(),
        changed: false,
    }
}

/// Strip shell-style quotes and surrounding whitespace from the path-like arguments.
fn clean_paths(call: &mut Map<String, Value>) {
    for key in ["path", "cwd", "root", "include"] {
        if let Some(value) = call.get_mut(key) {
            clean_value(value);
        }
    }
    for key in ["paths", "ranges"] {
        if let Some(list) = call.get_mut(key).and_then(|v| v.as_array_mut()) {
            for item in list {
                clean_value(item);
                if let Some(obj) = item.as_object_mut() {
                    clean_paths(obj);
                }
            }
        }
    }
    if let Some(edits) = call.get_mut("edits") {
        match edits {
            Value::Array(list) => {
                for item in list {
                    if let Some(obj) = item.as_object_mut() {
                        clean_paths(obj);
                    }
                }
            }
            Value::Object(obj) => clean_paths(obj),
            _ => {}
        }
    }
}

/// Strip matching quotes around one string value, and trim whitespace around it.
fn clean_value(value: &mut Value) {
    if let Some(text) = value.as_str() {
        let trimmed = text.trim();
        let unquoted = trimmed
            .strip_prefix('"')
            .and_then(|s| s.strip_suffix('"'))
            .or_else(|| {
                trimmed
                    .strip_prefix('\'')
                    .and_then(|s| s.strip_suffix('\''))
            })
            .unwrap_or(trimmed);
        if unquoted != text {
            *value = json!(unquoted);
        }
    }
}

/// Build a changed repair from the mutated object and its note.
fn changed(name: &str, call: &Map<String, Value>, note: impl Into<String>) -> Repair {
    Repair {
        name: name.to_string(),
        args: Value::Object(call.clone()),
        note: note.into(),
        changed: true,
    }
}
