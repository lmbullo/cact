"""driver: DRIVER, the child loop that turns one request line into one `@@K@@` reply.

WHAT IT IS
  A source string, not a module the parent imports for its functions: the child interpreter
  runs `python3 -u -c DRIVER` (or the same under bwrap). Per request it either exec()s a
  python cell or dispatches one typed verb from KERNEL_VERBS, in a namespace that survives
  between requests, then emits exactly one `@@K@@` JSON line.

ENTRY POINTS
  DRIVER   the child source; `__PROTOCOL__` is replaced with the parent's PROTOCOL at import
  The parent hands it two more sources through the environment, never through imports:
    KERNEL_PRELUDE / KERNEL_PRELUDE_MODE   prelude.PRELUDE
    KERNEL_RUN_PRELUDE                     run_prelude.RUN_PRELUDE
    KERNEL_HEADERS                         prelude.HEADERS_SOURCE (exec'd into `_HEADERS`)
    KERNEL_VERBS                           json.dumps(verbs.VERBS) for dispatch and help()

INVARIANTS
  - The child's stdout is a protocol channel: every message is exactly one `@@K@@`-prefixed
    JSON line. The cell's own stdout/stderr are redirected into buffers first, so a stray
    print cannot corrupt the channel.
  - Before dispatch the args are bound to the verb function's live signature
    (`inspect.signature().bind(**args)`) and the declared scalar types (integer, number,
    boolean, string, array-of-string) are coerced from the registry schema, recursively through
    `properties` and `oneOf`/`anyOf`. A bad keyword, missing argument or bad scalar becomes
    `kind: "bad_args"` with `error.code: "bad_arguments"`, the live signature, the offending
    keyword and the valid keyword list, never a traceback. A verb returning `{"ok": false}`
    is `rejected`, not `failed`.
  - A verb that raises is caught per typed call and answered as
    `{ok:false, kind:"tool_crashed", error:{code:"internal_error", ...}}`, so a tool bug
    can never read as `ok:true` with a traceback beside it.
  - stdin is read with one readline per request; the parent's request carries `timeout` and
    `check_cache`. `final_answer()` raises `Final`, which is reported as `Cell.final`
    unconditionally — the loop, not the child, decides what that means. A dict answer
    travels in that same field; the parent renders it and keeps its structured handoff.
  - `_OBS` entries are appended to KERNEL_OBS_LOG after each cell so recall() survives a
    restart; the namespace itself does not survive one.

WHERE TO CHANGE
  What a request kind does ...................... the `kind == "call"` branch and the exec path
  What a cell reports ........................... the final `emit({...})` payload (+
                                                 protocol.py's CellReply, then bump PROTOCOL)
  The bad_args answer ........................... `_bad_args` and the signature bind
  The observation log ........................... KERNEL_OBS_LOG append near `emit`
"""
from __future__ import annotations

from .protocol import PROTOCOL

# --- child side 1/4: DRIVER, the cell loop (stdin JSON in, `@@K@@` line out) ---------------
DRIVER = r'''
import ast, contextlib, difflib as _edifflib, inspect as _inspect, io, json, os, signal, sys, time, traceback

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
# Per-run experiment tools: the parent ships extra verb metadata and source. They are merged
# after the built-ins so a manifest can add a name but never silently replace a gate.
try:
    for _name, _meta in (json.loads(os.environ.get("KERNEL_EXTRA_VERBS") or "{}") or {}).items():
        ns["_VERB_META"].setdefault(_name, _meta)
except Exception:
    pass


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
# Experiment tools are source, not imports: a manifest defines its function in the surviving
# namespace, exactly like a prelude helper, so a run can add an action without editing bench.
_extra = os.environ.get("KERNEL_EXTRA_PRELUDE")
if _extra:
    exec(_extra, ns)
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


_USE_FALLBACK = "call help(tool) for the signature and the accepted arguments"
_USE_BY_CODE = {
    "outside_root": "typed editors are workspace-only; use exec(shell=...) for a scratch path",
    "not_found": "map(path) lists files; search(pattern) finds text",
    "unknown_verb": "help() lists every verb; help(name) matches a name",
    "bad_arguments": "call help(tool) for the accepted keywords and signature",
}


def _error_shape(value, tool, signature=None):
    # The one refusal shape, applied at the boundary every typed call goes through. A verb may
    # answer a bare {code, path} or put `reason` at the top level for python callers; the model
    # only ever sees {ok:false, kind, error:{code,message,use,signature}, path?}.
    error = value.get("error") if isinstance(value.get("error"), dict) else {}
    kind = str(value.get("kind") or "")
    if not kind or kind == tool:
        kind = tool + "_failed"
    code = str(error.get("code") or value.get("code") or kind)
    message = str(error.get("message") or value.get("reason") or value.get("message") or code)
    use = str(error.get("use") or value.get("use") or _USE_BY_CODE.get(code) or _USE_FALLBACK)
    shaped = {"code": code, "message": message, "use": use}
    for key in ("signature", "valid", "available", "argument"):
        if error.get(key) is not None:
            shaped[key] = error[key]
        elif value.get(key) is not None:
            shaped[key] = value[key]
    if signature and not shaped.get("signature"):
        shaped["signature"] = "%s%s" % (tool, signature)
    out = {key: item for key, item in value.items()
           if key not in ("error", "reason", "use", "code", "message")}
    out["ok"] = False
    out["kind"] = kind
    out["error"] = shaped
    if out.get("path") is None and error.get("path") is not None:
        out["path"] = error["path"]
    return out


class _BadArg(Exception):
    def __init__(self, name, value, expected):
        self.name = name
        self.value = value
        self.expected = expected


def _scalar(value, kind, name):
    # Coerce/validate the scalar types a schema declares. This is the boundary, not a verb:
    # every typed call gets the same answer for a bad integer/number/boolean/string.
    if value is None:
        return value
    if kind == "integer":
        if isinstance(value, bool):
            raise _BadArg(name, value, "an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip(), 10)
            except (TypeError, ValueError):
                pass
        raise _BadArg(name, value, "an integer")
    if kind == "number":
        if isinstance(value, bool):
            raise _BadArg(name, value, "a number")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                number = float(value.strip())
            except (TypeError, ValueError):
                raise _BadArg(name, value, "a number") from None
            return int(number) if number.is_integer() else number
        raise _BadArg(name, value, "a number")
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("true", "1", "yes", "on"):
                return True
            if text in ("false", "0", "no", "off"):
                return False
        raise _BadArg(name, value, "a boolean")
    if kind == "string":
        if isinstance(value, str):
            return value
        raise _BadArg(name, value, "a string")
    return value


def _coerce(value, schema, name):
    # Recursively coerce properties, array items and oneOf/anyOf branches. An array keeps a
    # string when its items are strings: exec(argv="...") is the verb's own shell-string path.
    if not isinstance(schema, dict):
        return value
    branches = schema.get("oneOf") or schema.get("anyOf")
    if branches:
        last = None
        for branch in branches:
            try:
                return _coerce(value, branch, name)
            except _BadArg as exc:
                last = exc
        if last is not None:
            raise last
        return value
    kind = schema.get("type")
    if kind == "object" and isinstance(value, dict):
        props = schema.get("properties") or {}
        for key in list(value):
            if key in props:
                value[key] = _coerce(value[key], props[key], key)
        return value
    if kind == "array":
        items = schema.get("items") if isinstance(schema.get("items"), dict) else None
        if isinstance(value, (list, tuple)):
            if items is None:
                return list(value)
            return [_coerce(item, items, name) for item in value]
        if isinstance(value, str):
            if isinstance(items, dict) and items.get("type") == "string":
                return value
            raise _BadArg(name, value, "a list")
        raise _BadArg(name, value, "a list")
    return _scalar(value, kind, name)


def _coerce_args(tool, args, meta, signature):
    schema = (meta.get(tool) or {}).get("schema") or {}
    props = schema.get("properties") or {}
    coerced = dict(args)
    for key, item in list(coerced.items()):
        if key in props:
            coerced[key] = _coerce(item, props[key], key)
    return coerced

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
        ns["_DUPE_NOTED"] = set()
        ns["_CHECK_NOTED"] = set()
        ns["_STATUS_NOTED"] = set()
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
    # Parent-side observations (a check failure's full output) are queued with the NEXT
    # request, so their index is deterministic: `_OBS` grows by exactly one per entry before
    # the cell's own append. Persist them too, so a restart keeps the same numbering.
    _extra_obs = request.get("extra_obs") or []
    if isinstance(_extra_obs, list) and _extra_obs:
        _log = ns.setdefault("_OBS", [])
        for _entry in _extra_obs:
            if not isinstance(_entry, dict):
                continue
            _log.append(_entry)
            if _OBS_PATH:
                try:
                    with open(_OBS_PATH, "a", encoding="utf-8") as _fh:
                        _fh.write(json.dumps(_entry) + "\n")
                except OSError:
                    pass
    _arm(budget)
    try:
        sys.stdin = io.StringIO("")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if kind == "call":
                tool = str(request.get("tool") or "")
                meta = ns.get("_VERB_META") or {}
                fn_name = (meta.get(tool) or {}).get("function") or tool
                fn = ns.get(fn_name)
                call_args = request.get("args") or {}
                signature = None
                if not callable(fn):
                    # An unknown name is the same class of mistake as a bad keyword: a refusal
                    # with the near names, never a NameError traceback.
                    known = sorted(name for name in meta
                                   if callable(ns.get((meta[name].get("function") or name))))
                    near = _edifflib.get_close_matches(tool, known, n=3, cutoff=0.3)
                    value = {"ok": False, "kind": "wrong_call",
                             "error": {"code": "unknown_verb",
                                       "message": "no verb named %r" % tool,
                                       "use": "help() lists every verb",
                                       "available": near or known}}
                else:
                    signature = _inspect.signature(fn)
                    try:
                        signature.bind(**call_args)
                    except TypeError as exc:
                        # A bare "see help()" cost repeated retries in a long run: name the
                        # accepted keywords in the answer itself, before the model spends a turn
                        # asking for the signature it already has.
                        names = [name for name, param in signature.parameters.items()
                                 if param.kind not in (_inspect.Parameter.VAR_POSITIONAL,
                                                       _inspect.Parameter.VAR_KEYWORD)]
                        value = {"ok": False, "kind": "bad_args",
                                 "error": {"code": "bad_arguments",
                                           "message": str(exc),
                                           "signature": "%s%s" % (tool, signature),
                                           "valid": names,
                                           "use": "call %s%s; valid keywords: %s"
                                                  % (tool, signature, ", ".join(names))}}
                    else:
                        try:
                            call_args = _coerce_args(tool, call_args, meta, signature)
                            value = fn(**call_args)
                        except _BadArg as exc:
                            names = [name for name, param in signature.parameters.items()
                                     if param.kind not in (_inspect.Parameter.VAR_POSITIONAL,
                                                           _inspect.Parameter.VAR_KEYWORD)]
                            value = {"ok": False, "kind": "bad_args", "argument": exc.name,
                                     "error": {"code": "bad_arguments",
                                               "message": "%s must be %s, got %r"
                                                          % (exc.name, exc.expected, exc.value),
                                               "signature": "%s%s" % (tool, signature),
                                               "valid": names,
                                               "argument": exc.name,
                                               "use": "call %s%s; %s must be %s"
                                                      % (tool, signature, exc.name, exc.expected)}}
                        except _CellTimeout:
                            raise
                        except Final:
                            raise
                        except BaseException as exc:
                            # A verb that raised is a tool bug, never a successful observation:
                            # the outer cell handler would otherwise leave result=None -> ok.
                            message = (str(exc).strip().splitlines()
                                       or [type(exc).__name__])[0]
                            value = {"ok": False, "kind": "tool_crashed",
                                     "error": {"code": "internal_error",
                                               "message": message,
                                               "use": "the verb raised - this is a tool bug, not your arguments"}}
                if isinstance(value, dict) and value.get("ok") is False:
                    # One refusal shape for every verb, applied where every typed call lands.
                    value = _error_shape(value, tool,
                                         str(signature) if signature is not None else None)
                if isinstance(value, dict):
                    if value.get("ok") is False:
                        rejected = True
                    if value.get("stdout"):
                        out.write(str(value.get("stdout")))
                    if value.get("stderr"):
                        err.write(str(value.get("stderr")))
                    if value.get("kind") in ("file", "files", "matches", "search_counts", "search_sections", "verbs", "diff", "outline", "outlines", "edits", "map", "maps", "recall", "guide", "guides", "skill", "skills", "status", "obs") or str(value.get("kind") or "").startswith("git_"):
                        out.write(str(value.get("text") or ""))
                    if value.get("hunk"):
                        out.write(str(value.get("hunk")))
                    if value.get("kind") in ("command", "commands"):
                        summary = {k: v for k, v in value.items()
                                   if k in ("ok", "kind", "count", "exit_code", "results", "note",
                                            "argv", "command", "shown", "total", "offset", "unit",
                                            "truncated")}
                        summary["stdout_chars"] = len(str(value.get("stdout") or ""))
                        summary["stderr_chars"] = len(str(value.get("stderr") or ""))
                    else:
                        summary = {k: v for k, v in value.items()
                                   if k not in ("stdout", "stderr", "text", "raw", "files", "hunk")}
                        if value.get("kind") == "matches":
                            # The stdout text is the delivery; keep the JSON envelope to
                            # counts and paging so it does not restate the hit table.
                            summary.pop("matches", None)
                        if value.get("kind") in ("outline", "outlines", "maps"):
                            summary["shown"] = len(value.get("entries") or value.get("files") or [])
                            summary.pop("entries", None)
                            summary.pop("files", None)
                        if value.get("kind") in ("recall", "guide", "guides"):
                            summary.pop("items", None)
                            summary.pop("modules", None)
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
        # A dict answer travels through the existing `final` field; the parent's
        # handoff.split renders it and keeps the structured fields on the Cell.
        final = f.value if isinstance(f.value, (str, dict)) else repr(f.value)
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
