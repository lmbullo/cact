//! kernel: the parent side of the persistent Python cell: spawn, request, RPC, timeout, restart.
//!
//! WHAT IT IS
//!   One child (`python3 -u -c <driver>`) holds the cell namespace. A typed verb call never
//!   touches the child: the parent dispatches it. A `code`/`python` cell runs in the child, and a
//!   verb used inside that cell comes back as an `rpc` line the parent answers with the same
//!   observation text a typed call would have produced.
//!
//! ENTRY POINTS
//!   Kernel::start(root, timeout, allow_run) -> Kernel      spawn the child
//!   .cell(ctx, code, tool) -> Cell                          run one cell (restart on timeout)
//!   Cell fields                                             stdout/stderr/result/final/reads/changed
//!
//! INVARIANTS
//!   a timeout kills the child and the next cell restarts it with an empty namespace; the flag
//!   `restarted` makes the card say so, so a lost variable is never silent.
//!   the child's real stdout carries only `@@K@@` lines; cell output arrives inside the JSON.
//!   `final_answer` in a cell sets `cell.final`; the loop decides stage vs accept, never the kernel.

use crate::envelope::Env;
use crate::obs::Entry;
use crate::tools::{self, Ctx};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};

/// The child driver: one JSON request in, one `@@K@@` JSON line out.
const DRIVER: &str = include_str!("kernel_driver.py");

/// The protocol marker the child prefixes its replies with.
pub const PROTO: &str = "@@K@@";

/// One finished cell, with everything a card needs.
#[derive(Debug, Clone, Default)]
pub struct Cell {
    pub tool: String,
    pub stdout: String,
    pub stderr: String,
    pub result: String,
    pub final_value: Option<Value>,
    pub error: Option<String>,
    pub envelope: Option<Env>,
    pub changed: Vec<String>,
    pub delta: Option<String>,
    pub reads: Vec<(String, usize, usize)>,
    pub duration_ms: u128,
    pub timed_out: bool,
    pub restarted: bool,
    pub failed: bool,
    pub obs_index: Option<usize>,
}

/// The persistent cell child plus its request/reply plumbing.
pub struct Kernel {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    lines: Option<Receiver<String>>,
    timeout_s: f64,
    root: std::path::PathBuf,
    /// Set when the previous cell killed the child, so the next card can say the namespace reset.
    pub restarted: bool,
    pub child_started: bool,
}

impl Kernel {
    /// Spawn the child in `root` with a `timeout` for each cell.
    pub fn start(root: &std::path::Path, timeout_s: f64) -> Kernel {
        let mut kernel = Kernel {
            child: None,
            stdin: None,
            lines: None,
            timeout_s,
            root: root.to_path_buf(),
            restarted: false,
            child_started: false,
        };
        kernel.spawn();
        kernel
    }

    /// Spawn (or respawn) the child; a missing python3 leaves the kernel dead, not the run.
    fn spawn(&mut self) {
        let python = std::env::var("PYTHON").unwrap_or_else(|_| "python3".to_string());
        let mut cmd = Command::new(python);
        cmd.arg("-u")
            .arg("-c")
            .arg(DRIVER)
            .current_dir(&self.root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONDONTWRITEBYTECODE", "1");
        match cmd.spawn() {
            Ok(mut child) => {
                let stdin = child.stdin.take();
                let stdout = child.stdout.take();
                self.child = Some(child);
                self.stdin = stdin;
                if let Some(stdout) = stdout {
                    let (tx, rx) = channel();
                    std::thread::spawn(move || {
                        let reader = BufReader::new(stdout);
                        for line in reader.lines().map_while(Result::ok) {
                            if tx.send(line).is_err() {
                                break;
                            }
                        }
                    });
                    self.lines = Some(rx);
                }
                self.child_started = true;
            }
            Err(_) => {
                self.child = None;
                self.stdin = None;
                self.lines = None;
            }
        }
    }

    /// True when the child is running.
    pub fn alive(&self) -> bool {
        self.child.is_some() && self.stdin.is_some()
    }

    /// Stop the child (the worker's SIGTERM path goes through here).
    pub fn stop(&mut self) {
        if let Some(child) = &mut self.child {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.child = None;
        self.stdin = None;
        self.lines = None;
    }

    /// Run one `code` cell; a dead child is respawned first and the restart is recorded.
    pub fn cell(&mut self, ctx: &Ctx, code: &str, tool: &str) -> Cell {
        let started = std::time::Instant::now();
        let mut restarted = false;
        if !self.alive() {
            self.spawn();
            restarted = self.child_started;
        }
        if !self.alive() {
            return Cell {
                tool: tool.into(),
                error: Some("no python3 on PATH: the code cell needs an interpreter".into()),
                failed: true,
                restarted,
                duration_ms: started.elapsed().as_millis(),
                ..Cell::default()
            };
        }
        let request = json!({"kind": "cell", "code": code}).to_string();
        if self.write(&request).is_err() {
            self.stop();
            return Cell { tool: tool.into(), error: Some("kernel stdin closed".into()), failed: true, restarted, ..Cell::default() };
        }
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(self.timeout_s.max(1.0));
        let mut reads: Vec<(String, usize, usize)> = Vec::new();
        loop {
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                self.stop();
                self.restarted = true;
                return Cell {
                    tool: tool.into(),
                    stdout: String::new(),
                    stderr: format!("cell timeout after {:.0}s; the kernel was restarted", self.timeout_s),
                    timed_out: true,
                    restarted: true,
                    failed: false,
                    reads,
                    duration_ms: started.elapsed().as_millis(),
                    ..Cell::default()
                };
            }
            let line = match self.read(remaining) {
                Some(line) => line,
                None => {
                    self.stop();
                    self.restarted = true;
                    return Cell {
                        tool: tool.into(),
                        stderr: "kernel child exited during the cell; the namespace was lost".into(),
                        error: Some("kernel_exit".into()),
                        restarted: true,
                        failed: true,
                        reads,
                        duration_ms: started.elapsed().as_millis(),
                        ..Cell::default()
                    };
                }
            };
            let payload = line.trim_start_matches(PROTO);
            if payload == line {
                continue;
            }
            let reply: Value = match serde_json::from_str(payload) {
                Ok(value) => value,
                Err(_) => continue,
            };
            match reply["kind"].as_str().unwrap_or("") {
                "rpc" => {
                    let name = reply["name"].as_str().unwrap_or("");
                    let args = &reply["args"];
                    let env = tools::call(ctx, name, args);
                    reads.extend(env.reads.iter().cloned());
                    let text = crate::card::render_body(&env);
                    let _ = self.write(&json!({"kind": "rpc_result", "text": text}).to_string());
                }
                "done" => {
                    let mut cell = Cell {
                        tool: tool.into(),
                        stdout: reply["stdout"].as_str().unwrap_or("").to_string(),
                        stderr: reply["stderr"].as_str().unwrap_or("").to_string(),
                        result: reply["result"].as_str().unwrap_or("").to_string(),
                        final_value: if reply["final"].is_null() { None } else { Some(reply["final"].clone()) },
                        error: reply["error"].as_str().map(|s| s.to_string()),
                        reads,
                        restarted,
                        duration_ms: started.elapsed().as_millis(),
                        ..Cell::default()
                    };
                    cell.failed = cell.error.is_some();
                    // The child keeps `_OBS` in step with the parent's store: index the cell output.
                    if !cell.stdout.is_empty() || !cell.stderr.is_empty() || !cell.result.is_empty() {
                        let index = ctx.obs.borrow_mut().push(Entry {
                            stdout: cell.stdout.clone(),
                            stderr: cell.stderr.clone(),
                            result: cell.result.clone(),
                        });
                        cell.obs_index = Some(index);
                    }
                    return cell;
                }
                _ => continue,
            }
        }
    }

    /// Write one protocol line to the child.
    fn write(&mut self, line: &str) -> Result<(), ()> {
        let stdin = self.stdin.as_mut().ok_or(())?;
        stdin.write_all(format!("{line}\n").as_bytes()).map_err(|_| ())?;
        stdin.flush().map_err(|_| ())
    }

    /// Read one protocol line, or `None` if the wait ran out or the child closed.
    fn read(&self, wait: std::time::Duration) -> Option<String> {
        self.lines.as_ref()?.recv_timeout(wait).ok()
    }
}
