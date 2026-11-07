"""The harness suite: tasks that grade how the agent uses its tools, not only the answer.

WHAT IT IS
  Eight small workspace tasks aimed at the architecture that the recent TUI runs exercised:
  map or read an HTML-in-JS file, search with a real regex, run a batch of argv
  commands, apply one atomic edit batch, use the explicit shell escape hatch, read several
  files in one call, read the project's own checksum instead of inventing one, and find a
  fact in the workspace's current docs instead of a model's priors. Every
  task grades the final answer *and* the tool-call record, so a correct answer found by the
  wrong path (shell `cat`, one edit per line, `bash -lc` in argv) fails.

ENTRY POINTS
  HarnessTask(id, prompt, grade, shape, source=HARNESS, mutates=False, check=None)
  HARNESS_TASKS / by_id(task_id)
  build()                       (re)create harness_fixtures/ when missing
  calls(run), tools(run)        flatten the per-call records of one run
  process(run, ...)             the reusable tool-use assertion -> list[str] of violations
                                (`require_path`, `no_python_io`, `max_repeats`, `first_in`
                                make the path, not just the answer, part of the grade)

INVARIANTS
  - Ground truth is read from `harness_fixtures/` at import: section ids and lines, search
    hits, the marker count, and the two edit targets. Editing a fixture silently redefines
    the expected answers, exactly like tasks.py.
  - A process failure is a grade failure. The goal here is the path as well as the answer;
    keep the constraints in the task definition, not in the prompt alone.
  - `process` reads only `run.turns[*]['calls']`; it never re-opens the workspace. The
    graders that do inspect files use `run.workspace`, which run.py sets.
  - The suite is offline to import and cheap to run; only the model calls need a key.

WHERE TO CHANGE
  New task ............ a grade function, a HarnessTask in HARNESS_TASKS, a fixture in build()
  New process rule .... process() and its callers
  Fixture lines ....... build(); the expected maps below re-read the files at import
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HARNESS = Path(__file__).parent / "harness_fixtures"

MAIN_JS = """import './theme.css'

const view = `
<header class="masthead" id="top">
  <h1>Harness field notes</h1>
  <p>A small page used by the loop evals.</p>
</header>
<main>
  <section class="card" id="hero">
    <h2>Why this page exists</h2>
    <p>The question comes before the models.</p>
  </section>

  <section class="card" id="costs">
    <h2>What the delay costs</h2>
    <p>Every year of delay adds to the bill.</p>
  </section>

  <section class="card" id="carbon">
    <h2>The loop that feeds itself</h2>
    <p>Warming releases carbon, and carbon adds warming.</p>
  </section>

  <section class="card" id="answer">
    <h2>What to do now</h2>
    <p>Cut emissions first; the feedback is smaller than the tap.</p>
  </section>
</main>
`

document.querySelector('#app').innerHTML = view
"""

SEARCH_JS = """export const legacyName = 'old path'

// deprecated: the old path is kept for one more release
export function findLegacy() {
  return legacyName
}

export function currentName() {
  return 'new path'
}
"""

NOTES_MD = """# Notes

The legacy path is still supported.
This paragraph is deprecated, so do not copy it.
The current path is the one to use.
"""

THEME_CSS = """/* Brand */
:root {
  --brand: #111111;
  --paper: #f4efe4;
}

/* Cards */
.card {
  border: 1px solid var(--brand);
}
"""

CONFIG_JS = """export const BRAND_COLOR = '#111111'
export const SITE_NAME = 'harness'
export const VERSION = '0.0.0'
"""

CURRENT_MD = """# Current workspace contract

This file is the source of truth for this checkout, not a model's training data.

- Release gate: BLUE-ORCHID-731
- Verifier: python3 tools/check_current.py --strict
- The next version is VERSION = '2.4.1'
"""
CURRENT_PATH = "docs/CURRENT.md"
DOC_FACT = "BLUE-ORCHID-731"

EVENTS_LOG = """alpha MARKER first
beta
gamma MARKER second
delta MARKER third
"""

LEDGER_PY = """def checksum(order_id):
    h = 0
    for ch in str(order_id):
        h = (h * 31 + ord(ch)) % 1000003
    return h
"""

CHECKSUM_ID = "ord-03"
CHECKSUM_VALUE = 0
for _ch in CHECKSUM_ID:
    CHECKSUM_VALUE = (CHECKSUM_VALUE * 31 + ord(_ch)) % 1000003


def build() -> None:
    """(Re)create `harness_fixtures/` when missing; an existing tree only gets docs/CURRENT.md."""
    if HARNESS.exists():
        (HARNESS / "docs").mkdir(exist_ok=True)
        current = HARNESS / "docs" / "CURRENT.md"
        if not current.exists():
            current.write_text(CURRENT_MD, encoding="utf-8")
        return
    (HARNESS / "src").mkdir(parents=True)
    (HARNESS / "docs").mkdir()
    (HARNESS / "data").mkdir()
    (HARNESS / "src" / "main.js").write_text(MAIN_JS, encoding="utf-8")
    (HARNESS / "src" / "search.js").write_text(SEARCH_JS, encoding="utf-8")
    (HARNESS / "src" / "theme.css").write_text(THEME_CSS, encoding="utf-8")
    (HARNESS / "src" / "config.js").write_text(CONFIG_JS, encoding="utf-8")
    (HARNESS / "docs" / "notes.md").write_text(NOTES_MD, encoding="utf-8")
    (HARNESS / "docs" / "CURRENT.md").write_text(CURRENT_MD, encoding="utf-8")
    (HARNESS / "data" / "events.log").write_text(EVENTS_LOG, encoding="utf-8")
    (HARNESS / "src" / "ledger.py").write_text(LEDGER_PY, encoding="utf-8")
    (HARNESS / "README.md").write_text("# Harness fixture\n\nOne page, one search, one log.\n", encoding="utf-8")


build()

SECTION_RE = re.compile(r'<section\b[^>]*\bid="([^"]+)"', re.IGNORECASE)
SECTION_LINES: dict[str, int] = {
    match.group(1): MAIN_JS[:match.start()].count("\n") + 1
    for match in SECTION_RE.finditer(MAIN_JS)
}


def _search_hits() -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for folder in ("src", "docs"):
        for path in sorted((HARNESS / folder).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            for number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if re.search(r"legacy|deprecated", line, re.IGNORECASE):
                    hits.append((path.relative_to(HARNESS).as_posix(), number))
    return hits


MARKER_COUNT = sum(1 for line in EVENTS_LOG.splitlines() if "MARKER" in line)
SEARCH_HITS = _search_hits()
OLD_COLOR = "#111111"
NEW_COLOR = "#c0392b"


def calls(run) -> list[dict]:
    """Every tool call of a run, flattened across turns: the record `process()` grades."""
    return [call for turn in (getattr(run, "turns", None) or []) for call in (turn.get("calls") or [])]


def tools(run) -> list[str]:
    """The tool names in call order (duplicates kept); the cheap half of a process grade."""
    return [str(call.get("name")) for call in calls(run)]


def _exec_commands(call: dict):
    args = call.get("args") or {}
    commands = args.get("commands")
    if isinstance(commands, list):
        return commands
    argv = args.get("argv")
    return [argv] if isinstance(argv, list) else []


def _reader_programs(call: dict) -> list[str]:
    readers = {"cat", "sed", "grep", "rg", "head", "tail", "wc", "find", "ls", "tree", "awk"}
    out = []
    for argv in _exec_commands(call):
        if argv:
            name = str(argv[0]).rsplit("/", 1)[-1]
            if name in readers:
                out.append(name)
    return out


def process(run, *, require=(), max_calls: int | None = None,
            no_exec_readers: bool = False, require_exec_commands: bool = False,
            require_exec_shell: bool = False, require_read_list: bool = False,
            require_edit_batch: bool = False, max_errors: int | None = 0,
            require_path: str | None = None, no_python_io: bool = False,
            max_repeats: int | None = None, first_in=()) -> list[str]:
    """Return process violations; an empty list means the task used the intended path.

    Besides tool requirements, this can require a particular path to have been inspected,
    forbid python file I/O and shell readers, reject duplicate calls, and fix the first tool
    family. That is how a docs-grounding task distinguishes reading the current checkout from
    answering from priors.
    """
    calls_ = calls(run)
    names = [str(call.get("name")) for call in calls_]
    problems: list[str] = []
    if max_calls is not None and len(calls_) > max_calls:
        problems.append(f"used {len(calls_)} calls; limit {max_calls}")
    for name in require:
        if name not in names:
            problems.append(f"required tool {name!r} was not called")
    if no_exec_readers:
        found = sorted({program for call in calls_ if call.get("name") == "exec"
                        for program in _reader_programs(call)})
        if found:
            problems.append("used exec as a file reader: " + ", ".join(found))
    if require_exec_commands:
        if not any(call.get("name") == "exec"
                   and len((call.get("args") or {}).get("commands") or []) >= 2
                   for call in calls_):
            problems.append("did not run a commands=[[...], [...]] batch")
    if require_exec_shell:
        if not any(call.get("name") == "exec" and (call.get("args") or {}).get("shell")
                   for call in calls_):
            problems.append("did not use exec(shell=...)")
    if require_read_list:
        batched = any(
            call.get("name") == "read" and (
                (isinstance((call.get("args") or {}).get("path"), list)
                 and len((call.get("args") or {}).get("path")) >= 2)
                or (isinstance((call.get("args") or {}).get("ranges"), list)
                    and len((call.get("args") or {}).get("ranges")) >= 2))
            for call in calls_)
        if not batched:
            problems.append("did not batch two files in one read call (path=[...] or ranges=[...])")
    if require_edit_batch:
        if not any(call.get("name") == "edit"
                   and len((call.get("args") or {}).get("edits") or []) >= 2
                   for call in calls_):
            problems.append("did not use one edit(edits=[...]) batch")
    if max_errors is not None and int(getattr(run, "errors", 0) or 0) > max_errors:
        problems.append(f"had {getattr(run, 'errors', 0)} rejected/failed calls; limit {max_errors}")
    if max_repeats is not None and int(getattr(run, "repeats", 0) or 0) > max_repeats:
        problems.append(f"repeated an identical call {getattr(run, 'repeats', 0)} time(s); limit {max_repeats}")
    if first_in and names and names[0] not in first_in:
        problems.append(f"first call was {names[0]!r}; expected one of {list(first_in)}")
    if require_path:
        needle = str(require_path).replace("\\", "/").lstrip("./")
        if not any(needle in json_args(call) for call in calls_):
            problems.append(f"never inspected {needle}")
    if no_python_io:
        pattern = re.compile(r"\b(open|read_text|read_bytes|Path)\s*\(")
        bad = [call for call in calls_ if call.get("name") == "python"
               and pattern.search(str((call.get("args") or {}).get("code") or ""))]
        if bad:
            problems.append("used python file I/O instead of the typed readers")
    return problems


def json_args(call: dict) -> str:
    """A call's arguments as one sorted JSON string with `/` separators, for path matching."""
    return json.dumps(call.get("args") or {}, sort_keys=True, default=repr).replace("\\", "/")


def _answer(answer: str) -> str:
    value = (answer or "").strip()
    # Uppercase only: a report line like `answer: 24` is content, not the ANSWER contract.
    hits = re.findall(r"(?m)^\s*ANSWER:\s*(.+?)\s*$", value)
    return hits[-1].strip() if hits else value


def _section_pairs(answer: str) -> dict[str, int]:
    text = _answer(answer)
    out: dict[str, int] = {}
    for ident, line in re.findall(r"\b([A-Za-z][\w-]*)\b[^\n\d]{0,24}(\d+)", text):
        out.setdefault(ident, int(line))  # the report table repeats ids with close lines
    return out


def grade_outline(answer: str, run) -> tuple[bool, str]:
    """L1: every section id with its 1-based line, found without a shell reader."""
    got = _section_pairs(answer)
    missing = {ident: line for ident, line in SECTION_LINES.items() if got.get(ident) != line}
    problems = process(run, max_calls=10, no_exec_readers=True)
    ok = not missing and not problems
    detail = []
    if missing:
        detail.append(f"expected {missing}; got {got}")
    detail.extend(problems)
    return ok, "; ".join(detail) or "all section ids and lines"


def grade_search(answer: str, run) -> tuple[bool, str]:
    """L2: every `path:line` hit for legacy|deprecated, found with the search tool."""
    text = _answer(answer)
    missing = [f"{path}:{line}" for path, line in SEARCH_HITS if f"{path}:{line}" not in text]
    problems = process(run, require=("search",), max_calls=10, no_exec_readers=True)
    ok = not missing and not problems
    detail = []
    if missing:
        detail.append("missing " + ", ".join(missing))
    detail.extend(problems)
    return ok, "; ".join(detail) or f"found {len(SEARCH_HITS)} hits"


def grade_exec_batch(answer: str, run) -> tuple[bool, str]:
    """L3: `one` then `two` in the answer, produced by one commands=[[...],[...]] batch."""
    text = _answer(answer)
    one, two = text.find("one"), text.find("two")
    problems = process(run, require=("exec",), max_calls=6, require_exec_commands=True)
    ok = one >= 0 and two > one and not problems
    detail = []
    if not (one >= 0 and two > one):
        detail.append(f"expected one then two; got {text[:100]!r}")
    detail.extend(problems)
    return ok, "; ".join(detail) or "commands=[[...],[...]] ran in order"


def grade_edit_batch(answer: str, run) -> tuple[bool, str]:
    """L4: both files carry NEW_COLOR and no OLD_COLOR, changed in one edit(edits=[...])."""
    root = Path(run.workspace)
    try:
        config = (root / "src" / "config.js").read_text(encoding="utf-8")
        theme = (root / "src" / "theme.css").read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"workspace unreadable: {exc}"
    wrong = []
    if NEW_COLOR not in config or OLD_COLOR in config:
        wrong.append("src/config.js")
    if NEW_COLOR not in theme or OLD_COLOR in theme:
        wrong.append("src/theme.css")
    problems = process(run, require=("edit",), max_calls=8, require_edit_batch=True)
    ok = not wrong and not problems
    detail = []
    if wrong:
        detail.append("not updated: " + ", ".join(wrong))
    detail.extend(problems)
    return ok, "; ".join(detail) or "both files changed in one batch"


def grade_shell(answer: str, run) -> tuple[bool, str]:
    """L5: the MARKER line count, computed through exec(shell=...)."""
    text = _answer(answer)
    problems = process(run, max_calls=8, require_exec_shell=True)
    ok = re.search(rf"(?<![\d.]){MARKER_COUNT}(?![\d])", text) is not None and not problems
    detail = []
    if not re.search(rf"(?<![\d.]){MARKER_COUNT}(?![\d])", text):
        detail.append(f"expected {MARKER_COUNT}; got {text[:100]!r}")
    detail.extend(problems)
    return ok, "; ".join(detail) or "explicit exec(shell=...)"


def grade_read_batch(answer: str, run) -> tuple[bool, str]:
    """L6: the Notes heading and findLegacy, from one two-file read call."""
    text = _answer(answer)
    problems = process(run, require=("read",), max_calls=8, require_read_list=True, no_exec_readers=True)
    ok = "Notes" in text and "findLegacy" in text and not problems
    detail = []
    if not ("Notes" in text and "findLegacy" in text):
        detail.append(f"expected Notes + findLegacy; got {text[:100]!r}")
    detail.extend(problems)
    return ok, "; ".join(detail) or "two files in one read"


def grade_local_code(answer: str, run) -> tuple[bool, str]:
    """L7: the checksum src/ledger.py computes, after actually reading that file."""
    text = _answer(answer)
    inspected = False
    for call in calls(run):
        if call.get("name") != "read":
            continue
        path = (call.get("args") or {}).get("path")
        paths = [path] if isinstance(path, str) else (path or [])
        if any("ledger.py" in str(item) for item in paths):
            inspected = True
            break
    problems = process(run, max_calls=8, no_exec_readers=True)
    if not inspected:
        problems.append("did not read src/ledger.py")
    ok = re.search(rf"(?<![\d.]){CHECKSUM_VALUE}(?![\d])", text) is not None and not problems
    detail = []
    if re.search(rf"(?<![\d.]){CHECKSUM_VALUE}(?![\d])", text) is None:
        detail.append(f"expected {CHECKSUM_VALUE}; got {text[:100]!r}")
    detail.extend(problems)
    return ok, "; ".join(detail) or "read the project checksum"


def grade_current_docs(answer: str, run) -> tuple[bool, str]:
    """L8: DOC_FACT from docs/CURRENT.md, read once via a typed tool (no python file I/O)."""
    text = _answer(answer)
    problems = process(run, max_calls=6, no_exec_readers=True, max_repeats=0,
                       require_path=CURRENT_PATH, no_python_io=True,
                       first_in=("map", "guide", "read", "search", "outline"))
    ok = DOC_FACT in text and not problems
    detail = []
    if DOC_FACT not in text:
        detail.append(f"expected {DOC_FACT} from {CURRENT_PATH}; got {text[:100]!r}")
    detail.extend(problems)
    return ok, "; ".join(detail) or f"read {CURRENT_PATH}, not priors"


@dataclass
class HarnessTask:
    """One tool-path task: grader, workspace, whether it writes, its goal and its prepare()."""

    id: str
    prompt: str
    grade: Callable[[str, object], tuple[bool, str]]
    shape: str
    source: Path = HARNESS
    mutates: bool = False
    check: Callable[[Path], bool] | None = None
    prepare: Callable[[Path], None] | None = None


HARNESS_TASKS: list[HarnessTask] = [
    HarnessTask(
        "L1_outline",
        "Analyze src/main.js. It is a JS file with an HTML template. Report every "
        "<section> id and its 1-based line number in file order, as `id: line`. Do not modify files.",
        grade_outline,
        "read/analyze: map or read an HTML-in-JS file, without shell readers",
    ),
    HarnessTask(
        "L2_search",
        "Find every line under src/ and docs/ that contains `legacy` or `deprecated` "
        "(case-insensitive). Report each hit as `path:line`.",
        grade_search,
        "search: regex alternation, include/path, no shell grep",
    ),
    HarnessTask(
        "L3_exec_batch",
        "Run two programs in one exec batch: print `one`, then print `two`. Report the "
        "outputs in order. Use commands=[[...],[...]], not a shell string.",
        grade_exec_batch,
        "exec: commands batch, argv not shell",
    ),
    HarnessTask(
        "L4_edit_batch",
        f"Change the brand color from {OLD_COLOR} to {NEW_COLOR} in src/config.js and "
        f"src/theme.css. Use one atomic edit batch; leave everything else unchanged.",
        grade_edit_batch,
        "edit: one atomic batch across two files",
        mutates=True,
    ),
    HarnessTask(
        "L5_shell",
        "Using an explicit shell pipeline, count the lines in data/events.log that contain "
        "MARKER and report the count. Use exec(shell=...), not bash -lc inside argv.",
        grade_shell,
        "exec: the explicit shell escape hatch",
    ),
    HarnessTask(
        "L6_read_batch",
        "Read docs/notes.md and src/search.js together in one read call. Report the first "
        "Markdown heading in notes and the exported function that returns legacyName.",
        grade_read_batch,
        "read: one call for several files/windows",
    ),
    HarnessTask(
        "L7_local_code",
        f"The workspace defines its own checksum in src/ledger.py. Read that code and report "
        f"the integer that ledger.checksum('{CHECKSUM_ID}') returns. Do not substitute a "
        "generic CRC or a guess.",
        grade_local_code,
        "read/analyze: use the project's code, not an invented checksum",
    ),
    HarnessTask(
        "L8_current_docs",
        f"Read this workspace's current documentation and report the release gate phrase. "
        f"The answer is in {CURRENT_PATH}; do not rely on prior knowledge, a shell reader, "
        "or a guess. Use exactly one pass over the docs and cite the file.",
        grade_current_docs,
        "docs-grounding: read the current contract, no shell/python file I/O",
    ),
]


def by_id(task_id: str) -> HarnessTask:
    """The HarnessTask with that id; KeyError when the id is not a harness task."""
    for task in HARNESS_TASKS:
        if task.id == task_id:
            return task
    raise KeyError(task_id)
