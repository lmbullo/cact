"""Kernel: the agent's entire action space, plus the subprocess that enforces it.

WHAT IT IS
  Two layers in one file.
  1. Source strings executed *inside* the child interpreter:
       DRIVER       loop: one JSON line per request on stdin -> either exec() a Python cell
                    or dispatch one typed verb call in the namespace that survives between
                    requests -> one `@@K@@` JSON line back on stdout.
       PRELUDE      injects the workspace verbs from verbs.VERBS that need no process
                    (map/read/search/outline/edit/splice/write/apply) plus the python
                    helpers guide()/grep()/recall()/verbs()/diff()/changes()/undo()/
                    commit()/help()/final_answer()/emit(), and journals every mutation
                    (snapshot at start, tree diff after each cell). `guide()` is headers.py,
                    shipped as source and exec'd into a private `_HEADERS` namespace (see
                    HEADERS_SOURCE).
       RUN_PRELUDE  injects exec_argv() (the one typed process tool: argv/commands/shell)
                    and git_tool(); run() is the python-only escape hatch and is removed
                    from the namespace unless allow_shell is on. Both process-only names
                    are absent when allow_run is off.
  2. `Kernel` (this process): spawns the child, supervises the per-cell deadline, restarts it
     when it dies, hangs or desynchronises, and returns a `Cell`.

ENTRY POINTS
  PROTOCOL = the version both halves speak; bump it when either shape below changes
  verbs.VERBS = the typed registry: name -> schema/summary/function. Shipped to the child as
                KERNEL_VERBS and used by tools.py to build the provider tool specs.
  CellRequest(timeout, code=None, kind="code"|"call", tool=None, args={},
              protocol=PROTOCOL, check_cache={})
    .to_wire() -> the one JSON line written to the child's stdin
  CellReply: the typed body of one `@@K@@` line; `.from_wire(dict)` parses it,
             `.protocol_ok` compares its version with PROTOCOL
  ExtendNotice: the interim `{"extend": seconds}` line a long cell sends
  Kernel(root, timeout=15.0, writable=False, prelude="off"|"all"|"mut",
         allow_run=False, allow_shell=None, supervise=True)
    .start()   spawn the child (lazy: .run()/.call() calls it)
    .run(code, check_cache=None)   one Python cell -> Cell
    .call(tool, args, check_cache=None)  one typed verb -> the same Cell pipeline
    .stop()    SIGKILL the process group, reap exec/run groups, delete the observation log
  Cell: stdout, stderr, result, final, duration_ms, restarted, answer, runs, obs_index,
        mutated, changed, deltas, delta, timeout, reads, tool, failed, rejected
    .failure(message, ...) the one restart/error constructor
    .from_reply(reply, duration_ms=...) the one success constructor
  Inside the kernel namespace: PRELUDE's typed verbs (map/read/search/outline/edit/
        splice/write/apply) and helpers guide()/grep()/recall()/verbs()/diff()/changes()/
        undo()/commit()/help(); RUN_PRELUDE adds exec_argv() (the typed `exec` tool) and
        git_tool(), and run() only when allow_shell. final_answer()/emit() are always there.
  Helper used by the loops: `run_check` lives in check.py; loops read `Cell.changed` /
  `Cell.deltas` / `Cell.delta` (state cards) and `Cell.final` / `Cell.answer` (finish
  protocol).

INVARIANTS
  - The child's stdout is a protocol channel: every message is exactly one `@@K@@`-prefixed
    JSON line. The driver redirects the cell's own stdout/stderr into buffers, so a stray
    print cannot corrupt the channel.
  - A cell that overruns `timeout`, dies, or emits a non-`@@K@@` line is killed and
    restarted with an EMPTY namespace, and the model is told so. Never pretend state
    survived. `supervise=False` disables the in-child alarm (legacy behaviour).
  - The session's observation log (`_OBS`) is the exception: it is appended to
    `KERNEL_OBS_LOG` after each cell and reloaded by the new child, so `recall()` survives
    a crash/restart even though variables do not. `stop()` deletes the log.
  - exec_argv() and run() start each program in a NEW session, so killing the kernel group
    does not reach it. The child journals its group leader (pid + /proc start id) to
    `KERNEL_ORPHAN_JOURNAL`; `stop()`/`_restart()` kill those groups before starting a new
    child. Without this, a timed-out or crashed cell leaks whatever exec/run started.
  - `final_answer(v)` raises `Final`; the driver reports the value as `Cell.final`
    unconditionally, and also reports `Cell.answer` (the `ANSWER` variable, used when a repl
    loop stops on "var"). It is the LOOP, not the kernel, that decides what an answer from a
    printing cell means: ToolLoop stages it (STAGED) and accepts it on the next no-tool reply;
    ReplLoop rejects it (UNREVIEWED) — see tool_loop.py / repl.py.
  - Every mutation goes through `_commit`: journal row, snapshot diff, `_CELL_CHANGED`.
    That single path is what changes()/diff()/undo() and the `[state] changed:` card read.
    `write()` replaces (refuses to drop a tail without force=True); `edit()` reports
    ambiguity instead of guessing; `splice(path, start, end, text)` replaces 1-based
    inclusive lines through the same journal (read() numbers lines, so a surgical change
    costs only the changed lines); `apply(unified diff)` writes several files in one cell,
    so one diff()/undo() covers the whole change set; `commit({path: text})` is a python
    helper.
  - exec_argv() runs a program and its argv directly, with no shell. It is the typed process
    tool: no quoting, no `cd` prefix (use cwd), no pipes or redirection. The same mutation
    gate applies to argv (rm, cp, mv, tee, sed -i, git apply/checkout/...), with a
    `kind: "wrong_call"` and a `use` hint. A shell interpreter with `-c`/`-lc` in argv is
    refused as `shell_via_exec`; `exec(shell=<string>)` is the explicit shell path. run() is
    the python-only shell helper and exists only when allow_shell is on. Both are a
    correctness boundary, not a capability boundary: a program can still write files, and
    the docs say so.
  - `guide()` is the only new name the header index adds, and it is read-only: headers.py is
    passed to the child in KERNEL_HEADERS, exec'd into `_HEADERS`, and called with `_ROOT`.
    Never inline that source again: a shared namespace let its private `_walk` overwrite the
    prelude's, which silently indexed every file instead of the source tree.
  - A typed verb that returns `{"ok": false}` is a structured rejection
    (`Cell.rejected`), distinct from an exception/timeout (`Cell.failed`). The loop counts
    both as errors so a bad argument shape is visible in the run metrics, not hidden behind
    an `ok: true` shell result.
  - Direct process spawning is gated by the same classifier. `sys.addaudithook` in
    RUN_PRELUDE sees `subprocess.Popen`, `os.system`, `os.exec*`, `os.posix_spawn` and
    `os.spawn`; a spawn containing a mutating verb raises `_SpawnBlocked`, which the driver
    turns into a `wrong_call` result with the helper hint. Allowed spawns are appended to
    `Cell.runs`. Inside run()/exec_argv() the gate classifies but does not double-log.
  - `read()` is a ledger, not a firehose: with no range it returns the whole file only while
    it fits 400 lines / 20 KB; beyond that the result carries `next.offset` and `has_more`.
    A list argument reads several files in one cell (same start/end, `text` concatenated with
    `# path` headers),
    so batching is the tool's shape rather than a rule. Every delivered range is returned as
    `Cell.reads = {path: {ranges, total}}`, so the loop can see holes. A contained range
    already delivered in this session, with the same file digest, returns `already_read`
    plus the `_OBS[n]` pointer; `read(..., repeat=True)` re-delivers the text. `_commit`
    drops the file's session entry.
  - `check_cache` is sent with each cell (`{command: {text, ok}}`); `run()`/`exec_argv()`
    answer a matching command with `kind: "check_cached"` instead of spawning it twice.
  - run/exec enabled => the child is a plain `python3 -u -c DRIVER`. Disabled => the child
    runs under bwrap with a read-only `/` (the root is bind-mounted writable only when
    `writable=True`).
  - The protocol is versioned. Every request carries `protocol: PROTOCOL` and every child
    message carries the same field. On the FIRST message of a cell the parent compares it;
    a mismatch restarts the child with an empty namespace (history is kept) and returns a
    `Cell` error, so a stale child can never be read through the current shape. Bump
    PROTOCOL whenever a field changes meaning or a field is removed.
  - A typed call and a Python cell are the same cell: the request kind only chooses how the
    child computes the value. Both reset `_READS_CELL`/`_CELL_CHANGED`, journal mutations,
    record `_OBS`, and return the same `CellReply` (with `tool` naming the typed verb). The
    driver writes the helper's stdout/stderr/text/hunk into the cell buffers, so the state
    card digests typed results exactly like printed output.
  - Before dispatch the driver binds the args to the verb function's live signature
    (`inspect.signature().bind(**args)`). An unknown keyword or a missing argument is a
    `kind: "bad_args"` result with `error.code: "bad_arguments"` and a `use: help(tool)`
    hint, so a schema mistake is answered rather than raised. The function still validates
    its own values and reports the other `bad_args` codes (`bad_argv`, `bad_commands`,
    `bad_env`) the same way.
  - `exec` never parses a shell string: a shell interpreter with `-c`/`-lc` in argv is
    refused as `shell_via_exec` and points at `exec(shell=<string>)`, the one explicit
    shell path. `run()` is removed from a typed-mode namespace entirely; it exists only
    when `allow_shell` is on (repl/bare).
  - `changed` is the list of paths; `deltas` is the per-path unified diff the same journal
    produced. `delta` is only the joined text the card renders. The loop reads `deltas`,
    never re-parses `delta`.

WHERE TO CHANGE
  Add a file/exec helper for the model .... PRELUDE or RUN_PRELUDE, plus the matching rule
                                            text in tool_loop.TOOL_RULES / repl.REPL_RULES /
                                            state_loop.STATE_RULES, or the model never sees it
  Change what a helper returns ............ the helper's dict shape in PRELUDE; the loops
                                            only pass the text through
  Change the cell result shape ............ `CellRequest` / `CellReply` / `Cell` + the driver's
                                            final `emit({...})` payload; bump `PROTOCOL`
  Change the protocol version ............. `PROTOCOL` (child placeholder replaced at import)
  Change timeout/crash handling ........... `Kernel.run`, `_restart`, `_kill_group`, `_arm`,
                                            `_reap_orphans` + `_journal_orphan`, `obs_log`
  Change what exec/run refuse ............ `_RUN_SHELL_MUTATORS` / `_RUN_GIT_MUTATORS` /
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
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .verbs import VERBS

# --- protocol: one typed request/response pair over the `@@K@@` line ----------------------
# Bump PROTOCOL when either half's shape changes. The child embeds the same number through
# the __PROTOCOL__ placeholder; the parent compares it on the first message of every cell.
PROTOCOL = 4

# --- child side 1/3: DRIVER, the cell loop (stdin JSON in, `@@K@@` line out) -------------
DRIVER = r'''
import ast, contextlib, inspect as _inspect, io, json, os, signal, sys, time, traceback

_PROTOCOL = __PROTOCOL__

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
        sys.__stdout__.write("@@K@@" + json.dumps({"protocol": _PROTOCOL, "extend": float(seconds)}) + "\n")
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
try:
    ns["_VERB_META"] = json.loads(os.environ.get("KERNEL_VERBS") or "{}")
except Exception:
    ns["_VERB_META"] = {}


class _Result(dict):
    # Tool results are dicts, but a model that writes `r.stdout` or `read(f)[:n]` is making
    # the natural mistake. Support both shapes without changing the dict contract: a slice
    # pages text, `content` aliases `text`, and attribute access maps to a key.
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, key):
        if isinstance(key, slice):
            for field in ("raw", "text", "stdout"):
                if isinstance(self.get(field), str):
                    return self[field][key]
        if key == "content" and "text" in self:
            key = "text"
        return super().__getitem__(key)


ns["_Result"] = _Result
_prelude = os.environ.get("KERNEL_PRELUDE")
if _prelude:
    exec(_prelude, ns)
    if os.environ.get("KERNEL_PRELUDE_MODE") == "mut":
        for _n in ("map", "read", "grep"):
            ns.pop(_n, None)
_run_prelude = os.environ.get("KERNEL_RUN_PRELUDE")
if _run_prelude:
    exec(_run_prelude, ns)
    if os.environ.get("KERNEL_SHELL") != "1":
        ns.pop("run", None)

class Final(Exception):
    def __init__(self, value):
        self.value = value

def final_answer(value):
    raise Final(value)

ns["final_answer"] = final_answer

# _OBS is the session's only L2/L3 history. Persist it so a restart (crash, timeout,
# protocol desync) loses variables but not the earlier cell output recall() searches.
_OBS_PATH = os.environ.get("KERNEL_OBS_LOG")
if _OBS_PATH:
    try:
        with open(_OBS_PATH, encoding="utf-8") as _fh:
            ns["_OBS"] = [json.loads(_line) for _line in _fh if _line.strip()]
    except (OSError, ValueError):
        ns["_OBS"] = []

def emit(payload):
    sys.__stdout__.write("@@K@@" + json.dumps(payload) + "\n")
    sys.__stdout__.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        request = json.loads(line)
        kind = request.get("kind") or "code"
        code = request.get("code")
        budget = float(request.get("timeout") or 15.0)
        ns["_CHECK_CACHE"] = request.get("check_cache") or {}
        ns["_READS_CELL"] = {}
        if kind == "call" and not isinstance(request.get("args") or {}, dict):
            raise ValueError("call args must be an object")
        if kind == "code" and not isinstance(code, str):
            raise ValueError("code must be a string")
    except Exception:
        emit({"protocol": _PROTOCOL, "stdout": "", "stderr": "kernel: bad request", "result": None, "final": None, "timeout": False})
        continue
    out, err = io.StringIO(), io.StringIO()
    result_repr = None
    final = None
    timed_out = False
    failed = False
    rejected = False
    if "_CELL_CHANGED" in ns:
        ns["_CELL_CHANGED"] = []
    saved_stdin = sys.stdin
    _arm(budget)
    try:
        sys.stdin = io.StringIO("")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if kind == "call":
                tool = str(request.get("tool") or "")
                meta = ns.get("_VERB_META") or {}
                fn_name = (meta.get(tool) or {}).get("function") or tool
                fn = ns.get(fn_name)
                if not callable(fn):
                    raise NameError("unknown kernel verb %r; call help() for the list" % tool)
                call_args = request.get("args") or {}
                try:
                    _inspect.signature(fn).bind(**call_args)
                except TypeError as exc:
                    value = {"ok": False, "kind": "bad_args",
                             "error": {"code": "bad_arguments",
                                       "message": str(exc),
                                       "use": "help(%r) prints the live signature" % tool}}
                else:
                    value = fn(**call_args)
                if isinstance(value, dict):
                    if value.get("ok") is False:
                        rejected = True
                    if value.get("stdout"):
                        out.write(str(value.get("stdout")))
                    if value.get("stderr"):
                        err.write(str(value.get("stderr")))
                    if value.get("kind") in ("file", "files", "matches", "verbs", "diff", "outline", "outlines", "edits", "map", "maps", "recall") or str(value.get("kind") or "").startswith("git_"):
                        out.write(str(value.get("text") or ""))
                    if value.get("hunk"):
                        out.write(str(value.get("hunk")))
                    if value.get("kind") == "commands":
                        summary = {k: v for k, v in value.items()
                                   if k in ("ok", "kind", "count", "exit_code", "results")}
                    else:
                        summary = {k: v for k, v in value.items()
                                   if k not in ("stdout", "stderr", "text", "raw", "files", "hunk")}
                        if value.get("kind") == "matches":
                            # The stdout text is the delivery; the result only carries the
                            # locators, so a hit's text is not sent twice in one card.
                            if value.get("locator_mode"):
                                summary.pop("matches", None)
                            else:
                                summary["matches"] = [
                                    {k: v for k, v in hit.items() if k in ("path", "line")}
                                    for hit in value.get("matches") or []]
                        if value.get("kind") in ("outline", "outlines", "maps"):
                            summary.pop("entries", None)
                            summary.pop("files", None)
                        if value.get("kind") == "recall":
                            summary.pop("items", None)
                    result_repr = json.dumps(summary, default=repr)
                elif isinstance(value, str):
                    out.write(value)
                    result_repr = json.dumps({"ok": True, "kind": tool, "chars": len(value)})
                else:
                    if tool == "changes" and isinstance(value, list):
                        out.write("\n".join(str(item) for item in value))
                    result_repr = json.dumps(value, default=repr)
            else:
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
        failed = True
        err.write(
            "timeout: the cell exceeded %.0fs and was interrupted. "
            "The namespace, imports and variables are intact.\n" % budget
        )
    except BaseException as exc:
        failed = True
        _blocked_cls = ns.get("_SpawnBlocked")
        if _blocked_cls is not None and isinstance(exc, _blocked_cls):
            _hint = (ns.get("_HINT") or {}).get(str(getattr(exc, "blocked", "")).split()[0], "read(), search(), map(), write() or edit()")
            result_repr = json.dumps({
                "ok": False, "kind": "wrong_call",
                "blocked": getattr(exc, "blocked", ""),
                "command": getattr(exc, "command", ""),
                "error": {"code": "mutation_via_run", "message": str(exc), "use": _hint},
            })
            err.write("the mutation gate blocked this process: " + str(exc) + "\n")
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
    _deltas = {}
    if callable(_delta_fn):
        try:
            _deltas = {str(_rel): str(_text) for _rel, _text in dict(_delta_fn()).items()}
        except Exception:
            _deltas = {}
    # `deltas` is the structured contract; `delta` is only the joined text the card renders.
    _delta = "\n".join(_deltas[_rel] for _rel in sorted(_deltas))[:12000]
    # `changed` is THIS cell, not the session: typed commits are in _CELL_CHANGED and a
    # program that wrote through open()/subprocess shows up in the tree delta. A file that
    # was created and removed in the same cell is not a change, so filter the typed list by
    # the current tree as well.
    _delta_paths = set(_deltas)
    _now_state = ns.get("_LAST_STATE") or {}
    _base_state = ns.get("_BASE") or {}
    _changed: list[str] = []
    for _rel in list(ns.get("_CELL_CHANGED") or []) + sorted(_delta_paths):
        if _rel in _changed:
            continue
        if _rel in _delta_paths or _base_state.get(_rel, {}).get("text") != _now_state.get(_rel):
            _changed.append(_rel)
    _mutated = _mutated or bool(_changed)
    _obs_log = ns.setdefault("_OBS", [])
    _obs_log.append({"stdout": out.getvalue(), "stderr": err.getvalue(), "result": result_repr})
    if _OBS_PATH:
        try:
            with open(_OBS_PATH, "a", encoding="utf-8") as _fh:
                _fh.write(json.dumps(_obs_log[-1]) + "\n")
        except OSError:
            pass
    emit({"protocol": _PROTOCOL,
          "stdout": out.getvalue(), "stderr": err.getvalue(), "result": result_repr, "final": final,
          "tool": request.get("tool"),
          "runs": emitted_runs, "reads": dict(ns.get("_READS_CELL") or {}),
          "obs_index": len(_obs_log) - 1, "mutated": _mutated,
          "changed": _changed, "deltas": _deltas, "delta": _delta, "timeout": timed_out,
          "failed": failed, "rejected": rejected,
          "answer": None if answer is None else (answer if isinstance(answer, str) else repr(answer))})
'''.replace("__PROTOCOL__", str(PROTOCOL))


# --- child side 2/3: PRELUDE, the file helpers and the mutation journal the model calls ----
# The header index is shipped to the child as source, not imported: a cell runs in a task
# workspace with a bare `python3 -c`, where `bench` is not importable. The source is exec'd
# into its own namespace `_HEADERS`, so its helpers can never collide with the prelude's (that
# namespace is why a private `_walk` here and one there stay two different functions), and the
# model sees exactly one new verb: guide().
HEADERS_SOURCE = Path(__file__).with_name("headers.py").read_text(encoding="utf-8")

PRELUDE = r"""
import ast as _ast, difflib as _difflib, fnmatch as _fnmatch, hashlib as _hashlib, os as _os, re as _re

_HEADERS = {}
exec(_os.environ.get("KERNEL_HEADERS") or "", _HEADERS)


def guide(query=None, path=None, limit=6):
    fn = _HEADERS.get("guide")
    if fn is None:
        return {"ok": False, "error": {"code": "no_index",
                "message": "the header index is not loaded; use map() and grep() instead"}}
    return fn(_ROOT, query=query, path=path, limit=limit)


_ROOT = _os.getcwd()
_READS_CELL = {}
# Session-level delivery ledger. A repeated read of a range that was already shown, while
# the file is unchanged, returns a pointer instead of the same text again. `repeat=True`
# opts back into a full delivery, so this is a context optimisation, not a cap.
_READS_SESSION = {}
_READ_MAX_CHARS = 20000
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
_IGNORE = None


def _ignore_patterns():
    # The root .gitignore patterns; map/search/_snapshot skip generated trees. This is
    # intentionally small: directory prefixes, basename globs and path globs. A `!` negation
    # line is ignored rather than pretending to implement full gitignore.
    global _IGNORE
    if _IGNORE is not None:
        return _IGNORE
    _IGNORE = []
    try:
        with _open(_os.path.join(_ROOT, ".gitignore"), encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue
                _IGNORE.append(line.lstrip("/").rstrip("/"))
    except OSError:
        pass
    return _IGNORE


def _ignored(rel, is_dir=False):
    rel = str(rel).replace("\\", "/").lstrip("./")
    if not rel or rel == ".":
        return False
    base = rel.rsplit("/", 1)[-1]
    for pattern in _ignore_patterns():
        if "/" not in pattern:
            if _fnmatch.fnmatch(base, pattern):
                return True
        elif _fnmatch.fnmatch(rel, pattern) or rel == pattern or rel.startswith(pattern + "/"):
            return True
    return False


def _glob_match(path, pattern):
    # Include globs use shell path semantics: `*`/`?` do not cross `/`, `**` does. This
    # keeps `bench/*.md` to direct children while `**/*.md` walks the tree.
    path = str(path).replace("\\", "/").lstrip("./")
    pattern = str(pattern).replace("\\", "/").lstrip("./")
    if pattern.startswith("**/"):
        if _glob_match(path, pattern[3:]):
            return True
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(_re.escape(ch))
            i += 1
    return _re.fullmatch("".join(out), path) is not None
_HINT = {
    "cat": "read(path)", "head": "read(path, offset=1, limit=N)",
    "tail": "read(path, offset=total-N)", "less": "read(path)",
    "more": "read(path)", "wc": "read(path)['total_lines']",
    "stat": "map(path)", "grep": "grep(pattern, path)",
    "rg": "grep(pattern, path, regex=True)", "ag": "grep(pattern, path)",
    "sed": "splice(path, start, end, text) or edit(path, old, new)",
    "awk": "read(path) with Python string slicing",
    "find": "map(path)", "ls": "map(path)", "tree": "map(path)",
    "du": "map(path)", "tee": "splice(path, start, end, text) or write(path, content)",
    "touch": "write(path, '')", "cp": "read + write",
    "mv": "write + os.remove", "rm": "os.remove",
    "truncate": "splice(path, 1, read(path)['total_lines'], '') or write(path, '')",
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
    global _MUT, _JSEQ, _IGNORE
    before = None
    try:
        if _os.path.isfile(target):
            before = _read_text(target)
    except OSError:
        before = None
    _os.makedirs(_os.path.dirname(target) or _ROOT, exist_ok=True)
    with _open(target, "w", encoding="utf-8") as fh:
        fh.write(body)
    _READS_SESSION.pop(rel, None)
    if rel == ".gitignore":
        _IGNORE = None
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
    deltas = {}
    for rel in sorted(set(prev) | set(now)):
        before, after = prev.get(rel), now.get(rel)
        if before != after:
            deltas[rel] = _row(rel, before, after)[3]
    _LAST_STATE = now
    return deltas


def session_diff(path=None, max_chars=8000):
    # Explicit name for the session journal; `git diff` is the working-tree tool.
    return diff(path=path, max_chars=max_chars)


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
        rel_dir = _os.path.relpath(dirpath, _ROOT)
        keep = []
        for name in dirnames:
            if name in _SKIP_DIRS or name.startswith("."):
                continue
            rel = name if rel_dir == "." else rel_dir + "/" + name
            if _ignored(rel, True):
                continue
            keep.append(name)
        dirnames[:] = sorted(keep)
        for name in sorted(filenames):
            full = _os.path.join(dirpath, name)
            rel = _os.path.relpath(full, _ROOT)
            if _ignored(rel, False):
                continue
            yield full


def map(path=".", max_files=120, include=None, offset=0):
    path = _coerce_paths(path)
    if isinstance(path, (list, tuple, set)):
        results = [map(item, max_files=max_files, include=include, offset=offset)
                   for item in (sorted(path) if isinstance(path, set) else path)]
        chunks = []
        for item in results:
            chunks.append("# " + str(item.get("root") or "") + "\n" + str(item.get("text") or ""))
        return {"ok": all(bool(item.get("ok")) for item in results), "kind": "maps",
                "files": results, "count": len(results), "text": "\n\n".join(chunks)}
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    if not _os.path.exists(target):
        return {"ok": True, "kind": "miss", "path": rel}
    includes = include if isinstance(include, (list, tuple, set)) else ([include] if include else [])

    def wanted(name):
        return not includes or any(_glob_match(name, pat) or
                                   _glob_match(_os.path.basename(name), pat)
                                   for pat in includes)

    if _os.path.isfile(target):
        try:
            body = _read_text(target)
        except OSError as exc:
            return {"ok": False, "kind": "read_error", "path": rel,
                    "error": {"code": "read_failed", "message": str(exc)}}
        entry = {"path": rel, "lines": len(body.splitlines()), "bytes": len(body)}
        rows = [entry] if wanted(rel) else []
        text = "\n".join("%6d  %s" % (item["lines"], item["path"]) for item in rows)
        return {"ok": True, "kind": "map", "root": rel, "files": rows,
                "total_files": len(rows), "shown": [1, len(rows)] if rows else [],
                "next": None, "has_more": False, "text": text}

    base = target
    entries = []
    for full in _walk(base):
        rel = _os.path.relpath(full, _ROOT)
        if not wanted(rel):
            continue
        try:
            body = _read_text(full)
        except OSError:
            continue
        entries.append({"path": rel, "lines": len(body.splitlines()), "bytes": len(body)})
    total = len(entries)
    start = max(0, int(offset or 0))
    page = entries[start:start + max(1, int(max_files or 1))]
    end = start + len(page)
    text = "\n".join("%6d  %s" % (entry["lines"], entry["path"]) for entry in page)
    return {"ok": True, "kind": "map", "root": _os.path.relpath(base, _ROOT),
            "files": page, "total_files": total,
            "shown": [start + 1, end] if page else [],
            "next": {"offset": end} if end < total else None,
            "has_more": end < total, "text": text}


def _outline_doc(node):
    try:
        text = _ast.get_docstring(node) or ""
    except Exception:
        return ""
    return text.strip().splitlines()[0].strip()[:120] if text.strip() else ""


def _outline_html(lines):
    # HTML landmarks inside a plain page or a JS/TS template literal. A reader can navigate
    # a component by sections, ids and headings before reading it; the JS AST cannot see a
    # template string's markup.
    entries = []
    for number, line in enumerate(lines, 1):
        for match in _re.finditer(
                r"<\s*(section|header|footer|main|nav|article|aside|h[1-6])\b([^>]*)>",
                line, _re.IGNORECASE):
            tag = match.group(1).lower()
            attrs = match.group(2) or ""
            ident = _re.search(r"\bid\s*=\s*[\"']([^\"']+)", attrs, _re.IGNORECASE)
            classes = _re.search(r"\bclass(?:Name)?\s*=\s*[\"']([^\"']+)", attrs, _re.IGNORECASE)
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                tail = line[match.end():]
                title = _re.sub(r"<[^>]*>", "", tail).split("</", 1)[0].strip()
                title = _re.sub(r"\s+", " ", title)[:120]
                name = ("h%s: %s" % (tag[1], title)) if title else tag
                kind = "heading"
            else:
                name = tag
                if ident:
                    name += "#" + ident.group(1)
                if classes:
                    name += "." + classes.group(1).strip().replace(" ", ".")[:80]
                kind = tag
            entries.append({"line": number, "kind": kind, "name": name[:160]})
    return entries


def outline(path, max_entries=200):
    # The structural view: headings and definitions with line numbers, so a long file can
    # be navigated before (or instead of) reading it whole. This is not a shell equivalent;
    # it is the map read() and search() do not provide.
    path = _coerce_paths(path)
    if isinstance(path, (list, tuple, set)):
        results = [outline(item, max_entries=max_entries) for item in path]
        chunks = []
        for item in results:
            rel = str(item.get("path") or "")
            chunks.append("# " + rel + "\n" + str(item.get("text") or ""))
        return {"ok": all(bool(item.get("ok")) for item in results), "kind": "outlines",
                "files": results, "count": len(results), "text": "\n\n".join(chunks)}
    target, rel = _resolve(path)
    if target is None:
        return _Result({"ok": False, "kind": "wrong_call", "path": rel,
                        "error": {"code": "outside_root", "path": rel}})
    if not _os.path.exists(target):
        return _Result({"ok": True, "kind": "miss", "path": rel})
    if _os.path.isdir(target):
        return _Result({"ok": False, "kind": "wrong_call", "path": rel,
                        "error": {"code": "outline_is_file",
                                  "message": "outline() takes a file; map() lists a directory",
                                  "use": "map(path) for files, outline(file) for structure"}})
    try:
        body = _read_text(target)
    except OSError as exc:
        return _Result({"ok": False, "kind": "read_error", "path": rel,
                        "error": {"code": "read_failed", "message": str(exc)}})
    lines = body.splitlines()
    ext = _os.path.splitext(target)[1].lower()
    entries = []

    if ext == ".py":
        try:
            tree = _ast.parse(body)
        except SyntaxError as exc:
            return _Result({"ok": True, "kind": "outline", "path": rel, "entries": [],
                            "total_lines": len(lines),
                            "text": "# %s: cannot parse Python (%s); use read() or search()" % (rel, exc.msg)})
        for node in tree.body:
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                kind = "class" if isinstance(node, _ast.ClassDef) else "function"
                entries.append({"line": node.lineno, "kind": kind, "name": node.name,
                                "doc": _outline_doc(node)})
                if isinstance(node, _ast.ClassDef):
                    for sub in node.body:
                        if isinstance(sub, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                            entries.append({"line": sub.lineno, "kind": "method",
                                            "name": node.name + "." + sub.name,
                                            "doc": _outline_doc(sub)})
            elif isinstance(node, _ast.Assign):
                names = [target.id for target in node.targets
                         if isinstance(target, _ast.Name)]
                if names:
                    entries.append({"line": node.lineno, "kind": "assign",
                                    "name": ", ".join(names)})
            elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                entries.append({"line": node.lineno, "kind": "assign",
                                "name": node.target.id})
    elif ext in (".md", ".markdown"):
        for number, line in enumerate(lines, 1):
            match = _re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
            if match:
                entries.append({"line": number, "kind": "h" + str(len(match.group(1))),
                                "name": match.group(2)[:160]})
    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        for number, line in enumerate(lines, 1):
            match = _re.match(
                r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                r"(function|class|interface|type|enum|const|let|var)\s+([A-Za-z_$][\w$]*)",
                line)
            if match:
                entries.append({"line": number, "kind": match.group(1),
                                "name": match.group(2)})
                continue
            match = _re.match(r"^\s*(?:test|it|describe)\(\s*['\"](.+?)['\"]", line)
            if match:
                entries.append({"line": number, "kind": "test", "name": match.group(1)[:120]})
        entries.extend(_outline_html(lines))
    elif ext in (".html", ".htm", ".vue", ".svelte"):
        entries.extend(_outline_html(lines))
    elif ext in (".css", ".scss", ".less"):
        # Section comments and at-rules are the useful map of a stylesheet; a full selector
        # list is too long to be an outline. Fall back to top-level selectors when there are
        # no comments.
        selectors = []
        for number, line in enumerate(lines, 1):
            text = line.strip()
            if text.startswith("/*"):
                body = text[2:].strip()
                body = body[:-2].strip() if body.endswith("*/") else body
                if body and not body.startswith("=") and not body.startswith("-"):
                    entries.append({"line": number, "kind": "section", "name": body[:140]})
            elif _re.match(r"@(media|supports|layer|container|keyframes)\b", text):
                entries.append({"line": number, "kind": "at-rule", "name": text.split("{", 1)[0].strip()[:140]})
            elif (line and not line[0].isspace() and text.endswith("{")
                  and not text.startswith(("/*", "*", "@", "}", "{"))):
                selectors.append({"line": number, "kind": "rule", "name": text[:-1].strip()[:140]})
        if not entries:
            entries.extend(selectors)
    else:
        for number, line in enumerate(lines, 1):
            match = _re.match(r"^\s*(?:def|class|function|export)\s+([A-Za-z_$][\w$.]*)", line)
            if match:
                entries.append({"line": number, "kind": "def", "name": match.group(1)})
            elif _re.match(r"^#{1,6}\s+", line):
                entries.append({"line": number, "kind": "heading", "name": line.strip()[:160]})

    entries.sort(key=lambda item: item["line"])
    entries = entries[:int(max_entries)]
    if not entries:
        text = "# %s: no structural lines; use read() or search()" % rel
    else:
        text = "\n".join("%d: %s %s" % (item["line"], item["kind"], item["name"])
                         for item in entries)
    return _Result({"ok": True, "kind": "outline", "path": rel, "entries": entries,
                    "total_lines": len(lines), "text": text})


def _coerce_paths(path):
    # A model sometimes serializes a path list as '["a", "b"]' instead of passing an array.
    # Accept that shape rather than silently searching for a file with the brackets in its
    # name; a literal path that looks like a list is vanishingly unlikely.
    if isinstance(path, str) and path.strip().startswith("[") and path.strip().endswith("]"):
        try:
            value = _ast.literal_eval(path)
        except (ValueError, SyntaxError):
            return path
        if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            return list(value)
    return path


def read(path, offset=1, limit=0, raw=False, start=None, end=None, repeat=False):
    path = _coerce_paths(path)
    # `start`/`end` are the natural line-range names; `offset`/`limit` stay for compatibility.
    explicit = end is not None
    if start is not None:
        offset = start
    if end is not None:
        limit = max(0, int(end) - int(offset) + 1)
    # A list is one call for several files: the model's natural batch shape. Each sub-read
    # records its own _READS_CELL ranges, so coverage stays exact. `text` is the concatenation
    # with a `# path` header, so print(read([a, b])['text']) gives the model everything at once.
    if isinstance(path, (list, tuple, set)):
        paths = sorted(path) if isinstance(path, set) else list(path)
        results = [read(item, offset=offset, limit=limit, raw=raw, start=start, end=end,
                        repeat=repeat) for item in paths]
        chunks = []
        for item in results:
            rel = str(item.get("path") or "")
            if item.get("kind") == "file":
                chunks.append("# " + rel + "\n" + (item.get("text") or ""))
            else:
                chunks.append("# " + rel + "\n" + str(dict(item)))
        return _Result({"ok": all(bool(item.get("ok")) for item in results), "kind": "files",
                        "files": results, "count": len(results), "text": "\n\n".join(chunks)})
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
    body = _read_text(target)
    lines = body.splitlines()
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
    has_more = stop < total
    # `truncated` means this call had to cut output the caller asked to see (the default
    # page limits). An explicit start/end that lands before EOF is complete, even though
    # the file has more: that is `has_more`, not a truncation.
    truncated = has_more and not explicit and int(limit) <= 0
    result = {"ok": True, "kind": "file", "path": rel, "total_lines": total,
              "shown": [start, stop],
              "next": {"offset": stop + 1} if has_more else None,
              "truncated": truncated, "has_more": has_more}
    digest = _digest(body)
    prior = _READS_SESSION.get(rel)
    hit = None
    if (not raw and not repeat and prior and prior.get("digest") == digest):
        hit = next((item for item in prior.get("ranges") or []
                    if item["start"] <= start and stop <= item["stop"]), None)
    if hit is not None:
        handle = "_OBS[%d]['stdout']" % int(hit["obs"])
        note = ("already delivered in this session; use read(%r, start=%d, end=%d, repeat=True) "
                "to receive it again, or print(%s)" % (rel, start, stop, handle))
        result.update({"already_read": True, "handle": handle, "text": "# " + note})
        return _Result(result)
    entry = _READS_CELL.setdefault(rel, {"ranges": [], "total": total})
    entry["ranges"].append([start, stop])
    entry["total"] = total
    if prior is None or prior.get("digest") != digest:
        prior = {"ranges": [], "total": total, "digest": digest}
    prior["ranges"].append({"start": start, "stop": stop,
                            "obs": len(_OBS) if "_OBS" in globals() else 0})
    prior["total"] = total
    _READS_SESSION[rel] = prior
    chunk = "\n".join(lines[start - 1:stop])
    if raw:
        result["text"] = chunk
        result["raw"] = chunk
    else:
        result["text"] = "\n".join(f"{n:>6}| {lines[n - 1]}" for n in range(start, stop + 1))
    return _Result(result)


def _grep_result(hits, occurrences, truncated, max_chars=12000):
    # Render each file once. Adjacent/overlapping hits share their context lines, so the
    # model sees every line once; a line that is both a hit and another hit's context keeps
    # the `:` marker. The structured `matches` stays complete for python callers.
    grouped = {}
    order = []
    for hit in hits:
        path = hit.get("path")
        if path not in grouped:
            grouped[path] = {}
            order.append(path)
        rows = grouped[path]
        for item in hit.get("before") or []:
            rows.setdefault(int(item["line"]), [item["text"], False])
        rows[int(hit["line"])] = [hit["text"], True]
        for item in hit.get("after") or []:
            rows.setdefault(int(item["line"]), [item["text"], False])
    lines = []
    for path in order:
        for number in sorted(grouped[path]):
            text, is_hit = grouped[path][number]
            lines.append("%s:%s%s %s" % (path, number, ":" if is_hit else "-", text))
    full = "\n".join(lines)
    if max_chars and len(full) > int(max_chars):
        # A broad context search can exceed the card budget and lose its middle. Deliver
        # locators instead of a silently digested blob; the caller can re-run with a path or
        # max_chars=0 for the full text.
        locators = "\n".join(
            "%s:%s: %s" % (hit["path"], hit["line"], (hit.get("text") or "")[:120])
            for hit in hits)
        return _Result({"ok": True, "kind": "matches", "matches": hits,
                        "occurrences": occurrences, "truncated": truncated,
                        "locator_mode": True, "hits": len(hits), "text_chars": len(full),
                        "note": "broad result: locators only; pass max_chars=0 for full text",
                        "text": locators})
    return _Result({"ok": True, "kind": "matches", "matches": hits,
                    "occurrences": occurrences, "truncated": truncated,
                    "text": full})


def grep(pattern, path=".", max_results=50, regex=False, include=None, context=0,
         ignore_case=False, max_chars=12000):
    # Typed `search`: one path or a list, an optional include glob, and optional context.
    # The python helper of the same name keeps the old call shape.
    path = _coerce_paths(path)
    if isinstance(path, (list, tuple, set)):
        hits, occurred, truncated = [], 0, False
        for item in (sorted(path) if isinstance(path, set) else path):
            result = grep(pattern, item, max_results=max_results, regex=regex,
                          include=include, context=context, ignore_case=ignore_case,
                          max_chars=max_chars)
            hits.extend(result.get("matches") or [])
            occurred += int(result.get("occurrences") or 0)
            truncated = truncated or bool(result.get("truncated"))
            if len(hits) >= int(max_results):
                return _grep_result(hits[:int(max_results)], None, True, max_chars)
        return _grep_result(hits, occurred, truncated, max_chars)
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    if not _os.path.exists(target):
        return {"ok": True, "kind": "miss", "path": rel}
    matcher = None
    flags = _re.IGNORECASE if ignore_case else 0
    if regex:
        try:
            matcher = _re.compile(pattern, flags)
        except _re.error as exc:
            return {"ok": False, "kind": "bad_args", "pattern": pattern,
                    "error": {"code": "bad_regex", "message": str(exc),
                              "use": "search(pattern, regex=False) for a literal substring"}}
    needle = pattern.lower() if ignore_case else pattern
    includes = include if isinstance(include, (list, tuple, set)) else ([include] if include else [])
    explicit_file = _os.path.isfile(target)
    files = [target] if explicit_file else list(_walk(target))
    hits = []
    occurrences = 0
    for full in files:
        full_rel = _os.path.relpath(full, _ROOT)
        if includes and not explicit_file:
            if not any(_glob_match(full_rel, pat) or
                       _glob_match(_os.path.basename(full_rel), pat)
                       for pat in includes):
                continue
        try:
            body = _read_text(full)
        except OSError:
            continue
        if matcher:
            occurrences += len(matcher.findall(body))
        else:
            occurrences += (body.lower() if ignore_case else body).count(needle)
        lines = body.splitlines()
        for number, line in enumerate(lines, 1):
            hay = line.lower() if ignore_case else line
            if not (matcher.search(line) if matcher else needle in hay):
                continue
            hit = {"path": full_rel, "line": number, "text": line.strip()}
            if context:
                width = int(context)
                hit["before"] = [{"line": n, "text": lines[n - 1].strip()}
                                 for n in range(max(1, number - width), number)]
                hit["after"] = [{"line": n, "text": lines[n - 1].strip()}
                                for n in range(number + 1, min(len(lines), number + width) + 1)]
            hits.append(hit)
            if len(hits) >= int(max_results):
                return _grep_result(hits, None, True, max_chars)
    return _grep_result(hits, occurrences, False, max_chars)


def search(pattern, path=".", max_results=50, regex=True, include=None, context=0,
           ignore_case=False, max_chars=12000):
    # The typed `search` tool is the public name; grep() stays the python helper. Search is
    # regex by default, matching the grep mental model: an alternation like `a|b` is one
    # call, not a literal miss. Pass regex=False for a literal substring.
    return grep(pattern, path=path, max_results=max_results, regex=regex,
                include=include, context=context, ignore_case=ignore_case,
                max_chars=max_chars)


def recall(pattern, limit=10, context=100):
    # Search the full output of earlier cells, including turns a fold removed. Every cell
    # keeps its raw stdout/stderr/result in _OBS; this returns matching snippets plus the
    # handle to page the full text. grep is for the tree, recall is for the session.
    try:
        rx = _re.compile(pattern)
    except _re.error:
        rx = _re.compile(_re.escape(pattern))
    out = []
    for i, entry in enumerate(globals().get("_OBS") or []):
        for field in ("stdout", "stderr", "result"):
            text = entry.get(field) or ""
            for match in rx.finditer(text):
                start = max(0, match.start() - int(context))
                end = min(len(text), match.end() + int(context))
                out.append({"obs": i, "field": field, "text": text[start:end],
                            "handle": "_OBS[%d]['%s']" % (i, field)})
                if len(out) >= int(limit):
                    return out
    return out


def recall_tool(pattern, limit=10, context=100):
    # Typed session search: the same _OBS snippets as recall(), with a text rendering so the
    # model does not need a python cell to recover a digested card.
    items = recall(pattern, limit=limit, context=context)
    text = "\n".join("%s:%s: %s" % (item["handle"], item["field"], item["text"])
                     for item in items)
    return {"ok": True, "kind": "recall", "items": items,
            "count": len(items), "text": text}


def verbs(topic=None):
    # The live catalog: the same registry the parent uses for tool specs. Summaries come
    # from KERNEL_VERBS; signatures are introspected here, so they cannot drift from the
    # functions a typed call actually dispatches to.
    import inspect as _inspect
    meta = globals().get("_VERB_META") or {}
    out = []
    for name in sorted(meta):
        if topic and str(topic) not in name:
            continue
        fn = globals().get((meta[name].get("function") or name))
        if not callable(fn):
            continue
        try:
            signature = str(_inspect.signature(fn))
        except (TypeError, ValueError):
            signature = "(...)"
        out.append({"name": name, "signature": signature,
                    "summary": meta[name].get("summary") or "",
                    "mutates": bool(meta[name].get("mutates"))})
    return {"ok": True, "kind": "verbs", "verbs": out,
            "text": "\n".join("%s%s  %s" % (item["name"], item["signature"], item["summary"])
                             for item in out)}


help = verbs


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


def edit_many(edits=None, path=None, old=None, new=None, all=False):
    # One typed edit: either edits=[{path, old, new}, ...] or the single path/old/new form.
    # Validate every edit against the in-memory bodies first, then commit; a bad old string
    # changes nothing. This is the tool for a docs pass, where one edit() per line is pure
    # overhead, and for the simple replacement.
    if edits is None:
        if isinstance(path, str) and isinstance(old, str) and isinstance(new, str):
            edits = [{"path": path, "old": old, "new": new, "all": bool(all)}]
        else:
            return {"ok": False, "kind": "edit_failed",
                    "reason": "pass edits=[...] or path, old and new"}
    if not isinstance(edits, (list, tuple)) or not edits:
        return {"ok": False, "kind": "edit_failed",
                "reason": "edits must be a non-empty list of {path, old, new}"}
    originals = {}
    bodies = {}
    order = []
    writes = 0
    for index, item in enumerate(edits):
        if not isinstance(item, dict):
            return {"ok": False, "kind": "edit_failed", "index": index,
                    "reason": "each edit must be an object with path, old, new"}
        item_path = item.get("path") or path
        old = item.get("old")
        new = item.get("new")
        if not isinstance(item_path, str) or not isinstance(old, str) or not isinstance(new, str) or not old:
            return {"ok": False, "kind": "edit_failed", "index": index,
                    "reason": "path, old and new must be strings; old must not be empty"}
        target, rel = _resolve(item_path)
        if target is None:
            return {"ok": False, "kind": "edit_failed", "index": index,
                    "error": {"code": "outside_root", "path": rel}}
        if rel not in originals:
            if not _os.path.isfile(target):
                return {"ok": False, "kind": "edit_failed", "index": index,
                        "path": rel, "reason": "missing"}
            originals[rel] = _read_text(target)
            bodies[rel] = originals[rel]
            order.append((target, rel))
        body = bodies[rel]
        count = body.count(old)
        if count == 0:
            probe = old.splitlines()[0].strip() if old.splitlines() else old
            near = [{"line": number, "text": line.strip()[:200]}
                    for number, line in enumerate(body.splitlines(), 1)
                    if _difflib.SequenceMatcher(None, probe, line.strip()).ratio() > 0.6][:3]
            return {"ok": False, "kind": "edit_failed", "index": index, "path": rel,
                    "reason": "not_found", "closest": near}
        if count > 1 and not bool(item.get("all")):
            return {"ok": False, "kind": "edit_failed", "index": index, "path": rel,
                    "reason": "ambiguous", "count": count,
                    "action": "extend old until unique, or pass all=True"}
        bodies[rel] = body.replace(old, new) if bool(item.get("all")) else body.replace(old, new, 1)
        writes += 1
    changed = []
    for target, rel in order:
        if bodies[rel] != originals[rel]:
            _commit(target, rel, bodies[rel], "edit")
            changed.append(rel)
    return {"ok": True, "kind": "edits", "paths": changed, "edits": len(edits),
            "replaced": writes,
            "text": "edited %d file(s) in %d replacement(s): %s" % (
                len(changed), writes, ", ".join(changed)) if changed else "no change"}


def splice(path, start, end=None, text=""):
    # Replace 1-based inclusive lines start..end with text. end < start inserts before start;
    # text="" deletes the range. read() already returns numbered lines, so the model never
    # re-emits unchanged lines for a surgical change. _commit keeps diff()/undo() honest.
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    if not _os.path.isfile(target):
        return {"ok": True, "kind": "no_change", "path": rel, "reason": "missing"}
    body = _read_text(target)
    lines = body.splitlines()
    total = len(lines)
    try:
        a = int(start)
        b = a if end is None else int(end)
    except (TypeError, ValueError):
        return {"ok": False, "kind": "splice", "path": rel,
                "reason": "start and end must be integers (1-based line numbers)"}
    if a < 1:
        return {"ok": False, "kind": "splice", "path": rel,
                "reason": "start is 1-based", "total_lines": total}
    replacement = str(text).splitlines()
    if b < a:
        if a > total + 1:
            return {"ok": False, "kind": "splice", "path": rel,
                    "reason": "insert position is past the end", "total_lines": total,
                    "next": total + 1}
        new_lines = lines[:a - 1] + replacement + lines[a - 1:]
    else:
        if a > total or b > total:
            return {"ok": False, "kind": "splice", "path": rel,
                    "reason": "range is past the end; read(path) shows total_lines",
                    "total_lines": total, "next": a}
        new_lines = lines[:a - 1] + replacement + lines[b:]
    trailing = "\n" if body.endswith("\n") or not lines else ""
    updated = "\n".join(new_lines) + trailing
    entry = _commit(target, rel, updated, "splice")
    lo = max(1, a - 3)
    hi = min(len(new_lines), a + len(replacement) + 3)
    hunk = "\n".join(f"{n:>6}| {new_lines[n - 1]}" for n in range(lo, hi + 1)) if hi >= lo else ""
    return {"ok": True, "kind": "splice", "path": rel,
            "replaced": [a, b] if b >= a else [a, a - 1],
            "lines": entry["after_lines"], "adds": entry["adds"], "dels": entry["dels"],
            "hunk": hunk, "hunk_lines": [lo, hi] if hi >= lo else []}

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

# --- child side 3/3: RUN_PRELUDE, exec()/run() for programs, behind the protocol check -----
RUN_PRELUDE = r"""
import json as _json, os as _ros, re as _re, signal as _rsig, subprocess as _rsp
_RUN_ROOT = _ros.getcwd()
# exec()/run() children get their own session, so killing the kernel group does not reach them.
# Journal each group leader here; the parent reaps leftovers when the kernel stops/restarts.
_ORPHAN_JOURNAL = _ros.environ.get("KERNEL_ORPHAN_JOURNAL")


def _proc_start(pid):
    try:
        with open("/proc/%s/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[1].split()[19]
    except Exception:
        return None


def _journal_orphan(pid):
    if not _ORPHAN_JOURNAL:
        return
    try:
        with open(_ORPHAN_JOURNAL, "a") as fh:
            fh.write(_json.dumps({"pid": int(pid), "start": _proc_start(pid)}) + "\n")
    except Exception:
        pass
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
    "sed -i": "splice(path, start, end, text) or edit(path, old, new)",
    "mkdir": "os.makedirs(path, exist_ok=True)",
    "patch": "apply(patch) or splice(path, start, end, text)",
    "git": "git add/commit/push are allowed; edit files with splice/edit/apply/commit",
    ">": "splice(path, start, end, text) or write(path, content)", "find -delete": "map() then os.remove(path)",
    "xargs": "a Python loop",
}


def _segments(command):
    return [part for part in _re.split(r"\|\||&&|\||;|\n", str(command)) if part.strip()]


_TMP_ROOTS = {_ros.path.normpath(p) for p in (
    "/tmp", "/var/tmp", "/dev/shm", _ros.environ.get("TMPDIR") or "/tmp")}


def _abs_outside(token):
    # Only scratch space is exempt: /tmp, /var/tmp, /dev/shm and $TMPDIR. A path outside the
    # workspace is not enough -- `rm -rf /` is also outside it.
    text = str(token).strip().strip("'\"")
    if not text.startswith("/") or "$" in text or ".." in text:
        return False
    path = _ros.path.normpath(text)
    if path == _RUN_ROOT or path.startswith(_RUN_ROOT + _ros.sep):
        return False
    return any(path == root or path.startswith(root + _ros.sep) for root in _TMP_ROOTS)


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
    # Quote-aware: `>` inside '...' or "..." is an argument, not a redirect. The old regex
    # refused `python3 -c "print(a > b)"` and nested command strings.
    quote = None
    i, n = 0, len(segment)
    while i < n:
        ch = segment[i]
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == ">" and (i == 0 or not segment[i - 1].isdigit()):
            j = i + 1
            if j < n and segment[j] == ">":
                j += 1
            while j < n and segment[j].isspace():
                j += 1
            if j < n and segment[j] in ("'", '"'):
                close = segment[j]
                j += 1
                start = j
                while j < n and segment[j] != close:
                    j += 1
                target = segment[start:j]
            else:
                start = j
                while j < n and segment[j] not in " \t\r\n;|&":
                    j += 1
                target = segment[start:j]
            target = target.strip("'\"")
            if target and target not in ("/dev/null", "/dev/stdout", "/dev/stderr", "&1", "&2") \
                    and not _abs_outside(target):
                return True
            i = j
            continue
        i += 1
    return False


def _mutating_command(command, redirection=True):
    # Return the mutating verb in a shell command, or None when it only reads / runs.
    # `redirection` is False for a list argv: there is no shell to parse `>`, so it is a
    # literal argument (e.g. a Python -c source containing `->`), not a file write.
    text = str(command).strip()
    if not text:
        return None
    for segment in _segments(text):
        if redirection and _writes_stdout(segment):
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
            inner = _mutating_command(" ".join(parts[2:]), redirection)
            if inner:
                return inner
        if base == "find" and any(arg in ("-delete", "-exec", "-execdir", "-ok", "-okdir")
                                  for arg in args):
            return "find -delete"
        if base == "xargs":
            inner = _mutating_command(" ".join(args), redirection)
            if inner:
                return "xargs " + inner
    return None


def _norm_command(command):
    return " ".join(str(command).split())


def _spawn_argv(event, args):
    # The raw argv: a list means no shell parsed it, a str means a shell did (or os.system).
    if event == "os.system":
        return str(args[0]) if args else ""
    if event in ("subprocess.Popen", "os.exec", "os.posix_spawn"):
        return args[1] if len(args) > 1 else args[0]
    if event == "os.spawn":
        return args[2] if len(args) > 2 else args[0]
    return ""


def _spawn_command(argv):
    if isinstance(argv, (list, tuple)):
        return " ".join(str(item) for item in argv)
    return str(argv)


def _gate_check(command, redirection=True):
    text = str(command).strip()
    if not text:
        return None
    parts = text.split()
    base = _ros.path.basename(parts[0]) if parts else ""
    if base in ("bash", "sh", "zsh") and len(parts) > 2 and parts[1] in ("-c", "-lc"):
        # `bash -c "cmd > f"` is a shell command even when the outer argv is a list.
        inner = " ".join(parts[2:]).strip().strip('"').strip("'")
        return _mutating_command(inner, redirection=True)
    return _mutating_command(text, redirection=redirection)


def _audit_spawn(event, args):
    if event not in _SPAWN_EVENTS:
        return
    argv = _spawn_argv(event, args)
    command = _spawn_command(argv)
    blocked = _gate_check(command, redirection=not isinstance(argv, (list, tuple)))
    if blocked:
        raise _SpawnBlocked(blocked, command)
    if not _GATE_IN_RUN:
        _RUN_LOG.append(command)


import sys as _sys
_sys.addaudithook(_audit_spawn)


def _run_shell(command, timeout=60, cwd=None):
    global _GATE_IN_RUN
    # A list is one call for several commands: the same gate and journal rules apply to each.
    # Each sub-run is a normal _run_shell(), so _RUN_LOG, check_cache and the spawn gate stay exact.
    if isinstance(command, (list, tuple)):
        items = [_run_shell(item, timeout=timeout, cwd=cwd) for item in command]
        stdout = "\n".join(str(item.get("stdout") or "") for item in items).strip()
        stderr = "\n".join(str(item.get("stderr") or "") for item in items).strip()
        return _Result({"ok": all(bool(item.get("ok")) for item in items), "kind": "commands",
                        "count": len(items), "commands": items,
                        "stdout": stdout, "stderr": stderr,
                        "exit_code": next((item.get("exit_code") for item in items
                                            if item.get("exit_code") not in (0, None)), 0)})
    _RUN_LOG.append(str(command))
    cached = _CHECK_CACHE.get(_norm_command(command))
    if cached:
        if isinstance(cached, dict):
            text = str(cached.get("text") or "")
            ok = bool(cached.get("ok", True))
        else:
            text, ok = str(cached), True
        return _Result({"ok": True, "kind": "check_cached", "command": str(command),
                "result": text, "exit_code": 0 if ok else 1, "stdout": text, "stderr": "",
                "note": "this command is the project check for the current mutation batch; its recorded result is authoritative."})
    blocked = _mutating_command(command)
    if blocked:
        use = _HINT.get(blocked) or _HINT.get(blocked.split()[0], "splice(), edit() or apply()")
        return _Result({
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
        })
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
            _journal_orphan(proc.pid)
        finally:
            _GATE_IN_RUN -= 1
    except _SpawnBlocked as exc:
        use = _HINT.get(str(exc.blocked)) or _HINT.get(str(exc.blocked).split()[0], "splice(), edit() or apply()")
        return _Result({"ok": False, "kind": "wrong_call", "command": str(command),
                "blocked": exc.blocked,
                "error": {"code": "mutation_via_run", "message": str(exc), "use": use},
                "exit_code": None, "stdout": "", "stderr": ""})
    except OSError as exc:
        return _Result({"ok": False, "kind": "spawn_error", "command": str(command),
                "error": {"code": "spawn_failed", "message": str(exc)},
                "exit_code": None, "stdout": "", "stderr": ""})
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
        return _Result({"ok": True, "kind": "timeout", "command": str(command), "timeout_s": float(timeout),
                "exit_code": None, "stdout": out or "", "stderr": err or "",
                "note": "the command hit its own timeout and its process group was killed; the kernel is intact"})
    return _Result({"ok": True, "kind": "command", "command": str(command), "exit_code": proc.returncode,
            "stdout": out or "", "stderr": err or ""})


run = _run_shell


def _exec_shell_string(argv):
    for index, item in enumerate(argv):
        if _ros.path.basename(str(item)) not in ("bash", "sh", "zsh", "dash", "ksh"):
            continue
        tail = argv[index + 1:]
        if any(str(arg) == "-c" or (str(arg).startswith("-") and not str(arg).startswith("--")
                                    and "c" in str(arg)[1:]) for arg in tail):
            return True
    return False


def exec_argv(argv=None, commands=None, shell=None, timeout=60, cwd=None, env=None, stdin=None):
    # The typed process tool: a program and its argv, never a shell string. A list of
    # commands runs in order and stops at the first non-zero exit. cwd replaces `cd`,
    # stdout/stderr stay separate, and the same orphan journal and mutation gate apply.
    global _GATE_IN_RUN
    if shell is not None:
        if argv is not None or commands is not None:
            return _Result({"ok": False, "kind": "bad_args", "error": {
                "code": "both_shell_and_argv",
                "message": "pass shell=<string> or argv/commands, not both",
                "use": "exec(shell='git status && git diff --stat')"}})
        if not isinstance(shell, str) or not shell.strip():
            return _Result({"ok": False, "kind": "bad_args", "error": {
                "code": "bad_shell", "message": "shell must be a non-empty string",
                "use": "exec(shell='git status && git diff --stat')"}})
        return _run_shell(shell, timeout=timeout, cwd=cwd)
    if commands is not None:
        if argv is not None:
            return _Result({"ok": False, "kind": "bad_args", "error": {
                "code": "both_argv_and_commands",
                "message": "pass argv=[...] or commands=[[...], ...], not both",
                "use": "exec(argv=['git','status']) or exec(commands=[['git','status'], ['git','diff']])"}})
        if not isinstance(commands, (list, tuple)) or not commands:
            return _Result({"ok": False, "kind": "bad_args", "error": {
                "code": "bad_commands", "message": "commands must be a non-empty list of argv lists",
                "use": "exec(commands=[['git','status'], ['git','diff']])"}})
        for index, item in enumerate(commands):
            if isinstance(item, str):
                return _Result({"ok": False, "kind": "bad_args", "index": index, "error": {
                    "code": "bad_commands",
                    "message": "each command must be an argv list, not a shell string",
                    "use": "exec(commands=[['git','status'], ['git','diff']]) or exec(shell='git status && git diff')"}})
            if (not isinstance(item, (list, tuple)) or not item
                    or any(isinstance(part, (list, tuple, dict, set)) for part in item)):
                return _Result({"ok": False, "kind": "bad_args", "index": index, "error": {
                    "code": "bad_commands",
                    "message": "each command must be a non-empty argv list of scalars",
                    "use": "exec(commands=[['git','status'], ['git','diff']])"}})
        items = []
        for item in commands:
            result = exec_argv(item, timeout=timeout, cwd=cwd, env=env, stdin=stdin)
            items.append(result)
            if not result.get("ok") or result.get("exit_code") not in (0, None):
                break
        ok = all(bool(item.get("ok")) and item.get("exit_code") in (0, None) for item in items)
        stdout = "\n".join(str(item.get("stdout") or "") for item in items).strip()
        stderr = "\n".join(str(item.get("stderr") or "") for item in items).strip()
        results = [{"argv": item.get("argv"), "exit_code": item.get("exit_code"),
                    "ok": item.get("ok")} for item in items]
        return _Result({"ok": ok, "kind": "commands", "count": len(items),
                        "results": results, "stdout": stdout, "stderr": stderr,
                        "exit_code": next((item.get("exit_code") for item in items
                                           if item.get("exit_code") not in (0, None)), 0)})
    if isinstance(argv, str):
        return _Result({"ok": False, "kind": "wrong_call", "argv": argv, "error": {
            "code": "shell_string",
            "message": "exec() runs a program by argv; it does not parse a shell string",
            "use": "exec(argv=['git','status']) -- or exec(shell=<command>) when you truly need shell syntax"}})
    if (not isinstance(argv, (list, tuple)) or not argv
            or any(isinstance(item, (list, tuple, dict, set)) for item in argv)):
        return _Result({"ok": False, "kind": "bad_args", "error": {
            "code": "bad_argv", "message": "argv must be a non-empty list of scalars",
            "use": "exec(argv=['python3','bench/smoke.py'])"}})
    argv = [str(item) for item in argv]
    command = " ".join(argv)
    _RUN_LOG.append(command)
    cached = _CHECK_CACHE.get(_norm_command(command))
    if cached:
        if isinstance(cached, dict):
            text = str(cached.get("text") or "")
            ok = bool(cached.get("ok", True))
        else:
            text, ok = str(cached), True
        return _Result({"ok": True, "kind": "check_cached", "command": command,
                "result": text, "exit_code": 0 if ok else 1, "stdout": text, "stderr": "",
                "note": "this command is the project check for the current mutation batch; its recorded result is authoritative."})
    blocked = _gate_check(command, redirection=False)
    if blocked:
        use = _HINT.get(blocked) or _HINT.get(blocked.split()[0], "splice(), edit() or apply()")
        return _Result({"ok": False, "kind": "wrong_call", "command": command,
                "blocked": blocked,
                "error": {"code": "mutation_via_exec",
                          "message": f"`{blocked}` changes files through a program, so it would bypass the journal; use the helper.",
                          "use": use},
                "exit_code": None, "stdout": "", "stderr": ""})
    if _exec_shell_string(argv):
        return _Result({"ok": False, "kind": "wrong_call", "command": command,
                "blocked": "bash -c",
                "error": {"code": "shell_via_exec",
                          "message": "exec(argv) is for programs; a shell interpreter with -c/-lc is a shell string. Use exec(shell=<command>) explicitly.",
                          "use": "exec(shell='git status && git diff --stat') or exec(commands=[[...],[...]])"},
                "exit_code": None, "stdout": "", "stderr": ""})
    if env is not None and not isinstance(env, dict):
        return _Result({"ok": False, "kind": "bad_args", "error": {
            "code": "bad_env", "message": "env must be an object of string values",
            "use": "exec(argv=['git','status'], env={'GIT_PAGER': 'cat'})"}})
    child_env = None
    if env:
        child_env = dict(_ros.environ)
        child_env.update({str(key): str(value) for key, value in env.items()})
    extend = globals().get("_CELL_EXTEND")
    if extend is not None:
        extend(float(timeout) + 20)
    try:
        _GATE_IN_RUN += 1
        try:
            proc = _rsp.Popen(
                argv, shell=False, cwd=cwd or _RUN_ROOT,
                stdout=_rsp.PIPE, stderr=_rsp.PIPE, text=True,
                encoding="utf-8", errors="replace",
                stdin=_rsp.PIPE if stdin is not None else _rsp.DEVNULL,
                env=child_env, start_new_session=True,
            )
            _journal_orphan(proc.pid)
        finally:
            _GATE_IN_RUN -= 1
    except _SpawnBlocked as exc:
        use = _HINT.get(str(exc.blocked)) or _HINT.get(str(exc.blocked).split()[0], "splice(), edit() or apply()")
        return _Result({"ok": False, "kind": "wrong_call", "command": command,
                "blocked": exc.blocked,
                "error": {"code": "mutation_via_exec", "message": str(exc), "use": use},
                "exit_code": None, "stdout": "", "stderr": ""})
    except OSError as exc:
        return _Result({"ok": False, "kind": "spawn_error", "command": command,
                "error": {"code": "spawn_failed", "message": str(exc)},
                "exit_code": None, "stdout": "", "stderr": ""})
    try:
        out, err = proc.communicate(input=(str(stdin) if stdin is not None else None),
                                    timeout=float(timeout))
    except _rsp.TimeoutExpired:
        try:
            _ros.killpg(_ros.getpgid(proc.pid), _rsig.SIGKILL)
        except OSError:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        return _Result({"ok": True, "kind": "timeout", "argv": argv, "command": command,
                "timeout_s": float(timeout), "exit_code": None, "stdout": out or "", "stderr": err or "",
                "note": "the program hit its own timeout and its process group was killed; the kernel is intact"})
    return _Result({"ok": True, "kind": "command", "argv": argv, "command": command,
            "cwd": cwd or _RUN_ROOT, "exit_code": proc.returncode,
            "stdout": out or "", "stderr": err or ""})


def _git_page(text, offset=0, max_chars=12000):
    text = str(text or "")
    start = max(0, int(offset or 0))
    limit = int(max_chars or 0)
    shown = text[start:] if limit <= 0 else text[start:start + limit]
    return shown, len(text), (start + len(shown) if start + len(shown) < len(text) else None)


def git_tool(action, paths=None, message=None, limit=20, offset=0, max_chars=12000,
             stat=False, cached=False, rev="HEAD", remote="origin", branch=None,
             cwd=None):
    # Typed git: no pager, no shell, no giant undifferentiated dump. diff/show page with
    # offset/max_chars; status/log/files return parsed fields plus text.
    action = str(action or "").strip().lower()
    paths = [str(item) for item in paths] if isinstance(paths, (list, tuple)) else (
        [str(paths)] if paths else [])
    base = ["git"]
    if action == "status":
        base += ["status", "--porcelain=v1", "-b"]
    elif action == "diff":
        base += ["diff", "--no-color"]
        if cached:
            base.append("--cached")
        if stat:
            base.append("--stat")
    elif action == "show":
        base += ["show", "--no-color"]
        if stat:
            base.append("--stat")
        if rev:
            base.append(str(rev))
    elif action == "log":
        base += ["log", "--oneline", "-n", str(max(1, int(limit or 20)))]
    elif action == "files":
        base += ["ls-files"]
    elif action == "add":
        base += ["add"] + (["--"] + paths if paths else ["-A"])
    elif action == "commit":
        if not message or not str(message).strip():
            return _Result({"ok": False, "kind": "git_error", "action": action,
                            "error": {"code": "missing_message", "message": "commit needs message"}})
        if paths:
            added = exec_argv(["git", "add", "--"] + paths, cwd=cwd)
            if added.get("exit_code") not in (0, None):
                return _Result({"ok": False, "kind": "git_error", "action": action,
                                "stdout": added.get("stdout") or "",
                                "stderr": added.get("stderr") or ""})
        base += ["commit", "-m", str(message)]
    elif action == "push":
        base += ["push", str(remote or "origin")]
        if branch:
            base.append(str(branch))
    elif action == "remote":
        base += ["remote", "-v"]
    elif action == "branch":
        base += ["branch", "--show-current"]
    else:
        return _Result({"ok": False, "kind": "git_error", "action": action,
                        "error": {"code": "unknown_action",
                                  "message": "action must be status/diff/show/log/files/add/commit/push/remote/branch"}})
    if paths and action in ("diff", "show"):
        base += ["--"] + paths
    result = exec_argv(base, cwd=cwd)
    out = str(result.get("stdout") or "")
    err = str(result.get("stderr") or "")
    code = result.get("exit_code")
    ok = code in (0, None)
    if action in ("diff", "show"):
        text, total, nxt = _git_page(out, offset, max_chars)
        return _Result({"ok": ok, "kind": "git_diff" if action == "diff" else "git_show",
                        "action": action, "paths": paths, "text": text,
                        "total_chars": total, "next_offset": nxt,
                        "next": {"offset": nxt} if nxt is not None else None,
                        "has_more": nxt is not None,
                        "exit_code": code, "stderr": err})
    if action == "status":
        changed = []
        branch_name = ""
        for line in out.splitlines():
            if line.startswith("##"):
                branch_name = line[2:].strip().split("...")[0].strip()
                continue
            if len(line) > 3:
                changed.append({"status": line[:2].strip(), "path": line[3:].strip()})
        return _Result({"ok": ok, "kind": "git_status", "action": action,
                        "branch": branch_name, "changed": changed, "clean": not changed,
                        "text": out, "exit_code": code, "stderr": err})
    if action == "log":
        commits = []
        for line in out.splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                commits.append({"sha": parts[0], "subject": parts[1]})
        return _Result({"ok": ok, "kind": "git_log", "action": action,
                        "commits": commits, "text": out, "exit_code": code, "stderr": err})
    if action == "files":
        files = [line for line in out.splitlines() if line.strip()]
        return _Result({"ok": ok, "kind": "git_files", "action": action,
                        "files": files, "text": out, "exit_code": code, "stderr": err})
    if action == "branch":
        return _Result({"ok": ok, "kind": "git_branch", "action": action,
                        "branch": out.strip(), "text": out, "exit_code": code, "stderr": err})
    sha = ""
    if action == "commit" and ok:
        probe = exec_argv(["git", "rev-parse", "--short", "HEAD"], cwd=cwd)
        sha = str(probe.get("stdout") or "").strip()
    return _Result({"ok": ok, "kind": "git_action", "action": action,
                    "paths": paths, "sha": sha, "text": out, "stdout": out,
                    "stderr": err, "exit_code": code})
"""

# --- parent side: supervision, protocol and the Cell the loops consume --------------------
MARKER = "@@K@@"
MAX_CODE = 20_000
MAX_OUTPUT = 0
CELL_TIMEOUT = 15.0


_RESTART_DIED = "kernel died; restarted with an empty namespace (earlier output survives in recall())"
_RESTART_DESYNC = "kernel protocol desync; restarted with an empty namespace (earlier output survives in recall())"


@dataclass
class CellRequest:
    """One request as it goes on the wire: a code cell or a typed verb call."""

    timeout: float
    code: str | None = None
    kind: str = "code"
    tool: str | None = None
    args: dict = field(default_factory=dict)
    protocol: int = PROTOCOL
    check_cache: dict = field(default_factory=dict)

    def to_wire(self) -> dict:
        payload = {"protocol": self.protocol, "timeout": float(self.timeout), "kind": self.kind}
        if self.kind == "call":
            payload["tool"] = self.tool
            payload["args"] = dict(self.args or {})
        else:
            payload["code"] = self.code
        if self.check_cache:
            payload["check_cache"] = self.check_cache
        return payload


@dataclass
class CellReply:
    """The typed body of one `@@K@@` line; `protocol` must equal PROTOCOL."""

    stdout: str = ""
    stderr: str = ""
    result: str | None = None
    final: str | None = None
    answer: str | None = None
    runs: list[str] = field(default_factory=list)
    obs_index: int | None = None
    mutated: bool = False
    changed: list[str] = field(default_factory=list)
    deltas: dict[str, str] = field(default_factory=dict)
    delta: str = ""
    timeout: bool = False
    reads: dict = field(default_factory=dict)
    tool: str | None = None
    failed: bool = False
    rejected: bool = False
    protocol: int | None = None

    @classmethod
    def from_wire(cls, data: dict) -> "CellReply":
        return cls(
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            result=data.get("result"),
            final=data.get("final"),
            answer=data.get("answer"),
            runs=[str(item) for item in (data.get("runs") or [])],
            obs_index=data.get("obs_index"),
            mutated=bool(data.get("mutated")),
            changed=[str(item) for item in (data.get("changed") or [])],
            deltas={str(path): str(text) for path, text in (data.get("deltas") or {}).items()},
            delta=str(data.get("delta") or ""),
            timeout=bool(data.get("timeout")),
            reads=dict(data.get("reads") or {}),
            tool=data.get("tool"),
            failed=bool(data.get("failed")),
            rejected=bool(data.get("rejected")),
            protocol=data.get("protocol"),
        )

    @property
    def protocol_ok(self) -> bool:
        return self.protocol == PROTOCOL


@dataclass
class ExtendNotice:
    """An interim `@@K@@` line a long cell sends before its reply."""

    seconds: float | None = None
    protocol: int | None = None

    @classmethod
    def from_wire(cls, data: dict) -> "ExtendNotice":
        seconds = data.get("extend")
        return cls(seconds=None if seconds is None else float(seconds),
                   protocol=data.get("protocol"))


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
    deltas: dict[str, str] = field(default_factory=dict)
    delta: str = ""
    timeout: bool = False
    reads: dict = field(default_factory=dict)
    tool: str | None = None
    failed: bool = False
    rejected: bool = False

    @classmethod
    def failure(cls, message: str, *, restarted: bool = True, timeout: bool = False,
                duration_ms: int = 0) -> "Cell":
        """The one error/restart constructor: no namespace state, the message is the card."""
        return cls(stdout="", stderr=message, result=None, final=None,
                   duration_ms=duration_ms, restarted=restarted, timeout=timeout,
                   failed=True)

    @classmethod
    def from_reply(cls, reply: CellReply, *, duration_ms: int) -> "Cell":
        """The one success constructor: a validated reply becomes the loop's Cell."""
        return cls(
            stdout=_cap(reply.stdout),
            stderr=_cap(reply.stderr),
            result=reply.result,
            final=reply.final,
            duration_ms=duration_ms,
            restarted=False,
            answer=reply.answer,
            runs=list(reply.runs),
            obs_index=reply.obs_index,
            mutated=reply.mutated,
            changed=list(reply.changed),
            deltas=dict(reply.deltas),
            delta=reply.delta,
            timeout=reply.timeout,
            reads=dict(reply.reads),
            tool=reply.tool,
            failed=reply.failed,
            rejected=reply.rejected,
        )


def _cap(text: str, limit: int = MAX_OUTPUT) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


class Kernel:
    def __init__(self, root: Path, timeout: float = CELL_TIMEOUT, writable: bool = False,
                 prelude: str = "off", allow_run: bool = False, allow_shell: bool | None = None,
                 supervise: bool = True) -> None:
        self.root = Path(root).resolve()
        self.timeout = timeout
        self.writable = writable
        self.prelude = prelude
        self.allow_run = allow_run
        self.allow_shell = bool(allow_run if allow_shell is None else (allow_shell and allow_run))
        self.supervise = supervise
        self.proc: subprocess.Popen | None = None
        self.orphan_journal: str | None = None
        self.obs_log: str | None = None
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

    def _start_id(self, pid: int) -> str | None:
        try:
            data = Path(f"/proc/{pid}/stat").read_text()
            return data.rsplit(")", 1)[1].split()[19]
        except OSError:
            return None

    def _reap_orphans(self) -> None:
        """Kill exec()/run() process groups the dead/stopped kernel left behind.

        `exec()`/`run()` start each program in its own session, so `_kill_group()` (the kernel's
        group) never reaches it. The child journals its group leader; reaping is best-effort
        and identity-checked so a reused pid is not killed by mistake.
        """
        path = self.orphan_journal
        self.orphan_journal = None
        if not path:
            return
        seen: set[int] = set()
        try:
            lines = Path(path).read_text().splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                record = json.loads(line)
                pid = int(record.get("pid") or 0)
            except (ValueError, TypeError):
                continue
            if pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            start = record.get("start")
            if start and self._start_id(pid) not in (None, start):
                continue
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            os.unlink(path)
        except OSError:
            pass

    def start(self) -> None:
        if self.prelude != "off" and not self.obs_log:
            fd, self.obs_log = tempfile.mkstemp(prefix="cact-obs-", suffix=".jsonl")
            os.close(fd)
        if self.allow_run:
            fd, self.orphan_journal = tempfile.mkstemp(prefix="cact-orphans-", suffix=".jsonl")
            os.close(fd)
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
                "KERNEL_SHELL": "1" if self.allow_shell else "0",
                **{key: os.environ[key] for key in (
                    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL") if key in os.environ},
                **({"KERNEL_ALLOW_RUN": "1", "KERNEL_RUN_PRELUDE": RUN_PRELUDE,
                    **({"KERNEL_ORPHAN_JOURNAL": self.orphan_journal} if self.orphan_journal else {})}
                   if self.allow_run else {}),
                **({"KERNEL_OBS_LOG": self.obs_log} if self.obs_log else {}),
                **({"KERNEL_PRELUDE": PRELUDE, "KERNEL_PRELUDE_MODE": self.prelude,
                    "KERNEL_HEADERS": HEADERS_SOURCE, "KERNEL_VERBS": json.dumps(VERBS)}
                   if self.prelude != "off" else {}),
            },
        )

    def _stop(self, keep_history: bool) -> None:
        if self.proc and self.proc.poll() is None:
            self._kill_group()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
        self._reap_orphans()
        if not keep_history and self.obs_log:
            try:
                os.unlink(self.obs_log)
            except OSError:
                pass
            self.obs_log = None

    def stop(self) -> None:
        self._stop(keep_history=False)

    def _kill_group(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except OSError:
                self.proc.kill()

    def _restart(self) -> None:
        # Keep the observation log: the new child reloads it, so recall() survives the crash.
        self._stop(keep_history=True)
        self.start()

    @staticmethod
    def _protocol_error(message: dict) -> str | None:
        """None when the child speaks this parent's protocol, else the restart message."""
        got = message.get("protocol")
        if got == PROTOCOL:
            return None
        return (f"kernel protocol mismatch: child speaks {got!r}, parent expects {PROTOCOL}; "
                "restarted with an empty namespace (earlier output survives in recall())")

    def run(self, code: str, check_cache: dict | None = None) -> Cell:
        if len(code) > MAX_CODE:
            return Cell.failure("kernel: code exceeds 20000 characters; split it into smaller cells",
                                restarted=False)
        return self._execute(CellRequest(code=code, timeout=float(self.timeout),
                                         check_cache=dict(check_cache or {})))

    def call(self, tool: str, args: dict | None = None, check_cache: dict | None = None) -> Cell:
        """Execute one typed verb through the same child, journal and Cell pipeline."""
        return self._execute(CellRequest(kind="call", tool=str(tool), args=dict(args or {}),
                                         timeout=float(self.timeout),
                                         check_cache=dict(check_cache or {})))

    def _execute(self, request: CellRequest) -> Cell:
        if self.proc is None:
            self.start()
        assert self.proc is not None and self.proc.stdin and self.proc.stdout

        started = time.perf_counter()
        try:
            self.proc.stdin.write(json.dumps(request.to_wire()) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._restart()
            return Cell.failure(_RESTART_DIED)

        sel = selectors.DefaultSelector()
        sel.register(self.proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + self.timeout + 5.0
        payload = None
        checked = False
        try:
            while payload is None:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    self._kill_group()
                    self._restart()
                    return Cell.failure(
                        f"kernel did not answer within {self.timeout:.0f}s; killed and restarted with an empty namespace (earlier output survives in recall())",
                        timeout=True,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                if not sel.select(remain):
                    continue
                line = self.proc.stdout.readline()
                if not line:
                    self._restart()
                    return Cell.failure(_RESTART_DIED)
                if not line.startswith(MARKER):
                    self._restart()
                    return Cell.failure(_RESTART_DESYNC)
                message = json.loads(line[len(MARKER):])
                if not checked:
                    checked = True
                    error = self._protocol_error(message)
                    if error is not None:
                        self._restart()
                        return Cell.failure(error)
                notice = ExtendNotice.from_wire(message)
                if notice.seconds is not None:
                    deadline = time.monotonic() + notice.seconds + 30.0
                    continue
                payload = message
        finally:
            sel.close()

        reply = CellReply.from_wire(payload)
        return Cell.from_reply(reply, duration_ms=int((time.perf_counter() - started) * 1000))
