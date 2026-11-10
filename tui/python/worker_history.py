"""worker_history: the cross-task memory: state dir, prompt prefix, history.jsonl row.
WHAT IT IS
  Where a workspace's memory lives and how it is read back: state_dir(workspace) resolves the
  per-workspace state directory, history_brief(workspace) renders the `[workspace history]` prefix
  prepended to the next prompt, record_history(...) appends one history.jsonl row (last 50 kept)
  from the loop's structured handoff dict; worker.py re-exports them.
ENTRY POINTS
  state_dir(workspace) -> Path               the per-workspace state directory
  history_brief(workspace, limit=4, issues=6) -> str   the prompt prefix ('' when no history)
  record_history(workspace, prompt, result, files) -> None   append one row
INVARIANTS
  the same state_dir decides where history.jsonl, docs/ and the logs live, so a task's memory and
  its log never disagree about the root.
  record_history persists the structured handoff dict (summary/changed/gates/deferred/untested)
  after a completed run even when the task failed: a failed attempt still moved files.
  history_brief is prepended in state mode only and is bounded (limit rows, 6 open items, clipped
  fields) so the prefix cannot crowd out the task.
  a malformed jsonl line is skipped, never fatal: history is memory, not a contract file.
WHERE TO CHANGE
  the row format .................... record_history
  the prompt prefix ................. history_brief;  rendered block ... bench/agent/handoff.py
  where the state dir lives ......... state_dir
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from bench.agent import handoff


def state_dir(workspace: Path) -> Path:
    """The per-workspace state dir: CODE_TUI_STATE, else CODE_TUI_STATE_ROOT, else XDG."""
    explicit = os.environ.get("CODE_TUI_STATE", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    root = os.environ.get("CODE_TUI_STATE_ROOT", "").strip()
    base = Path(root).expanduser() if root else Path(
        os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "code-tui"
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    return base.resolve() / "workspaces" / digest


def history_brief(workspace: Path, limit: int = 4, issues: int = 6) -> str:
    """The prompt prefix: the last few tasks, then their open deferred/untested items."""
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
    open_items = []
    for row in reversed(rows):
        for label in ("deferred", "untested"):
            for item in row.get(label) or []:
                open_items.append(f"- {row.get('when', '?')} {label}: {str(item)[:200]}")
                if len(open_items) >= issues:
                    break
            if len(open_items) >= issues:
                break
        if len(open_items) >= issues:
            break
    if open_items:
        lines.append("open items from earlier tasks (newest first):")
        lines.extend(open_items)
    return "[workspace history] previous tasks in this workspace and what they changed:\n" + "\n".join(lines)


def record_history(workspace: Path, prompt: str, result, files: list[str]) -> None:
    """Append one history.jsonl row (last 50 kept) so the next task sees what changed."""
    path = state_dir(workspace) / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    structured = dict(getattr(result, "handoff", None) or {})
    data = handoff.fields(structured) if structured else {}
    if data and not data.get("summary"):
        data["summary"] = str(result.final or "").strip()[:handoff.SUMMARY_CHARS]
    row = {
        "when": time.strftime("%H:%M"),
        "prompt": prompt[:200],
        "summary": (data.get("summary") or (result.final or "").strip())[:400],
        "files": files[:12],
        "stop": result.stop,
        "changed": (data.get("changed") or files)[:12],
        "gates": data.get("gates") or [],
        "deferred": data.get("deferred") or [],
        "untested": data.get("untested") or [],
    }
    rows = []
    if path.exists():
        rows = [line for line in path.read_text().splitlines() if line.strip()][-49:]
    rows.append(json.dumps(row))
    path.write_text("\n".join(rows) + "\n")
