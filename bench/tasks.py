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

