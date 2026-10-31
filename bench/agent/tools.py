from __future__ import annotations

import ast
import difflib
import hashlib
import json
import math
import operator
import posixpath
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

MAX_CHARS = 16_000
MAX_ENTRIES = 400
MAX_MATCHES = 200
MAX_SNIPPET = 240
DEFAULT_LIMIT = 2_000
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules"}

READ_DESCRIPTION = """Read a file or directory under ROOT. This is the only source of truth: a fact not in tool output is unknown.

CALL
  read(path, offset=1, limit=2000)

PATH
  Relative to ROOT. Accepts "./x", "x", "/x"; a trailing ":LINE" becomes offset. Surrounding quotes/backticks are stripped.

RETURNS ok:true, kind:"file"
  path, total_lines, shown:[first,last], truncated, next:{offset}, sha, text
  text is lines formatted "{n:>6}| {content}". Line numbers are authoritative; cite them.
  truncated:true means content continues at next.offset.

RETURNS ok:true, kind:"dir"
  entries:[{name, type:"file"|"dir", bytes}], count, truncated
  Descend with read(path + "/" + name).

RETURNS ok:true, kind:"miss"
  did_you_mean:[real paths], nearby:{path, entries}
  The requested path does not exist. This is information, not a failure; the neighbours are listed.

RETURNS ok:true, kind:"binary"
  bytes, note

FAILS ok:false, error.code
  unknown_tool | bad_json | bad_args | outside_root | tool_crashed
  These are protocol faults. error.hint names the call that fixes it.

GUARANTEES
  A directory passed as path returns its listing.
  An offset past EOF returns the tail plus a note.
  An identical repeated call returns the cached envelope.
  Output never exceeds 16000 characters; overflow is reported via truncated.
"""

READ_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path relative to ROOT. File or directory. Optional trailing ':LINE'.",
        },
        "offset": {
            "type": "integer",
            "minimum": 1,
            "description": "1-based first line to return. Default 1.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": DEFAULT_LIMIT,
            "description": "Maximum lines to return. Default 2000.",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}

FIND_DESCRIPTION = """Find lines matching a pattern, recursively under a path. Use this to locate a symbol or count occurrences instead of paging through files.

CALL
  find(pattern, path=".", max_results=50, regex=False)

ARGS
  pattern      Substring, or a regular expression when regex=true. Empty pattern is invalid.
  path         File or directory under ROOT. Default "." (whole tree).
  max_results  Cap on returned matches, 1..200. Default 50.
  regex        true -> Python regex (case-sensitive). Invalid regex falls back to a literal search and says so in note.

RETURNS ok:true, kind:"matches"
  pattern, path, regex, files_searched, lines_matched, occurrences, matches:[{path, line, text}], truncated
  matches lists matching lines; text is the line, stripped, at most 240 characters.
  lines_matched = len(matches). occurrences = total substring/regex hits, so a line holding
  two hits counts twice there and once in lines_matched. Use occurrences when the question
  asks "how many times".
  occurrences is null when truncated:true, because the scan stopped early; narrow the pattern.
  lines_matched:0 is a real answer, not an error.

RETURNS ok:true, kind:"miss"
  Same contract as read: the requested path does not exist; neighbours are listed.

FAILS ok:false, error.code
  unknown_tool | bad_json | bad_args | outside_root | tool_crashed
"""

FIND_PARAMETERS = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "minLength": 1,
            "description": "Substring or regex to search for.",
        },
        "path": {
            "type": "string",
            "description": "File or directory relative to ROOT. Default '.'.",
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_MATCHES,
            "description": "Maximum matches to return. Default 50.",
        },
        "regex": {
            "type": "boolean",
            "description": "Treat pattern as a Python regex. Default false.",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}

CALC_DESCRIPTION = """Evaluate one Python arithmetic/aggregation expression over numeric literals. Use this for every numeric answer that depends on computation. Do not evaluate arithmetic mentally: hand-computed values are the most common wrong answer.

CALL
  calc(expression)

ARGS
  expression  One expression built from:
                numbers; + - * / // % **; parentheses
                comparisons < <= > >= == !=; and or not; x if cond else y
                list/tuple literals; list comprehensions, e.g. sum([1 for a in [12.5, 240, 87.25] if a > 100])
                functions: abs min max round sum len
              No variables except comprehension targets, no strings, no attribute access, no imports, no statements.
              Write the data into the expression. Do not pre-count or pre-summarise by hand.
              Example that answers "how many of these exceed 100": sum([1 for a in [12.5,240,87.25,130] if a > 100])

RETURNS ok:true, kind:"value"
  expression, value, repr, type
  value is the JSON number; repr is the exact Python form.

FAILS ok:false, error.code
  bad_expr: the expression uses a construct outside the subset; the message names it.
  bad_args: the argument does not match CALC_PARAMETERS.
  error.hint states the correction.
"""

CALC_PARAMETERS = {
    "type": "object",
    "properties": {
        "expression": {
            "type": "string",
            "minLength": 1,
            "description": "Arithmetic expression over numeric literals.",
        }
    },
    "required": ["expression"],
    "additionalProperties": False,
}

