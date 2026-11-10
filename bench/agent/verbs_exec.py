"""verbs_exec: the registry rows of the process verbs: exec, run and git.

WHAT IT IS
  VERBS_EXEC: name -> entry for exec (argv process, typed), run (python-cell shell escape, the
  one entry with typed=False) and git (the read-only/mutating git surface). verbs.py merges the
  family tables into VERBS in the same order they appear here.

ENTRY POINTS
  VERBS_EXEC   three registry entries: function, summary, mutates, typed, schema

INVARIANTS
  data only: no code, no imports, no filesystem; safe to ship to the kernel child as JSON.
  run is typed=False: it stays in the registry so help() documents the python helper, but it
  never becomes a provider tool (tools.TOOL_NAMES is built from typed entries).
  exec refuses a shell string and the mutation tools it would bypass (shell_via_exec,
  wrong_call): a process may run a build or a test, not edit around the journal.
  mutates is copied from the entry, not assumed: git carries mutates=False as authored in the
  source table, so a reader must not infer the flag from the verb's name.

WHERE TO CHANGE
  one verb's description or arguments ... its entry here
  what the verb actually runs .......... kernel/process_spawn.py (exec), prelude_verbs.py (run)
"""
from __future__ import annotations

VERBS_EXEC: dict[str, dict] = {
    "exec": {
        "function": "exec_argv",
        "summary": "The typed process tool: run argv directly, commands=[[...]] for several programs, or shell=<string> for explicit shell syntax. cwd/env/stdin are explicit; run() is python-only. For files use search/read, not the shell. Programs that mutate tracked files are refused because they bypass the journal; scratch paths (/tmp, /var/tmp, /dev/shm, $TMPDIR) are allowed. A scratch redirect whose target contains a shell variable may still be refused because the gate cannot prove the expansion.",
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
