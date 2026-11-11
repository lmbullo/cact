//! util: paths, text clipping, process spawning, diffs.
//!
//! Every helper here is pure or fs-only and reports a plain `Result<_, String>`, so the tool
//! layer decides the envelope error code. Nothing here reads the agent's own state.

use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// Clip `text` to `cap` chars (byte-safe on char boundaries) and name the cut.
pub fn clip(text: &str, cap: usize) -> String {
    if cap == 0 || text.chars().count() <= cap {
        return text.to_string();
    }
    let head: String = text.chars().take(cap).collect();
    format!("{head}...[clipped at {cap} chars]")
}

/// Resolve `path` against `root`; an absolute path is taken as given.
pub fn resolve(root: &Path, path: &str) -> PathBuf {
    let p = Path::new(path);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        root.join(p)
    }
}

/// The workspace-relative spelling of `path` when it is under `root`, else the path itself.
pub fn relative(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| path.to_string_lossy().to_string())
}

/// The 1-based line window `start..=end` of `text`, plus the file's line count.
pub fn line_window(text: &str, start: usize, end: Option<usize>, page: usize) -> (String, usize, usize) {
    let lines: Vec<&str> = text.lines().collect();
    let total = lines.len();
    let first = start.max(1);
    if total == 0 || first > total {
        return (String::new(), first, first.saturating_sub(1));
    }
    let last = match end {
        Some(e) if e >= first => e.min(total),
        Some(_) => first.saturating_sub(1),
        None => (first + page - 1).min(total),
    };
    let body = if last >= first {
        lines[first - 1..last].join("\n")
    } else {
        String::new()
    };
    (body, first, last)
}

/// Number `body` with 1-based line numbers starting at `first`, `" 12| text"` per line.
pub fn numbered(body: &str, first: usize) -> String {
    body.lines()
        .enumerate()
        .map(|(i, line)| format!("{:>5}| {line}", first + i))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Run a program and capture stdout/stderr (no shell); `None` timeout is unbounded here.
pub struct Output {
    pub exit: Option<i32>,
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
    pub duration_ms: u128,
}

/// Run `argv` in `cwd` with `env`, piping `stdin`, bounded by `timeout_s`.
pub fn run_argv(
    argv: &[String],
    cwd: &Path,
    env: &BTreeMap<String, String>,
    stdin: Option<&str>,
    timeout_s: f64,
) -> Result<Output, String> {
    if argv.is_empty() {
        return Err("empty command".into());
    }
    let started = std::time::Instant::now();
    let mut cmd = Command::new(&argv[0]);
    cmd.args(&argv[1..])
        .current_dir(cwd)
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (k, v) in env {
        cmd.env(k, v);
    }
    let mut child = cmd.spawn().map_err(|e| format!("could not spawn {}: {e}", argv[0]))?;
    if let Some(text) = stdin {
        if let Some(mut pipe) = child.stdin.take() {
            let _ = pipe.write_all(text.as_bytes());
        }
    }
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        // The wait runs on its own thread so a timeout can kill the child instead of blocking.
        let out = child.wait_with_output();
        let _ = tx.send(out);
    });
    let wait = std::time::Duration::from_millis((timeout_s.max(0.1) * 1000.0) as u64);
    match rx.recv_timeout(wait) {
        Ok(Ok(out)) => Ok(Output {
            exit: out.status.code(),
            stdout: String::from_utf8_lossy(&out.stdout).to_string(),
            stderr: String::from_utf8_lossy(&out.stderr).to_string(),
            timed_out: false,
            duration_ms: started.elapsed().as_millis(),
        }),
        Ok(Err(e)) => Err(format!("process error: {e}")),
        Err(_) => Ok(Output {
            exit: None,
            stdout: String::new(),
            stderr: format!("timeout after {timeout_s:.1}s"),
            timed_out: true,
            duration_ms: started.elapsed().as_millis(),
        }),
    }
}

/// Run `cmd` through `/bin/sh -c` in `cwd`; the explicit shell escape hatch.
pub fn run_shell(cmd: &str, cwd: &Path, env: &BTreeMap<String, String>, timeout_s: f64) -> Result<Output, String> {
    run_argv(
        &["/bin/sh".to_string(), "-c".to_string(), cmd.to_string()],
        cwd,
        env,
        None,
        timeout_s,
    )
}

/// A unified diff between two bodies: common prefix/suffix trimmed, the middle replaced.
pub fn unified_diff(path: &str, before: &str, after: &str) -> String {
    let a: Vec<&str> = before.lines().collect();
    let b: Vec<&str> = after.lines().collect();
    let mut head = 0;
    while head < a.len() && head < b.len() && a[head] == b[head] {
        head += 1;
    }
    let mut tail = 0;
    while tail < a.len() - head && tail < b.len() - head && a[a.len() - 1 - tail] == b[b.len() - 1 - tail] {
        tail += 1;
    }
    let a_mid = &a[head..a.len() - tail];
    let b_mid = &b[head..b.len() - tail];
    if a_mid.is_empty() && b_mid.is_empty() {
        return String::new();
    }
    let mut out = format!("--- a/{path}\n+++ b/{path}\n");
    let a_start = if a_mid.is_empty() { head } else { head + 1 };
    let b_start = if b_mid.is_empty() { head } else { head + 1 };
    out.push_str(&format!(
        "@@ -{a_start},{} +{b_start},{} @@\n",
        a_mid.len(),
        b_mid.len()
    ));
    for line in a_mid {
        out.push('-');
        out.push_str(line);
        out.push('\n');
    }
    for line in b_mid {
        out.push('+');
        out.push_str(line);
        out.push('\n');
    }
    out
}

/// Apply hunks of a unified diff to `before`, returning the new body or an error naming the hunk.
pub fn apply_patch(before: &str, patch: &str) -> Result<String, String> {
    let mut lines: Vec<String> = before.lines().map(|l| l.to_string()).collect();
    let had_trailing = before.ends_with('\n');
    let mut i = 0;
    let src: Vec<&str> = patch.lines().collect();
    while i < src.len() {
        let line = src[i];
        if !line.starts_with("@@") {
            i += 1;
            continue;
        }
        let (old_start, old_len) = parse_hunk_header(line)?;
        let mut j = i + 1;
        let mut old_block: Vec<String> = Vec::new();
        let mut new_block: Vec<String> = Vec::new();
        while j < src.len() && !src[j].starts_with("@@") {
            let l = src[j];
            if let Some(rest) = l.strip_prefix(' ') {
                old_block.push(rest.to_string());
                new_block.push(rest.to_string());
            } else if let Some(rest) = l.strip_prefix('-') {
                old_block.push(rest.to_string());
            } else if let Some(rest) = l.strip_prefix('+') {
                new_block.push(rest.to_string());
            } else if l.starts_with('\\') {
                // "\ No newline at end of file": nothing to move.
            }
            j += 1;
        }
        let at = if old_len == 0 { old_start } else { old_start.saturating_sub(1) };
        if at > lines.len() {
            return Err(format!("hunk @@ -{old_start},{old_len} @@ starts past the end of the file"));
        }
        let end = (at + old_block.len()).min(lines.len());
        if lines[at..end] != old_block[..] {
            return Err(format!(
                "hunk @@ -{old_start},{old_len} @@ does not match the file at line {}",
                old_start
            ));
        }
        lines.splice(at..end, new_block);
        i = j;
    }
    let mut out = lines.join("\n");
    if had_trailing || !out.is_empty() {
        out.push('\n');
    }
    Ok(out)
}

/// Parse `@@ -a,b +c,d @@` into `(a, b)`; a missing count is one line.
fn parse_hunk_header(line: &str) -> Result<(usize, usize), String> {
    let body = line
        .trim_start_matches('@')
        .trim_end_matches('@')
        .trim();
    let mut parts = body.split_whitespace();
    let old = parts.next().ok_or("bad hunk header")?;
    let old = old.trim_start_matches('-');
    let mut it = old.split(',');
    let start: usize = it.next().unwrap_or("0").parse().unwrap_or(0);
    let len: usize = it.next().map(|v| v.parse().unwrap_or(1)).unwrap_or(1);
    Ok((start, len))
}

/// A cheap content digest used by the check cache and the journal notes.
pub fn digest(text: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(text.as_bytes());
    format!("{:x}", h.finalize())[..16].to_string()
}

/// True when `name` looks binary (a NUL byte in the first 8 KiB).
pub fn looks_binary(bytes: &[u8]) -> bool {
    bytes.iter().take(8192).any(|b| *b == 0)
}

/// The one-line first failure of a command's output, per test-runner dialect.
pub fn first_failure(stdout: &str, stderr: &str) -> String {
    let patterns = [
        r"^\s*\(fail\)\s*(.+)$",
        r"^\s*FAILED\s+(.+)$",
        r"^\s*([^\s:()]+\.\w+\(\d+,\d+\)):\s*(error\s+TS\d+:.*)$",
        r"^\s*([^\s:()]+\.\w+:\d+:\d+):\s*(error:.*)$",
        r"^\s*([^\s:()]+\.\w+:\d+):\s*(error:.*)$",
        r"^\s*(?:AssertionError|Error|Exception):\s*(.+)$",
    ];
    let compiled: Vec<regex::Regex> = patterns.iter().filter_map(|p| regex::Regex::new(p).ok()).collect();
    for text in [stderr, stdout] {
        for line in text.lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            for re in &compiled {
                if re.is_match(trimmed) {
                    return trimmed.to_string();
                }
            }
        }
    }
    for text in [stderr, stdout] {
        if let Some(line) = text.lines().find(|l| !l.trim().is_empty()) {
            return line.trim().to_string();
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn line_window_is_inclusive_and_bounded() {
        let text = "a\nb\nc\nd\n";
        let (body, first, last) = line_window(text, 2, Some(3), 400);
        assert_eq!((body.as_str(), first, last), ("b\nc", 2, 3));
        let (body, _, last) = line_window(text, 1, None, 2);
        assert_eq!((body.as_str(), last), ("a\nb", 2));
        let (body, _, _) = line_window(text, 9, None, 400);
        assert!(body.is_empty());
    }

    #[test]
    fn a_diff_round_trips_through_apply() {
        let before = "one\ntwo\nthree\n";
        let after = "one\nTWO\nthree\nfour\n";
        let patch = unified_diff("a.txt", before, after);
        assert!(patch.contains("--- a/a.txt") && patch.contains("+++ b/a.txt"));
        assert_eq!(apply_patch(before, &patch).unwrap(), after);
        assert!(apply_patch(before, "@@ -2,1 +2,1 @@\n-nope\n+x\n").is_err());
    }

    #[test]
    fn glob_matches_the_documented_shapes() {
        use crate::tools::map::glob;
        assert!(glob("*.md", "README.md"));
        assert!(glob("bench/*.md", "bench/HARD.md"));
        assert!(!glob("bench/*.md", "bench/sub/x.md"));
        assert!(glob("src/main.rs", "src/main.rs"));
        assert!(!glob("*.rs", "src/main.py"));
    }

    #[test]
    fn clip_names_its_cut() {
        assert_eq!(clip("abcdef", 3), "abc...[clipped at 3 chars]");
        assert_eq!(clip("abc", 3), "abc");
    }

    #[test]
    fn failure_parsing_prefers_the_runner_dialect() {
        assert_eq!(first_failure("", "(fail) adds two numbers"), "(fail) adds two numbers");
        assert_eq!(first_failure("FAILED tests/x.py::t", ""), "FAILED tests/x.py::t");
        assert_eq!(first_failure("plain line", ""), "plain line");
    }
}
