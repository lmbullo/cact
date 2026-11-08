"""task_journal: read a TUI task journal back — tasks/<id>.json + events/<id>.jsonl -> summary.

WHAT IT IS
  Post-hoc analysis for one task in a TUI state directory
  (`~/.local/state/code-tui/workspaces/<sha256-of-cwd,16 hex>/`). Counts the typed call mix
  (`tool_mix`), structured rejections, which kernel verbs the model used, which files it
  wrote, steps, tokens, stop reason — and the escape hatch: how often `exec` stood in for a
  typed verb (`exec_verb_dupes`), ran `python3 -c` or a shell string, how many observation
  characters reached the model (`obs_chars`), and the turn of the first mutating cell
  (`first_mutation_turn`). (No benchmark CLI runs any more: `bench/eval.py` is the eval front
  end; this is the reader for the journals the TUI records.)

ENTRY POINTS
  python3 bench/task_journal.py <state-dir> <task-id>
  python3 bench/task_journal.py --recent [N]  every workspace's latest tasks, newest first
  load(state_dir, task_id) -> (task, turns)
  summarize(state_dir, task_id) -> dict
  recent(state_root, limit) -> list[dict]

INVARIANTS
  - Read-only: it never writes into the state dir.
  - `VERBS` is a regex list of `verb(` occurrences over the concatenated cell code, so it is
    an approximation, not a parse. Keep it in sync when the kernel gains a verb.
  - Malformed journal lines are skipped, not fatal: a killed worker leaves a partial file.
  - The escape metrics read `cell.start` events, so a folded or digested cell still counts:
    the number is about the call the model made, not about what the card kept.

WHERE TO CHANGE
  New kernel verb ........ VERBS
  New escape metric ...... EXEC_DUPES, MUTATING_VERBS, _escape, summarize()
  New metric ............. summarize()
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

VERBS = ("map", "read", "outline", "search", "grep", "guide", "recall", "write", "edit", "splice",
         "apply", "commit", "exec", "git", "run", "diff", "undo", "changes", "help",
         "final_answer")
# A cell that can change the tree. The first one is the turn the run stopped exploring and
# started editing; when it is late, the affordances above it are the problem.
MUTATING_VERBS = frozenset({"write", "edit", "splice", "apply", "delete", "mkdir", "commit"})
# exec programs a typed verb already answers (grep -> search, cat -> read, ...).
EXEC_DUPES = frozenset({"grep", "rg", "cat", "ls", "find", "sed", "mkdir", "rm"})


def _events(state_dir: Path, task_id: str) -> list[dict]:
    """Every well-formed journal event; a killed worker's partial line is skipped, not fatal."""
    events = []
    for line in (state_dir / "events" / f"{task_id}.jsonl").read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def load(state_dir: Path, task_id: str) -> tuple[dict, list[dict]]:
    """The task record and its `turn` events; malformed lines are skipped."""
    task = json.loads((state_dir / "tasks" / f"{task_id}.json").read_text())
    turns = [event.get("data") or {} for event in _events(state_dir, task_id)
             if event.get("kind") == "turn"]
    return task, turns


def _cells(state_dir: Path, task_id: str) -> list[dict]:
    """The `cell.start` records: one per cell, in call order (its tool, code and args).

    These events are flat (no `data` wrapper), like every other kernel event.
    """
    return [event for event in _events(state_dir, task_id)
            if event.get("kind") == "cell.start"]


def _escape(cells: list[dict]) -> dict:
    """How often exec stood in for a verb: the counts a cheaper affordance would have removed."""
    dupes = python3_c = shell = 0
    for cell in cells:
        if cell.get("tool") != "exec":
            continue
        args = cell.get("args") or {}
        if args.get("shell") is not None:
            shell += 1
        if args.get("argv") is not None:
            argvs = [args["argv"]]
        else:
            argvs = list(args.get("commands") or [])
        for argv in argvs:
            parts = argv.split() if isinstance(argv, str) else [str(item) for item in argv or []]
            if not parts:
                continue
            if parts[0] in EXEC_DUPES:
                dupes += 1
            if parts[0] in ("python3", "python") and "-c" in parts:
                python3_c += 1
    return {"exec_verb_dupes": dupes, "python3_c": python3_c, "shell": shell}


def _first_mutation_turn(turns: list[dict], cells: list[dict]) -> int | None:
    """The 1-based turn that held the first mutating cell; None when nothing was changed."""
    index = 0
    for turn in turns:
        for _ in turn.get("calls") or []:
            cell = cells[index] if index < len(cells) else {}
            index += 1
            if str(cell.get("tool") or "") in MUTATING_VERBS:
                return int(turn.get("turn") or 0)
    return None


def summarize(state_dir: Path, task_id: str) -> dict:
    """The one-row metrics dict printed by `main()`; keys are the contract."""
    task, turns = load(state_dir, task_id)
    cells = _cells(state_dir, task_id)
    code = "\n".join(turn.get("code") or "" for turn in turns)
    obs = "\n".join((turn.get("observation") or {}).get("stdout", "") + (turn.get("observation") or {}).get("stderr", "") for turn in turns)
    verbs = {verb: len(re.findall(rf"\b{verb}\(", code)) for verb in VERBS}
    writes = re.findall(r"write\(\s*['\"]([^'\"]+)", code)
    edits = re.findall(r"edit\(\s*['\"]([^'\"]+)", code)
    tool_mix: dict[str, int] = {}
    rejected = 0
    for turn in turns:
        for call in turn.get("calls") or []:
            name = str(call.get("name") or "")
            if name:
                tool_mix[name] = tool_mix.get(name, 0) + 1
            if (call.get("observation") or {}).get("rejected") or (call.get("observation") or {}).get("exit") == "error":
                rejected += 1
    return {
        "id": task_id,
        "status": task.get("status"),
        "stop": task.get("stop"),
        "steps": len(turns),
        "cells": len(cells),
        "untouched_src_writes": sum(1 for path in writes if path.startswith("src/")),
        "edits": len(edits),
        "verbs": verbs,
        "tool_mix": dict(sorted(tool_mix.items())),
        "rejected": rejected,
        "restarts": obs.count("empty namespace"),
        "goal_unmet": obs.count("final_answer rejected") + obs.count("without a passing check"),
        "refused": obs.count("refused_truncation"),
        "check_runs": len(re.findall(r"bun run typecheck", code)),
        "test_runs": len(re.findall(r"bun test", code)),
        **_escape(cells),
        "obs_chars": len(obs),
        "first_mutation_turn": _first_mutation_turn(turns, cells),
        "usage": (json.loads((state_dir / "tasks" / f"{task_id}.json").read_text()).get("usage") or {}),
    }


def _state_root() -> Path:
    """The state root the server uses: CODE_TUI_STATE_ROOT, else XDG_STATE_HOME, else ~."""
    root = os.environ.get("CODE_TUI_STATE_ROOT")
    if root:
        return Path(root)
    xdg = os.environ.get("XDG_STATE_HOME")
    return Path(xdg) / "code-tui" if xdg else Path.home() / ".local" / "state" / "code-tui"


def _workspace_name(state_dir: Path) -> str:
    """server.json's workspace path, falling back to the state directory name."""
    info = state_dir / "server.json"
    try:
        return str(json.loads(info.read_text()).get("workspace") or state_dir.name)
    except (OSError, json.JSONDecodeError):
        return state_dir.name


def recent(state_root: Path | None = None, limit: int = 20) -> list[dict]:
    """Latest tasks across every workspace under one state root, newest first."""
    root = state_root or _state_root()
    rows = []
    for path in root.glob("workspaces/*/tasks/*.json"):
        try:
            task = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        state = path.parent.parent
        steps = task.get("steps")
        if steps is None:
            steps = 0
            try:
                for line in (state / "events" / f"{task.get('id')}.jsonl").read_text().splitlines():
                    try:
                        if json.loads(line).get("kind") == "turn":
                            steps += 1
                    except json.JSONDecodeError:
                        continue
            except OSError:
                pass
        rows.append({
            "workspace": _workspace_name(state),
            "state": state.name,
            "task": task,
            "steps": steps,
            "created": int(task.get("createdAt") or 0),
        })
    rows.sort(key=lambda row: row["created"], reverse=True)
    return rows[:max(1, int(limit))]


def main() -> int:
    """CLI: `--recent [N]` across workspaces, or `<state-dir> <task-id>...` summaries."""
    if len(sys.argv) < 2:
        print("usage: python3 bench/task_journal.py <state-dir> <task-id>... | --recent [N]",
              file=sys.stderr)
        return 2
    if sys.argv[1] == "--recent":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        for row in recent(limit=limit):
            task = row["task"]
            usage = task.get("usage") or {}
            stamp = time.strftime("%m-%d %H:%M", time.localtime(row["created"] / 1000))
            prompt = (task.get("prompt") or "").replace("\n", " ")[:58]
            print(f"{stamp}  {row['workspace']:<34}  {task.get('id', ''):<20} "
                  f"{str(task.get('status')):<9} {str(task.get('stop')):<10} "
                  f"{row['steps']:>3} steps {usage.get('total_tokens', 0):>9} tok  {prompt}")
        return 0
    state_dir = Path(sys.argv[1])
    for task_id in sys.argv[2:]:
        row = summarize(state_dir, task_id)
        print(f"{row['id']:22} status={row['status']:8} stop={str(row['stop']):12} steps={row['steps']:3}")
        print(f"   src-writes={row['untouched_src_writes']:2} edits={row['edits']:2} restarts={row['restarts']} "
              f"goal-unmet={row['goal_unmet']} refused={row['refused']} checks={row['check_runs']} tests={row['test_runs']}")
        print(f"   escape: exec-dupes={row['exec_verb_dupes']} python3-c={row['python3_c']} "
              f"shell={row['shell']} obs-chars={row['obs_chars']} "
              f"first-mutation-turn={row['first_mutation_turn']}")
        print(f"   verbs={row['verbs']} tools={row.get('tool_mix')} rejected={row.get('rejected')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
