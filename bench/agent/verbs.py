"""The verb registry: every typed action, its JSON schema, and its one-line contract.

WHAT IT IS
  The single source of truth for the typed tool layer. `VERBS[name]` carries the tool
  description, the JSON schema the provider sees, whether the action mutates the tree, the
  prelude function that implements it (`function`, default `name`), and whether it is a
  typed tool (`typed`, default True) or a python-only helper. `tools.py` builds the provider
  specs from the typed entries; `kernel/process.py` ships the whole dict to the child as
  KERNEL_VERBS so `help()`/`verbs()` and the driver resolve the same names. Adding a verb
  here, implementing the function in kernel/prelude.py (or, for a parent-side tool like
  `checks`, a handler in tool_loop.py plus a prelude stub), adding a smoke call in
  bench/smoke_checks/, and mentioning it in the rules text is the whole job.

ENTRY POINTS
  VERBS          name -> {function, summary, schema, mutates, typed?, requires_run?}
  TOOL_NAMES     the typed tool names (no `python`; that is the escape hatch)
  Look one up straight from VERBS[name]; there is no accessor layer.

INVARIANTS
  - Stdlib-only and import-free: kernel/process.py imports it in the parent and ships its JSON
    to the child, so it must never touch the filesystem or import bench modules.
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
  A new typed action ................ add the entry here, the function in kernel/prelude.py,
                                      and a `Kernel.call` case in bench/smoke_checks/verbs.py
  A verb's arguments ................ its `schema` here and the function signature in PRELUDE
  What the model is told ............ `summary` (provider tool description)
"""
from __future__ import annotations

VERBS: dict[str, dict] = {
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
        "summary": "Run the check plan this task was started with. One row per spec: {name, ok, exit, first_line}; name= runs that one spec. A cached green spec for this tree is not re-run, and a failing plan still blocks final_answer. The plan is checks(); do not run a gate by hand.",
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
        "summary": "One call for tree state: branch, capped `git status --short`, ahead/behind when cheap, and the session journal's paths with add/del counts (never diff text). Read-only: it never journals and never mutates. Tree state is status().",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Run git here (default the workspace root)."},
            },
            "additionalProperties": False,
        },
    },
    "search": {
        "function": "search",
        "summary": "Search for a regex (default) or literal; `path` takes a list and the pattern is a regex, so search(pattern='alpha|beta', path=[a, b], max_results=0) is one whole multi-file grep in one call. stdout is path:line:text for hits and path:line-text for context; merged context appears once. The envelope carries shown/total/offset/next/unit/complete (offset is the current start, next the cursor); above max_chars broad context becomes locators (max_chars is a threshold, not a cap); a paged result adds by-file hit ranges; count=true returns only path:count; within=[start,end] or section='Heading' restricts a range. include filters directory walks; a file path is searched as given.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                         "description": "A file path is searched as given; a directory walk is filtered by include."},
                "regex": {"type": "boolean", "default": True,
                          "description": "Treat pattern as a regular expression (default true). Set false for a literal substring."},
                "include": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                            "description": "Filters a directory walk only; literal = exact relative path, glob = full path or basename ('*.py' any depth, 'bench/*.py' one level). Ignored for an explicit file path."},
                "context": {"type": "integer", "minimum": 0,
                            "description": "Lines before and after each hit; 0 returns the matching line only."},
                "ignore_case": {"type": "boolean"},
                "max_results": {"type": "integer", "minimum": 0,
                                "description": "Page size (default 50); 0 returns every hit in one answer; passing it makes the page an explicit window, so a cut result is has_more rather than truncated."},
                "offset": {"type": "integer", "minimum": 0,
                           "description": "Skip this many matching lines; next.offset pages the result."},
                "count": {"type": "boolean", "default": False,
                          "description": "Only count matching lines per file; returns path:count rows, no hit text."},
                "section": {"type": "string",
                            "description": "Restrict to an outline section by name (Markdown heading or code symbol)."},
                "sections": {"type": "boolean", "description": "Group a broad single-file result by outline section (auto above 20 hits; sections=false keeps the hit table)."},
                "within": {"type": "array", "items": {"type": "integer", "minimum": 1},
                           "minItems": 2, "maxItems": 2,
                           "description": "Restrict line matching to [start, end] (1-based); useful with an outline range."},
                "max_chars": {"type": "integer", "minimum": 0,
                              "description": "Locator threshold, not a cap: above it broad results are text_elided locators; 0 disables locator mode and returns full text."},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
    "recall": {
        "function": "recall_tool",
        "summary": "Search this session's earlier cell output (including digested/folded turns) and return snippets plus the _OBS handle; this is the session counterpart of search.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
                "offset": {"type": "integer", "minimum": 0,
                           "description": "Skip this many snippets; next.offset pages the result."},
                "context": {"type": "integer", "minimum": 0},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
    "page": {
        "function": "page",
        "summary": "Read a slice of an _OBS[n] entry by the exact handle a card named. Kernel-side, no I/O: offset is a character index, limit the slice size, field stdout (default), stderr or result. An unknown handle is a structured refusal.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "Exactly the handle a card named, like _OBS[12]."},
                "offset": {"type": "integer", "minimum": 0,
                           "description": "Character offset into the field (default 0)."},
                "limit": {"type": "integer", "minimum": 0,
                          "description": "Characters to return (default 8000)."},
                "field": {"type": "string", "enum": ["stdout", "stderr", "result"],
                          "description": "Which part of the _OBS entry to read (default stdout)."},
            },
            "required": ["handle"],
            "additionalProperties": False,
        },
    },
    "outline": {
        "function": "outline",
        "summary": "Return a file's structure with line ranges (Markdown headings, Python classes/functions/methods, TS/JS defs and tests, HTML sections/headings inside JS templates, CSS section comments). A Markdown heading ends at the next same-or-shallower heading, so its range covers its subtree; keys=true expands multiline dict keys. path may be a list.",
        "mutates": False,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                         "description": "A file or a list of files; map() lists a directory."},
                "max_entries": {"type": "integer", "minimum": 1,
                                "description": "Page size (default 200)."},
                "keys": {"type": "boolean", "default": False,
                         "description": "Expand top-level dict keys as entries (default false: one assignment row)."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "edit": {
        "function": "edit_many",
        "summary": "Apply one or several old->new replacements in one atomic call. exact old is the normal match; if only leading indentation differs, a unique block is accepted and reported as match=indentation. Ambiguous or differently-worded old text changes nothing and returns the closest lines. There is no single-file form: put every edit in edits=[{path, old, new}, ...].",
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
                    "description": "One or more replacements; all are validated before anything is written. Use this for every edit, including a single file. A unique leading-indentation-only match is accepted; the result names match=indentation.",
                },
            },
            "required": ["edits"],
            "additionalProperties": False,
        },
    },
    "splice": {
        "function": "splice",
        "summary": "Replace 1-based inclusive lines start..end with text. end is required: passing end=start-1 inserts before start and text='' deletes the range, but omitting end is a bad_arguments refusal (the old default silently replaced the start line). Use this when read/outline gave you line numbers; it is the line-anchored alternative to edit.",
        "mutates": True,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "minimum": 1},
                "end": {"type": "integer", "minimum": 0,
                        "description": "Inclusive last line (required). Use start-1 to insert before start."},
                "text": {"type": "string"},
            },
            "required": ["path", "start", "end"],
            "additionalProperties": False,
        },
    },
    "write": {
        "function": "write",
        "summary": "Write a whole file. Use it for a new file or a real rewrite; for an existing file prefer edit/splice. Without force=true it refuses any body shorter than the current file (pass force=true to replace it), and it notes content whose only line breaks are a literal backslash-n.",
        "mutates": True,
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "force": {"type": "boolean", "description": "Allow a body shorter than the current file (a truncated rewrite)."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    "delete": {
        "function": "delete",
        "summary": "Delete one file through the journal; use it instead of rm/mv through exec. Directories are refused (map the files first).",
        "mutates": True,
        "schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "mkdir": {
        "function": "mkdir",
        "summary": "Create one directory (parents included); use it instead of mkdir through exec.",
        "mutates": True,
        "schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "apply": {
        "function": "apply",
        "summary": "Apply a unified diff, possibly across several files, in one journal entry. Prefer edit(edits=[...]) for ordinary atomic replacements; use apply when you already have a real unified diff.",
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
        "summary": "The typed process tool: run argv directly, commands=[[...]] for several programs, or shell=<string> for explicit shell syntax. cwd/env/stdin are explicit; run() is python-only. For files use search/read, not the shell.",
        "mutates": False,
        "requires_run": True,
        "schema": {
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"},
                         "description": "Program and arguments, e.g. ['git', 'status', '--short']. A bare shell string is run once through shell with a note."},
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
        "summary": "Typed git (working tree): status, diff, show, log, files, add, commit, push, remote, branch. diff/show page with offset/max_chars and return next.offset plus has_more, the same paging shape as read; commit takes message and optional paths. Use it instead of git through exec; the typed session_diff tool is the journal view, not git.",
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
