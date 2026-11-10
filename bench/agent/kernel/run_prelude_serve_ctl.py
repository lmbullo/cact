"""run_prelude_serve_ctl: serve(), start and stop — RUN_PRELUDE fragment 8/8.

WHAT IT IS
  Raw child source `_SERVE_CTL`: `serve(action=...)`, the one typed answer to "where is the TUI server for
  this workspace, is it answering, is it stale, how do I start or stop it", plus `_serve_start`
  (detached nohup bun src/server.ts, output appended to server.log) and `_serve_stop` (SIGTERM, poll).

ENTRY POINTS
  _SERVE_CTL   source text; inside: _serve_start, _serve_stop, serve

INVARIANTS
  status/logs never mutate; start/stop refuse with busy while a task runs or the pid hosts this worker.
  an unknown action -> unknown_action, no server.json -> no_server, no log -> no_log; never raises.
  the last fragment: it ends the program, so nothing may follow it.

WHERE TO CHANGE
  the action switch ... serve   launch ... _serve_start   shutdown ... _serve_stop
"""
_SERVE_CTL = r"""def _serve_start(workspace, state_dir, facts, timeout):
    # Exactly what launcher.ts::launchDetachedServer runs: a detached, session-leading
    # `bun src/server.ts --workspace ... --state ...` whose output appends to server.log. The
    # log is opened here instead of a shell `>>`, so no shell parses the command and the
    # mutation gate has nothing to refuse.
    tui = _ros.path.join(workspace, "tui")
    entry = _ros.path.join(tui, "src", "server.ts")
    bun = _serve_which_bun()
    if not _ros.path.isfile(entry) or not bun:
        return _serve_refuse(
            "start", facts, "no_tui",
            "cannot start: %s" % ("no bun on PATH" if not bun
                                  else "no server entry at %s" % entry),
            "start the TUI from a checkout of this repo (bin/code runs tui/src/launcher.ts)")
    try:
        _ros.makedirs(state_dir, exist_ok=True)
    except OSError as exc:
        return _serve_refuse("start", facts, "spawn_failed", str(exc),
                             "check the state root in serve(action='status')")
    log = _ros.path.join(state_dir, "server.log")
    argv = ["nohup", bun, entry, "--workspace", workspace, "--state", state_dir]
    try:
        with _rio.open(log, "ab") as out:
            proc = _rsp.Popen(argv, cwd=workspace, stdin=_rsp.DEVNULL, stdout=out,
                              stderr=_rsp.STDOUT, start_new_session=True)
    except OSError as exc:
        return _serve_refuse("start", facts, "spawn_failed", str(exc),
                             "run `code` in this workspace and read the log it prints")
    _journal_orphan(proc.pid)
    deadline = _rtime.time() + max(1.0, float(timeout or 12))
    while _rtime.time() < deadline:
        facts = _serve_facts(workspace, state_dir)
        if facts["healthy"]:
            break
        _rtime.sleep(0.1)
    tail = _serve_log_tail(state_dir)
    text = _serve_text(facts)
    if not facts["healthy"]:
        text += "\n  log     %s\n%s" % (log, "\n".join("    " + line for line in tail) or "    (empty)")
    return _serve_result(facts, "start", started=facts["healthy"], log=log,
                         lines=tail, text=text)


def _serve_stop(workspace, state_dir, facts, timeout):
    pid = facts["pid"]
    if not pid or not facts["alive"]:
        return _serve_result(facts, "stop", stopped=False,
                             text="no live server for %s (pid %s)\n%s"
                                  % (workspace, pid or "-", _serve_text(facts)))
    try:
        _ros.kill(pid, _rsig.SIGTERM)
    except OSError as exc:
        return _serve_refuse("stop", facts, "not_running", str(exc),
                             "serve(action='status') re-reads server.json")
    deadline = _rtime.time() + max(1.0, float(timeout or 8))
    while _rtime.time() < deadline and _serve_alive(pid):
        _rtime.sleep(0.05)
    after = _serve_facts(workspace, state_dir)
    text = _serve_text(after)
    if after["alive"]:
        text += "\n  note    pid %s ignored SIGTERM; it is still running" % pid
    return _serve_result(after, "stop", stopped=not after["alive"], stopped_pid=pid, text=text)


def serve(action="status", tail=40, timeout=8):
    # Typed `serve`: the one answer to "where is the TUI server for this workspace, is it
    # answering, is it stale, and how do I start or stop it". status/logs never mutate.
    act = str(action or "status").strip().lower()
    if act not in _SERVE_ACTS:
        return _serve_refuse(
            act, {}, "unknown_action", "action must be status/logs/start/stop, not %r" % act,
            "serve(action='status') answers where the server is; serve(action='logs', "
            "tail=40) tails its log")
    workspace, state_dir = _serve_state()
    if act == "logs":
        facts = _serve_facts(workspace, state_dir, probe=False)
        log = _ros.path.join(state_dir, "server.log")
        try:
            with _rio.open(log, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return _serve_refuse("logs", facts, "no_log", "no server.log at %s" % log,
                                 "serve(action='start') writes one; serve(action='status') "
                                 "reports where the state dir is")
        want = max(1, min(int(tail or 40), 400))
        shown = lines[-want:]
        return _serve_result(facts, "logs", path=log, lines=shown, tail=want,
                             shown=len(shown), total=len(lines),
                             text="\n".join(shown) or "(server.log is empty)")
    facts = _serve_facts(workspace, state_dir)
    if act == "status":
        if not facts["server_json"]:
            return _serve_refuse(
                "status", facts, "no_server",
                "no server.json in %s: no code-tui server has run for %s"
                % (state_dir, workspace),
                "serve(action='start') starts one with nohup bun src/server.ts; a server "
                "started by `code` writes the same file")
        return _serve_result(facts, "status")
    if act == "stop" and not facts["server_json"]:
        # stop with nothing published is the launcher's exit 0: there is no pid to signal.
        return _serve_result(facts, "stop", stopped=False,
                             text="no server.json in %s: nothing to stop\n%s"
                                  % (state_dir, _serve_text(facts)))
    busy = []
    if facts["queue"]["running"]:
        busy.append("%s task(s) running" % facts["queue"]["running"])
    if _serve_hosts_worker(facts["pid"]):
        busy.append("pid %s is the server hosting this worker" % facts["pid"])
    if busy:
        return _serve_refuse(
            act, facts, "busy",
            "serve(%r) refused: %s" % (act, "; ".join(busy)),
            "wait for the task to finish (serve(action='status') shows the queue); a server "
            "hosting this worker cannot be restarted from inside a run")
    if act == "start":
        if facts["healthy"]:
            return _serve_result(facts, "start", started=False,
                                 text="already up\n" + _serve_text(facts))
        return _serve_start(workspace, state_dir, facts, timeout)
    return _serve_stop(workspace, state_dir, facts, timeout)
"""
