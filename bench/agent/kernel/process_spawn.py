"""process_spawn: spawn, stop and restart the kernel child — the parent-side supervisor half.

WHAT IT IS
  `SpawnMixin`: `_argv` (plain `python3 -u -c DRIVER` or the bwrap command), `start` (child
  process, obs log and orphan journal, the wired env), `_stop`/`stop`, `_kill_group` and
  `_restart`. process.py inherits it into `Kernel`, which keeps `__init__` and the public surface.

ENTRY POINTS
  SpawnMixin._argv / .start / ._stop / .stop / ._kill_group / ._restart
  used by Kernel.start()/stop(); `_stop` reaps orphan groups through process_reap.ReapMixin.

INVARIANTS
  child env = the wire: KERNEL_PRELUDE, KERNEL_RUN_PRELUDE, KERNEL_HEADERS, KERNEL_VERBS,
  KERNEL_SHELL, KERNEL_SUPERVISE, KERNEL_OBS_LOG, KERNEL_ORPHAN_JOURNAL, KERNEL_EXTRA_VERBS,
  KERNEL_EXTRA_PRELUDE, KERNEL_CHECK_COMMANDS; env= adds keys, never drops one.
  a restart keeps the observation log (recall survives); stop() deletes it. bwrap is used
  unless `allow_run`, with the same tmpfs rule. These blocks moved verbatim, only the class.

WHERE TO CHANGE
  plain vs bwrap ................ _argv
  child spawn and its env ....... start
  stop, kill group, restart ..... _stop, stop, _kill_group, _restart
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile

from ..verbs import VERBS
from .driver import DRIVER
from .prelude import HEADERS_SOURCE, PRELUDE
from .run_prelude import RUN_PRELUDE


class SpawnMixin:
    """Spawn, signal and restart the child process; one half of Kernel."""

    def _argv(self) -> list[str]:
        if self.allow_run:
            return [self._python, "-u", "-c", DRIVER]
        root = str(self.root)
        # `--tmpfs /tmp` hides a workspace that lives under /tmp: the bind below would point
        # into the empty tmpfs and every cell would die on startup. Only mask /tmp when the
        # workspace is outside it; otherwise the read-only `/` bind already carries the tree.
        in_tmp = root == "/tmp" or root.startswith(("/tmp/", "/var/tmp/", "/dev/shm/"))
        return [
            "bwrap",
            "--die-with-parent",
            "--unshare-all",
            "--ro-bind", "/", "/",
            *(["--bind", root, root] if self.writable else []),
            *([] if in_tmp else ["--tmpfs", "/tmp"]),
            "--proc", "/proc",
            "--dev", "/dev",
            "--chdir", root,
            self._python, "-u", "-c", DRIVER,
        ]

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
                **({"KERNEL_EXTRA_VERBS": json.dumps(self.extra_verbs),
                    "KERNEL_EXTRA_PRELUDE": self.extra_prelude}
                   if self.extra_verbs or self.extra_prelude else {}),
                **({"KERNEL_CHECK_COMMANDS": json.dumps(self.check_commands)}
                   if self.check_commands else {}),
                **self.env,
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
