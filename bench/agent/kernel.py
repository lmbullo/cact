from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

DRIVER = r'''
import ast, contextlib, io, json, os, signal, sys, time, traceback

class _CellTimeout(Exception):
    pass

def _on_alarm(_signum, _frame):
    raise _CellTimeout()

signal.signal(signal.SIGALRM, _on_alarm)
_CELL_END = 0.0
_SUPERVISE = os.environ.get("KERNEL_SUPERVISE", "1") != "0"

def _extend(seconds):
    global _CELL_END
    if not _SUPERVISE:
        return
    _CELL_END = max(_CELL_END, time.monotonic() + float(seconds))
    signal.setitimer(signal.ITIMER_REAL, max(0.05, _CELL_END - time.monotonic()))
    try:
        sys.__stdout__.write("@@K@@" + json.dumps({"extend": float(seconds)}) + "\n")
        sys.__stdout__.flush()
    except Exception:
        pass

def _arm(seconds):
    global _CELL_END
    if not _SUPERVISE:
        return
    _CELL_END = time.monotonic() + max(0.5, float(seconds))
    signal.setitimer(signal.ITIMER_REAL, max(0.5, float(seconds)))

def _disarm():
    signal.setitimer(signal.ITIMER_REAL, 0)

ns = {"__name__": "__agent__", "_CELL_EXTEND": _extend}
_prelude = os.environ.get("KERNEL_PRELUDE")
if _prelude:
    exec(_prelude, ns)
    if os.environ.get("KERNEL_PRELUDE_MODE") == "mut":
        for _n in ("map", "read", "grep"):
            ns.pop(_n, None)
_run_prelude = os.environ.get("KERNEL_RUN_PRELUDE")
if _run_prelude:
    exec(_run_prelude, ns)

class Final(Exception):
    def __init__(self, value):
        self.value = value

def final_answer(value):
    raise Final(value)

ns["final_answer"] = final_answer

def emit(payload):
    sys.__stdout__.write("@@K@@" + json.dumps(payload) + "\n")
    sys.__stdout__.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        request = json.loads(line)
        code = request["code"]
        budget = float(request.get("timeout") or 15.0)
    except Exception:
        emit({"stdout": "", "stderr": "kernel: bad request", "result": None, "final": None, "timeout": False})
        continue
    out, err = io.StringIO(), io.StringIO()
    result_repr = None
    final = None
    timed_out = False
    if "_CELL_CHANGED" in ns:
        ns["_CELL_CHANGED"] = []
    saved_stdin = sys.stdin
    _arm(budget)
    try:
        sys.stdin = io.StringIO("")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            tree = ast.parse(code, "<cell>", "exec")
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                head = ast.Module(tree.body[:-1], type_ignores=[])
                exec(compile(head, "<cell>", "exec"), ns)
                value = eval(compile(ast.Expression(tree.body[-1].value), "<cell>", "eval"), ns)
                if value is not None:
                    result_repr = repr(value)
            else:
                exec(compile(tree, "<cell>", "exec"), ns)
    except Final as f:
        final = f.value if isinstance(f.value, str) else repr(f.value)
    except _CellTimeout:
        timed_out = True
        err.write(
            "timeout: the cell exceeded %.0fs and was interrupted. "
            "The namespace, imports and variables are intact.\n" % budget
        )
    except BaseException:
        err.write(traceback.format_exc())
    finally:
        _disarm()
        sys.stdin = saved_stdin
    answer = ns.get("ANSWER")
    _runs = ns.get("_RUN_LOG")
    if isinstance(_runs, list):
        emitted_runs = [str(x) for x in _runs]
        _runs.clear()
    else:
        emitted_runs = []
    _mutated = bool(ns.get("_MUT"))
    ns["_MUT"] = False
    _delta_fn = ns.get("_delta")
    _delta = ""
    if callable(_delta_fn):
        try:
            _delta = "\n".join(_delta_fn())[:12000]
        except Exception:
            _delta = ""
    _changed_fn = ns.get("_changed")
    if callable(_changed_fn):
        try:
            _changed = [rel for rel, _before, _after in _changed_fn()]
        except Exception:
            _changed = list(ns.get("_CELL_CHANGED") or [])
    else:
        _changed = list(ns.get("_CELL_CHANGED") or [])
    _prev_changed = set(ns.get("_LAST_CHANGED") or [])
    _mutated = _mutated or bool(set(_changed) - _prev_changed)
    ns["_LAST_CHANGED"] = set(_changed)
    _obs_log = ns.setdefault("_OBS", [])
    _obs_log.append({"stdout": out.getvalue(), "stderr": err.getvalue(), "result": result_repr})
    emit({"stdout": out.getvalue(), "stderr": err.getvalue(), "result": result_repr, "final": final,
          "runs": emitted_runs, "obs_index": len(_obs_log) - 1, "mutated": _mutated,
          "changed": _changed, "delta": _delta, "timeout": timed_out,
          "answer": None if answer is None else (answer if isinstance(answer, str) else repr(answer))})
'''


PRELUDE = r"""
import difflib as _difflib, hashlib as _hashlib, os as _os, re as _re

_ROOT = _os.getcwd()
_MUT = False
_JOURNAL = []
_SNAP = {}
_JSEQ = 0
_CELL_CHANGED = []
_open = open
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", "vendor", ".cache",
}
_HINT = {
    "cat": "read(path)", "head": "read(path, offset=1, limit=N)",
    "tail": "read(path, offset=total-N)", "less": "read(path)",
    "more": "read(path)", "wc": "read(path)['total_lines']",
    "stat": "map(path)", "grep": "grep(pattern, path)",
    "rg": "grep(pattern, path, regex=True)", "ag": "grep(pattern, path)",
    "sed": "read(path) or edit(path, old, new)",
    "awk": "read(path) with Python string slicing",
    "find": "map(path)", "ls": "map(path)", "tree": "map(path)",
    "du": "map(path)", "tee": "write(path, content)",
    "touch": "write(path, '')", "cp": "read + write",
    "mv": "write + os.remove", "rm": "os.remove",
    "truncate": "write(path, '')",
}


class _Writer:
    def __init__(self, target, rel, base):
        self.target = target
        self.rel = rel
        self._parts = [base]
        self.closed = False

    def write(self, text):
        self._parts.append(text)
        return len(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        return None

    def close(self):
        if self.closed:
            return
        self.closed = True
        _commit(self.target, self.rel, "".join(self._parts), "open()")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open(file, mode="r", *args, **kwargs):
    if any(ch in mode for ch in "wax+") and "b" not in mode and "r" not in mode:
        target, rel = _resolve(file)
        if target is not None:
            base = ""
            if "a" in mode and _os.path.isfile(target):
                base = _read_text(target)
            return _Writer(target, rel, base)
    if "b" not in mode and "errors" not in kwargs:
        kwargs["errors"] = "replace"
    return _open(file, mode, *args, **kwargs)


def _resolve(path):
    raw = str(path).strip() or "."
    target = _os.path.normpath(raw if _os.path.isabs(raw) else _os.path.join(_ROOT, raw))
    if target != _ROOT and not target.startswith(_ROOT + _os.sep):
        return None, raw
    return target, _os.path.relpath(target, _ROOT)


def _read_text(path):
    with _open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _digest(text):
    return _hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def _commit(target, rel, body, source):
    global _MUT, _JSEQ
    before = None
    try:
        if _os.path.isfile(target):
            before = _read_text(target)
    except OSError:
        before = None
    _os.makedirs(_os.path.dirname(target) or _ROOT, exist_ok=True)
    with _open(target, "w", encoding="utf-8") as fh:
        fh.write(body)
    _MUT = True
    _JSEQ += 1
    diff_text = "".join(_difflib.unified_diff(
        (before or "").splitlines(True), body.splitlines(True),
        fromfile=("/dev/null" if before is None else "a/" + rel), tofile="b/" + rel, n=3))
    adds = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    dels = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    entry = {"seq": _JSEQ, "path": rel, "source": source, "created": before is None,
             "before_lines": 0 if before is None else len(before.splitlines()),
             "after_lines": len(body.splitlines()), "adds": adds, "dels": dels,
             "before_hash": None if before is None else _digest(before),
             "after_hash": _digest(body), "diff": diff_text}
    _JOURNAL.append(entry)
    _SNAP[entry["seq"]] = before
    if len(_JOURNAL) > 200:
        old = _JOURNAL.pop(0)
        _SNAP.pop(old["seq"], None)
    if rel not in _CELL_CHANGED:
        _CELL_CHANGED.append(rel)
    return entry


_BASE = {}
_BASE_READY = False
_LAST_STATE = None


def _snapshot():
    global _BASE_READY, _LAST_STATE
    if _BASE_READY:
        return
    for full in _walk(_ROOT):
        rel = _os.path.relpath(full, _ROOT)
        try:
            if _os.path.getsize(full) > 2_000_000:
                continue
            body = _read_text(full)
        except (OSError, UnicodeError):
            continue
        _BASE[rel] = {"hash": _digest(body), "text": body}
    _BASE_READY = True
    _LAST_STATE = {rel: row["text"] for rel, row in _BASE.items()}


def _tree_state():
    _snapshot()
    now = {}
    for full in _walk(_ROOT):
        rel = _os.path.relpath(full, _ROOT)
        try:
            if _os.path.getsize(full) > 2_000_000:
                continue
            body = _read_text(full)
        except (OSError, UnicodeError):
            continue
        now[rel] = {"hash": _digest(body), "text": body}
    return now


def _row(rel, before, after):
    if before is None:
        diff_text = "".join(_difflib.unified_diff(
            [], after.splitlines(True), fromfile="/dev/null", tofile="b/" + rel, n=3))
        state = "added"
    elif after is None:
        diff_text = "".join(_difflib.unified_diff(
            before.splitlines(True), [], fromfile="a/" + rel, tofile="/dev/null", n=3))
        state = "deleted"
    else:
        diff_text = "".join(_difflib.unified_diff(
            before.splitlines(True), after.splitlines(True), fromfile="a/" + rel, tofile="b/" + rel, n=3))
        state = "modified"
    adds = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    dels = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    return state, adds, dels, diff_text


def _changed(now=None):
    now = now or _tree_state()
    out = []
    for rel in sorted(set(_BASE) | set(now)):
        before = _BASE.get(rel, {}).get("text")
        after = now.get(rel, {}).get("text")
        if before != after:
            out.append((rel, before, after))
    return out


def _delta():
    global _LAST_STATE
    _snapshot()
    now = {rel: row["text"] for rel, row in _tree_state().items()}
    prev = _LAST_STATE if _LAST_STATE is not None else {rel: row["text"] for rel, row in _BASE.items()}
    texts = []
    for rel in sorted(set(prev) | set(now)):
        before, after = prev.get(rel), now.get(rel)
        if before != after:
            texts.append(_row(rel, before, after)[3])
    _LAST_STATE = now
    return texts


def changes():
    now = _tree_state()
    files = {}
    for rel, before, after in _changed(now):
        state, adds, dels, _ = _row(rel, before, after)
        files[rel] = {"state": state, "adds": adds, "dels": dels}
    return {"ok": True, "kind": "changes", "files": files, "count": len(files)}


def diff(path=None, max_chars=8000):
    rel_filter = None
    if path is not None:
        _, rel_filter = _resolve(path)
    now = _tree_state()
    texts = []
    files = []
    for rel, before, after in _changed(now):
        if rel_filter is not None and rel != rel_filter:
            continue
        state, adds, dels, text = _row(rel, before, after)
        texts.append(text)
        files.append({"path": rel, "state": state, "adds": adds, "dels": dels})
    joined = "\n".join(texts)
    cut = len(joined) > max_chars
    if cut:
        joined = joined[:max_chars] + "\n...[diff truncated]"
    return {"ok": True, "kind": "diff", "files": files, "text": joined, "truncated": cut}


