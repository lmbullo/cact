"""The verb registry: every typed action, its JSON schema, and its one-line contract.

WHAT IT IS
  The single source of truth for the typed tool layer. `VERBS[name]` carries the tool
  description, the JSON schema the provider sees, whether the action mutates the tree, the
  prelude function that implements it (`function`, default `name`), and whether it is a
  typed tool (`typed`, default True) or a python-only helper. `tools.py` builds the provider
  specs from the typed entries; `kernel.py` ships the whole dict to the child as KERNEL_VERBS
  so `help()`/`verbs()` and the driver resolve the same names. Adding a verb here,
  implementing the function in kernel.PRELUDE, adding a smoke call, and mentioning it in
  the rules text is the whole job.

ENTRY POINTS
  VERBS          name -> {function, summary, schema, mutates, typed?, requires_run?}
  TOOL_NAMES     the typed tool names (no `python`; that is the escape hatch)
  schema(name)   the parameters object for one verb
  summary(name)  the provider-facing description for one verb

INVARIANTS
  - Stdlib-only and import-free: kernel.py imports it in the parent and ships its JSON to the
    child, so it must never touch the filesystem or import bench modules.
  - The schema is the contract, and the function's real signature is the authority: the
    driver binds each typed call with `inspect.signature().bind`, so a schema/signature
    mismatch surfaces as `bad_args`/`bad_arguments` instead of a traceback, and `help()`
    prints each live signature. A mismatch is a bug in the schema or the function, never a
    reason to loosen the check.
  - `mutates=True` means the function writes the workspace and must go through `_commit`.
    The loop uses it only for rendering; the kernel's journal is still the authority.
  - `python` is deliberately absent. It is the persistent-kernel escape hatch, not a verb.
  - `typed=False` entries (`run`) remain in the registry so `help()` documents the python
    helpers, but they never become provider tools. `exec` is the typed process tool; `run`
    is the shell-string escape hatch inside a python cell.

WHERE TO CHANGE
  A new typed action ................ add the entry here, the function in kernel.PRELUDE,
                                      and a `Kernel.call` case in bench/smoke.py
  A verb's arguments ................ its `schema` here and the function signature in PRELUDE
  What the model is told ............ `summary` (provider tool description)
"""
from __future__ import annotations

NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

VERBS: dict[str, dict] = {
    "map": {
        "function": "map",
        "summary": "List the files under a directory with line counts, without shell ls/find; include filters by glob. Use it to orient in a repo, then outline/read/search a file.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory; default the workspace root."},
                "max_files": {"type": "integer", "minimum": 1},
                "include": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                            "description": "Glob(s) a relative path must match, e.g. '*.md'."},
            },
            "additionalProperties": False,
        },
    },
    "read": {
        "function": "read",
        "summary": "Read one file or several files. Returns kind/total_lines/shown/next/truncated and the numbered text; a list of paths is one call. A range already shown this session returns a short pointer; pass repeat=true to re-deliver it.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                         "description": "File path, or a list of paths to read in one call."},
                "offset": {"type": "integer", "minimum": 1, "description": "First 1-based line (default 1)."},
                "limit": {"type": "integer", "minimum": 0, "description": "Maximum lines; 0 means the reader's page size."},
                "start": {"type": "integer", "minimum": 1, "description": "Alias for offset; the natural line-range name."},
                "end": {"type": "integer", "minimum": 0, "description": "Last line to include (with start)."},
                "raw": {"type": "boolean", "description": "Return unnumbered text."},
                "repeat": {"type": "boolean",
                           "description": "Re-deliver a range already shown in this session; default false returns a pointer."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "search": {
        "function": "search",
        "summary": "Search files for a regex (default) or a literal. Returns path:line:text and optional context; include filters paths (e.g. '*.py'), and path takes one path or a list.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                "regex": {"type": "boolean", "default": True,
                          "description": "Treat pattern as a regular expression (default true). Set false for a literal substring."},
                "include": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                            "description": "Glob(s) a file's relative path must match, e.g. '*.py'."},
                "context": {"type": "integer", "minimum": 0,
                            "description": "Lines before and after each hit; 0 returns the matching line only."},
                "ignore_case": {"type": "boolean"},
                "max_results": {"type": "integer", "minimum": 1},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
    "outline": {
        "function": "outline",
        "summary": "Return a file's structure with line numbers (Markdown headings; Python classes/functions/methods; TS/JS defs and tests; HTML sections/headings inside JS template literals; CSS section comments). Use it before reading a long file, then read only the ranges you need.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "A file; map() lists a directory."},
                "max_entries": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "edit": {
        "function": "edit_many",
        "summary": "Apply one or several exact old->new replacements. A list is atomic: every old must match exactly (or all=True), otherwise nothing changes. Use it for a batch of doc/source edits instead of one call per line.",
        "mutates": True,
        "schema": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old": {"type": "string"},
                            "new": {"type": "string"},
                            "all": {"type": "boolean", "description": "Replace every occurrence of old."},
                        },
                        "required": ["path", "old", "new"],
                        "additionalProperties": False,
                    },
                },
                "path": {"type": "string", "description": "Single-edit form: file path (or default path for a list)."},
                "old": {"type": "string", "description": "Single-edit form: exact text to replace."},
                "new": {"type": "string", "description": "Single-edit form: replacement text."},
                "all": {"type": "boolean", "description": "Single-edit form: replace every occurrence."},
            },
            "additionalProperties": False,
        },
    },
    "splice": {
        "function": "splice",
        "summary": "Replace 1-based inclusive lines start..end with text; end < start inserts before start, text='' deletes. Use it to avoid re-emitting unchanged lines.",
        "mutates": True,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "minimum": 1},
                "end": {"type": "integer", "description": "Inclusive last line; omit to replace one line, use start-1 to insert."},
                "text": {"type": "string"},
            },
            "required": ["path", "start"],
            "additionalProperties": False,
        },
    },
    "write": {
        "function": "write",
        "summary": "Write a whole file. Use it for a new file or a real rewrite; for an existing file prefer edit/splice.",
        "mutates": True,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "force": {"type": "boolean", "description": "Allow a write that truncates an existing tail."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    "apply": {
        "function": "apply",
        "summary": "Apply a unified diff, possibly across several files, in one journal entry.",
        "mutates": True,
        "schema": {
            "type": "object",
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
            "additionalProperties": False,
        },
    },
    "exec": {
        "function": "exec_argv",
        "summary": "The single process tool: run argv directly, commands=[[...]] for several programs, or shell=<string> for explicit shell syntax. cwd replaces cd. Do not pass bash -lc in argv; use shell. For files use search/read, not the shell.",
        "mutates": False,
        "requires_run": True,
        "schema": {
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"},
                         "description": "Program and arguments, e.g. ['git', 'status', '--short']."},
                "commands": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                             "description": "Several argv commands, run in order."},
                "shell": {"type": "string",
                          "description": "Explicit shell string (the escape hatch). Do not pass bash -lc in argv."},
                "cwd": {"type": "string", "description": "Working directory; default the workspace root."},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "stdin": {"type": "string"},
                "timeout": {"type": "number", "minimum": 1},
            },
            "additionalProperties": False,
        },
    },
    "run": {
        "function": "run",
        "typed": False,
        "requires_run": True,
        "summary": "Python helper, not a typed tool: run a shell command or a list of commands from a python cell. Use exec for the typed path.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "command": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                "timeout": {"type": "number", "minimum": 1},
                "cwd": {"type": "string"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    "git": {
        "function": "git_tool",
        "summary": "Typed git: status, diff, show, log, files, add, commit, push, remote, branch. diff/show page with offset/max_chars; commit takes message and optional paths. Use it instead of git through exec.",
        "mutates": False,
        "requires_run": True,
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["status", "diff", "show", "log", "files", "add",
                                    "commit", "push", "remote", "branch"]},
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
                "cwd": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
}

TOOL_NAMES = tuple(name for name, meta in VERBS.items() if meta.get("typed", True))


def schema(name: str) -> dict:
    return VERBS[name]["schema"]


def summary(name: str) -> str:
    return VERBS[name]["summary"]
