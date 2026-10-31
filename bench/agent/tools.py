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

class CalcError(ValueError):
    pass

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

def _calc_sum(*items):
    if len(items) == 1 and isinstance(items[0], (list, tuple)):
        return sum(items[0])
    return sum(items)

_CALC_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sum": _calc_sum,
    "len": len,
}

_CMP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

def _eval_calc(node: ast.AST, env: dict | None = None):

    env = env or {}
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError(f"constant {node.value!r} is not numeric")
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise CalcError(f"unknown name {node.id!r}; only comprehension targets are allowed")
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval_calc(node.left, env)
        right = _eval_calc(node.right, env)
        if isinstance(node.op, ast.Pow) and abs(right) > 10_000:
            raise CalcError("exponent above 10000 refused")
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Not)):
        value = _eval_calc(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not value
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.Compare):
        left = _eval_calc(node.left, env)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_calc(comparator, env)
            if type(op) not in _CMP:
                raise CalcError(f"unsupported comparison: {type(op).__name__}")
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        values = [_eval_calc(v, env) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.IfExp):
        return _eval_calc(node.body if _eval_calc(node.test, env) else node.orelse, env)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_eval_calc(e, env) for e in node.elts]
    if isinstance(node, ast.ListComp):
        if len(node.generators) != 1:
            raise CalcError("use one for-clause per comprehension")
        gen = node.generators[0]
        if gen.is_async or not isinstance(gen.target, ast.Name):
            raise CalcError("comprehension target must be a plain name")
        source = _eval_calc(gen.iter, env)
        if not isinstance(source, (list, tuple)):
            raise CalcError("comprehension source must be a list literal")
        out = []
        for item in source:
            inner = dict(env)
            inner[gen.target.id] = item
            if all(_eval_calc(cond, inner) for cond in gen.ifs):
                out.append(_eval_calc(node.elt, inner))
        return out
    if isinstance(node, ast.GeneratorExp):
        raise CalcError("use a list literal in brackets: sum([... for ...])")
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _CALC_FUNCS
        and not node.keywords
    ):
        return _CALC_FUNCS[node.func.id](*[_eval_calc(arg, env) for arg in node.args])
    raise CalcError(f"unsupported expression element: {type(node).__name__}")



@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., dict]
    mutates: bool = False

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

def err(code: str, message: str, *, hint: str | None = None, **extra: Any) -> dict:
    body: dict = {"code": code, "message": message}
    if hint:
        body["hint"] = hint
    body.update(extra)
    return {"ok": False, "error": body}

_PY_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}

def coerce(schema: dict, value: Any) -> Any:
    kind = schema.get("type")
    if kind == "object" and isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: coerce(props.get(k, {}), v) for k, v in value.items()}
    if kind == "integer" and not isinstance(value, bool):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            return int(value.strip())
    if kind == "number" and isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    if kind == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if kind == "boolean" and isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value

def validate(schema: dict, value: Any, path: str = "args") -> list[str]:
    problems: list[str] = []
    kind = schema.get("type")
    if kind:
        py = _PY_TYPES[kind]
        good = isinstance(value, py) and not (kind in ("integer", "number") and isinstance(value, bool))
        if not good:
            return [f"{path}: expected {kind}, got {type(value).__name__} ({value!r})"]
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        problems.append(f"{path}: must be at least {schema['minLength']} characters")
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: must be one of {schema['enum']}, got {value!r}")
    if kind in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: must be >= {schema['minimum']}, got {value!r}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: must be <= {schema['maximum']}, got {value!r}")
    if kind == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}.{key}: required, missing")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    problems.append(f"{path}.{key}: unknown argument; allowed: {', '.join(props)}")
        for key, sub in props.items():
            if key in value:
                problems.extend(validate(sub, value[key], f"{path}.{key}"))
    return problems

def _parse_path(raw: str) -> tuple[str, int | None]:
    p = raw.strip().strip('"').strip("'").strip("`").strip()
    p = p.replace("\\", "/")
    line_no: int | None = None
    m = re.match(r"^(.*?):(\d{1,9})$", p)
    if m and m.group(1):
        p, line_no = m.group(1), int(m.group(2))
    if p.startswith("~"):
        p = p[1:]
    p = posixpath.normpath(p.lstrip("/") or ".")
    if p == ".":
        p = ""
    return p, line_no

def _entries(directory: Path, limit: int = MAX_ENTRIES) -> list[dict]:
    try:
        children = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
    except OSError:
        return []
    out = []
    for child in children:
        if child.name.startswith(".") or child.name in SKIP_DIRS:
            continue
        is_dir = child.is_dir()
        out.append(
            {
                "name": child.name,
                "type": "dir" if is_dir else "file",
                "bytes": None if is_dir else child.stat().st_size,
            }
        )
    return out[:limit]

def _miss(root: Path, rel: str, tree: Callable[[], list[str]]) -> dict:

    close = difflib.get_close_matches(rel or ".", tree(), n=3, cutoff=0.4)
    near = rel
    while near and not (root / near).exists():
        near = posixpath.dirname(near)
    near_path = root / near if near else root
    nearby = {"path": near or ".", "entries": _entries(near_path, 50)} if near_path.is_dir() else None
    if close:
        hint = f"read(path='{close[0]}')"
    elif nearby:
        hint = f"read(path='{near or '.'}')"
    else:
        hint = "read(path='.')"
    return {
        "ok": True,
        "kind": "miss",
        "path": rel,
        "did_you_mean": close,
        "nearby": nearby,
        "note": f"'{rel}' does not exist; this is information, not a fault. {hint} to continue.",
    }

def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]

def make_read(root: Path, max_chars: int = MAX_CHARS) -> Tool:
    root = root.resolve()
    cache: list[str] | None = None

    def tree() -> list[str]:
        nonlocal cache
        if cache is None:
            items = []
            for p in root.rglob("*"):
                rel = p.relative_to(root)
                if any(part.startswith(".") or part in SKIP_DIRS for part in rel.parts):
                    continue
                items.append(rel.as_posix())
            cache = sorted(items)
        return cache

    def listing(rp: Path, rel: str) -> dict:
        entries = _entries(rp)
        truncated = len(entries) >= MAX_ENTRIES
        base = rel or "."
        prefix = "" if base == "." else base + "/"
        return {
            "ok": True,
            "kind": "dir",
            "path": base,
            "count": len(entries),
            "entries": entries,
            "truncated": truncated,
            "note": f"'{base}' is a directory. Descend with read(path='{prefix}<name>').",
        }

    def file(rp: Path, rel: str, offset: int, limit: int) -> dict:
        raw = rp.read_bytes()
        size = len(raw)
        if _is_binary(raw):
            return {
                "ok": True,
                "kind": "binary",
                "path": rel,
                "bytes": size,
                "note": f"binary file ({size} bytes); not decoded.",
            }
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        offset = max(1, offset)
        limit = max(1, min(limit, DEFAULT_LIMIT))
        sha = hashlib.sha256(raw).hexdigest()[:12]

        notes: list[str] = []
        if total == 0:
            return {
                "ok": True,
                "kind": "file",
                "path": rel,
                "total_lines": 0,
                "shown": [0, 0],
                "truncated": False,
                "sha": sha,
                "text": "",
                "note": "file is empty",
            }
        if offset > total:
            offset = max(1, total - limit + 1)
            notes.append(f"offset past EOF; file has {total} lines, returned the tail from line {offset}")

        end = min(total, offset + limit - 1)
        body: list[str] = []
        used = 0
        i = offset
        last = offset - 1
        while i <= end:
            rendered = f"{i:>6}| {lines[i - 1]}"
            if used + len(rendered) + 1 > max_chars:
                if not body:
                    body.append(rendered[:max_chars])
                    last = i
                    notes.append("line exceeded the 16000 character budget and was cut")
                else:
                    notes.append(f"character budget reached before line {i}")
                break
            body.append(rendered)
            used += len(rendered) + 1
            last = i
            i += 1

        truncated = last < total
        env: dict = {
            "ok": True,
            "kind": "file",
            "path": rel,
            "total_lines": total,
            "shown": [offset, last],
            "truncated": truncated,
            "sha": sha,
            "text": "\n".join(body),
        }
        if truncated:
            env["next"] = {"offset": last + 1}
        if notes:
            env["note"] = "; ".join(notes)
        return env

    def read(path: str, offset: int = 1, limit: int = DEFAULT_LIMIT) -> dict:
        rel, line_hint = _parse_path(path)
        if line_hint is not None and offset == 1:
            offset = line_hint

        target = root / rel
        try:
            resolved = target.resolve()
        except OSError:
            resolved = target.absolute()
        if not (resolved == root or root in resolved.parents):
            return err(
                "outside_root",
                f"'{rel}' resolves outside ROOT",
                hint=f"ROOT is '{root.name}/'; pass a path relative to it",
            )
        if not resolved.exists():
            return _miss(root, rel, tree)
        if resolved.is_dir():
            return listing(resolved, rel)
        return file(resolved, rel, offset, limit)

    return Tool("read", READ_DESCRIPTION.replace("16000", str(max_chars)), READ_PARAMETERS, read)

