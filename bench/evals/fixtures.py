"""fixtures: deterministic workspaces the evals build, never a committed tree that can drift.

WHAT IT IS
  Each function writes one small workspace under a caller's temp directory and returns its path:
  a broken function with a visible test, a contract whose visible tests are incomplete, a package
  with latent bugs behind a green suite, a large distractor tree, a project with an on-demand
  skill.  The eval code owns the oracle; the fixture owns the input.

ENTRY POINTS
  mutation_tree(root)       one failing function + visible test + SPEC
  hidden_spec_tree(root)    visible tests green, SPEC requires more behavior
  bug_hunt_tree(root)       five latent bugs behind a green visible suite
  long_tree(root, files=60) one fact plus many distractors, for compaction
  skill_tree(root)          .agent/skills/release.md and a project that needs it

INVARIANTS
  every write is explicit and complete; no fixture reads another and calling one twice rewrites
  the same bytes.
  the visible tests are the model's evidence; the hidden oracle lives in this package.
  paths are relative to the workspace root and use `/`; only long_tree is large.

WHERE TO CHANGE
  a new eval input .. one builder here plus its oracle in oracle.py;  bulk .. long_tree
"""
from __future__ import annotations

from pathlib import Path

SPEC = """# calc contract

`add(a, b)` returns the sum of two integers.
`mul(a, b)` returns the product.
`clamp(x, lo, hi)` returns x limited to [lo, hi].

The visible test only covers `add`. The contract above is authoritative.
"""


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def mutation_tree(root: str | Path) -> Path:
    """A broken `add` with one visible failing test: the journal/mutation control case."""
    root = Path(root)
    _write(root, "calc.py", "def add(a, b):\n    return a - b\n")
    _write(root, "tests/test_calc.py",
           "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    _write(root, "SPEC.md", "`add(a, b)` returns the sum of two integers.\n")
    return root


def hidden_spec_tree(root: str | Path) -> Path:
    """Visible tests pass; SPEC requires `mul` and `clamp`, which are missing."""
    root = Path(root)
    _write(root, "calc.py",
           "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n")
    _write(root, "tests/test_calc.py",
           "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    _write(root, "SPEC.md", SPEC)
    return root


BUGGY = {
    "ledger/money.py": '"""Money helpers."""\n\n\ndef split(cents, n):\n    return [cents // n] * n\n',
    "ledger/dates.py": '"""Date helpers."""\n\n\ndef days_between(a, b):\n    return (b - a).days + 1\n',
    "ledger/text.py": '"""Text helpers."""\n\n\ndef normalize(s):\n    return s.strip()\n',
    "ledger/stats.py": '"""Stats helpers."""\n\n\ndef median(xs):\n    xs = sorted(xs)\n    return xs[len(xs) // 2]\n',
    "ledger/rounding.py": '"""Rounding helpers."""\n\n\ndef round_half_up(x):\n    return round(x)\n',
    "ledger/__init__.py": '"""Ledger package."""\n',
}

VISIBLE = '''from ledger.money import split
from ledger.text import normalize


def test_split_visible():
    assert split(10, 2) == [5, 5]


def test_normalize_visible():
    assert normalize("  x ") == "x"
'''


BUG_NAMES = ("money", "dates", "text", "stats", "rounding")


def bug_hunt_tree(root: str | Path) -> Path:
    """Five latent bugs: the visible suite is green, the hidden oracle is not."""
    root = Path(root)
    for rel, body in BUGGY.items():
        _write(root, rel, body)
    _write(root, "tests/test_visible.py", VISIBLE)
    _write(root, "BUGS.md", """# Ledger behavior contract

The visible tests are a smoke screen. These are the intended behaviors; the implementation
has one latent bug in each module.

- `split(cents, n)` returns a list of n integers that sum to `cents`; the remainder goes to
  the first entries. `split(10, 3) == [4, 3, 3]`.
- `days_between(a, b)` is the number of whole days from a to b, exclusive of the start:
  `days_between(2020-01-01, 2020-01-02) == 1`.
- `normalize(s)` trims and casefolds: `normalize("  MiXeD ") == "mixed"`.
- `median(xs)` averages the two middle values for an even-length list:
  `median([1, 2, 3, 4]) == 2.5`.
- `round_half_up(x)` rounds a half toward positive infinity:
  `round_half_up(2.5) == 3` and `round_half_up(-2.5) == -2`.
""")
    return root


def long_tree(root: str | Path, files: int = 60) -> Path:
    """One fact near the top and many distractors, so a small context budget forces a fold."""
    root = Path(root)
    _write(root, "fact.txt", "DEPLOY_CODE=ORBIT-7419\n")
    for index in range(files):
        _write(root, f"noise/{index:02d}.txt",
               f"file {index}\n" + ("irrelevant line\n" * 40))
    _write(root, "README.md", "The deployment code is recorded in fact.txt.\n")
    return root


def docs_tree(root: str | Path, url: str) -> Path:
    """A stub whose exact behavior is only in the linked documentation page."""
    root = Path(root)
    _write(root, "slug.py", "def slugify(text):\n    raise NotImplementedError\n")
    _write(root, "SPEC.md",
           "# Slug contract\n\nImplement `slugify(text)` in `slug.py`. The exact rules live "
           f"in the SDK documentation at {url} . Read it with docs(url); do not guess.\n")
    return root


def quality_tree(root: str | Path) -> Path:
    """A project whose own lint config rejects the current file."""
    root = Path(root)
    _write(root, "ruff.toml", 'target-version = "py310"\n\n[lint]\nselect = ["F"]\n')
    _write(root, "bad.py", "import os\n\n\ndef value():\n    return 1\n")
    return root


def skill_tree(root: str | Path) -> Path:
    """A project procedure that exists only on demand, plus the files it tells you to touch."""
    root = Path(root)
    _write(root, ".agent/skills/release.md",
           "# Release\n\n1. Read VERSION.\n2. Append the version to CHANGELOG.md.\n3. Do not edit VERSION.\n")
    _write(root, "VERSION", "2.4.1\n")
    _write(root, "CHANGELOG.md", "# Changelog\n\n")
    _write(root, "README.md", "Release steps live in .agent/skills/release.md.\n")
    return root
