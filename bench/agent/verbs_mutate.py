"""verbs_mutate: the registry rows of the verbs that write the workspace through the journal.

WHAT IT IS
  VERBS_MUTATE: name -> entry for edit, splice, write, delete, mkdir and apply. One family: every
  entry has mutates=True and every one goes through the prelude's single `_commit`, so the diff,
  undo and `chg:` views see it. verbs.py merges the family tables into VERBS in the same order
  they appear here.

ENTRY POINTS
  VERBS_MUTATE   six registry entries: function, summary, mutates=True, schema

INVARIANTS
  data only: no code, no imports, no filesystem; safe to ship to the kernel child as JSON.
  mutates=True is the contract the loop reads for rendering; the kernel journal is still the
  authority for what changed.
  a mutation verb refuses rather than guesses: an ambiguous edit returns closest lines, a write
  that would drop a tail needs force=True, a splice needs both bounds. The schema documents the
  refusal, it never loosens the function.
  prefer apply/commit for a multi-file change: one diff, one undo.

WHERE TO CHANGE
  one verb's description or arguments ... its entry here
  what the mutation does ................ kernel/prelude_verbs.py (the _commit path)
"""
from __future__ import annotations

VERBS_MUTATE: dict[str, dict] = {
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
}
