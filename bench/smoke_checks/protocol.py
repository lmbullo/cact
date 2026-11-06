"""protocol: a stale child is rejected and restarted, the current one is accepted.

WHAT IT IS
  Two fake kernels whose `_argv` is replaced by a tiny script that answers one `@@K@@` line
  with a chosen protocol number. The point is the supervision contract: a child speaking a
  different PROTOCOL is killed, restarted empty and reported as an error Cell; a child
  speaking the current one is read through the normal shape.

ENTRY POINTS
  run(check, root)   called by bench/smoke.py::main after plans (it needs the temp root)

INVARIANTS
  - The fake never runs the real DRIVER: it is a `Kernel` with `_argv` swapped, so the
    restart/kill logic under test is the production one.
  - Both kernels are stopped in a `finally`, so a failure cannot leak a child process.

WHERE TO CHANGE
  The protocol number ................... kernel/protocol.py::PROTOCOL
  The mismatch message .................. kernel/process.py::_protocol_error
"""
from __future__ import annotations

import sys

from bench.agent.kernel import PROTOCOL, Kernel


def run(check, root):
    # --- protocol: a stale child is rejected, the current one is accepted -----------------
    def _fake_kernel(protocol):
        script = (
            "import json, sys\n"
            "for line in sys.stdin:\n"
            "    json.loads(line)\n"
            "    sys.__stdout__.write('@@K@@' + json.dumps({'protocol': __P__, 'stdout': 'fake'}) + '\\n')\n"
            "    sys.__stdout__.flush()\n"
        ).replace("__P__", str(protocol))
        fake = Kernel(root, timeout=10)
        fake._argv = lambda: [sys.executable, "-u", "-c", script]
        return fake

    stale = _fake_kernel(PROTOCOL - 1)
    try:
        cell = stale.run("pass")
        check("kernel: a stale protocol is rejected and restarted",
              cell.restarted and "protocol mismatch" in cell.stderr and not cell.stdout,
              cell.stderr[:200])
    finally:
        stale.stop()
    current = _fake_kernel(PROTOCOL)
    try:
        cell = current.run("pass")
        check("kernel: the current protocol is accepted",
              cell.stdout == "fake" and not cell.restarted, f"{cell.stdout!r} {cell.stderr!r}")
    finally:
        current.stop()
