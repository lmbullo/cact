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

