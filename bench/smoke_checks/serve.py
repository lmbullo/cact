"""serve: the TUI-server verb: where the state root is, what server.json says, when it refuses.

WHAT IT IS
  One fixture state root and one kernel child, five cases:
    no server.json            -> ok:false, code "no_server", a `use` naming start
    a live pid + /health      -> status parses pid, port, healthy, model, effort, concurrency, checks, stale, changed, queue
    server.log                -> logs tails the last `tail` lines and reports shown/total
    a running task            -> stop refuses with "busy" and the live pid is untouched
    the worker's own ancestor -> stop refuses because that pid hosts this worker

ENTRY POINTS
  run(check)   called by bench/smoke.py::main after revert; builds and drops its own fixtures

INVARIANTS
  no case spawns a real server: status/logs only read and every stop/start case asserts a
  refusal; /health is a fake in-process HTTP server returning a canned payload.
  the state root comes from CODE_TUI_STATE_ROOT through Kernel(env=...); the expected dir is
  <root>/workspaces/<sha256(realpath(cwd))[:16]>.
  the live pid is a sibling sleeping process, never this process.

WHERE TO CHANGE
  state-dir formula .. kernel/run_prelude.py::_serve_state (launcher.ts, server.ts, stop.ts carry
  the same formula);  refusal codes .. no_server, no_log, busy, unknown_action;  fields .. _serve_facts
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from bench.smoke_support import kernel_for

HEALTH = {
    "ok": True,
    "pid": 0,
    "port": 0,
    "model": "deepseek-flash",
    "effort": "low",
    "concurrency": 2,
    "checks": [{"name": "lint", "cmd": "ruff check ."},
               {"name": "test", "cmd": "pytest -q"}],
    "stale": True,
    "changed": ["src/server.ts"],
}
TASKS = [{"id": "a", "status": "running"},
         {"id": "b", "status": "queued"},
         {"id": "c", "status": "queued"},
         {"id": "d", "status": "done"}]


def _state_dir(root: Path, workspace: Path) -> Path:
    """The documented state dir: <root>/workspaces/<sha256(realpath(cwd))[:16]>."""
    digest = hashlib.sha256(os.path.realpath(workspace).encode("utf-8")).hexdigest()[:16]
    return Path(os.path.realpath(root)) / "workspaces" / digest


def _fake(payload: dict) -> HTTPServer:
    """A local HTTP server answering /health and /api/snapshot with canned JSON."""

    class Handler(BaseHTTPRequestHandler):
        """The two endpoints serve() reads; anything else is a 404."""

        def do_GET(self):  # noqa: N802 - the BaseHTTPRequestHandler spelling
            """Answer the canned payload for /health and /api/snapshot."""
            if self.path == "/health":
                body = payload
            elif self.path == "/api/snapshot":
                body = {"type": "snapshot", "server": payload, "tasks": TASKS}
            else:
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            """Stay quiet: the smoke output is the check lines, not access logs."""
            return

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _ok(cell) -> bool:
    """The envelope's own verdict: the data half carries fields, not `ok`."""
    return bool((cell.envelope or {}).get("ok"))


def _kind(cell) -> str:
    """The envelope's kind (`serve`, or `serve_failed` for a refusal)."""
    return str((cell.envelope or {}).get("kind") or "")


def _data(cell) -> dict:
    """The structured half of one typed reply: what an agent reads, not the printout."""
    return (cell.envelope or {}).get("data") or {}


def _error(cell) -> dict:
    """The structured refusal of one typed reply, or {}."""
    return (cell.envelope or {}).get("error") or {}


def run(check):
    """The five serve cases over a fixture state root, its fake /health and a sleeping pid."""
    root = Path(tempfile.mkdtemp(prefix="cact-serve-root-"))
    workspace = Path(tempfile.mkdtemp(prefix="cact-serve-ws-"))
    state = _state_dir(root, workspace)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    srv = None
    try:
        with kernel_for(workspace, env={"CODE_TUI_STATE_ROOT": str(root)}) as kernel:
            cell = kernel.call("serve", {"action": "status"})
            data, error = _data(cell), _error(cell)
            check("serve: no server.json is no_server and names start",
                  not _ok(cell) and error.get("code") == "no_server"
                  and "start" in str(error.get("use"))
                  and data.get("server_json") is False,
                  "%r | %r" % (data, error))
            check("serve: the state dir is the launcher's formula",
                  data.get("state_dir") == str(state),
                  "%r != %r" % (data.get("state_dir"), str(state)))

            payload = dict(HEALTH, workspace=str(os.path.realpath(workspace)))
            srv = _fake(payload)
            port = srv.server_address[1]
            payload.update({"pid": sleeper.pid, "port": port})
            state.mkdir(parents=True, exist_ok=True)
            (state / "server.json").write_text(
                json.dumps({"workspace": str(os.path.realpath(workspace)),
                            "stateDir": str(state), "pid": sleeper.pid, "port": port}),
                encoding="utf-8")
            (state / "server.log").write_text(
                "".join("line %d\n" % n for n in range(1, 11)), encoding="utf-8")

            cell = kernel.call("serve", {"action": "status"})
            data = _data(cell)
            check("serve: status parses server.json, /health and /api/snapshot",
                  _ok(cell) and _kind(cell) == "serve"
                  and data.get("pid") == sleeper.pid and data.get("port") == port
                  and data.get("healthy") is True and data.get("alive") is True
                  and data.get("model") == "deepseek-flash"
                  and data.get("effort") == "low" and data.get("concurrency") == 2
                  and data.get("checks") == ["lint", "test"]
                  and data.get("stale") is True
                  and data.get("changed") == ["src/server.ts"]
                  and data.get("queue") == {"running": 1, "queued": 2},
                  json.dumps(data)[:400])

            cell = kernel.call("serve", {"action": "logs", "tail": 3})
            data, page = _data(cell), (cell.envelope or {}).get("page") or {}
            check("serve: logs tails the last `tail` lines of server.log",
                  _ok(cell) and data.get("lines") == ["line 8", "line 9", "line 10"]
                  and page.get("shown") == 3 and page.get("total") == 10,
                  "%r | %r" % (data.get("lines"), page))

            cell = kernel.call("serve", {"action": "stop"})
            data, error = _data(cell), _error(cell)
            check("serve: stop while a task is running refuses and signals nothing",
                  not _ok(cell) and error.get("code") == "busy"
                  and "running" in str(error.get("message"))
                  and "serve(action='status')" in str(error.get("use"))
                  and sleeper.poll() is None,
                  "%r | alive=%s" % (error, sleeper.poll() is None))

            cell = kernel.call("serve", {"action": "start"})
            error = _error(cell)
            check("serve: start while a task is running refuses too",
                  not _ok(cell) and error.get("code") == "busy", "%r" % (error,))

            (state / "server.json").write_text(
                json.dumps({"pid": os.getpid(), "port": 0}), encoding="utf-8")
            cell = kernel.call("serve", {"action": "stop"})
            error = _error(cell)
            check("serve: stop refuses the server hosting this worker",
                  not _ok(cell) and error.get("code") == "busy"
                  and "hosting this worker" in str(error.get("message")),
                  "%r" % (error,))
    finally:
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        sleeper.kill()
        sleeper.wait(timeout=5)
