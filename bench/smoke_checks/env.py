"""env: the environment card and task-path resolution.

WHAT IT IS
  Offline checks for `bench/agent/env.py`: the root/top probe skips generated trees, an
  existing task path is not reported, a path under a missing prefix resolves to the real
  path at this root, and an unresolvable path is marked missing rather than guessed.

ENTRY POINTS
  run(check)   called by bench/smoke.py before the kernel groups

INVARIANTS
  - A temp directory only; the git probe sees no repository and answers None.
  - The replacement path is asserted from the structured row, not parsed out of the card.

WHERE TO CHANGE
  The probe or the card ............... bench/agent/env.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from bench.agent import env


def run(check):
    """The environment probe, path resolution and the rendered card."""
    root = Path(tempfile.mkdtemp(prefix="cact-env-"))
    (root / "src").mkdir()
    (root / "src" / "x.ts").write_text("x\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "node_modules").mkdir()

    data = env.environment(root, "add tui/src/x.ts and tui/tests/")
    check("env: the root and top listing skip generated trees",
          data["root"] == str(root.resolve()) and "src/" in data["top"]
          and "node_modules/" not in data["top"], str(data["top"]))
    rows = {row["given"]: row for row in data["task_paths"]}
    check("env: a path under a missing prefix resolves to the real path",
          rows.get("tui/src/x.ts", {}).get("use") == "src/x.ts", str(rows))
    check("env: an existing path is not reported",
          env.resolve_path(root, "src/x.ts") is None)
    check("env: an unresolvable path is missing, not guessed",
          env.resolve_path(root, "nope/x.ts") == {"given": "nope/x.ts", "missing": True})
    text = env.render_environment(data)
    check("env: the card names the root and the replacement path",
          "[environment]" in text and "use src/x.ts" in text, text[:240])
