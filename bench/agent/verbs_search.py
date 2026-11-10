"""verbs_search: the registry rows of the text-lookup and paging verbs.

WHAT IT IS
  VERBS_SEARCH: name -> entry for search, recall, page and outline. One family: find text (tree
  or session), then page the full result or outline its structure. verbs.py merges the family
  tables into VERBS in the same order they appear here, so the registry is one ordered dict.

ENTRY POINTS
  VERBS_SEARCH   four registry entries: function, summary, mutates=False, schema

INVARIANTS
  data only: no code, no imports, no filesystem; safe to ship to the kernel child as JSON.
  summary = the model-facing tool description, schema = the contract; verbs.py copies a row into
  VERBS untouched.
  read-only: search never runs a process (the kernel's own prelude implements it), and page is
  a read of an existing _OBS handle, never a re-run.
  a missing hit is an ok:true miss with a note, not an error the model has to repair.

WHERE TO CHANGE
  one verb's description or arguments ... its entry here
  the paging envelope ................... kernel/prelude_recall.py + kernel/prelude_read.py
"""
from __future__ import annotations

VERBS_SEARCH: dict[str, dict] = {
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
}
