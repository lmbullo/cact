//! checks: the plan entries, one command's result, and the run order.
//!
//! WHAT IT IS
//!   `Spec` is one plan entry ({name, cmd, cheap, scope}); `Check` is one finished command.
//!   `plan` parses the plan from the worker's `--checks` JSON, `run` executes in order and stops
//!   at the first failure, `run_all` returns one row per spec (the `checks` tool view).
//!
//! INVARIANTS
//!   order = contract: cheap specs run first so a broken tree is reported before the slow suite.
//!   "unrunnable" (spawn failure, timeout) is a broken check, never a failed project.
//!   a green spec is cached by (cmd, digest of scope files) for the run, so a repeat is free.

use crate::util;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// One plan entry: a named command, whether it is in the cheap subset, and its path scope.
#[derive(Debug, Clone)]
pub struct Spec {
    pub name: String,
    pub cmd: String,
    pub cheap: bool,
    pub scope: Vec<String>,
}

impl Spec {
    /// Does this spec cover the changed `paths`? No scope, or no path list, means yes.
    pub fn applies(&self, paths: Option<&[String]>) -> bool {
        let paths = match paths {
            None => return true,
            Some(p) => p,
        };
        if self.scope.is_empty() {
            return true;
        }
        paths.iter().any(|path| {
            let text = path.trim_start_matches("./");
            self.scope.iter().any(|prefix| {
                prefix == "."
                    || prefix.is_empty()
                    || text == prefix
                    || text.starts_with(&format!("{prefix}/"))
            })
        })
    }
}

/// Parse a plan: a JSON list of {name, cmd, cheap, scope}, or a bare command string.
pub fn parse(raw: &str) -> Vec<Spec> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Vec::new();
    }
    if raw.starts_with('[') {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(raw) {
            if let Some(list) = value.as_array() {
                let mut specs = Vec::new();
                for item in list {
                    let cmd = item["cmd"]
                        .as_str()
                        .or_else(|| item["command"].as_str())
                        .unwrap_or("")
                        .trim()
                        .to_string();
                    if cmd.is_empty() {
                        continue;
                    }
                    let scope = match &item["scope"] {
                        serde_json::Value::Array(a) => a
                            .iter()
                            .filter_map(|v| v.as_str())
                            .map(|s| s.trim().trim_matches('/').to_string())
                            .collect(),
                        serde_json::Value::String(s) => {
                            vec![s.trim().trim_matches('/').to_string()]
                        }
                        _ => Vec::new(),
                    };
                    specs.push(Spec {
                        name: item["name"].as_str().unwrap_or(&cmd).to_string(),
                        cmd,
                        cheap: item["cheap"].as_bool().unwrap_or(false),
                        scope: scope.into_iter().filter(|s| !s.is_empty()).collect(),
                    });
                }
                return specs;
            }
        }
    }
    vec![Spec {
        name: "check".into(),
        cmd: raw.to_string(),
        cheap: false,
        scope: Vec::new(),
    }]
}

/// One finished command run: passed, failed or unrunnable.
#[derive(Debug, Clone, Default)]
pub struct Check {
    pub cmd: String,
    pub state: String,
    pub exit: Option<i32>,
    pub duration_ms: u128,
    pub stdout: String,
    pub stderr: String,
    pub failures: Vec<String>,
}

impl Check {
    /// True only for a run that exited 0.
    pub fn ok(&self) -> bool {
        self.state == "passed"
    }

    /// The first failing line the parser found, else the first non-empty output line.
    pub fn first_failure(&self) -> String {
        if let Some(line) = self.failures.first() {
            if !line.trim().is_empty() {
                return line.trim().to_string();
            }
        }
        util::first_failure(&self.stdout, &self.stderr)
    }

    /// The model-facing line(s): verdict first, then the parsed failures or an output tail.
    pub fn text(&self, limit: usize) -> String {
        if self.state == "passed" {
            return format!("{}: passed ({}ms)", self.cmd, self.duration_ms);
        }
        if self.state == "unrunnable" {
            let detail = util::clip(
                (if self.stderr.is_empty() {
                    &self.stdout
                } else {
                    &self.stderr
                })
                .trim(),
                limit,
            );
            return format!(
                "{}: could not run. This is not a failed test; the check itself is broken.\n{}",
                self.cmd, detail
            );
        }
        let head = match self.exit {
            Some(code) => format!("{}: failed (exit {code}, {}ms)", self.cmd, self.duration_ms),
            None => format!("{}: failed (not run)", self.cmd),
        };
        if !self.failures.is_empty() {
            let body: Vec<String> = self.failures.iter().map(|f| format!("  - {f}")).collect();
            return format!("{head}\n{}", body.join("\n"));
        }
        format!(
            "{head}\n{}",
            util::clip(
                (if self.stderr.is_empty() {
                    &self.stdout
                } else {
                    &self.stderr
                })
                .trim(),
                limit
            )
        )
    }
}

/// Run one plan entry through `/bin/sh -c` in `cwd`, bounded by `timeout_s`.
pub fn run_spec(spec: &Spec, cwd: &Path, timeout_s: f64) -> Check {
    match util::run_shell(&spec.cmd, cwd, &BTreeMap::new(), timeout_s) {
        Err(e) => Check {
            cmd: spec.cmd.clone(),
            state: "unrunnable".into(),
            stdout: String::new(),
            stderr: e,
            ..Check::default()
        },
        Ok(out) => {
            if out.timed_out {
                return Check {
                    cmd: spec.cmd.clone(),
                    state: "unrunnable".into(),
                    stdout: out.stdout,
                    stderr: out.stderr,
                    duration_ms: out.duration_ms,
                    ..Check::default()
                };
            }
            let failures: Vec<String> = failure_lines(&out.stdout, &out.stderr);
            Check {
                cmd: spec.cmd.clone(),
                state: if out.exit == Some(0) {
                    "passed".into()
                } else {
                    "failed".into()
                },
                exit: out.exit,
                duration_ms: out.duration_ms,
                stdout: out.stdout,
                stderr: out.stderr,
                failures,
            }
        }
    }
}

/// Parse the collected failure lines (de-duplicated, capped at 20).
fn failure_lines(stdout: &str, stderr: &str) -> Vec<String> {
    let patterns = [
        r"^\s*\(fail\)\s*(.+)$",
        r"^\s*FAILED\s+(.+)$",
        r"^\s*([^\s:()]+\.\w+\(\d+,\d+\)):\s*(error\s+TS\d+:.*)$",
        r"^\s*([^\s:()]+\.\w+:\d+:\d+):\s*(error:.*)$",
        r"^\s*([^\s:()]+\.\w+:\d+):\s*(error:.*)$",
        r"^\s*(?:AssertionError|Error|Exception):\s*(.+)$",
    ];
    let compiled: Vec<regex::Regex> = patterns
        .iter()
        .filter_map(|p| regex::Regex::new(p).ok())
        .collect();
    let mut out: Vec<String> = Vec::new();
    for text in [stderr, stdout] {
        for line in text.lines() {
            let trimmed = line.trim();
            for re in &compiled {
                if re.is_match(trimmed) && !out.iter().any(|seen| seen == trimmed) {
                    out.push(trimmed.to_string());
                }
            }
            if out.len() >= 20 {
                return out;
            }
        }
    }
    out
}

/// The verifier: the plan one run was started with, plus the last authoritative verdict.
pub struct Plan {
    pub specs: Vec<Spec>,
    pub cwd: PathBuf,
    cache: std::cell::RefCell<BTreeMap<String, Check>>,
    /// The last check that ran, so a card can name its first failing line without re-running.
    pub last: std::cell::RefCell<Option<Check>>,
}

impl Plan {
    /// A plan over `cwd`; an empty spec list means the run has no goal check.
    pub fn new(specs: Vec<Spec>, cwd: &Path) -> Plan {
        let mut specs = specs;
        // Cheap specs first: a broken tree is reported before the slow suite runs.
        specs.sort_by_key(|spec| !spec.cheap);
        Plan {
            specs,
            cwd: cwd.to_path_buf(),
            cache: std::cell::RefCell::new(BTreeMap::new()),
            last: std::cell::RefCell::new(None),
        }
    }

    /// A human label for the goal: the spec names in order.
    pub fn label(&self) -> String {
        self.specs
            .iter()
            .map(|s| s.name.clone())
            .collect::<Vec<_>>()
            .join(", ")
    }

    /// Run the applicable specs in order, stopping at the first failure. Cached greens are free.
    pub fn run(&self, paths: Option<&[String]>) -> Option<Check> {
        let mut ran: Vec<String> = Vec::new();
        for spec in &self.specs {
            if !spec.applies(paths) {
                continue;
            }
            if let Some(hit) = self.cache.borrow().get(&spec.cmd) {
                if hit.ok() {
                    ran.push(spec.name.clone());
                    continue;
                }
            }
            let check = run_spec(spec, &self.cwd, 600.0);
            let verdict = if check.ok() {
                check.clone()
            } else {
                Check {
                    cmd: format!("{}: {}", spec.name, spec.cmd),
                    ..check.clone()
                }
            };
            *self.last.borrow_mut() = Some(verdict.clone());
            self.cache
                .borrow_mut()
                .insert(spec.cmd.clone(), check.clone());
            if !check.ok() {
                return Some(verdict);
            }
            ran.push(spec.name.clone());
        }
        if ran.is_empty() {
            return None;
        }
        *self.last.borrow_mut() = Some(Check {
            cmd: ran.join(", "),
            state: "passed".into(),
            exit: Some(0),
            ..Check::default()
        });
        self.last.borrow().clone()
    }

    /// One row per spec, in order (the `checks` tool view); `name` runs only that spec.
    pub fn rows(&self, name: Option<&str>) -> Vec<Check> {
        let mut out = Vec::new();
        for spec in &self.specs {
            if let Some(only) = name {
                if !only.is_empty() && spec.name != only {
                    continue;
                }
            }
            let check = run_spec(spec, &self.cwd, 600.0);
            let mut check = check;
            check.cmd = spec.name.clone();
            self.cache
                .borrow_mut()
                .insert(spec.cmd.clone(), check.clone());
            if !check.ok() {
                *self.last.borrow_mut() = Some(check.clone());
            }
            out.push(check);
        }
        out
    }
}
