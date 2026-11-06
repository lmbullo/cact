"""index: the contract index reaches an agent, and the map CLI agrees with it.

WHAT IT IS
  Drives bench/agent/headers.py the way a kernel cell does: `index(ROOT)` must contain the
  modules an agent is told to read, `guide()` must page with an offset, a topic query must
  name the owning module, an unknown module must be a miss and not an exception, and a known
  module must read back whole. Then `python3 bench/map.py --check` must exit clean.

ENTRY POINTS
  run(check)   called by bench/smoke.py::main after helpers

INVARIANTS
  - One example path is asserted (`bench/agent/kernel/prelude.py`): if the kernel package is
    renamed or re-split, this check moves with it, because the index is how an agent finds
    the action space.
  - The map CLI is invoked in-process with a redirected stdout, so no child and no shell.

WHERE TO CHANGE
  The indexed roots or skip rules ..... headers.SKIP_DIRS / LANGUAGES (not this file)
  The example query or module ......... this file's checks
"""
from __future__ import annotations

import contextlib
import io

import bench.agent.headers as headers
import bench.map as map_cli
from bench.smoke_support import ROOT


def run(check):
    """The header index reaches an agent (`guide`), pages truthfully, and agrees with map.py."""
    # --- the contract index: every module explains itself --------------------------------
    rows = headers.index(ROOT)
    paged = headers.guide(ROOT, query="read", limit=1, offset=1)
    check("headers: guide(query) pages with offset",
          paged.get("shown") == 1 and paged.get("offset") == 1
          and (paged.get("total") or 0) >= 2,
          str(paged)[:240])

    check("headers: the index reaches the agent",
          any(r["path"] == "bench/agent/kernel/prelude.py" for r in rows),
          str([r["path"] for r in rows[:3]]))
    gaps = headers.problems(ROOT)
    check("headers: every module header is complete", not gaps, str(gaps[:3]))
    hit = headers.guide(ROOT, query="check plan", limit=3)
    check("headers: a query names the owning module",
          bool(hit["modules"]) and hit["modules"][0]["path"] == "bench/agent/check_plan.py",
          str([r["path"] for r in hit["modules"]]))
    check("headers: an unknown module is a miss, not an exception",
          headers.guide(ROOT, path="no/such.py")["modules"] == [])
    check("headers: one module reads back whole", "ENTRY POINTS" in headers.guide(ROOT, path="bench/map.py")["text"])
    with contextlib.redirect_stdout(io.StringIO()):
        quiet = map_cli.main(["--check", "--json"])
    check("map: --check is clean", quiet == 0)
