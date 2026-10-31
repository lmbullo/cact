"""Read a TUI task journal back: tasks/<id>.json + events/<id>.jsonl -> a summary.

WHAT IT IS
  Post-hoc analysis for one task in a TUI state directory
  (`~/.local/state/code-tui/workspaces/<sha256-of-cwd,16 hex>/`). Counts which kernel verbs
  the model used, which files it wrote, steps, tokens, stop reason.

ENTRY POINTS
  python3 bench/runs.py <state-dir> <task-id>
  load(state_dir, task_id) -> (task, turns)
  summarize(state_dir, task_id) -> dict

INVARIANTS
  - Read-only: it never writes into the state dir.
  - `VERBS` is a regex list of `verb(` occurrences over the concatenated cell code, so it is
    an approximation, not a parse. Keep it in sync when the kernel gains a verb.
  - Malformed journal lines are skipped, not fatal: a killed worker leaves a partial file.

WHERE TO CHANGE
  New kernel verb ........ VERBS
  New metric ............. summarize()
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

VERBS = ("map", "read", "grep", "write", "edit", "run", "diff", "undo", "changes", "final_answer")


def load(state_dir: Path, task_id: str) -> tuple[dict, list[dict]]:
    task = json.loads((state_dir / "tasks" / f"{task_id}.json").read_text())
    turns = []
    for line in (state_dir / "events" / f"{task_id}.jsonl").read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") == "turn":
            turns.append(event.get("data") or {})
    return task, turns


def summarize(state_dir: Path, task_id: str) -> dict:
    task, turns = load(state_dir, task_id)
    code = "\n".join(turn.get("code") or "" for turn in turns)
    obs = "\n".join((turn.get("observation") or {}).get("stdout", "") + (turn.get("observation") or {}).get("stderr", "") for turn in turns)
    verbs = {verb: len(re.findall(rf"\b{verb}\(", code)) for verb in VERBS}
    writes = re.findall(r"write\(\s*['\"]([^'\"]+)", code)
    edits = re.findall(r"edit\(\s*['\"]([^'\"]+)", code)
    return {
        "id": task_id,
        "status": task.get("status"),
        "stop": task.get("stop"),
        "steps": len(turns),
        "untouched_src_writes": sum(1 for path in writes if path.startswith("src/")),
        "edits": len(edits),
        "verbs": verbs,
        "restarts": obs.count("empty namespace"),
        "goal_unmet": obs.count("final_answer rejected") + obs.count("without a passing check"),
        "refused": obs.count("refused_truncation"),
        "check_runs": len(re.findall(r"bun run typecheck", code)),
        "test_runs": len(re.findall(r"bun test", code)),
        "usage": (json.loads((state_dir / "tasks" / f"{task_id}.json").read_text()).get("usage") or {}),
    }


def main() -> int:
    state_dir = Path(sys.argv[1])
    for task_id in sys.argv[2:]:
        row = summarize(state_dir, task_id)
        print(f"{row['id']:22} status={row['status']:8} stop={str(row['stop']):12} steps={row['steps']:3}")
        print(f"   src-writes={row['untouched_src_writes']:2} edits={row['edits']:2} restarts={row['restarts']} "
              f"goal-unmet={row['goal_unmet']} refused={row['refused']} checks={row['check_runs']} tests={row['test_runs']}")
        print(f"   verbs={row['verbs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
