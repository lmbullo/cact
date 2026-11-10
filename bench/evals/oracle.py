"""oracle: the hidden ground truth every eval grades against, never a fixture in the workspace.

WHAT IT IS
  Small deterministic predicates over a finished workspace or answer.  The runner calls one and
  the eval row records its (ok, detail).  Oracles run the workspace's code in a subprocess from
  the workspace root, so an agent cannot satisfy them by editing a visible test or answering well.

ENTRY POINTS
  calc_spec(root) -> (ok, detail)          mul/clamp, which the visible test does not cover
  calc_add(root) -> (ok, detail)           the mutation control
  ledger(root) -> (ok, detail)             five hidden invariants
  answer_eq(answer, expected) -> (ok, detail)
  has_text(root, rel, needle) -> (ok, detail)
  run_python(root, code) -> (ok, detail)   the one subprocess primitive

INVARIANTS
  a subprocess failure is a failed oracle with stderr as the detail, never an exception that aborts
  a suite; code is passed as -c, not through a shell, with the workspace as cwd.
  expected values live here, not in fixtures.py: a fixture is input, an oracle is the answer.
  answer_eq normalises quotes and whitespace; correctness is the value.

WHERE TO CHANGE
  a new eval's answer .. a predicate here;  subprocess contract .. run_python
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_python(root: str | Path, code: str) -> tuple[bool, str]:
    """Run `code` with the workspace as cwd; return (ok, stdout-or-stderr)."""
    # -B and a cleared cache: a same-size edit in the same second otherwise leaves a stale
    # .pyc that makes the oracle grade the previous file. That is a real loop bug, not a
    # fixture detail, so the oracle is explicit about it.
    for cache in Path(root).rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(root),
                          capture_output=True, text=True, timeout=30, env=env)
    text = (proc.stdout or proc.stderr or "").strip()
    return proc.returncode == 0, text


def calc_add(root: str | Path) -> tuple[bool, str]:
    """`add` is the sum, not the difference."""
    return run_python(root, "from calc import add\nassert add(2, 3) == 5\nassert add(-2, 2) == 0\nprint('add ok')")


def calc_spec(root: str | Path) -> tuple[bool, str]:
    """The whole SPEC, including `mul` and `clamp`, which the visible test never checks."""
    return run_python(root, (
        "from calc import add, mul, clamp\n"
        "assert add(2, 3) == 5\n"
        "assert mul(4, 3) == 12\n"
        "assert clamp(9, 0, 5) == 5\n"
        "assert clamp(-1, 0, 5) == 0\n"
        "print('spec ok')\n"))


_LEDGER_BUGS = {
    "money": ("from ledger.money import split\n"
              "assert split(10, 3) == [4, 3, 3], split(10, 3)\n"),
    "dates": ("from ledger.dates import days_between\n"
              "import datetime as dt\n"
              "assert days_between(dt.date(2020,1,1), dt.date(2020,1,2)) == 1\n"),
    "text": ("from ledger.text import normalize\n"
             "assert normalize('  MiXeD ') == 'mixed'\n"),
    "stats": ("from ledger.stats import median\n"
              "assert median([1, 2, 3, 4]) == 2.5\n"),
    "rounding": ("from ledger.rounding import round_half_up\n"
                 "assert round_half_up(2.5) == 3 and round_half_up(-2.5) == -2\n"),
}


def ledger(root: str | Path, bug: str | None = None) -> tuple[bool, str]:
    """The latent ledger bugs: one named bug, or every bug when `bug` is None."""
    names = [bug] if bug else list(_LEDGER_BUGS)
    code = "".join(_LEDGER_BUGS[name] for name in names if name in _LEDGER_BUGS)
    return run_python(root, code + "print('ledger ok')\n")


def slugify_spec(root: str | Path) -> tuple[bool, str]:
    """The documented slug rules: lowercase, runs of non-alnum to '-', trim the edges."""
    return run_python(root, (
        "from slug import slugify\n"
        "assert slugify(' Hello, World! ') == 'hello-world', slugify(' Hello, World! ')\n"
        "assert slugify('A--B') == 'a-b', slugify('A--B')\n"
        "assert slugify('---x---') == 'x', slugify('---x---')\n"
        "print('slug ok')\n"))


def ruff_clean(root: str | Path) -> tuple[bool, str]:
    """The workspace's own ruff config passes and the behavior is unchanged."""
    proc = subprocess.run([sys.executable, "-m", "ruff", "check", "."], cwd=str(root),
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return False, (proc.stdout or proc.stderr or "ruff failed").strip()[:500]
    ok, detail = run_python(root, "from bad import value\nassert value() == 1\nprint('value ok')")
    return ok, detail


def answer_eq(answer: str, expected: str) -> tuple[bool, str]:
    """Compare the answer's value after stripping whitespace and surrounding quotes."""
    got = (answer or "").strip().strip("`").strip().strip("'\"").strip()
    want = str(expected).strip()
    return got == want, f"expected {want!r}; got {got!r}"


def has_text(root: str | Path, rel: str, needle: str) -> tuple[bool, str]:
    """The file contains `needle`; a missing file is a failure, not an exception."""
    path = Path(root) / rel
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"{rel}: {exc}"
    return needle in body, (f"{rel} contains {needle!r}" if needle in body
                             else f"{rel} does not contain {needle!r}")
