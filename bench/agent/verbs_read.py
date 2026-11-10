"""verbs_read: the registry rows of the read-only verbs: look, list, run state.

WHAT IT IS
  VERBS_READ: name -> entry for guide, skill, map, read, session_diff, checks, serve,
  revert_check and status. One family: none of these writes the workspace; each answers a
  question about the tree, the session, the run or the check plan. verbs.py merges the four
  family tables into VERBS in the same order they appear here, so the registry stays one ordered
  dict and the KERNEL_VERBS JSON the child receives is unchanged.

ENTRY POINTS
  VERBS_READ   nine registry entries: function, summary, mutates=False, schema

INVARIANTS
  data only: no code, no imports, no filesystem; safe to ship to the kernel child as JSON.
  summary = the model-facing tool description, schema = the contract; verbs.py copies a row
  into VERBS untouched, so editing a row here is editing the tool.
  mutates is False for every entry here: a verb that writes belongs in verbs_mutate.py.
  order is part of the answer: verbs.py concatenates read, search, mutate, exec in that order.

WHERE TO CHANGE
  one verb's description or arguments ... its entry here
  a new read-only verb ................... the row here + the function in kernel/prelude.py +
                                          the Kernel.call wiring + a bench/smoke_checks call
"""
from __future__ import annotations

VERBS_READ: dict[str, dict] = {
    "guide": {
        "function": "guide",
        "summary": "Answer 'which module owns this?' from the source headers: no query gives the module map, query gives ranked one-line rows (truthful total/next; default limit 10; offset pages), path gives one module's whole header (or a list of paths: several headers in one call). Read-only and in-kernel (no bench/guide.py process). Only source headers are indexed; current docs use map/outline/search.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                         "description": "One module path, or a list of them for several headers in one call."},
                "limit": {"type": "integer", "minimum": 1},
                "offset": {"type": "integer", "minimum": 0,
                           "description": "Row offset into the ranked query results; use next.offset."},
                "chars": {"type": "integer", "minimum": 0},
                "headers": {"type": "integer", "minimum": 0,
                            "description": "Inline the full header of the top N hits in the same answer (default 0), instead of a second guide(path) call."},
            },
            "additionalProperties": False,
        },
    },
    "skill": {
        "function": "skill",
        "summary": "Load a project procedure on demand from .agent/skills/<name>.md. skill() lists the names; skill(name) returns that procedure's text. Read-only and cheap: use it when the task names a project workflow instead of guessing the steps.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name without the .md suffix."},
                "root": {"type": "string", "description": "Workspace root (default '.')."},
            },
            "additionalProperties": False,
        },
    },
    "map": {
        "function": "map",
        "summary": "List the files under a directory with line counts, without shell ls/find; path takes one path or a list, a literal include is an exact relative path and a glob matches the full path or basename (``*.md`` is any depth; ``bench/*.md`` is one level), ignored trees are skipped, and shown/next/has_more page a large list. A file path returns that one row. For 'which module owns this' use guide(), not map().",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                         "description": "File, directory, or a list of either; default the workspace root."},
                "max_files": {"type": "integer", "minimum": 0,
                              "description": "Page size (default 120); 0 returns every row in one answer."},
                "offset": {"type": "integer", "minimum": 0,
                           "description": "0-based index into the matched file list."},
                "include": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                            "description": "Literal = exact relative path; glob = full path or basename ('*.md' any depth, 'bench/*.md' one level)."},
            },
            "additionalProperties": False,
        },
    },
    "read": {
        "function": "read",
        "summary": "Read one file or several. start/end are the canonical line range; path=[...] applies one window to several files, ranges=[{path,start,end}] batches different windows. page.complete means this request was served; page.has_more means the file continues; truncated means the page limit cut the request. A range already shown this session returns a pointer; repeat=true re-delivers it.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                         "description": "File path, or a list of paths to read in one call."},
                "start": {"type": "integer", "minimum": 1, "description": "First 1-based line (default 1)."},
                "end": {"type": "integer", "minimum": 0, "description": "Last line to include; omit for the reader's page."},
                "raw": {"type": "boolean", "description": "Return unnumbered text."},
                "repeat": {"type": "boolean",
                           "description": "Re-deliver a range already shown in this session; default false returns a pointer."},
                "section": {"type": "string",
                            "description": "Resolve a heading/symbol through outline() and read that range; use outline(path) to see names."},
                "ranges": {"type": "array",
                           "items": {"type": "object",
                                     "properties": {"path": {"type": "string"},
                                                    "start": {"type": "integer", "minimum": 1},
                                                    "end": {"type": "integer", "minimum": 0}},
                                     "required": ["path"]},
                           "description": "Batched ranged reads: [{path, start, end}, ...] in one call."},
            },
            "anyOf": [{"required": ["path"]}, {"required": ["ranges"]}],
            "additionalProperties": False,
        },
    },
    "session_diff": {
        "function": "diff_tool",
        "summary": "The session journal view: the unified diff of mutations made this run, optionally for one path. For the working tree/history use git(action='diff').",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    "checks": {
        "function": "checks",
        "summary": "Run the check plan this task was started with. One row per spec: {name, ok, exit, first_line, detail?}; name= runs that one spec. The default plan includes the project's lint/format/test commands (including cargo fmt/clippy/test), so a green test suite does not excuse a failing lint. A cached green spec for this tree is not re-run, and a failing plan still blocks final_answer. The plan is checks(); do not run a gate by hand.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Run only the plan entry with this name."},
            },
            "additionalProperties": False,
        },
    },
    "serve": {
        "function": "serve",
        "summary": "Find, read, start or stop the TUI server for this workspace: the state root (CODE_TUI_STATE_ROOT, else $XDG_STATE_HOME/code-tui, then ~/.local/state/code-tui) and workspaces/<sha256(cwd)[:16]>, read from server.json plus /health and /api/snapshot. status returns {workspace, state_dir, server_json, pid, port, healthy, model, effort, concurrency, checks, stale, changed, queue:{running,queued}}; logs tails server.log; start spawns the same detached nohup bun src/server.ts the launcher does and polls /health; stop sends SIGTERM and waits for the pid to exit. action=status with no server.json is code `no_server`; start/stop refuse with `busy` while a task is running or when the pid is the server hosting this worker. status and logs never mutate.",
        "mutates": False,
        "requires_run": True,
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "logs", "start", "stop"],
                           "description": "status (default) reports; logs tails server.log; start and stop change the process."},
                "tail": {"type": "integer", "minimum": 1,
                         "description": "Lines of server.log for action='logs' (default 40, capped at 400)."},
                "timeout": {"type": "number", "minimum": 1,
                            "description": "Seconds to wait for /health after start, or for the pid to exit after stop."},
            },
            "additionalProperties": False,
        },
    },
    "revert_check": {
        "function": "revert_check",
        "summary": "Answer 'does this test fail without the fix?': swap `paths` back to their HEAD content, run cmd, and always restore the working copy in a finally. Returns {broken_test, exit_with_original, exit_now, restored}; a test that also PASSES on the original code is broken_test=true and verifies nothing. Refuses unless the tree is clean outside paths (naming the other dirty paths), refuses an untracked path, and never stashes or checks out. Verify restored=true; a false one names the path that must be repaired by hand.",
        "mutates": True,
        "requires_run": True,
        "schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "The tracked paths the fix touches; they are swapped to HEAD and restored."},
                "cmd": {"oneOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}],
                        "description": "The test command: an argv list, or a shell string when shell syntax is needed."},
                "timeout": {"type": "number", "minimum": 1,
                            "description": "Seconds per run (default 120): the command runs twice."},
            },
            "required": ["paths", "cmd"],
            "additionalProperties": False,
        },
    },
    "status": {
        "function": "status",
        "summary": "One call for tree state: branch, capped `git status --short`, ahead/behind when cheap, and the session journal's paths with add/del counts (never diff text). A directory outside a git work tree is a valid state (`git: false`, `no_repo: true`), not a failed call; the journal summary is still returned. Read-only: it never journals and never mutates.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Run git here (default the workspace root)."},
            },
            "additionalProperties": False,
        },
    },
}
