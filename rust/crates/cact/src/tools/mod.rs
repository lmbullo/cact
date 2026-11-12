//! tools: the typed verb layer: one dispatcher, one module per family.
//!
//! WHAT IT IS
//!   `Ctx` holds the run's state (workspace root, session journal, `_OBS` store, check plan and
//!   the durable run state); `call` dispatches one tool name + JSON args to its family module
//!   and returns an `Env`. `Scope` decides what may be called at all, enforced here again.
//!
//! ENTRY POINTS
//!   Ctx::new(root, plan, max_output, allow_exec)   one run's context
//!   call(ctx, name, args) -> Env                    the single dispatch point
//!   Ctx::changed() / Ctx::plan_lines()              what a card and the handoff read
//!
//! INVARIANTS
//!   a mutation goes through the journal: mutating verbs read the before-body, write, and record;
//!   the returned `changed`/`delta` come from the journal, never re-parsed from the message.
//!   an unknown or ungranted name is a structured refusal (`unknown_tool`), never a panic.
//!   `exec` cannot stand in for a typed mutation when the target is inside the workspace.

use crate::checks::{Plan, Spec};
use crate::envelope::{Env, Page};
use crate::journal::Journal;
use crate::obs::Store;
use serde_json::Value;
use std::cell::RefCell;
use std::path::{Path, PathBuf};

pub mod exec;
pub mod git;
pub mod guide;
pub mod map;
pub mod mutate;
pub mod outline;
pub mod read;
pub mod search;

/// Directories a walk never descends into: build output, caches, VCS internals.
pub const IGNORED_DIRS: &[&str] = &[
    ".git",
    "node_modules",
    "target",
    "__pycache__",
    ".ruff_cache",
    ".cache",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".next",
    ".turbo",
    "coverage",
    ".cact",
];

/// One plan item: the text plus whether it is done.
#[derive(Debug, Clone)]
pub struct PlanItem {
    pub text: String,
    pub done: bool,
}

/// The durable run state: the plan, the phase, and the one-line last check verdict.
#[derive(Debug, Clone)]
pub struct RunState {
    pub plan: Vec<PlanItem>,
    pub phase: String,
    pub last_check: Option<String>,
    pub steps: usize,
}

impl Default for RunState {
    fn default() -> Self {
        RunState {
            plan: Vec::new(),
            phase: "act".into(),
            last_check: None,
            steps: 0,
        }
    }
}

impl RunState {
    /// The `p=<phase>` fragment a card renders.
    pub fn phase_line(&self) -> String {
        format!("p={}", self.phase)
    }

    /// One `[plan] N. text` line per item, done items marked.
    pub fn plan_lines(&self) -> Vec<String> {
        if self.plan.is_empty() {
            return Vec::new();
        }
        let rows: Vec<String> = self
            .plan
            .iter()
            .enumerate()
            .map(|(i, item)| {
                format!(
                    "{}{}. {}",
                    if item.done { "x" } else { " " },
                    i + 1,
                    item.text
                )
            })
            .collect();
        vec![format!("[plan] {}", rows.join(" | "))]
    }
}

/// One run's tool context: everything a verb may touch, in one place.
pub struct Ctx {
    pub root: PathBuf,
    pub journal: RefCell<Journal>,
    pub obs: RefCell<Store>,
    pub plan: Option<Plan>,
    pub state: RefCell<RunState>,
    pub shown: RefCell<std::collections::BTreeMap<String, Vec<(usize, usize)>>>,
    pub allow_exec: bool,
    pub max_output: usize,
}

impl Ctx {
    /// A context rooted at `root`; `specs` is the run's check plan (empty = no goal check).
    pub fn new(root: &Path, specs: Vec<Spec>, allow_exec: bool, max_output: usize) -> Ctx {
        Ctx {
            root: root.to_path_buf(),
            journal: RefCell::new(Journal::new(root)),
            obs: RefCell::new(Store::new()),
            plan: if specs.is_empty() {
                None
            } else {
                Some(Plan::new(specs, root))
            },
            state: RefCell::new(RunState::default()),
            shown: RefCell::new(std::collections::BTreeMap::new()),
            allow_exec,
            max_output,
        }
    }

    /// Every path the journal recorded this session.
    pub fn changed(&self) -> Vec<String> {
        self.journal.borrow().paths()
    }

    /// Record one mutation and stamp the returned envelope with changed/delta.
    pub fn commit(
        &self,
        env: Env,
        path: &str,
        before: Option<String>,
        after: Option<String>,
    ) -> Env {
        let recorded = self.journal.borrow_mut().record(path, before, after);
        let mut env = env;
        if recorded {
            if let Some(plan) = &self.plan {
                plan.invalidate();
            }
            env.changed = vec![path.to_string()];
            env.delta = Some(self.journal.borrow().diff(Some(path), 6000));
        }
        env
    }
}

/// Dispatch one typed call; `scope` already decided the name is granted.
pub fn call(ctx: &Ctx, name: &str, args: &Value) -> Env {
    let started = std::time::Instant::now();
    let mut env = match name {
        "guide" => guide::guide(ctx, args),
        "skill" => guide::skill(ctx, args),
        "map" => map::map(ctx, args),
        "read" => read::read(ctx, args),
        "search" => search::search(ctx, args),
        "page" => page(ctx, args),
        "outline" => outline::outline(ctx, args),
        "write" => mutate::write(ctx, args),
        "edit" => mutate::edit(ctx, args),
        "splice" => mutate::splice(ctx, args),
        "delete" => mutate::delete(ctx, args),
        "mkdir" => mutate::mkdir(ctx, args),
        "apply" => mutate::apply(ctx, args),
        "exec" => exec::exec(ctx, args),
        "git" => git::git(ctx, args),
        "status" => status(ctx, args),
        "checks" => checks_tool(ctx, args),
        "session_diff" => session_diff(ctx, args),
        "undo" => undo(ctx),
        "plan" => plan_tool(ctx, args),
        "task" => task_tool(ctx),
        other => Env::fail(
            "tool",
            "unknown_tool",
            format!("no typed verb named `{other}`"),
            "call one of the names in the tool list, or put the code in a `code` cell",
        ),
    };
    if env.kind.is_empty() {
        env.kind = name.to_string();
    }
    env.duration_ms = started.elapsed().as_millis();
    env
}

/// `page`: read a slice of an `_OBS[n]` entry by handle.
fn page(ctx: &Ctx, args: &Value) -> Env {
    let handle = args["handle"].as_str().unwrap_or("");
    if handle.is_empty() {
        return Env::fail(
            "page",
            "bad_arguments",
            "page needs the handle a card named",
            "pass handle=\"_OBS[3]\"",
        );
    }
    let offset = args["offset"].as_u64().unwrap_or(0) as usize;
    let limit = args["limit"].as_u64().unwrap_or(8000) as usize;
    let field = args["field"].as_str().unwrap_or("stdout");
    match ctx.obs.borrow().page(handle, offset, limit, field) {
        Ok((text, total, start)) => {
            let shown = text.chars().count();
            Env::ok("page")
                .field("handle", handle)
                .field("field", field)
                .field("unit", "char")
                .paged(Page::window(shown, total, start, "char"))
                .body(text)
        }
        Err(message) => Env::fail(
            "page",
            "unknown_handle",
            message,
            "use the exact handle the card named, like _OBS[12]",
        ),
    }
}

/// `status`: branch, capped short status, ahead/behind, and the journal's per-path counts.
fn status(ctx: &Ctx, args: &Value) -> Env {
    let cwd = args["cwd"]
        .as_str()
        .map(|c| crate::util::resolve(&ctx.root, c))
        .unwrap_or_else(|| ctx.root.clone());
    let mut env = Env::ok("status");
    let branch = crate::util::run_argv(
        &[
            "git".into(),
            "rev-parse".into(),
            "--abbrev-ref".into(),
            "HEAD".into(),
        ],
        &cwd,
        &Default::default(),
        None,
        10.0,
    );
    let mut lines: Vec<String> = Vec::new();
    match branch {
        Ok(out) if out.exit == Some(0) => {
            let name = out.stdout.trim().to_string();
            lines.push(format!("branch {name}"));
            env = env.field("git", true).field("branch", name);
            if let Ok(short) = crate::util::run_argv(
                &["git".into(), "status".into(), "--short".into()],
                &cwd,
                &Default::default(),
                None,
                20.0,
            ) {
                let rows: Vec<&str> = short.stdout.lines().collect();
                let shown = rows.len().min(40);
                for row in &rows[..shown] {
                    lines.push(row.to_string());
                }
                if rows.len() > shown {
                    lines.push(format!(
                        "... {} more lines (git action=status shows all)",
                        rows.len() - shown
                    ));
                }
            }
        }
        _ => {
            env = env.field("git", false).field("no_repo", true);
            lines.push("not a git work tree".to_string());
        }
    }
    let stats = ctx.journal.borrow().stats();
    if !stats.is_empty() {
        lines.push("journal:".to_string());
        for (path, adds, dels) in stats {
            lines.push(format!("  {path} +{adds} -{dels}"));
        }
    }
    env.body(lines.join("\n"))
}

/// `checks`: run the plan (all specs, one row each) and return the rows.
fn checks_tool(ctx: &Ctx, args: &Value) -> Env {
    let plan = match &ctx.plan {
        Some(plan) => plan,
        None => {
            return Env::fail(
                "checks",
                "no_plan",
                "this run was started without a check plan",
                "there is no gate to run; verify with exec(argv=[...]) instead",
            )
        }
    };
    let only = args["name"].as_str().filter(|s| !s.is_empty());
    let rows = plan.rows(only);
    if rows.is_empty() {
        return Env::fail(
            "checks",
            "unknown_spec",
            "no spec matched that name",
            "call checks() with no argument to list the plan",
        );
    }
    let mut out: Vec<String> = Vec::new();
    let mut all_ok = true;
    for check in &rows {
        all_ok &= check.ok();
        let line = if check.ok() {
            format!("{}: ok", check.cmd)
        } else if check.state == "unrunnable" {
            format!("{}: unrunnable", check.cmd)
        } else {
            format!(
                "{}: failed (exit {})",
                check.cmd,
                check.exit.map(|c| c.to_string()).unwrap_or_default()
            )
        };
        out.push(format!(
            "{line} | {}",
            crate::util::clip(&check.first_failure(), 200)
        ));
        if !check.ok() {
            out.push(crate::util::clip(&check.text(2000), 2000));
        }
    }
    ctx.state.borrow_mut().last_check = Some(if all_ok {
        "pass".into()
    } else {
        rows[0].first_failure()
    });
    Env::ok("checks")
        .field("rows", rows.iter().map(|c| serde_json::json!({"name": c.cmd, "ok": c.ok(), "exit": c.exit, "first_line": c.first_failure()})).collect::<Vec<_>>())
        .body(out.join("\n"))
}

/// `undo`: restore the most recent journaled change.
fn undo(ctx: &Ctx) -> Env {
    match ctx.journal.borrow_mut().undo() {
        Some(path) => Env::ok("undo")
            .field("path", path.clone())
            .body(format!("restored {path}")),
        None => Env::fail(
            "undo",
            "nothing_to_undo",
            "the journal is empty",
            "only a typed write/edit/splice/delete/apply is undoable",
        ),
    }
}

/// `session_diff`: the journal's unified diff, all of it or one path.
fn session_diff(ctx: &Ctx, args: &Value) -> Env {
    let path = args["path"].as_str();
    let max_chars = args["max_chars"].as_u64().unwrap_or(24_000) as usize;
    let diff = ctx.journal.borrow().diff(path, max_chars);
    if diff.is_empty() {
        return Env::ok("session_diff")
            .field("paths", ctx.changed())
            .body("no tracked change this session".to_string());
    }
    Env::ok("session_diff")
        .field("paths", ctx.changed())
        .body(diff)
}

/// `plan`: replace the plan and/or mark 1-based items done.
fn plan_tool(ctx: &Ctx, args: &Value) -> Env {
    let mut state = ctx.state.borrow_mut();
    if let Some(items) = args["items"].as_array() {
        let done: Vec<usize> = state
            .plan
            .iter()
            .enumerate()
            .filter(|(_, i)| i.done)
            .map(|(n, _)| n + 1)
            .collect();
        state.plan = items
            .iter()
            .filter_map(|v| v.as_str())
            .map(|text| PlanItem {
                text: text.to_string(),
                done: false,
            })
            .collect();
        for n in done {
            if let Some(item) = state.plan.get_mut(n.saturating_sub(1)) {
                item.done = true;
            }
        }
    }
    if let Some(done) = args["done"].as_array() {
        for n in done.iter().filter_map(|v| v.as_u64()) {
            if let Some(item) = state.plan.get_mut(n.saturating_sub(1) as usize) {
                item.done = true;
            }
        }
    }
    let lines = state.plan_lines();
    drop(state);
    Env::ok("plan")
        .field("plan", lines.clone())
        .body(lines.join("\n"))
}

/// `task`: the durable principal view: phase, plan, changed paths, last check, open items.
fn task_tool(ctx: &Ctx) -> Env {
    let state = ctx.state.borrow();
    let changed = ctx.changed();
    let mut lines = vec![format!("phase: {}", state.phase)];
    lines.extend(state.plan_lines());
    lines.push(format!(
        "changed: {}",
        if changed.is_empty() {
            "none".into()
        } else {
            changed.join(", ")
        }
    ));
    lines.push(format!(
        "last check: {}",
        state.last_check.clone().unwrap_or_else(|| "not run".into())
    ));
    let open: Vec<String> = state
        .plan
        .iter()
        .filter(|i| !i.done)
        .map(|i| i.text.clone())
        .collect();
    if !open.is_empty() {
        lines.push(format!("open: {}", open.join(" | ")));
    }
    Env::ok("task")
        .field("phase", state.phase.clone())
        .field("changed", changed)
        .field("last_check", state.last_check.clone())
        .body(lines.join("\n"))
}

/// True when `path` is inside `root` (the workspace), used by the exec mutation gate.
pub fn inside(root: &Path, path: &Path) -> bool {
    let root = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    let full = if path.is_absolute() {
        path.to_path_buf()
    } else {
        root.join(path)
    };
    let parent = full.parent().unwrap_or(&full);
    let resolved = parent
        .canonicalize()
        .unwrap_or_else(|_| parent.to_path_buf());
    resolved.starts_with(&root)
}
