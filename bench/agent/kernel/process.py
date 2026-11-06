"""process: Kernel, the parent-side subprocess supervisor and its one Cell pipeline.

WHAT IT IS
  `Kernel` spawns the child (plain `python3 -u -c DRIVER`, or the same under bwrap with a
  read-only `/`), supervises the per-cell deadline, restarts it when it dies, hangs or
  desynchronises, reaps the process groups that exec()/run() started, and returns a `Cell`.
  `Kernel.run(code)` and `Kernel.call(tool, args)` are two requests through the one
  `_execute` pipeline, so a python cell and a typed verb share journal, coverage and replay.

ENTRY POINTS
  Kernel(root, timeout=15.0, writable=False, prelude="off"|"all"|"mut",
         allow_run=False, allow_shell=None, supervise=True)
    .start()                            spawn the child (lazy: run()/call() start it)
    .run(code, check_cache=None)        one python cell -> Cell
    .call(tool, args, check_cache=None) one typed verb -> the same Cell pipeline
    .stop()                             SIGKILL the group, reap orphans, drop the obs log
  The child's environment is the wire: KERNEL_PRELUDE, KERNEL_RUN_PRELUDE, KERNEL_HEADERS,
  KERNEL_VERBS, KERNEL_SHELL, KERNEL_SUPERVISE, KERNEL_OBS_LOG, KERNEL_ORPHAN_JOURNAL.

INVARIANTS
  - A cell that overruns `timeout`, dies, or emits a non-`@@K@@` line is killed and restarted
    with an EMPTY namespace, and the model is told so. Never pretend state survived.
    `supervise=False` disables the in-child alarm (legacy behaviour).
  - The observation log is the one thing that survives: it is appended to KERNEL_OBS_LOG and
    reloaded by the new child, so recall() works after a crash. `stop()` deletes it;
    `_restart()` keeps it.
  - exec()/run() children run in their own session, so `_kill_group()` cannot reach them.
    `_reap_orphans()` reads the journal and kills those groups identity-checked (pid + /proc
    start id) before a new child starts. Removing it leaks a test server, browser or build.
  - The protocol is versioned: the first message of a cell is compared with PROTOCOL; a
    mismatch restarts the child and returns an error Cell rather than misreading a stale one.
  - `run()` refuses code over MAX_CODE characters instead of letting the child choke.

WHERE TO CHANGE
  Timeout/crash handling ....................... `_execute`, `_restart`, `_kill_group`,
                                                 `_reap_orphans`
  The sandbox (plain vs bwrap) ................. `_argv`, `start`
  What the child is told ....................... the `env=` block in `start`
  What a request carries ....................... protocol.CellRequest / `run` / `call`
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
from pathlib import Path

from ..verbs import VERBS
from .driver import DRIVER
from .prelude import HEADERS_SOURCE, PRELUDE
from .protocol import (
    CELL_TIMEOUT,
    MARKER,
    MAX_CODE,
    PROTOCOL,
    Cell,
    CellReply,
    CellRequest,
    ExtendNotice,
    _RESTART_DESYNC,
    _RESTART_DIED,
)
from .run_prelude import RUN_PRELUDE

# --- parent side: supervision, protocol and the Cell the loops consume ---------------------


class Kernel:
    """The parent-side supervisor of one kernel child.

    Spawns the child (bubblewrapped unless `allow_run`), sends one request line per cell, parses
    the `@@K@@` reply into a `Cell`, enforces the per-cell deadline, restarts a dead or stale
    child, and reaps the process groups its children started. `writable` decides whether the
    file verbs may commit; `prelude` selects which helpers the child has.
    """

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
        """Spawn the child in its own session; creates the obs log and the orphan journal."""
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
        """Kill the child and every process group its cells started (`keep_history` keeps _OBS)."""
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
        """One python cell -> one Cell; restarts the child on a timeout, a crash or a stale protocol."""
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
