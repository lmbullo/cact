//! card: one cell rendered as a state card: [s]/[obs]/[page]/[data]/[read], never a transcript.
//!
//! WHAT IT IS
//!   `render_body(env)` is the observation text a verb returns (also what a cell's verb call
//!   gets back); `render(cell, ...)` wraps one finished cell in the card the model reads next turn.
//!
//! INVARIANTS
//!   a refusal is structured: `[error] kind: code: message; use ...` on one line plus the body.
//!   paging is quoted from the envelope, never recomputed; `complete`/`has_more` are the truth.
//!   process output names the `_OBS[n]` handle holding the full text, so nothing is silently lost.
//!   `[read]` is built from the ranges actually delivered, so it cannot claim an undelivered line.
//!   `clip_card` is a delivery policy, never a verdict: `[error]`/`[check]` lines stay visible.

use crate::envelope::Env;
use crate::kernel::Cell;
use crate::tools::RunState;
use crate::util;

/// The observation body one verb returns: status, paging, data fields, refusal, body.
pub fn render_body(env: &Env) -> String {
    let mut parts: Vec<String> = Vec::new();
    let status = if env.ok { "ok" } else { "error" };
    parts.push(format!("[obs] {}: {status}", env.kind));
    if let Some(page) = &env.page {
        parts.push(format!(
            "[page] shown={} total={} unit={} offset={} has_more={} truncated={} complete={}",
            page.shown,
            page.total,
            page.unit,
            page.offset,
            page.has_more,
            page.truncated,
            page.complete
        ));
        if let Some(next) = page.next {
            parts.push(format!("[page] next.offset={next}"));
        }
    }
    if let Some(data) = &env.data {
        if !data.is_empty() {
            let rows: Vec<String> = data
                .iter()
                .map(|(k, v)| format!("{k}={}", util::clip(&compact(v), 400)))
                .collect();
            parts.push(format!("[data] {}", rows.join(" ")));
        }
    }
    for note in &env.notes {
        parts.push(format!("[note] {}", collapse(note)));
    }
    if let Some(error) = &env.error {
        parts.push(format!(
            "[error] {}: {}: {}; use {}",
            env.kind,
            error.code,
            collapse(&error.message),
            collapse(&error.use_hint)
        ));
    }
    if let Some(body) = &env.body {
        if !body.is_empty() {
            parts.push(body.clone());
        }
    }
    parts.join("\n")
}

/// One finished cell as the card the model reads. `observed` is whether the verb's own envelope
/// was already rendered into the body.
pub fn render(
    cell: &Cell,
    step: usize,
    prompt_tokens: u64,
    state: &RunState,
    obs_handle: Option<&str>,
) -> String {
    let mut parts = vec![format!(
        "[s] step={step} ms={} tok={prompt_tokens}",
        cell.duration_ms
    )];
    parts.push(state.phase_line());
    parts.extend(state.plan_lines());
    if !cell.changed.is_empty() {
        parts.push(format!("chg: {}", cell.changed.join(", ")));
    }
    if cell.timed_out {
        parts.push("[state] timeout: cell interrupted; the kernel was restarted".into());
    }
    if cell.restarted {
        parts.push("[state] kernel restarted: the namespace was lost; variables and imports are gone (earlier output survives in page())".into());
    }
    if let Some(delta) = &cell.delta {
        if !delta.is_empty() {
            parts.push(format!("[diff]\n{}", util::clip(delta, 6000)));
        }
    }
    if let Some(check) = &state.last_check {
        parts.push(format!("[check] {}", util::clip(check, 240)));
    }
    if let Some(env) = &cell.envelope {
        parts.push(render_body(env));
    } else if !cell.tool.is_empty() && !has_cell_output(cell) {
        parts.push(format!("[tool] {}", cell.tool));
    }
    if let Some(error) = &cell.error {
        let tail: String = error
            .lines()
            .rev()
            .take(6)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>()
            .join("\n");
        parts.push(format!(
            "[error] cell: runtime_error: {}\n{}",
            util::clip(error.lines().last().unwrap_or("error"), 200),
            util::clip(&tail, 1200)
        ));
    }
    let handle = obs_handle
        .map(|h| h.to_string())
        .or_else(|| cell.obs_index.map(|i| format!("_OBS[{i}]")));
    let has_output = !cell.stdout.is_empty() || !cell.stderr.is_empty() || !cell.result.is_empty();
    if cell.envelope.is_none() && has_output {
        let budget = 6000;
        let mut shown_stdout = util::clip(&cell.stdout, budget);
        let mut shown_stderr = util::clip(&cell.stderr, budget);
        if let Some(handle) = &handle {
            if shown_stdout.len() < cell.stdout.len() {
                shown_stdout = format!(
                    "{}\n...[full output: {handle}; page(handle={handle})]",
                    shown_stdout
                );
            }
            if shown_stderr.len() < cell.stderr.len() {
                shown_stderr = format!(
                    "{}\n...[full output: {handle}; page(handle={handle})]",
                    shown_stderr
                );
            }
        }
        if !shown_stdout.trim().is_empty() {
            parts.push(format!("[stdout]\n{}", shown_stdout.trim_end()));
        }
        if !shown_stderr.trim().is_empty() {
            parts.push(format!("[stderr]\n{}", shown_stderr.trim_end()));
        }
        if !cell.result.is_empty() {
            parts.push(format!("[result] {}", util::clip(&cell.result, 2000)));
        }
    }
    if !cell.reads.is_empty() {
        let rows: Vec<String> = cell
            .reads
            .iter()
            .map(|(path, start, end)| format!("{path} {start}-{end}"))
            .collect();
        parts.push(format!("[read] {}", rows.join("; ")));
    }
    parts.join("\n")
}

/// `(text, was_clipped)`: a whole card while it fits, else its head plus a named `_OBS` handle.
///
/// The head cut never hides a refusal or a verdict: every `[error]`/`[check]` line not already
/// in the head is appended after the marker. The caller names the exact resume call.
pub fn clip_card(text: &str, budget: usize, handle: &str) -> (String, bool) {
    let total = text.chars().count();
    if total <= budget {
        return (text.to_string(), false);
    }
    let marker = format!("[card clipped: {total} chars total; full text: {handle}]");
    let marker_len = marker.chars().count();
    let keep = budget.saturating_sub(marker_len + 1);
    let head: String = text.chars().take(keep).collect();
    let shown: Vec<&str> = head.lines().collect();
    let mut pinned: Vec<&str> = Vec::new();
    for line in text.lines() {
        if (line.starts_with("[error]") || line.starts_with("[check]"))
            && !shown.contains(&line)
            && !pinned.contains(&line)
        {
            pinned.push(line);
        }
    }
    let mut out = head;
    if !out.ends_with('\n') {
        out.push('\n');
    }
    out.push_str(&marker);
    if !pinned.is_empty() {
        out.push('\n');
        out.push_str(&pinned.join("\n"));
    }
    (out, true)
}

/// True when the cell produced text of its own (stdout, stderr or a value).
fn has_cell_output(cell: &Cell) -> bool {
    !cell.stdout.is_empty() || !cell.stderr.is_empty() || !cell.result.is_empty()
}

/// Whitespace-collapse a note so it stays on one line.
fn collapse(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// A compact JSON rendering for one `[data]` field.
fn compact(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}
