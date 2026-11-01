"""Kernel: the agent's entire action space, plus the subprocess that enforces it.

WHAT IT IS
  Two layers in one file.
  1. Source strings executed *inside* the child interpreter:
       DRIVER       loop: one JSON line per cell on stdin -> exec() in a namespace that
                    survives between cells -> one `@@K@@` JSON line back on stdout.
       PRELUDE      injects read/grep/map/write/edit/changes/diff/undo into that namespace
                    and journals every mutation (snapshot at start, tree diff after each cell).
       RUN_PRELUDE  injects run(): whole programs only, behind the file-verb protocol check.
  2. `Kernel` (this process): spawns the child, supervises the per-cell deadline, restarts it
     when it dies, hangs or desynchronises, and returns a `Cell`.

ENTRY POINTS
  Kernel(root, timeout=15.0, writable=False, prelude="off"|"all"|"mut",
         allow_run=False, supervise=True)
    .start()   spawn the child (lazy: .run() calls it)
    .run(code, check_cache=None) write one cell, wait for it, -> Cell
    .stop()    SIGKILL the process group
  Cell: stdout, stderr, result, final, duration_ms, restarted, answer, runs, obs_index,
        mutated, changed, delta, timeout, reads
  Inside the kernel namespace: read(), grep(), map(), write(), edit(), apply(), commit(),
        changes(), diff(), undo(), run() (when allow_run), final_answer(), emit()
  Helper used by the loops: `run_check` lives in check.py; loops read `Cell.changed` /
  `Cell.delta` (state cards) and `Cell.final` / `Cell.answer` (finish protocol).

INVARIANTS
  - The child's stdout is a protocol channel: every message is exactly one `@@K@@`-prefixed
    JSON line. The driver redirects the cell's own stdout/stderr into buffers, so a stray
    print cannot corrupt the channel.
  - A cell that overruns `timeout`, dies, or emits a non-`@@K@@` line is killed and
    restarted with an EMPTY namespace, and the model is told so. Never pretend state
    survived. `supervise=False` disables the in-child alarm (legacy behaviour).
  - `final_answer(v)` raises `Final`; the driver reports the value as `Cell.final`
    unconditionally, and also reports `Cell.answer` (the `ANSWER` variable, used when a repl
    loop stops on "var"). It is the LOOP, not the kernel, that decides what an answer from a
    printing cell means: ToolLoop stages it (STAGED) and accepts it on the next no-tool reply;
    ReplLoop rejects it (UNREVIEWED) — see tool_loop.py / repl.py.
  - Every mutation goes through `_commit`: journal row, snapshot diff, `_CELL_CHANGED`.
    That single path is what changes()/diff()/undo() and the `[state] changed:` card read.
    `write()` replaces (refuses to drop a tail without force=True); `edit()` reports
    ambiguity instead of guessing; `apply(unified diff)` and `commit({path: text})` write
    several files in one cell, so one diff()/undo() covers the whole change set.
  - run() runs whole commands, not a whitelist. Reads, searches, tests, builds, linters and
    read-only git are allowed as they are; the shell is a normal tool. Only *mutating* shell
    verbs (tee, cp, mv, rm, sed -i, git apply/checkout/..., `>` redirection, ...) are refused
    with `kind: "wrong_call"` plus a `use` hint, because they would bypass the journal and
    make diff()/undo() lie. That is a correctness boundary, not a capability boundary: a
    program can still write files, and the docs say so.
  - Direct process spawning is gated by the same classifier. `sys.addaudithook` in
    RUN_PRELUDE sees `subprocess.Popen`, `os.system`, `os.exec*`, `os.posix_spawn` and
    `os.spawn`; a spawn containing a mutating verb raises `_SpawnBlocked`, which the driver
    turns into a `wrong_call` result with the helper hint. Allowed spawns are appended to
    `Cell.runs`. Inside run() the gate classifies but does not double-log.
  - `read()` is a ledger, not a firehose: `limit=0` returns the whole file only while it fits
    400 lines / 9 KB; beyond that the result carries `next.offset`. Every delivered range is
    returned as `Cell.reads = {path: {ranges, total}}`, so the loop can see holes.
  - `check_cache` is sent with each cell (`{command: {text, ok}}`); `run()` answers a
    matching command with `kind: "check_cached"` instead of spawning the same check twice.
  - run() enabled => the child is a plain `python3 -u -c DRIVER`. run() disabled => the child
    runs under bwrap with a read-only `/` (the root is bind-mounted writable only when
    `writable=True`).

WHERE TO CHANGE
  Add a file/exec helper for the model .... PRELUDE or RUN_PRELUDE, plus the matching rule
                                            text in tool_loop.TOOL_RULES / repl.REPL_RULES /
                                            state_loop.STATE_RULES, or the model never sees it
  Change what a helper returns ............ the helper's dict shape in PRELUDE; the loops
                                            only pass the text through
  Change the cell result shape ............ `Cell` + the driver's final `emit({...})` payload
  Change timeout/crash handling ........... `Kernel.run`, `_restart`, `_kill_group`, `_arm`
  Change what run() refuses .............. `_RUN_SHELL_MUTATORS` / `_RUN_GIT_MUTATORS` /
                                            `_mutating_command` + `_HINT`, then the RUN_NOTE
                                            text in repl.py and the README contract list
  The spawn gate .......................... `_audit_spawn`, `_gate_check`, `_SpawnBlocked`
  Read delivery budget .................... `_READ_MAX_LINES`, `_READ_MAX_CHARS` in PRELUDE
"""
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

# --- child side 1/3: DRIVER, the cell loop (stdin JSON in, `@@K@@` line out) -------------
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
        ns["_CHECK_CACHE"] = request.get("check_cache") or {}
        ns["_READS_CELL"] = {}
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
    except BaseException as exc:
        _blocked_cls = ns.get("_SpawnBlocked")
        if _blocked_cls is not None and isinstance(exc, _blocked_cls):
            _hint = (ns.get("_HINT") or {}).get(str(getattr(exc, "blocked", "")).split()[0], "read(), grep(), map(), write() or edit()")
            result_repr = json.dumps({
                "ok": False, "kind": "wrong_call",
                "blocked": getattr(exc, "blocked", ""),
                "command": getattr(exc, "command", ""),
                "error": {"code": "mutation_via_run", "message": str(exc), "use": _hint},
            })
            err.write("run() blocked this process: " + str(exc) + "\n")
        else:
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
    # `changed` is THIS cell, not the session: typed commits are in _CELL_CHANGED and a
    # program that wrote through open()/subprocess shows up in the tree delta.
    _changed = list(ns.get("_CELL_CHANGED") or [])
    for _line in _delta.splitlines():
        if _line.startswith("+++ b/"):
            _rel = _line[6:].strip()
            if _rel not in _changed:
                _changed.append(_rel)
    _mutated = _mutated or bool(_changed)
    _obs_log = ns.setdefault("_OBS", [])
    _obs_log.append({"stdout": out.getvalue(), "stderr": err.getvalue(), "result": result_repr})
    emit({"stdout": out.getvalue(), "stderr": err.getvalue(), "result": result_repr, "final": final,
          "runs": emitted_runs, "reads": dict(ns.get("_READS_CELL") or {}),
          "obs_index": len(_obs_log) - 1, "mutated": _mutated,
          "changed": _changed, "delta": _delta, "timeout": timed_out,
          "answer": None if answer is None else (answer if isinstance(answer, str) else repr(answer))})
'''


# --- child side 2/3: PRELUDE, the file helpers and the mutation journal the model calls ----
PRELUDE = r"""
import difflib as _difflib, hashlib as _hashlib, os as _os, re as _re

_ROOT = _os.getcwd()
_READS_CELL = {}
_READ_MAX_CHARS = 9000
_READ_MAX_LINES = 400
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
        self._dirty = False
        self.closed = False

    def write(self, text):
        self._parts.append(str(text))
        self._dirty = True
        self._flush_to_journal()
        return len(text)

    def writelines(self, lines):
        self._parts.extend(str(line) for line in lines)
        self._dirty = True
        self._flush_to_journal()

    def _flush_to_journal(self):
        if not self._dirty:
            return
        _commit(self.target, self.rel, "".join(self._parts), "open()")
        self._dirty = False

    def flush(self):
        self._flush_to_journal()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._flush_to_journal()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        # `open(path, "w").write(text)` has no name to close; commit when the writer is
        # collected so the journal does not silently miss a write the model just made.
        try:
            self.close()
        except Exception:
            pass


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
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n...[diff truncated]"
    # A string, not a dict: the model reads and slices the diff directly, and `changes()`
    # already carries the per-file metadata.
    return joined


def undo(path=None, n=1):
    global _MUT
    now = _tree_state()
    targets = []
    for rel, before, after in _changed(now):
        if path is not None:
            _, want = _resolve(path)
            if rel != want:
                continue
        targets.append((rel, before))
    if not targets:
        return {"ok": True, "kind": "undo", "restored": [],
                "note": "workspace already matches its session-start state"}
    restored = []
    for rel, before in reversed(targets):
        target, _ = _resolve(rel)
        try:
            if before is None:
                _os.remove(target)
            else:
                _os.makedirs(_os.path.dirname(target) or _ROOT, exist_ok=True)
                with _open(target, "w", encoding="utf-8") as fh:
                    fh.write(before)
        except OSError as exc:
            return {"ok": False, "kind": "undo", "path": rel,
                    "error": {"code": "restore_failed", "message": str(exc)}}
        _MUT = True
        restored.append(rel)
    return {"ok": True, "kind": "undo", "restored": restored}


def _walk(root):
    for dirpath, dirnames, filenames in _os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            yield _os.path.join(dirpath, name)


def map(path=".", max_files=400):
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    if not _os.path.exists(target):
        return {"ok": True, "kind": "miss", "path": rel}
    base = target if _os.path.isdir(target) else (_os.path.dirname(target) or _ROOT)
    entries = []
    for full in _walk(base):
        try:
            body = _read_text(full)
        except OSError:
            continue
        entries.append({"path": _os.path.relpath(full, _ROOT),
                        "lines": len(body.splitlines()), "bytes": len(body)})
    return {"ok": True, "kind": "map", "root": _os.path.relpath(base, _ROOT),
            "files": entries[:int(max_files)], "total_files": len(entries)}


def read(path, offset=1, limit=0, raw=False):
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    if not _os.path.exists(target):
        near = []
        try:
            near = _difflib.get_close_matches(
                _os.path.basename(rel), _os.listdir(_os.path.dirname(target) or _ROOT), n=3, cutoff=0.3)
        except OSError:
            pass
        return {"ok": True, "kind": "miss", "path": rel, "did_you_mean": near}
    if _os.path.isdir(target):
        return {"ok": True, "kind": "dir", "path": rel, "entries": sorted(_os.listdir(target))}
    lines = _read_text(target).splitlines()
    total = len(lines)
    start = max(1, min(int(offset), total or 1))
    want = int(limit)
    if want <= 0:
        stop = total
        budget = 0
        for n in range(start - 1, total):
            budget += len(lines[n]) + 1
            if n - (start - 1) + 1 > _READ_MAX_LINES or budget > _READ_MAX_CHARS:
                stop = max(start, n)
                break
    else:
        stop = min(total, start + max(1, want) - 1)
    result = {"ok": True, "kind": "file", "path": rel, "total_lines": total,
              "shown": [start, stop],
              "next": {"offset": stop + 1} if stop < total else None,
              "truncated": stop < total}
    entry = _READS_CELL.setdefault(rel, {"ranges": [], "total": total})
    entry["ranges"].append([start, stop])
    entry["total"] = total
    chunk = "\n".join(lines[start - 1:stop])
    if raw:
        result["text"] = chunk
        result["raw"] = chunk
    else:
        result["text"] = "\n".join(f"{n:>6}| {lines[n - 1]}" for n in range(start, stop + 1))
    return result


def grep(pattern, path=".", max_results=50, regex=False):
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    if not _os.path.exists(target):
        return {"ok": True, "kind": "miss", "path": rel}
    matcher = None
    if regex:
        try:
            matcher = _re.compile(pattern)
        except _re.error as exc:
            return {"ok": True, "kind": "bad_regex", "pattern": pattern, "message": str(exc)}
    files = [target] if _os.path.isfile(target) else list(_walk(target))
    hits = []
    occurrences = 0
    for full in files:
        try:
            body = _read_text(full)
        except OSError:
            continue
        occurrences += len(matcher.findall(body)) if matcher else body.count(pattern)
        for number, line in enumerate(body.splitlines(), 1):
            if matcher.search(line) if matcher else pattern in line:
                hits.append({"path": _os.path.relpath(full, _ROOT), "line": number,
                             "text": line.strip()[:200]})
                if len(hits) >= int(max_results):
                    return {"ok": True, "kind": "matches", "matches": hits,
                            "occurrences": None, "truncated": True}
    return {"ok": True, "kind": "matches", "matches": hits,
            "occurrences": occurrences, "truncated": False}


def write(path, content, force=False):
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    existed = _os.path.isfile(target)
    if existed and not force:
        current = _read_text(target)
        if len(content) < len(current) and current.startswith(content):
            return {"ok": False, "kind": "refused_truncation", "path": rel,
                    "existing_lines": len(current.splitlines()), "new_lines": len(content.splitlines()),
                    "note": "the new content is a prefix of the current file, so this write would delete its tail. "
                            "If that is intended, pass force=True; otherwise edit() the region."}
    entry = _commit(target, rel, content, "write")
    return {"ok": True, "kind": "write", "path": rel, "created": entry["created"],
            "lines": entry["after_lines"], "line_delta": None if entry["created"] else entry["after_lines"] - entry["before_lines"],
            "adds": entry["adds"], "dels": entry["dels"]}


def edit(path, old, new, all=False, replace_all=None):
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    if not _os.path.isfile(target):
        return {"ok": True, "kind": "no_change", "path": rel, "reason": "missing"}
    if replace_all is not None:
        all = bool(replace_all)
    body = _read_text(target)
    count = body.count(old)
    if count == 0:
        probe = old.splitlines()[0].strip() if old.splitlines() else old
        near = [{"line": number, "text": line.strip()[:200]}
                for number, line in enumerate(body.splitlines(), 1)
                if _difflib.SequenceMatcher(None, probe, line.strip()).ratio() > 0.6][:3]
        return {"ok": True, "kind": "no_change", "path": rel, "reason": "not_found", "closest": near}
    if count > 1 and not all:
        return {"ok": True, "kind": "ambiguous", "path": rel, "count": count,
                "action": "extend old until unique, or pass all=True"}
    updated = body.replace(old, new) if all else body.replace(old, new, 1)
    entry = _commit(target, rel, updated, "edit")
    at = updated.find(new)
    first = updated[:at].count("\n") + 1 if at >= 0 else 1
    lines = updated.splitlines()
    lo = max(1, first - 3)
    hi = min(len(lines), first + new.count("\n") + 3)
    hunk = "\n".join(f"{n:>6}| {lines[n - 1]}" for n in range(lo, hi + 1))
    return {"ok": True, "kind": "edit", "path": rel, "replaced": count if all else 1,
            "lines": entry["after_lines"], "hunk": hunk, "hunk_lines": [lo, hi],
            "adds": entry["adds"], "dels": entry["dels"]}

def _parse_patch(patch):
    files = []
    current = None
    hunk = None
    for raw in str(patch).splitlines():
        if raw.startswith("--- "):
            current = {"src": raw[4:].split("\t")[0].strip(), "dst": None, "hunks": []}
            files.append(current)
            hunk = None
        elif raw.startswith("+++ ") and current is not None:
            current["dst"] = raw[4:].split("\t")[0].strip()
        elif raw.startswith("@@") and current is not None:
            match = _re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
            if not match:
                return None
            hunk = {"old_start": int(match.group(1)), "lines": []}
            current["hunks"].append(hunk)
        elif hunk is not None and raw[:1] in (" ", "+", "-"):
            hunk["lines"].append(raw)
    return [item for item in files if item["hunks"]]


def _patch_path(value):
    if not value or value == "/dev/null":
        return None
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def _hunk_old(hunk):
    return [line[1:] for line in hunk["lines"] if line[:1] in (" ", "-")]


def _hunk_new(hunk):
    return [line[1:] for line in hunk["lines"] if line[:1] in (" ", "+")]


def apply(patch):
    parsed = _parse_patch(patch)
    if not parsed:
        return {"ok": False, "kind": "patch_failed", "reason": "no hunks found in the patch text"}
    results = []
    for item in parsed:
        rel = _patch_path(item["dst"] or item["src"])
        if rel is None:
            return {"ok": False, "kind": "patch_failed", "reason": "missing +++ path"}
        target, rel = _resolve(rel)
        if target is None:
            return {"ok": False, "error": {"code": "outside_root", "path": rel}}
        exists = _os.path.isfile(target)
        body = _read_text(target) if exists else ""
        lines = body.splitlines()
        for hunk in item["hunks"]:
            old = _hunk_old(hunk)
            new = _hunk_new(hunk)
            base = max(0, int(hunk["old_start"]) - 1)
            position = None
            for offset in range(0, 200):
                for candidate in (base + offset, base - offset):
                    if candidate < 0 or candidate + len(old) > len(lines):
                        continue
                    if lines[candidate:candidate + len(old)] == old:
                        position = candidate
                        break
                if position is not None:
                    break
            if position is None:
                return {"ok": False, "kind": "patch_failed", "path": rel,
                        "reason": "context did not match", "hunk": old[:4]}
            lines[position:position + len(old)] = new
        trailing = "\n" if body.endswith("\n") or not exists else ""
        entry = _commit(target, rel, "\n".join(lines) + trailing, "apply")
        results.append({"path": rel, "adds": entry["adds"], "dels": entry["dels"]})
    return {"ok": True, "kind": "apply", "files": results}


def commit(files, force=False):
    if not isinstance(files, dict) or not files:
        return {"ok": False, "kind": "commit", "reason": "pass a dict of {path: text}"}
    done = []
    for path, content in files.items():
        if not isinstance(content, str):
            return {"ok": False, "kind": "commit", "path": str(path), "reason": "content must be text"}
        result = write(path, content, force=force)
        if not result.get("ok"):
            return {"ok": False, "kind": "commit", "path": str(path), "detail": result}
        done.append({"path": result["path"], "adds": result["adds"], "dels": result["dels"]})
    return {"ok": True, "kind": "commit", "files": done}


_snapshot()
"""

# --- child side 3/3: RUN_PRELUDE, run() for whole programs, behind the protocol check -----
RUN_PRELUDE = r"""
import os as _ros, re as _re, signal as _rsig, subprocess as _rsp
_RUN_ROOT = _ros.getcwd()
_RUN_LOG = []
_CHECK_CACHE = {}
_GATE_IN_RUN = 0
_SPAWN_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn"}


class _SpawnBlocked(Exception):
    def __init__(self, blocked, command):
        super().__init__("`%s` changes files through a process, so it would bypass the journal; use the helper" % blocked)
        self.blocked = blocked
        self.command = command
# Shell verbs that change the tree. They are refused because they bypass the journal, so
# diff()/undo() could not see them; typed helpers are the only journalled way to mutate.
_RUN_SHELL_MUTATORS = {
    "tee", "touch", "cp", "mv", "rm", "truncate", "shred", "dd", "install",
    "ln", "mkdir", "rmdir", "chmod", "chown", "chgrp", "patch",
}
# Git index/history verbs (add, commit, push, fetch, init, clone) are allowed: they do not
# overwrite working-tree files, and an agent asked to "commit and push" must be able to do it.
# Only verbs that can replace tracked content stay gated.
_RUN_GIT_MUTATORS = {
    "apply", "checkout", "reset", "clean", "stash", "rm", "mv", "pull",
    "merge", "rebase", "cherry-pick", "revert", "am", "switch", "restore",
}
_HINT = {
    "tee": "write(path, content)", "touch": "write(path, '')",
    "cp": "read(path) then write(new_path, text)",
    "mv": "write(new_path, read(path)['text']) then os.remove(path)",
    "rm": "os.remove(path)", "truncate": "write(path, '')",
    "sed -i": "edit(path, old, new)", "mkdir": "os.makedirs(path, exist_ok=True)",
    "patch": "apply(patch)",
    "git": "git add/commit/push are allowed; edit files with write/edit/apply/commit",
    ">": "write(path, content)", "find -delete": "map() then os.remove(path)",
    "xargs": "a Python loop",
}


def _segments(command):
    return [part for part in _re.split(r"\|\||&&|\||;|\n", str(command)) if part.strip()]


def _abs_outside(token):
    # A path the journal does not cover: absolute, outside the root, with no shell/escape
    # tricks. Mutating /tmp is fine; mutating the workspace is not.
    text = str(token).strip().strip("'\"")
    if not text.startswith("/") or "$" in text or ".." in text:
        return False
    path = _ros.path.normpath(text)
    return path != _RUN_ROOT and not path.startswith(_RUN_ROOT + _ros.sep)


def _mutator_targets_outside(parts):
    base = _ros.path.basename(parts[0])
    paths = [part for part in parts[1:] if not part.startswith("-")]
    if not paths:
        return False
    # cp/ln/install only read the source; the destination is the mutation.
    if base in ("cp", "ln", "install"):
        return _abs_outside(paths[-1])
    return all(_abs_outside(path) for path in paths)


def _writes_stdout(segment):
    for match in _re.finditer(r"(?<![0-9])>>?\s*([^\s|;&]+)", segment):
        target = match.group(1).strip("'\"")
        if target in ("/dev/null", "/dev/stdout", "/dev/stderr", "&1", "&2"):
            continue
        if _abs_outside(target):
            continue
        return True
    return False


def _mutating_command(command):
    # Return the mutating verb in a shell command, or None when it only reads / runs.
    text = str(command).strip()
    if not text:
        return None
    for segment in _segments(text):
        if _writes_stdout(segment):
            return ">"
        parts = segment.strip().split()
        if not parts:
            continue
        base = _ros.path.basename(parts[0])
        args = parts[1:]
        names = [_ros.path.basename(arg) for arg in args]
        if base in _RUN_SHELL_MUTATORS:
            if _mutator_targets_outside(parts):
                continue
            return base
        if base == "sed" and any(arg.startswith("-i") or arg.startswith("--in-place") for arg in args):
            return "sed -i"
        if base == "git" and names and names[0] in _RUN_GIT_MUTATORS:
            return "git " + names[0]
        if base in ("bash", "sh", "zsh") and len(parts) > 2 and parts[1] in ("-c", "-lc"):
            inner = _mutating_command(" ".join(parts[2:]))
            if inner:
                return inner
        if base == "find" and any(arg in ("-delete", "-exec", "-execdir", "-ok", "-okdir")
                                  for arg in args):
            return "find -delete"
        if base == "xargs":
            inner = _mutating_command(" ".join(args))
            if inner:
                return "xargs " + inner
    return None


def _norm_command(command):
    return " ".join(str(command).split())


def _spawn_command(event, args):
    if event == "os.system":
        return str(args[0]) if args else ""
    if event == "subprocess.Popen":
        argv = args[1] if len(args) > 1 else args[0]
    elif event == "os.exec":
        argv = args[1] if len(args) > 1 else args[0]
    elif event == "os.posix_spawn":
        argv = args[1] if len(args) > 1 else args[0]
    elif event == "os.spawn":
        argv = args[2] if len(args) > 2 else args[0]
    else:
        return ""
    if isinstance(argv, (list, tuple)):
        return " ".join(str(item) for item in argv)
    return str(argv)


def _gate_check(command):
    text = str(command).strip()
    if not text:
        return None
    inner = text
    parts = text.split()
    base = _ros.path.basename(parts[0]) if parts else ""
    if base in ("bash", "sh", "zsh") and len(parts) > 2 and parts[1] in ("-c", "-lc"):
        inner = " ".join(parts[2:]).strip().strip('"').strip("'")
    return _mutating_command(inner)


def _audit_spawn(event, args):
    if event not in _SPAWN_EVENTS:
        return
    command = _spawn_command(event, args)
    blocked = _gate_check(command)
    if blocked:
        raise _SpawnBlocked(blocked, command)
    if not _GATE_IN_RUN:
        _RUN_LOG.append(command)


import sys as _sys
_sys.addaudithook(_audit_spawn)


def run(command, timeout=60, cwd=None):
    global _GATE_IN_RUN
    _RUN_LOG.append(str(command))
    cached = _CHECK_CACHE.get(_norm_command(command))
    if cached:
        if isinstance(cached, dict):
            text = str(cached.get("text") or "")
            ok = bool(cached.get("ok", True))
        else:
            text, ok = str(cached), True
        return {"ok": True, "kind": "check_cached", "command": str(command),
                "result": text, "exit_code": 0 if ok else 1, "stdout": text, "stderr": "",
                "note": "this command is the project check for the current mutation batch; its recorded result is authoritative."}
    blocked = _mutating_command(command)
    if blocked:
        use = _HINT.get(blocked) or _HINT.get(blocked.split()[0], "write(), edit(), apply() or commit()")
        return {
            "ok": False,
            "kind": "wrong_call",
            "command": str(command),
            "blocked": blocked,
            "error": {
                "code": "mutation_via_run",
                "message": f"`{blocked}` changes files through the shell, so it would bypass the "
                           "journal and diff()/undo() could not see it. Use the helper.",
                "use": use,
            },
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }
    extend = globals().get("_CELL_EXTEND")
    if extend is not None:
        extend(float(timeout) + 20)
    try:
        _GATE_IN_RUN += 1
        try:
            proc = _rsp.Popen(
                str(command), shell=True, cwd=cwd or _RUN_ROOT,
                stdout=_rsp.PIPE, stderr=_rsp.PIPE, text=True,
                encoding="utf-8", errors="replace", executable="/bin/bash",
                start_new_session=True,
            )
        finally:
            _GATE_IN_RUN -= 1
    except _SpawnBlocked as exc:
        use = _HINT.get(str(exc.blocked)) or _HINT.get(str(exc.blocked).split()[0], "write(), edit(), apply() or commit()")
        return {"ok": False, "kind": "wrong_call", "command": str(command),
                "blocked": exc.blocked,
                "error": {"code": "mutation_via_run", "message": str(exc), "use": use},
                "exit_code": None, "stdout": "", "stderr": ""}
    except OSError as exc:
        return {"ok": False, "kind": "spawn_error", "command": str(command),
                "error": {"code": "spawn_failed", "message": str(exc)},
                "exit_code": None, "stdout": "", "stderr": ""}
    try:
        out, err = proc.communicate(timeout=float(timeout))
    except _rsp.TimeoutExpired:
        try:
            _ros.killpg(_ros.getpgid(proc.pid), _rsig.SIGKILL)
        except OSError:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        return {"ok": True, "kind": "timeout", "command": str(command), "timeout_s": float(timeout),
                "exit_code": None, "stdout": out or "", "stderr": err or "",
                "note": "the command hit its own timeout and its process group was killed; the kernel is intact"}
    return {"ok": True, "kind": "command", "command": str(command), "exit_code": proc.returncode,
            "stdout": out or "", "stderr": err or ""}
"""

# --- parent side: supervision, protocol and the Cell the loops consume --------------------
MARKER = "@@K@@"
MAX_CODE = 20_000
MAX_OUTPUT = 0
CELL_TIMEOUT = 15.0


@dataclass
class Cell:
    stdout: str
    stderr: str
    result: str | None
    final: str | None
    duration_ms: int
    restarted: bool = False
    answer: str | None = None
    runs: list[str] | None = None
    obs_index: int | None = None
    mutated: bool = False
    changed: list[str] = field(default_factory=list)
    delta: str = ""
    timeout: bool = False
    reads: dict = field(default_factory=dict)


def _cap(text: str, limit: int = MAX_OUTPUT) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


class Kernel:
    def __init__(self, root: Path, timeout: float = CELL_TIMEOUT, writable: bool = False,
                 prelude: str = "off", allow_run: bool = False, supervise: bool = True) -> None:
        self.root = Path(root).resolve()
        self.timeout = timeout
        self.writable = writable
        self.prelude = prelude
        self.allow_run = allow_run
        self.supervise = supervise
        self.proc: subprocess.Popen | None = None
        self._python = shutil.which("python3") or "/usr/bin/python3"

    def _argv(self) -> list[str]:
        if self.allow_run:
            return [self._python, "-u", "-c", DRIVER]
        return [
            "bwrap",
            "--die-with-parent",
            "--unshare-all",
            "--ro-bind", "/", "/",
            *(["--bind", str(self.root), str(self.root)] if self.writable else []),
            "--tmpfs", "/tmp",
            "--proc", "/proc",
            "--dev", "/dev",
            "--chdir", str(self.root),
            self._python, "-u", "-c", DRIVER,
        ]

    def start(self) -> None:
        self.proc = subprocess.Popen(
            self._argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=str(self.root),
            start_new_session=True,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/tmp"),
                "PYTHONIOENCODING": "utf-8",
                "KERNEL_SUPERVISE": "1" if self.supervise else "0",
                **({"KERNEL_ALLOW_RUN": "1", "KERNEL_RUN_PRELUDE": RUN_PRELUDE} if self.allow_run else {}),
                **({"KERNEL_PRELUDE": PRELUDE, "KERNEL_PRELUDE_MODE": self.prelude}
                   if self.prelude != "off" else {}),
            },
        )

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self._kill_group()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None

    def _kill_group(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except OSError:
                self.proc.kill()

    def _restart(self) -> None:
        self.stop()
        self.start()

    def run(self, code: str, check_cache: dict | None = None) -> Cell:
        if self.proc is None:
            self.start()
        assert self.proc is not None and self.proc.stdin and self.proc.stdout
        if len(code) > MAX_CODE:
            return Cell("", "kernel: code exceeds 20000 characters", None, None, 0)

        started = time.perf_counter()
        try:
            request = {"code": code, "timeout": float(self.timeout)}
            if check_cache:
                request["check_cache"] = check_cache
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._restart()
            return Cell("", "kernel died; restarted with an empty namespace", None, None, 0, True)

        sel = selectors.DefaultSelector()
        sel.register(self.proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + self.timeout + 5.0
        payload = None
        try:
            while payload is None:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    self._kill_group()
                    self._restart()
                    return Cell(
                        "",
                        f"kernel did not answer within {self.timeout:.0f}s; killed and restarted with an empty namespace",
                        None, None,
                        int((time.perf_counter() - started) * 1000),
                        True, timeout=True,
                    )
                if not sel.select(remain):
                    continue
                line = self.proc.stdout.readline()
                if not line:
                    self._restart()
                    return Cell("", "kernel died; restarted with an empty namespace", None, None, 0, True)
                if not line.startswith(MARKER):
                    self._restart()
                    return Cell("", "kernel protocol desync; restarted with an empty namespace", None, None, 0, True)
                message = json.loads(line[len(MARKER):])
                if "extend" in message:
                    deadline = time.monotonic() + float(message["extend"]) + 30.0
                    continue
                payload = message
        finally:
            sel.close()

        return Cell(
            _cap(payload.get("stdout") or ""),
            _cap(payload.get("stderr") or ""),
            payload.get("result"),
            payload.get("final"),
            int((time.perf_counter() - started) * 1000),
            False,
            payload.get("answer"),
            payload.get("runs") or [],
            payload.get("obs_index"),
            bool(payload.get("mutated")),
            payload.get("changed") or [],
            payload.get("delta") or "",
            bool(payload.get("timeout")),
            payload.get("reads") or {},
        )