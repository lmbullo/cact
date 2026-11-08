"""wiring: the two cross-file contracts a new kind or verb silently breaks.

WHAT IT IS
  Static, regex-only checks over the files that name the journal protocol:
    kinds   every `kind: "..."` server.ts / worker-events.ts / worker.py writes to a journal is
            named in tui/tests/, so a new event kind cannot ship untested.
    verbs   every entry in verbs.VERBS has a `Kernel.call` case (or the equivalent python-helper
            / `_checks_cell` form) in bench/smoke_checks/, so a new verb cannot ship uncalled.

  Three defects in eight runs came from an untested branch of this class: the worker's
  `extra_obs` signature, a `cell.end` branch, and a per-verb `_int_arg`. A static gate catches
  the name that was never wired in without spawning a process.

ENTRY POINTS
  run(check) -> None   called by bench/smoke.py::main after `repl`

INVARIANTS
  - Read-only and offline: regex over committed source, no process, no import side effect
    beyond `bench.agent.verbs` (which is stdlib-only by contract).
  - A fixture case proves each gate fails on an invented name, so a gate that silently stopped
    matching cannot pass the tree.
  - "Named" means the quoted kind or the verb appears in tui/tests/ or bench/smoke_checks/; the
    test files stay the source of truth for behavior.

WHERE TO CHANGE
  A journal kind .......................... tui/python/worker.py (source) + a tui/tests/ case
  A typed verb ............................ bench/agent/verbs.py + a Kernel.call case here
  What "covered" means .................... `_verb_covered`
"""
from __future__ import annotations

import re
from pathlib import Path

from bench.agent.verbs import VERBS

ROOT = Path(__file__).resolve().parents[2]
KIND_RE = re.compile(r'["\']?kind["\']?\s*[:=]\s*["\']([A-Za-z_][\w.]*)["\']')
SERVER_SOURCES = (
    ROOT / "tui" / "src" / "server.ts",
    ROOT / "tui" / "src" / "worker-events.ts",
    ROOT / "tui" / "python" / "worker.py",
)


def _kinds(text: str) -> set[str]:
    """Every journal kind spelled as a literal in one source file."""
    return set(KIND_RE.findall(text))


def _missing_kinds(kinds: set[str], tests_text: str) -> set[str]:
    """Kinds no tui/tests/ file names."""
    return {kind for kind in kinds if kind not in tests_text}


def _verb_covered(name: str, smoke_text: str) -> bool:
    """True when a verb has a Kernel.call case, a python-helper call, or its parent equivalent."""
    if re.search(rf'call\(\s*["\']{re.escape(name)}["\']', smoke_text):
        return True
    if name == "checks" and "_checks_cell(" in smoke_text:
        return True
    return re.search(rf'\b{re.escape(name)}\(\s*["\']', smoke_text) is not None


def _missing_verbs(names: tuple[str, ...], smoke_text: str) -> set[str]:
    """Verbs with no case or equivalent in bench/smoke_checks/."""
    return {name for name in names if not _verb_covered(name, smoke_text)}


def run(check) -> None:
    """Assert every journal kind is tested and every verb is called by the smoke suite."""
    kinds: set[str] = set()
    for path in SERVER_SOURCES:
        kinds |= _kinds(path.read_text(encoding="utf-8"))
    tests_text = "\n".join(path.read_text(encoding="utf-8")
                           for path in (ROOT / "tui" / "tests").glob("*.ts*"))
    missing = _missing_kinds(kinds, tests_text)
    check("wiring: every journal kind is named in tui/tests/", not missing,
          ", ".join(sorted(missing)))
    check("wiring: the kind gate flags an invented untested kind",
          _missing_kinds(kinds | {"brand.new"}, tests_text) == {"brand.new"},
          ", ".join(sorted(kinds)))

    smoke_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "bench" / "smoke_checks").glob("*.py")
        if path.name != "wiring.py")  # never let this gate satisfy itself
    missing_verbs = _missing_verbs(tuple(VERBS), smoke_text)
    check("wiring: every verb has a Kernel.call case or equivalent", not missing_verbs,
          ", ".join(sorted(missing_verbs)))
    check("wiring: the verb gate flags an invented uncalled verb",
          _missing_verbs(("brand_new_verb",), smoke_text) == {"brand_new_verb"},
          str(len(VERBS)))
