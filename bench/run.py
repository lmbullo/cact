from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from bench.agent.kernel import Kernel
    from bench.agent.llm import Client
    from bench.agent.loop import RULES, AgentLoop, Budget
    from bench.agent.repl import BARE_RULES, REPL_RULES, ReplLoop
    from bench.agent.tool_loop import TOOL_RULES, ToolLoop
    from bench import edge as bench_edge
    from bench.agent.tools import Registry, build_map, make_calc, make_edit, make_find, make_map, make_read, make_write, map_text
    from bench.agent.trace import Trace, render
    from bench import tasks as bench_tasks
else:
    from .agent.kernel import Kernel
    from .agent.llm import Client
    from .agent.loop import RULES, AgentLoop, Budget
    from .agent.repl import BARE_RULES, REPL_RULES, RUN_NOTE, RUN_NOTE_MUT, ReplLoop
    from .agent.state_loop import StateLoop
    from .agent.tool_loop import TOOL_RULES, ToolLoop
    from . import edge as bench_edge
    from .agent.tools import Registry, build_map, make_calc, make_edit, make_find, make_map, make_read, make_write, map_text
    from .agent.trace import Trace, render
    from . import tasks as bench_tasks

LIB_NOTE = (
    "\nPRELOADED HELPERS (plain functions, each returns a dict with ok/kind and a note when action is needed): "
    "map(), read(path, offset=1, limit=2000), grep(pattern, path='.', max_results=50), "
    "write(path, content), edit(path, old, new, all=False). write always replaces.\n"
)

LIB_MUT_NOTE = (
    "\nPRELOADED HELPERS for mutation (plain functions, each returns a dict with ok/kind and a note when action is needed): "
    "write(path, content), edit(path, old, new, all=False). write always replaces. "
    "Reading is ordinary Python; open() decodes with errors='replace'.\n"
)

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
TRACES = HERE / "traces"


def digest(root: Path, budget: int = 60_000) -> str:
    blocks, used = [], 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()
        body = path.read_text(encoding="utf-8", errors="replace")
        lines = body.splitlines()
        if used + len(body) > budget:
            head = "\n".join(lines[:30])
            tail = "\n".join(lines[-10:])
            block = f"### {rel} ({len(lines)} lines, {len(body)} bytes, truncated)\n{head}\n...\n{tail}"
        else:
            block = f"### {rel} ({len(lines)} lines)\n{body}"
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


