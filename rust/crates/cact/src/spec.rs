//! spec: the model-facing tool specs, the system rules, and one run's capability scope.
//!
//! WHAT IT IS
//!   `TOOL_RULES`/`STATE_RULES` (system prompt), the per-tool JSON schemas, and `Scope`, the
//!   capability limit: which verbs the model may see and call, calls per turn, output ceilings.
//!
//! INVARIANTS
//!   a spec's name is the verb name; `Scope::allows` is enforced twice (provider filtered list
//!   and dispatch), so an invented name is a structured refusal, never a crash.
//!   the description is the model's whole manual: a wording change is a behaviour change.

use serde_json::{json, Value};

/// Process rules: how the typed tools are meant to be used (the production contract).
pub const TOOL_RULES: &str = r#"You are an autonomous coding agent. The task is a contract.

ACT: typed tools for workspace I/O, mutation, process and git; the code cell for computation or a pipeline the typed tools cannot express. One call that the job. Read before you write.
OBSERVE: typed results are structured. Read [page] before [data]; follow next/has_more/complete. Every refusal carries error.use. Large output is on the _OBS handle; page it. A repeat read is a pointer unless repeat=true.
CHANGE: tracked edits go through write, edit, splice, delete, mkdir or apply. Batch every replacement for one file in one edit(edits=[...]) call. Keep the diff small: one-line docstring on every public name, no unrelated edits. Read [diff] and [check] after a mutation. A failed check is a repair instruction; never weaken it.
VERIFY: a check you wrote can repeat your own assumption. For a named shape or format, read the code that produces it and test a real value, not only your fixture. A timestamp/id is often base36, not decimal; read the producer before writing its regex.
FINISH: stop as soon as the plan passes. Do not run status/git/log, re-read files, or clean generated caches (__pycache__, .pytest_cache) after a green check. Finish with final_answer(summary=...): changed paths, exact commands and results, and what is unverified. Facts, not narration; no headings, no restating the task. No human answers: choose and report a defensible default."#;

/// The card contract the state loop appends to `TOOL_RULES`.
pub const STATE_RULES: &str = r#"
STATE CARDS
A result is a state card, not a transcript: `[s]` step/cost, `p=` phase, `chg:` changed, `[obs]` typed result, `[page]` paging, `[data]` fields, `[diff]` mutation, `[check]` verdict, `[read]` delivered ranges. Read [page] before [data]. `next` is replayable; complete/has_more/truncated are the paging truth. Every `ok:false` carries code/message/use. Process output is bounded and names _OBS[n]; page it. Coverage is guidance, not a finish gate."#;

/// The unattended-task appendix the worker prepends to the rules.
pub const AUTONOMOUS: &str = r#"

AUTONOMOUS TASK MODE
You are running unattended. No human will answer questions or approve actions.
- Inspect, change, and verify with real commands. Keep working until the task is complete or genuinely blocked.
- Verify with checks(); a check is clean only when its latest execution passed. Never claim a gate without the run that passed it.
- Finish with final_answer(summary=...), as the typed tool or inside a silent cell. A finish that passes the plan ends the turn.
- When you leave work open, pass the structured fields {summary, changed, gates, deferred, untested}; the next task inherits them."#;

/// The process-model note: exec is the tool, the journal owns mutation.
pub const RUN_NOTE: &str = r#"
RUNNING PROGRAMS
In typed modes, exec(argv, timeout=60, cwd=None, env=None, stdin=None) is the one process tool: argv or commands=[[...],[...]] for programs, shell=<string> for explicit shell syntax. Use cwd=... instead of `cd`; env/stdin are explicit. Use search()/read() for files, not the shell.
Mutation inside the workspace goes through the journal, not a process: write/edit/splice/delete/mkdir/apply instead of tee, cp, mv, rm, sed -i or `>`. exec refuses those with kind "wrong_call". Scratch paths (/tmp, /var/tmp, /dev/shm, $TMPDIR) are not gated."#;

/// Build a function spec: `props` is the `properties` object, `required` the required names.
fn tool(name: &str, desc: &str, props: Value, required: &[&str]) -> Value {
    let mut params = json!({"type": "object", "properties": props, "additionalProperties": false});
    if !required.is_empty() {
        params["required"] = json!(required);
    }
    json!({"type": "function", "function": {"name": name, "description": desc, "parameters": params}})
}

/// One spec per typed verb this port implements, in registry order.
pub fn typed_specs() -> Vec<Value> {
    vec![
        tool("guide", "Answer 'which module owns this?' from source headers: guide() lists modules, guide(query=...) gives ranked one-line rows, guide(path=...) returns one module's header. Read-only.", json!({
            "query": {"type": "string"},
            "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "limit": {"type": "integer", "minimum": 1},
            "offset": {"type": "integer", "minimum": 0},
            "headers": {"type": "integer", "minimum": 0}
        }), &[]),
        tool("skill", "Load a project procedure from .agent/skills/<name>.md. skill() lists the names; skill(name) returns that procedure's text.", json!({
            "name": {"type": "string"},
            "root": {"type": "string"}
        }), &[]),
        tool("map", "List the files under a directory with line counts, without shell ls/find; path takes one path or a list; include filters (a glob matches the full path or basename); ignored trees are skipped; shown/next/has_more page a large list. A file path returns that one row.", json!({
            "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "include": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "max_files": {"type": "integer", "minimum": 0},
            "offset": {"type": "integer", "minimum": 0}
        }), &[]),
        tool("read", "Read one file or several. start/end are the canonical line range; path=[...] applies one window to several files, ranges=[{path,start,end}] batches different windows. page.complete means this request was served; page.has_more means the file continues; truncated means the page limit cut the request. A range already shown this session returns a pointer; repeat=true re-delivers it.", json!({
            "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "start": {"type": "integer", "minimum": 1},
            "end": {"type": "integer", "minimum": 0},
            "raw": {"type": "boolean"},
            "repeat": {"type": "boolean"},
            "section": {"type": "string"},
            "ranges": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer", "minimum": 1}, "end": {"type": "integer", "minimum": 0}}, "required": ["path"]}}
        }), &[]),
        tool("session_diff", "The session journal's unified diff of every tracked change, not git's: session_diff() shows all of it, session_diff(path=...) one file.", json!({
            "path": {"type": "string"},
            "max_chars": {"type": "integer", "minimum": 0}
        }), &[]),
        tool("checks", "Run the check plan this task was started with. One row per spec: {name, ok, exit, first_line, detail?}; name= runs that one spec. The plan is checks(); do not run a gate by hand. A failing plan blocks final_answer.", json!({
            "name": {"type": "string"}
        }), &[]),
        tool("final_answer", "Finish the task. summary is the short report; changed/gates/deferred/untested are the optional structured handoff. The run accepts only after the check plan passes, and a rejection names the failing line. Use this instead of final_answer(...) inside a cell.", json!({
            "summary": {"type": "string"},
            "changed": {"type": "array", "items": {"type": "string"}},
            "gates": {"type": "array", "items": {"type": "string"}},
            "deferred": {"type": "array", "items": {"type": "string"}},
            "untested": {"type": "array", "items": {"type": "string"}}
        }), &["summary"]),
        tool("status", "One call for tree state: branch, capped `git status --short`, ahead/behind, and the session journal's paths with add/del counts (never diff text). A directory outside a git work tree is a valid state (git: false, no_repo: true). Read-only.", json!({
            "cwd": {"type": "string"}
        }), &[]),
        tool("search", "Search a regex (default) or literal. stdout is path:line:text for hits and path:line-text for context; the envelope carries shown/total/offset/next/unit/complete; include filters directory walks; count=true returns path:count rows; within=[start,end] or section='Heading' restricts a range.", json!({
            "pattern": {"type": "string"},
            "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "regex": {"type": "boolean"},
            "include": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "context": {"type": "integer", "minimum": 0},
            "ignore_case": {"type": "boolean"},
            "max_results": {"type": "integer", "minimum": 0},
            "offset": {"type": "integer", "minimum": 0},
            "count": {"type": "boolean"},
            "within": {"type": "array", "items": {"type": "integer", "minimum": 1}}
        }), &["pattern"]),
        tool("page", "Read a slice of an _OBS[n] entry by the exact handle a card named. No I/O: offset is a character index, limit the slice size, field stdout (default), stderr or result.", json!({
            "handle": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 0},
            "field": {"type": "string", "enum": ["stdout", "stderr", "result"]}
        }), &["handle"]),
        tool("outline", "Return a file's structure with line ranges (Markdown headings, Python classes/functions/methods, TS/JS defs and tests). A Markdown heading covers its subtree. path may be a list.", json!({
            "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "max_entries": {"type": "integer", "minimum": 1}
        }), &["path"]),
        tool("edit", "Apply one or several old->new replacements in one atomic call. `old` is exact; a unique leading-indentation-only match is accepted. Ambiguous or absent old text changes nothing and returns the closest lines. There is no single-file form: every edit goes in edits=[{path, old, new}].", json!({
            "edits": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}, "all": {"type": "boolean"}}, "required": ["path", "old", "new"]}}
        }), &["edits"]),
        tool("splice", "Replace 1-based inclusive lines start..end with text. end is required; start-1 inserts before start and text='' deletes the range. Use this when read/outline gave you line numbers.", json!({
            "path": {"type": "string"},
            "start": {"type": "integer", "minimum": 1},
            "end": {"type": "integer", "minimum": 0},
            "text": {"type": "string"}
        }), &["path", "start", "end"]),
        tool("write", "Write a whole file. Use it for a new file or a real rewrite; for an existing file prefer edit/splice. Without force=true it refuses any body shorter than the current file, and it notes content whose only line breaks are a literal backslash-n.", json!({
            "path": {"type": "string"},
            "content": {"type": "string"},
            "force": {"type": "boolean"}
        }), &["path", "content"]),
        tool("delete", "Delete one file through the journal; use it instead of rm through exec. Directories are refused (map the files first).", json!({
            "path": {"type": "string"}
        }), &["path"]),
        tool("mkdir", "Create one directory (parents included); use it instead of mkdir through exec.", json!({
            "path": {"type": "string"}
        }), &["path"]),
        tool("apply", "Apply a unified diff, possibly across several files, in one journal entry. Prefer edit(edits=[...]) for ordinary atomic replacements.", json!({
            "patch": {"type": "string"}
        }), &["patch"]),
        tool("exec", "The typed process tool: run argv directly, commands=[[...]] for several programs, or shell=<string> for explicit shell syntax. cwd/env/stdin are explicit. Programs that mutate tracked files are refused because they bypass the journal; scratch paths (/tmp, /var/tmp, /dev/shm, $TMPDIR) are allowed.", json!({
            "argv": {"type": "array", "items": {"type": "string"}},
            "commands": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            "shell": {"type": "string"},
            "cwd": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "stdin": {"type": "string"},
            "timeout": {"type": "number", "minimum": 1}
        }), &[]),
        tool("git", "Typed git (working tree): status, diff, show, log, files, add, commit, push, remote, branch. branch() reads the current branch; branch=<name> creates/switches to it. diff/show page with offset/max_chars and return next.offset plus has_more. Use it instead of git through exec; session_diff is the journal view, not git.", json!({
            "action": {"type": "string", "enum": ["status", "diff", "show", "log", "files", "add", "commit", "push", "remote", "branch"]},
            "paths": {"type": "array", "items": {"type": "string"}},
            "message": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
            "offset": {"type": "integer", "minimum": 0},
            "max_chars": {"type": "integer", "minimum": 0},
            "stat": {"type": "boolean"},
            "cached": {"type": "boolean"},
            "rev": {"type": "string"},
            "remote": {"type": "string"},
            "branch": {"type": "string"},
            "cwd": {"type": "string"}
        }), &["action"]),
    ]
}

/// The kernel cell spec: `code` in code mode, `python` otherwise.
pub fn cell_spec(name: &str) -> Value {
    let desc = if name == "code" {
        "Run one Python cell in the persistent kernel. This is the orchestration surface: the typed verbs are already callable in the cell (read, search, map, outline, edit, splice, write, delete, mkdir, apply, checks, status, git, exec, ...). Use a cell for any multi-step, batch, filter or dependent sequence: keep intermediate data in the cell and print only the result you need. Variables and imports persist between cells. A typed tool is for one action; a cell is for a pipeline. Call help() in the cell for live verb signatures."
    } else {
        "Execute Python in a persistent kernel. Variables, imports and data persist between calls. The value of the last expression and anything printed are returned as the tool result. Use the typed tools for reading, searching, editing and running programs; use python for computation and for anything the typed tools do not cover. help() lists the verbs with their live signatures."
    };
    tool(
        name,
        desc,
        json!({"code": {"type": "string", "description": "Python source to execute in the kernel."}}),
        &["code"],
    )
}

/// The durable plan spec.
pub fn plan_spec() -> Value {
    tool(
        "plan",
        "Set or update the durable plan for this task. plan(items=['read the contract', 'change the symbol']) replaces the plan; plan(done=[1, 2]) marks 1-based items done without replacing the text. The [plan] lines are rendered in every state card, so the plan survives a long transcript.",
        json!({
            "items": {"type": "array", "items": {"type": "string"}},
            "done": {"type": "array", "items": {"type": "integer", "minimum": 1}}
        }),
        &[],
    )
}

/// The durable task-state spec.
pub fn task_spec() -> Value {
    tool(
        "task",
        "Return the durable task state: phase, plan, changed paths, last checks and open items. This is the principal view, not a copy of the transcript.",
        json!({}),
        &[],
    )
}

/// The capability set one run grants the model; `DEFAULT_TOOLS` is the production scope.
#[derive(Debug, Clone)]
pub struct Scope {
    pub tools: Vec<String>,
    pub allow_exec: bool,
    pub max_calls_per_turn: usize,
    pub max_turn_tokens: usize,
    pub max_report_chars: usize,
    pub effort: Option<String>,
    pub inline_finish: bool,
    /// Provider name of the persistent cell: `code` when true, `python` when false.
    pub code_mode: bool,
}

/// The `fast-code` profile: the lean typed set the measured loop actually uses, plus one cell.
/// The cell name is `python` unless `CODE_TUI_CODE_MODE=1` overrides it at construction time.
pub const DEFAULT_TOOLS: &[&str] = &[
    "code",
    "read",
    "edit",
    "write",
    "search",
    "checks",
    "final_answer",
    "exec",
    "status",
    "outline",
    "page",
    "apply",
    "delete",
    "mkdir",
    "splice",
    "git",
    "guide",
    "skill",
    "map",
];

/// The `fast-code` profile tool list: the lean typed set plus one cell.
pub fn fast_code_tools() -> Vec<String> {
    DEFAULT_TOOLS.iter().map(|s| s.to_string()).collect()
}

impl Scope {
    /// The production scope: every default verb, one cell, `exec` allowed.
    pub fn production() -> Scope {
        Scope {
            tools: fast_code_tools(),
            allow_exec: true,
            max_calls_per_turn: 8,
            max_turn_tokens: 8192,
            max_report_chars: 4000,
            effort: Some("low".into()),
            inline_finish: true,
            code_mode: std::env::var("CODE_TUI_CODE_MODE").ok().as_deref() == Some("1"),
        }
    }

    /// A scope narrowed to an explicit comma-separated tool list (env override wins).
    pub fn from_names(names: &str) -> Scope {
        let mut scope = Scope::production();
        scope.tools = names
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        scope
    }

    /// True when this scope grants `name`; `exec` also needs `allow_exec`.
    pub fn allows(&self, name: &str) -> bool {
        let name = if name == "python" { "code" } else { name };
        if name == "exec" && !self.allow_exec {
            return false;
        }
        self.tools.iter().any(|t| t == name)
    }

    /// The provider specs whose function name this scope grants, in the same order.
    pub fn filter(&self, specs: Vec<Value>) -> Vec<Value> {
        specs
            .into_iter()
            .filter(|spec| {
                spec["function"]["name"]
                    .as_str()
                    .map(|n| self.allows(n))
                    .unwrap_or(false)
            })
            .collect()
    }
}
