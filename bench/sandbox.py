"""The sandbox suite: one deliberately mutable feature task for studying the whole loop.

WHAT IT IS
  A single eval that exercises the path a feature request actually takes: read the
  workspace's current contract, inspect the package, change two files through one typed
  edit batch, run the project's test command, and commit the result with typed git. The
  task is intentionally one editable fixture; change its prompt, docs, source or grade to
  target whatever tool-use question is next (discovery, planning, repair, git, ...).

ENTRY POINTS
  SANDBOX / TASKS / by_id(task_id)
  build()                 create sandbox_fixtures/ when missing
  prepare(workspace)      create the git repository the task commits into
  grade_feature(answer, run)

INVARIANTS
  - The hidden tests live under `sandbox_hidden/`, outside the copied workspace. A model
    can see failures through the goal check but cannot read or edit the oracle.
  - The docs are the source of truth: `docs/CURRENT.md` holds the threshold and zone table;
    the answer is not derivable from the visible tests or a model's priors.
  - The fixture is copied per run by run.py; prepare() only runs on that copy, never on the
    committed fixture.
  - This suite is not part of the pass/fail benchmark table; it is a probe. Run it with
    `python3 bench/run.py --suite sandbox --mode state --tasks S1_feature --traces`.

WHERE TO CHANGE
  The feature and its docs .... S1_FEATURE, build() and sandbox_hidden/test_quote.py
  What the loop must do ....... grade_feature(), prepare()
  The fixture copy lifecycle .. bench/run.py::workspace() (calls task.prepare)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from .agent.check import Check, run_check
from .harness import HarnessTask

HERE = Path(__file__).parent
SANDBOX = HERE / "sandbox_fixtures"
HIDDEN = HERE / "sandbox_hidden"
DOC_PATH = "docs/CURRENT.md"
FREE_SHIPPING_CENTS = 4500
ZONE_SURCHARGE = {"north": 0, "south": 150, "west": 275}


def _write(path: Path, text: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build() -> None:
    _write(SANDBOX / ".gitignore", "__pycache__/\n*.pyc\n")
    _write(SANDBOX / "README.md", """# Tiny pricing workspace

The package is small on purpose. `docs/CURRENT.md` is the current contract; the visible
tests cover the old `shipping()` helper only.
""")
    _write(SANDBOX / "docs" / "CURRENT.md", """# Current pricing contract

This file is the source of truth for this checkout, not a model's training data.

- Base charge: 400 cents.
- Free shipping threshold: 4500 cents (the total is the order total, before shipping).
- Zone surcharge, in cents: north 0, south 150, west 275.
- `quote(total_cents, zone)` returns 0 when `total_cents >= 4500`; otherwise it returns
  the base charge plus the zone surcharge. An unknown zone raises `ValueError`.
- Keep the existing `shipping(weight_g, zone)` behavior unchanged.
""")
    _write(SANDBOX / "pricing" / "__init__.py", """from .rates import shipping

__all__ = ["shipping"]
""")
    _write(SANDBOX / "pricing" / "rates.py", """ZONE_SURCHARGE = {"north": 0, "south": 150}
BASE_CHARGE = 400


def shipping(weight_g, zone):
    if zone not in ZONE_SURCHARGE:
        raise ValueError("unknown zone: %r" % (zone,))
    return BASE_CHARGE + ZONE_SURCHARGE[zone] + max(0, int(weight_g) - 500) // 100
""")
    _write(SANDBOX / "tests" / "__init__.py", "")
    _write(SANDBOX / "tests" / "test_rates.py", """import unittest

from pricing import shipping


class Visible(unittest.TestCase):
    def test_shipping_base(self):
        self.assertEqual(shipping(500, "north"), 400)

    def test_shipping_south_and_weight(self):
        self.assertEqual(shipping(700, "south"), 552)

    def test_shipping_rejects_unknown_zone(self):
        with self.assertRaises(ValueError):
            shipping(500, "east")


if __name__ == "__main__":
    unittest.main()
""")
    _write(HIDDEN / "__init__.py", "")
    _write(HIDDEN / "test_quote.py", """import unittest

from pricing import quote


class QuoteContract(unittest.TestCase):
    def test_north_charge(self):
        self.assertEqual(quote(100, "north"), 400)

    def test_south_charge(self):
        self.assertEqual(quote(100, "south"), 550)

    def test_west_charge(self):
        self.assertEqual(quote(100, "west"), 675)

    def test_free_shipping_at_threshold(self):
        self.assertEqual(quote(4500, "west"), 0)

    def test_free_shipping_above_threshold(self):
        self.assertEqual(quote(9000, "south"), 0)

    def test_unknown_zone(self):
        with self.assertRaises(ValueError):
            quote(100, "east")


if __name__ == "__main__":
    unittest.main()
""")


build()


def prepare(workspace: Path) -> None:
    """Create the repository on the per-run copy, so `git commit` is a real action."""
    for cache in workspace.rglob("__pycache__"):
        for child in cache.iterdir():
            child.unlink()
        cache.rmdir()
    env = {"GIT_AUTHOR_NAME": "sandbox", "GIT_AUTHOR_EMAIL": "sandbox@example.invalid",
           "GIT_COMMITTER_NAME": "sandbox", "GIT_COMMITTER_EMAIL": "sandbox@example.invalid"}
    commands = [
        ["git", "init", "-q"],
        ["git", "config", "user.name", "sandbox"],
        ["git", "config", "user.email", "sandbox@example.invalid"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "fixture: pricing workspace"],
    ]
    for command in commands:
        subprocess.run(command, cwd=workspace, env={**__import__("os").environ, **env},
                       check=True, capture_output=True, text=True)


def _hidden_check(workspace: Path):
    return run_check(
        f"{sys.executable} -m unittest discover -s {HIDDEN} -p 'test_*.py'",
        str(workspace), timeout=120.0,
    )


def check(workspace: Path):
    result = _hidden_check(workspace)
    if not result.ok:
        return result
    # The feature is not done until it is committed; otherwise a green hidden suite ends the
    # turn before the prompt's last instruction (the commit) is attempted.
    log = run_check("git log -1 --format=%s", str(workspace), timeout=30.0)
    subject = (log.stdout or "").strip()
    if not log.ok or subject == "fixture: pricing workspace":
        return Check(cmd="git commit", state="failed", stdout=log.stdout,
                     stderr="no commit after the fixture; commit the feature with typed git")
    return result


def _git_commit(run) -> bool:
    for turn in getattr(run, "turns", None) or []:
        for call in turn.get("calls") or []:
            if call.get("name") != "git":
                continue
            args = call.get("args") or {}
            if str(args.get("action") or "").lower() == "commit" and str(args.get("message") or "").strip():
                return True
    return False


def grade_feature(answer: str, run) -> tuple[bool, str]:
    from .harness import process

    problems = process(
        run, require=("read", "edit", "exec", "git"),
        require_edit_batch=True, no_exec_readers=True, max_calls=20,
        max_errors=0, max_repeats=1, require_path=DOC_PATH, no_python_io=True,
        first_in=("read", "outline", "map", "guide", "search"),
    )
    if not _git_commit(run):
        problems.append("did not commit with typed git(action='commit')")
    result = _hidden_check(Path(run.workspace))
    if not result.ok:
        problems.append("hidden tests failed: " + result.text(1200))
    return not problems, "; ".join(problems) or "feature implemented, tested, committed"


S1_FEATURE = HarnessTask(
    "S1_feature",
    "Read docs/CURRENT.md, then add the quote(total_cents, zone) function described there "
    "to the pricing package and export it from pricing/__init__.py. Keep the existing "
    "shipping() behavior intact. Change both files in one edit batch, run the package tests "
    "with exec, and commit the result with typed git(action='commit'). Do not modify the "
    "visible tests or use shell readers.",
    grade_feature,
    "feature: docs -> edit batch -> tests -> typed git commit",
    source=SANDBOX,
    mutates=True,
    check=check,
    prepare=prepare,
)


TASKS: list[HarnessTask] = [S1_FEATURE]


def by_id(task_id: str) -> HarnessTask:
    for task in TASKS:
        if task.id == task_id:
            return task
    raise KeyError(task_id)
