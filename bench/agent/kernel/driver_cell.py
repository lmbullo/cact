"""driver_cell: cell outcome, exception handling and the reply — DRIVER fragment 4/4.

WHAT IT IS
  Raw child source `_CELL`: the `except Final`/`_CellTimeout`/`BaseException` arms that close the
  `try:` opened in `_LOOP`, then the changed/delta/obs bookkeeping and the `@@K@@` reply emit.
  driver.py concatenates it last; it starts with the continuation of the loop's `try`.

ENTRY POINTS
  _CELL   source text; inside: the except arms, changed/delta computation, obs append, emit

INVARIANTS
  fragment, not an API: the outcome fields (ok/stdout/stderr/result/final/timeout/failed/
  rejected/answer/changed/deltas) and the mutation-gate refusal are byte-identical. It must
  follow `_LOOP` directly: its first lines are that file's `except` clauses.

WHERE TO CHANGE
  timeout/crash/mutation-gate handling ... the except arms
  changed/delta and obs logging ......... the bookkeeping block
  the wire reply ........................ the final emit
"""
_CELL = r'''    except Final as f:
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
'''
