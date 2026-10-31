"""The read suite (T1-T5) and the mutation suite (M1-M3), with their graders.

WHAT IT IS
  `TASKS` are text-answer tasks over `bench/fixtures/`; `MUTATION_TASKS` are edits graded by
  loading the resulting workspace and exercising it. `ALL_TASKS = TASKS + MUTATION_TASKS`.

ENTRY POINTS
  Task(id, prompt, grade, shape)        grade(answer, run) -> (ok, detail)
  TASKS / MUTATION_TASKS / ALL_TASKS / by_id(task_id)
  grade_count, grade_arg_expr, grade_checksum, grade_last_line, grade_todo   (read suite)
  grade_rename, grade_row_total, grade_skip_legal                            (mutation suite)
  answer_line(text), _has_int(text, n)  answer parsing helpers

INVARIANTS
  - Ground truth is computed at import time from the fixtures (`_ground_truth`). Editing a
    file in bench/fixtures silently redefines the expected answers for every future run.
  - Reads are string/number tolerant: `answer_line` pulls the ANSWER line and `_has_int`
    accepts the number anywhere sane. Mutation graders instead *execute* the workspace
    (`exec` lib.py, run app.py with a probe CSV) — they assert behaviour, not text.
  - `run.workspace` is where the grader reads the tree; run.py sets it before grading.
  - A grader must never raise: return (False, detail) instead, so a run still prints.

WHERE TO CHANGE
  New read task ....... a first-class function `grade_*` + a Task in TASKS (and any fixture
  New mutation task ... a `grade_*` that loads/runs the workspace + a Task in MUTATION_TASKS
  Answer tolerance .... answer_line / _has_int
"""
from __future__ import annotations

import ast
import csv
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

FIX = Path(__file__).parent / "fixtures"

def _fixture_files() -> list[Path]:
    return [
        p
        for p in FIX.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith(".")
    ]

def _ground_truth() -> dict:
    rows = list(csv.DictReader((FIX / "ledger.csv").open(encoding="utf-8")))
    over_100 = sum(1 for r in rows if float(r["amount"]) > 100.0)

    ns: dict = {}
    exec(compile((FIX / "lib.py").read_text(encoding="utf-8"), "lib.py", "exec"), ns)
    checksum_7 = ns["checksum"](7)

    tree = ast.parse((FIX / "app.py").read_text(encoding="utf-8"))
    arg_expr = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "parse_row":
            arg_expr = ast.unparse(node.args[0])
            break

    note_lines = [l for l in (FIX / "notes.md").read_text(encoding="utf-8").splitlines() if l.strip()]
    todo = sum(p.read_text(encoding="utf-8").count("TODO") for p in _fixture_files())

    return {
        "rows_over_100": over_100,
        "rows_total": len(rows),
        "arg_expr": arg_expr,
        "checksum_file": "lib.py",
        "checksum_7": checksum_7,
        "notes_last": note_lines[-1],
        "notes_lines": len(note_lines),
        "todo_count": todo,
    }

GT = _ground_truth()

def answer_line(text: str) -> str:
    hits = re.findall(r"(?im)^\s*ANSWER:\s*(.+?)\s*$", text or "")
    if hits:
        return hits[-1].strip()
    return (text or "").strip()

def _has_int(text: str, n: int) -> bool:
    return re.search(rf"(?<![\d.]){n}(?![\d])", text) is not None

def grade_count(answer: str, run) -> tuple[bool, str]:
    value = answer_line(answer)
    ok = _has_int(value, GT["rows_over_100"])
    detail = f"got {value!r}; expected {GT['rows_over_100']}"
    return ok, detail

def grade_arg_expr(answer: str, run) -> tuple[bool, str]:
    compact = re.sub(r"\s+", "", answer or "")
    ok = re.sub(r"\s+", "", GT["arg_expr"]) in compact
    detail = f"expected {GT['arg_expr']!r}; answer {answer_line(answer)!r}"
    return ok, detail

def grade_checksum(answer: str, run) -> tuple[bool, str]:
    value = answer_line(answer)
    ok = _has_int(value, GT["checksum_7"]) and GT["checksum_file"] in (answer or "").lower()
    detail = f"expected {GT['checksum_file']} + {GT['checksum_7']}; got {value!r}"
    return ok, detail

def grade_last_line(answer: str, run) -> tuple[bool, str]:
    norm = re.sub(r"\s+", " ", answer or "").strip().lower()
    ok = re.sub(r"\s+", " ", GT["notes_last"]).strip().lower() in norm
    detail = f"expected {GT['notes_last']!r}; answer {answer_line(answer)!r}"
    return ok, detail

def grade_todo(answer: str, run) -> tuple[bool, str]:
    value = answer_line(answer)
    ok = _has_int(value, GT["todo_count"])
    detail = f"got {value!r}; expected {GT['todo_count']}"
    return ok, detail

@dataclass
class Task:
    id: str
    prompt: str
    grade: Callable[[str, object], tuple[bool, str]]
    shape: str

TASKS: list[Task] = [
    Task(
        "T1_count",
        "In ledger.csv, how many rows have amount greater than 100?",
        grade_count,
        "single file, aggregate",
    ),
    Task(
        "T2_join",
        "app.py calls parse_row, which is imported from lib.py. "
        "What exact Python expression does app.py pass as that call's first argument?",
        grade_arg_expr,
        "two files, join",
    ),
    Task(
        "T3_locate",
        "Which file under the working tree defines the function checksum, and what value does checksum(7) return?",
        grade_checksum,
        "navigation + computation",
    ),
    Task(
        "T4_tail",
        "What is the last line of notes.md? Quote it exactly.",
        grade_last_line,
        "long file, tail",
    ),
    Task(
        "T5_coverage",
        "How many occurrences of the string TODO are there across all files in the working tree "
        "(case-sensitive, every file, including nested directories)?",
        grade_todo,
        "traversal + coverage",
    ),
]

def by_id(task_id: str) -> Task:
    for task in ALL_TASKS:
        if task.id == task_id:
            return task
    raise KeyError(task_id)

def grade_rename(answer: str, run) -> tuple[bool, str]:
    root = Path(run.workspace)
    try:
        src = (root / "lib.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        ns: dict = {}
        exec(compile(src, "lib.py", "exec"), ns)
    except Exception as exc:
        return False, f"lib.py unusable: {type(exc).__name__}: {exc}"
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    leftover = "PAGE" in ns or "PAGE" in names
    ok = (not leftover) and ns.get("PAGE_SIZE") == 4096
    return ok, f"PAGE_SIZE={ns.get('PAGE_SIZE')} leftover_PAGE={leftover}"


def grade_row_total(answer: str, run) -> tuple[bool, str]:
    root = Path(run.workspace)
    try:
        ns: dict = {}
        exec(compile((root / "lib.py").read_text(encoding="utf-8"), "lib.py", "exec"), ns)
    except Exception as exc:
        return False, f"lib.py unusable: {type(exc).__name__}: {exc}"
    fn = ns.get("row_total")
    if not callable(fn):
        return False, "row_total not defined"
    sample = [
        {"amount": 10.0, "currency": "USD"},
        {"amount": 5.0, "currency": "EUR"},
        {"amount": 2.5, "currency": "USD"},
    ]
    try:
        got = fn(sample)
    except Exception as exc:
        return False, f"row_total raised {type(exc).__name__}: {exc}"
    intact = ns.get("checksum", lambda x: None)(7) == 1401181143
    ok = abs(float(got) - 12.5) < 1e-9 and intact
    return ok, f"row_total={got!r} expected 12.5; checksum_intact={intact}"


def grade_skip_legal(answer: str, run) -> tuple[bool, str]:
    root = Path(run.workspace)
    probe = root / "__probe.csv"
    probe.write_text(
        "id,account,amount,currency\n"
        "A,legal,500,USD\n"
        "B,growth,10,USD\n"
        "C,ops,2.5,USD\n"
        "D,legal,7,USD\n"
        "E,infra,1.25,USD\n",
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [sys.executable, "app.py", "__probe.csv"],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        return False, f"app.py did not run: {type(exc).__name__}: {exc}"
    out = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not out:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return False, f"exit={proc.returncode} stderr={tail[0][:120]}"
    try:
        got = float(out[-1])
    except ValueError:
        return False, f"last line is not a number: {out[-1][:80]!r}"
    ok = abs(got - 13.75) < 1e-6
    return ok, f"printed {got} expected 13.75"


MUTATION_TASKS: list[Task] = [
    Task(
        "M1_rename",
        "Rename the constant PAGE to PAGE_SIZE in lib.py, and update every reference to it in the workspace. Keep the value unchanged.",
        grade_rename,
        "rename symbol",
    ),
    Task(
        "M2_add_fn",
        "Add a function row_total(rows) to lib.py. rows is a list of dicts with keys amount (float) and currency (str). "
        "Return the sum of amount for rows whose currency is 'USD'. Keep the existing functions unchanged.",
        grade_row_total,
        "add function",
    ),
    Task(
        "M3_branch",
        "app.py prints the total amount of every row in the CSV passed on the command line. "
        "Change it so rows whose account is 'legal' are skipped. Keep the same output format.",
        grade_skip_legal,
        "change control flow",
    ),
]

ALL_TASKS = TASKS + MUTATION_TASKS
