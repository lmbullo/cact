from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.agent import config  # noqa: E402
from bench.agent.check import run_check  # noqa: E402
from bench.agent.kernel import Kernel  # noqa: E402
from bench.agent.llm import Client, LLMError  # noqa: E402
from bench.agent.repl import RUN_NOTE  # noqa: E402
from bench.agent.state_loop import STATE_RULES  # noqa: E402
from bench.agent.tool_loop import TOOL_RULES  # noqa: E402

PREFIX = "@@TUI@@"


def state_dir(workspace: Path) -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    return root / "code-tui" / "workspaces" / digest


def history_brief(workspace: Path, limit: int = 4) -> str:
    path = state_dir(workspace) / "history.jsonl"
    if not path.exists():
        return ""
    rows = []
    for line in path.read_text().splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return ""
    lines = []
    for row in rows:
        files = ", ".join(row.get("files") or [])[:120]
        lines.append(f"- {row.get('when', '?')}: {row.get('summary', '')[:180]} [{files}]")
    return "[workspace history] previous tasks in this workspace and what they changed:\n" + "\n".join(lines)


def record_history(workspace: Path, prompt: str, result, files: list[str]) -> None:
    path = state_dir(workspace) / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "when": time.strftime("%H:%M"),
        "prompt": prompt[:200],
        "summary": (result.final or "").strip()[:400],
        "files": files[:12],
        "stop": result.stop,
    }
    rows = []
    if path.exists():
        rows = [line for line in path.read_text().splitlines() if line.strip()][-49:]
    rows.append(json.dumps(row))
    path.write_text("\n".join(rows) + "\n")


