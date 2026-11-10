"""driver_loop: the request loop and typed-call dispatch — DRIVER fragment 3/4.

WHAT IT IS
  Raw child source `_LOOP`: reads one JSON request per line, seeds the per-cell state, dispatches
  `kind == "call"` (unknown verb, signature bind, coercion, tool exceptions) into a result, and
  executes a python cell's AST. driver.py concatenates it before the outcome fragment.
  The fragment ends inside the cell `try:`; `_CELL` begins with its `except` clauses.

ENTRY POINTS
  _LOOP   source text; inside: the top-level `for line in sys.stdin:` dispatch

INVARIANTS
  fragment, not an API: the wire fields (kind/code/args/timeout/check_cache/extra_obs) and the
  reply shapes are unchanged. The statement order is one program after concatenation; do not
  split or reorder the `try:` it opens.

WHERE TO CHANGE
  request parsing and dispatch ... the loop head and the `kind == "call"` branch
  python cell execution ......... the `else:` branch (ast.parse/exec/eval)
"""
_LOOP = r'''for line in sys.stdin:
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
                    if value.get("kind") in ("file", "files", "matches", "search_counts", "search_sections", "verbs", "diff", "outline", "outlines", "edits", "map", "maps", "recall", "guide", "guides", "skill", "skills", "status", "serve", "obs") or str(value.get("kind") or "").startswith("git_"):
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
'''
