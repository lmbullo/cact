"""driver_boot: child imports, timeout signal, namespace and extras — DRIVER fragment 1/4.

WHAT IT IS
  Raw child source `_BOOT`: imports, the SIGALRM cell timeout (_extend/_arm/_disarm), the cell
  namespace `ns`, prelude/extra-prelude/run-prelude loading, `Final`/final_answer and `emit`.
  driver.py concatenates it before the coercion and loop fragments.

ENTRY POINTS
  _BOOT   source text; inside: _CellTimeout, _on_alarm, _extend, _arm, _disarm, _Result, Final,
          final_answer, emit

INVARIANTS
  fragment, not an API: `__PROTOCOL__` is still substituted once on the assembled DRIVER; the
  namespace name and the environment variable names are the wire contract.

WHERE TO CHANGE
  cell timeout and signal ... _extend, _arm, _disarm
  namespace and prelude load . ns setup, _prelude/_extra/_run_prelude blocks
  final_answer and emit ...... Final, final_answer, emit
"""
_BOOT = r'''
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


'''
