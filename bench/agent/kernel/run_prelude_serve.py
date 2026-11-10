"""run_prelude_serve: serve() state, probe and text — RUN_PRELUDE fragment 7/8.

WHAT IT IS
  Raw child source `_SERVE`: the read half of `serve()` — where the state dir is (_serve_state), reading
  server.json and the HTTP probe (_serve_get, _serve_read_json, _serve_facts), liveness and worker probes
  (_serve_alive, _serve_ppid, _serve_hosts_worker), the text and refuse envelopes (_serve_text,
  _serve_result, _serve_refuse), the log tail and the bun lookup.

ENTRY POINTS
  _SERVE   source text; inside: _SERVE_ACTS, _SERVE_TIMEOUT, _serve_state, _serve_get, _serve_read_json,
           _serve_alive, _serve_ppid, _serve_hosts_worker, _serve_facts, _serve_text, _serve_result,
           _serve_refuse, _serve_log_tail, _serve_which_bun

INVARIANTS
  state root: CODE_TUI_STATE_ROOT, else $XDG_STATE_HOME/code-tui, else ~/.local/state/code-tui, then
  workspaces/<sha256(resolved cwd)[:16]> — the same formula launcher.ts, server.ts and stop.ts use.
  read-only: status/logs never mutate, and a probe failure is a fact, not a raise.

WHERE TO CHANGE
  state root and server.json ... _serve_state, _serve_read_json, _serve_facts   envelopes ... _serve_text,
  _serve_result, _serve_refuse   log tail ... _serve_log_tail
"""
_SERVE = r"""# --- serve(): find, read, start and stop the TUI server for this workspace -----------------
_SERVE_ACTS = ("status", "logs", "start", "stop")
_SERVE_TIMEOUT = 2.0


def _serve_state():
    # The SAME formula launcher.ts, server.ts and stop.ts use: CODE_TUI_STATE_ROOT, else
    # $XDG_STATE_HOME/code-tui, else ~/.local/state/code-tui, then workspaces/<hash>, where the
    # hash is sha256 of the resolved workspace, first 16 hex. Change it in all four places or
    # the pieces stop finding each other.
    space = _ros.path.realpath(_RUN_ROOT)
    root = _ros.environ.get("CODE_TUI_STATE_ROOT") or ""
    if root.strip():
        root = _ros.path.realpath(_ros.path.expanduser(root.strip()))
    else:
        base = _ros.environ.get("XDG_STATE_HOME") or ""
        base = (_ros.path.expanduser(base.strip()) if base.strip()
                else _ros.path.join(_ros.path.expanduser("~"), ".local", "state"))
        root = _ros.path.join(base, "code-tui")
    digest = _rhash.sha256(space.encode("utf-8")).hexdigest()[:16]
    return space, _ros.path.join(root, "workspaces", digest)


def _serve_get(url, timeout=None):
    # A short one-shot GET. Refused, timed out or not-JSON all answer None, never an exception:
    # "is it running" is a status question, so the answer is `healthy: false`.
    import urllib.request as _urlreq
    try:
        with _urlreq.urlopen(url, timeout=float(timeout or _SERVE_TIMEOUT)) as resp:
            value = _json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _serve_read_json(path):
    try:
        with _rio.open(path, encoding="utf-8", errors="replace") as fh:
            value = _json.load(fh)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _serve_alive(pid):
    if not pid:
        return False
    try:
        _ros.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def _serve_ppid(pid):
    try:
        with _rio.open("/proc/%s/stat" % int(pid), encoding="utf-8", errors="replace") as fh:
            return int(fh.read().rsplit(")", 1)[1].split()[1])
    except Exception:
        return 0


def _serve_hosts_worker(pid):
    # This worker, and every parent above it, includes the server that spawned it. SIGTERM to
    # that pid kills the run mid-answer, so `start`/`stop` refuse it instead.
    target = int(pid or 0)
    cur, seen = _ros.getpid(), set()
    while cur > 1 and cur not in seen:
        if cur == target:
            return True
        seen.add(cur)
        cur = _serve_ppid(cur)
    return False


def _serve_facts(workspace, state_dir, probe=True):
    # One read of server.json, plus /health and /api/snapshot when `probe` is on, folded into
    # what serve() reports. `logs` passes probe=False: tailing a file must not wait on HTTP.
    server_file = _ros.path.join(state_dir, "server.json")
    present = _ros.path.isfile(server_file)
    info = (_serve_read_json(server_file) if present else None) or {}
    try:
        pid = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    try:
        port = int(info.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    health = _serve_get("http://127.0.0.1:%s/health" % port) if (probe and port) else None
    if health and str(health.get("workspace") or workspace) != workspace:
        health = None  # a recycled port answering for another workspace is not this server
    snap = _serve_get("http://127.0.0.1:%s/api/snapshot" % port) if (probe and port) else None
    tasks = (snap or {}).get("tasks")
    tasks = tasks if isinstance(tasks, list) else []
    live = [item for item in tasks if isinstance(item, dict)]
    source = health or info
    specs = source.get("checks")
    specs = specs if isinstance(specs, list) else []
    changed = source.get("changed")
    try:
        concurrency = int(source.get("concurrency") or 0)
    except (TypeError, ValueError):
        concurrency = 0
    return {
        "workspace": workspace,
        "state_dir": state_dir,
        "server_json": present,
        "pid": pid,
        "port": port,
        "alive": _serve_alive(pid),
        "healthy": health is not None,
        "model": str(source.get("model") or ""),
        "effort": str(source.get("effort") or ""),
        "concurrency": concurrency,
        "checks": [str(item.get("name") or item.get("cmd") or "")
                   for item in specs if isinstance(item, dict)],
        "stale": bool(source.get("stale")),
        "changed": [str(item) for item in (changed if isinstance(changed, list) else [])],
        "queue": {"running": sum(1 for item in live if item.get("status") == "running"),
                  "queued": sum(1 for item in live if item.get("status") == "queued")},
    }


def _serve_text(facts):
    # The card a reader needs: where the state root is, whether it answers, and what it holds.
    return "\n".join([
        "code-tui server for %s" % facts["workspace"],
        "  state   %s" % facts["state_dir"],
        "  server  json=%s pid=%s alive=%s port=%s healthy=%s"
        % (facts["server_json"], facts["pid"] or "-", facts["alive"],
           facts["port"] or "-", facts["healthy"]),
        "  model   %s effort=%s concurrency=%s"
        % (facts["model"] or "-", facts["effort"] or "-", facts["concurrency"] or "-"),
        "  checks  %s" % (", ".join(facts["checks"]) or "-"),
        "  source  stale=%s changed=%s"
        % (facts["stale"], ", ".join(facts["changed"]) or "-"),
        "  queue   %s running, %s queued" % (facts["queue"]["running"], facts["queue"]["queued"]),
    ])


def _serve_result(facts, action, **extra):
    out = dict(facts)
    out.update({"ok": True, "kind": "serve", "action": action})
    out.update(extra)
    out.setdefault("text", _serve_text(facts))
    return _Result(out)


def _serve_refuse(action, facts, code, message, use, **extra):
    out = dict(facts or {})
    out.update({"ok": False, "kind": "serve", "action": action})
    out.update(extra)
    out["error"] = {"code": code, "message": message, "use": use}
    return _Result(out)


def _serve_log_tail(state_dir, lines=12):
    try:
        with _rio.open(_ros.path.join(state_dir, "server.log"), encoding="utf-8",
                       errors="replace") as fh:
            return fh.read().splitlines()[-max(1, int(lines)):]
    except Exception:
        return []


def _serve_which_bun():
    for name in ("bun",):
        found = _rshutil.which(name)
        if found:
            return found
    for path in (_ros.path.expanduser("~/.bun/bin/bun"), "/usr/local/bin/bun",
                 "/opt/homebrew/bin/bun"):
        if _ros.path.isfile(path) and _ros.access(path, _ros.X_OK):
            return path
    return ""


"""
