"""task_journal: read a TUI task journal back — tasks/<id>.json + events/<id>.jsonl -> summary.

WHAT IT IS
  Post-hoc analysis for one task in a TUI state directory
  (`~/.local/state/code-tui/workspaces/<sha256-of-cwd,16 hex>/`). Counts the typed call mix
  (`tools`), the kernel verbs the model used, which files it wrote, steps, tokens, stop reason
  — and the escape hatch: how often `exec` stood in for a typed verb (`exec_verb_dupes`), ran
  `python3 -c` or a shell string, how many observation characters reached the model
  (`obs_chars`, `obs_chars_per_turn`) and how many card characters were delivered against
  the turn budget (`card_chars`, `card_chars_per_turn`, `card_budget`), and the turn of the
  first mutating cell (`first_mutation_turn`). The bookkeeping keys measure the tax: `out_tokens` and
  `reasoning_tokens`, `gate_cells` (an exec cell whose commands are all exact plan specs),
  `narrow_cells` (the same program with extra args, e.g. one test file), `git_read_cells`
  (status/log/diff/show) and `git_write_cells` (add/commit/push), with `git_cells` the total,
  and `verify_turns` (turns after the first mutation that ran no mutation).
  `checks_calls`/`status_calls`/`docs_calls` count
  parent-side tools from `turn.tool_calls`, because those calls emit no `cell.start`. The
  causal keys say *why* behavior changed: the turn the typed-dupe
  `note` first appeared (`dupe_note_turn`), the dupes after it (`dupes_after_note`), and how
  often a read/map/search resumed the page the previous result left open (`paged_followups`).
  (No benchmark CLI runs any more: `bench/eval.py` is the eval front end; this is the reader
  for the journals the TUI records.)

ENTRY POINTS
  python3 bench/task_journal.py <state-dir> <task-id>
  python3 bench/task_journal.py --compare <state-dir> <task-a> <task-b>  the two rows + deltas
  python3 bench/task_journal.py --recent [N]  every workspace's latest tasks, newest first
  load(state_dir, task_id) -> (task, turns)
  summarize(state_dir, task_id) -> dict
  compare(state_dir, task_ids) -> int
  recent(state_root, limit) -> list[dict]

INVARIANTS
  - Read-only: it never writes into the state dir.
  - A TUI run's counts come from its `cell.start` records (one per call: `tool` + `args`), not
    from the python `turn.code` the TUI never emits. `sum(tools.values()) == cells`, by
    construction and asserted; that one line would have caught the regex counters reading zero.
  - A new TUI run also writes one compact `cell.end` per call (`ok`, `code`, `page`, `chars`,
    `notes`, no bodies). When present, `obs_chars`, `rejected` and `dupe_note_turn` read those
    records; the archived `turn.observation` shape remains the fallback. `restarts` reads the
    observation's boolean `restarted`, never the phrase "empty namespace" in delivered text.
  - The `turn.code` regex path survives only for the archived journal shape (turns with
    `calls`/`code` and no `cell.start`); on a TUI run `VERBS` is just the tool mix.
  - Malformed journal lines are skipped, not fatal: a killed worker leaves a partial file.
  - The escape metrics read `cell.start` events, so a folded or digested cell still counts:
    the number is about the call the model made, not about what the card kept.

WHERE TO CHANGE
  New kernel verb ........ VERBS
  New escape metric ...... EXEC_DUPES, MUTATING_VERBS, _escape, summarize()
  New bookkeeping metric . _plan_specs, _plan_cells, _git_cells, _verify_turns, _token_sum
  New parent-side metric . _tool_call_count, summarize()
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
# The kernel puts this on an exec result the first time a program repeats a typed verb.
DUPE_NOTE = "repeats a typed verb"
# A check/test run, matched on the command line a cell ran; the archived shape matched the
# same strings inside the python cell's code.
CHECK_COMMAND = "bun run typecheck"
TEST_COMMAND = "bun test"
# A cell that ran a plan command by hand. It is bookkeeping, not project work: `checks()`
# answers the same plan in one call and reuses the Verifier's cache. The plan is server.json's
# `checks`; the match rule is run_prelude._spec_names: same program, same args.
_GIT_READ = frozenset({"status", "log", "diff", "show"})
_GIT_WRITE = frozenset({"add", "commit", "push"})
_SHELL_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


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


def _tool_mix(cells: list[dict]) -> dict[str, int]:
    """One count per `cell.start`, keyed by tool: sum(values) == len(cells) by construction."""
    counts: dict[str, int] = {}
    for cell in cells:
        name = str(cell.get("tool") or "")
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _call_mix(turns: list[dict]) -> dict[str, int]:
    """The archived shape: one `calls` entry per call, with no `cell.start` records."""
    counts: dict[str, int] = {}
    for turn in turns:
        for call in turn.get("calls") or []:
            name = str(call.get("name") or "")
            if name:
                counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _tool_call_count(turns: list[dict], name: str) -> int:
    """Calls to one parent-side tool from `turn.tool_calls`: `cell.start` cannot see them."""
    total = 0
    for turn in turns:
        for call in turn.get("tool_calls") or []:
            if isinstance(call, dict):
                call = (call.get("function") or {}).get("name") or call.get("name")
            if str(call) == name:
                total += 1
    return total


def _commands(cell: dict) -> list[str]:
    """Every command line one cell ran: a shell string, one argv, or each entry of `commands`."""
    args = cell.get("args") or {}
    lines = [] if args.get("shell") is None else [str(args["shell"])]
    argvs = [args["argv"]] if args.get("argv") is not None else list(args.get("commands") or [])
    for argv in argvs:
        lines.append(argv if isinstance(argv, str) else " ".join(str(part) for part in argv or []))
    return lines


def _escape(cells: list[dict]) -> dict:
    """How often exec stood in for a verb: the counts a cheaper affordance would have removed."""
    dupes = python3_c = shell = 0
    for cell in cells:
        if cell.get("tool") != "exec":
            continue
        if (cell.get("args") or {}).get("shell") is not None:
            shell += 1
        for line in _commands(cell):
            parts = line.split()
            if not parts:
                continue
            if parts[0] in EXEC_DUPES:
                dupes += 1
            if parts[0] in ("python3", "python") and "-c" in parts:
                python3_c += 1
    return {"exec_verb_dupes": dupes, "python3_c": python3_c, "shell": shell}


def _rejected(turns: list[dict], cells: list[dict] | None = None) -> int:
    """Calls a typed verb refused (or a verb crashed); the archived fallback reads turns."""
    if cells is not None:
        return sum(1 for cell in cells if not cell.get("ok", True))
    archived = sum(1 for turn in turns
                   for call in turn.get("calls") or []
                   if (call.get("observation") or {}).get("rejected")
                   or (call.get("observation") or {}).get("exit") == "error")
    return archived + sum(1 for turn in turns
                          if (turn.get("observation") or {}).get("exit") == "error")


def _turn_cells(turns: list[dict], cells: list[dict]) -> list[tuple[int, list[dict]]]:
    """`(turn number, its cells)` in journal order; archived calls without a cell become `{tool}`."""
    grouped: list[tuple[int, list[dict]]] = []
    index = 0
    for turn in turns:
        calls = turn.get("calls") or turn.get("tool_calls") or []
        items = []
        for call in calls:
            if index < len(cells):
                items.append(cells[index])
            else:
                name = call.get("name") if isinstance(call, dict) else call
                items.append({"tool": str(name or "")})
            index += 1
        grouped.append((int(turn.get("turn") or 0), items))
    return grouped


def _first_mutation_turn(turns: list[dict], cells: list[dict]) -> int | None:
    """The 1-based turn that held the first mutating cell; None when nothing was changed."""
    for turn, items in _turn_cells(turns, cells):
        if any(str(cell.get("tool") or "") in MUTATING_VERBS for cell in items):
            return turn
    return None


def _plan_specs(state_dir: Path) -> list[str]:
    """The plan the worker was started with: server.json's `checks`, else no plan."""
    try:
        info = json.loads((state_dir / "server.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return [str(item.get("cmd")) for item in info.get("checks") or []
            if isinstance(item, dict) and item.get("cmd")]


def _plan_program(command: str) -> str:
    """The program part: `cd tui && bun test` and `bun test` (cwd=tui) are the same program."""
    segments = [part.strip() for part in _SHELL_SPLIT.split(
        " ".join(str(command).split())) if part.strip()]
    for segment in reversed(segments):
        if not segment.startswith("cd "):
            return segment
    return segments[-1] if segments else ""


def _plan_match(command: str, specs: list[str]) -> bool:
    """True when the command is exactly a plan spec: same program, no extra arguments."""
    program = _plan_program(command)
    return bool(program) and any(_plan_program(spec) == program for spec in specs)


def _plan_narrow(command: str, specs: list[str]) -> bool:
    """True when the command narrows a plan spec with extra args (a file, `-t name`)."""
    parts = _plan_program(command).split()
    return any(parts[:len(program.split())] == program.split()
               and len(parts) > len(program.split())
               for program in map(_plan_program, specs))


def _plan_cells(cells: list[dict], specs: list[str], narrow: bool = False) -> int:
    """exec cells the plan could have answered: every command exact (or narrowed when asked)."""
    count = 0
    for cell in cells:
        if cell.get("tool") != "exec":
            continue
        lines = _commands(cell)
        if not lines:
            continue
        if narrow:
            if (all(_plan_match(line, specs) or _plan_narrow(line, specs) for line in lines)
                    and any(_plan_narrow(line, specs) for line in lines)):
                count += 1
        elif all(_plan_match(line, specs) for line in lines):
            count += 1
    return count


def _git_cells(cells: list[dict]) -> tuple[int, int]:
    """(read-only, mutating) git cells: the typed `git` tool or `git ...` through exec."""
    read = write = 0
    for cell in cells:
        actions = []
        if cell.get("tool") == "git":
            actions.append(str((cell.get("args") or {}).get("action") or ""))
        elif cell.get("tool") == "exec":
            for line in _commands(cell):
                parts = line.split()
                if parts and os.path.basename(parts[0]) == "git":
                    actions.append(next((part for part in parts[1:]
                                         if not part.startswith("-")), ""))
        for action in actions:
            if action in _GIT_READ:
                read += 1
            elif action in _GIT_WRITE:
                write += 1
    return read, write


def _verify_turns(turns: list[dict], cells: list[dict]) -> int:
    """Turns after the first mutation that ran no mutation: the verification detour."""
    grouped = _turn_cells(turns, cells)
    first = _first_mutation_turn(turns, cells)
    if first is None:
        return 0
    return sum(1 for turn, items in grouped if turn >= first
               and not any(str(cell.get("tool") or "") in MUTATING_VERBS for cell in items))


def _token_sum(turns: list[dict], key: str) -> int:
    """Sum one provider usage field over every turn; reasoning sits in completion details."""
    total = 0
    for turn in turns:
        usage = turn.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        value = details.get(key, usage.get(key))
        try:
            total += int(value or 0)
        except (TypeError, ValueError):
            continue
    return total


def _dupe_note(events: list[dict], cell_ends: bool = False) -> tuple[int | None, int | None]:
    """The first turn whose exec result carried the typed-dupe note, and its event index.

    New journals carry the note on `cell.end`; the owning `turn` record follows its cells, so
    the first later `turn` names the turn. The archived shape reads `turn.observation`.
    """
    if cell_ends:
        for index, event in enumerate(events):
            if event.get("kind") != "cell.end":
                continue
            notes = event.get("notes") or []
            if not any(DUPE_NOTE in str(item) for item in notes):
                continue
            turn = next((int((later.get("data") or {}).get("turn") or 0)
                         for later in events[index + 1:] if later.get("kind") == "turn"), None)
            return turn, index
        return None, None
    for index, event in enumerate(events):
        if event.get("kind") != "turn":
            continue
        data = event.get("data") or {}
        observation = data.get("observation") or {}
        if str(observation.get("tool") or "") != "exec":
            continue
        result = observation.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                continue
        if not isinstance(result, dict):
            continue
        note = result.get("note")
        notes = note if isinstance(note, list) else ([note] if note else [])
        if any(DUPE_NOTE in str(item) for item in notes):
            return int(data.get("turn") or 0), index
    return None, None


def _page_key(args: dict) -> str | None:
    """The page a cell addresses: its path, else its pattern, normalized to one string."""
    key = args.get("path", args.get("pattern"))
    if isinstance(key, list):
        return " ".join(str(part) for part in key)
    return None if key is None else str(key)


def _paged_followups(events: list[dict]) -> int:
    """How often a read/map/search cell resumes the page the previous result left open."""
    count = 0
    pending: dict | None = None
    previous: dict[str, str | None] = {}
    for event in events:
        if event.get("kind") == "cell.start":
            tool = str(event.get("tool") or "")
            if pending and tool == pending["tool"]:
                args = event.get("args") or {}
                cursor = args.get("start", args.get("offset"))
                key = _page_key(args)
                same_page = (pending["key"] == key if pending["key"]
                             else previous.get(tool) == key)
                if (cursor is not None and str(cursor) == str(pending["offset"]) and same_page):
                    count += 1
            previous[tool] = _page_key(event.get("args") or {})
            continue
        if event.get("kind") != "turn":
            continue
        data = event.get("data") or {}
        observation = data.get("observation") or {}
        result = observation.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = None
        if not isinstance(result, dict):
            pending = None
            continue
        nxt = result.get("next")
        offset = nxt.get("offset") if isinstance(nxt, dict) else None
        pending = ({"tool": str(observation.get("tool") or ""), "offset": offset,
                    "key": str(result.get("path") or "")}
                   if offset is not None else None)
    return count


def summarize(state_dir: Path, task_id: str) -> dict:
    """The one-row metrics dict printed by `main()`; keys are the contract."""
    task, turns = load(state_dir, task_id)
    events = _events(state_dir, task_id)
    records = [event for event in events if event.get("kind") == "cell.start"]
    cell_ends = [event for event in events if event.get("kind") == "cell.end"]
    obs = "\n".join((turn.get("observation") or {}).get("stdout", "")
                    + (turn.get("observation") or {}).get("stderr", "") for turn in turns)
    if cell_ends:
        # One record per call: chars are len(stdout)+len(json result) as delivered, so a
        # 3-call turn sums all three without re-reading any payload body.
        obs_chars = sum(int(event.get("chars") or 0) for event in cell_ends)
    else:
        obs_chars = len(obs)
    if records:
        # The TUI shape: one `cell.start` per call carries the tool and its args, so the counts
        # are the truth. The regexes below are the archived shape's only record.
        tools = _tool_mix(records)
        verbs = {verb: tools.get(verb, 0) for verb in VERBS}
        writes = [str((cell.get("args") or {}).get("path") or "")
                  for cell in records if cell.get("tool") == "write"]
        edits = tools.get("edit", 0)
        commands = [line for cell in records for line in _commands(cell)]
        checks = sum(1 for line in commands if CHECK_COMMAND in line)
        tests = sum(1 for line in commands if TEST_COMMAND in line)
    else:
        code = "\n".join(turn.get("code") or "" for turn in turns)
        tools = _call_mix(turns)
        verbs = {verb: len(re.findall(rf"\b{verb}\(", code)) for verb in VERBS}
        writes = re.findall(r"write\(\s*['\"]([^'\"]+)", code)
        edits = len(re.findall(r"edit\(\s*['\"]([^'\"]+)", code))
        checks = code.count(CHECK_COMMAND)
        tests = code.count(TEST_COMMAND)
    cells = len(records) or sum(tools.values())
    # One fact one place: every call is exactly one cell, so the tool mix covers the count.
    assert sum(tools.values()) == cells, (task_id, tools, cells)
    note_turn, note_index = _dupe_note(events, cell_ends=bool(cell_ends))
    after_note = _escape([event for index, event in enumerate(events)
                          if note_index is not None and index > note_index
                          and event.get("kind") == "cell.start"])
    plan_specs = _plan_specs(state_dir)
    git_read, git_write = _git_cells(records)
    return {
        "id": task_id,
        "status": task.get("status"),
        "stop": task.get("stop"),
        "steps": len(turns),
        "cells": cells,
        "untouched_src_writes": sum(1 for path in writes if path.startswith("src/")),
        "edits": edits,
        "verbs": verbs,
        "tools": tools,
        "rejected": _rejected(turns, cell_ends if cell_ends else None),
        "restarts": sum(1 for turn in turns
                        if (turn.get("observation") or {}).get("restarted")),
        "goal_unmet": obs.count("final_answer rejected") + obs.count("without a passing check"),
        "refused": obs.count("refused_truncation"),
        "check_runs": checks,
        "test_runs": tests,
        # Parent-side verbs run in the loop, not the kernel: `tool_calls` is the only record.
        "checks_calls": _tool_call_count(turns, "checks"),
        "status_calls": _tool_call_count(turns, "status"),
        "docs_calls": _tool_call_count(turns, "docs"),
        **_escape(records),
        "obs_chars": obs_chars,
        "obs_chars_per_turn": round(obs_chars / len(turns)) if turns else 0,
        "card_chars": sum(int(turn.get("card_chars") or 0) for turn in turns),
        "card_chars_per_turn": (round(sum(int(turn.get("card_chars") or 0)
                                        for turn in turns) / len(turns))
                               if turns else 0),
        "card_budget": next((int(turn.get("card_budget") or 0) for turn in turns
                            if turn.get("card_budget")), 0),
        "out_tokens": _token_sum(turns, "completion_tokens"),
        "reasoning_tokens": _token_sum(turns, "reasoning_tokens"),
        "gate_cells": _plan_cells(records, plan_specs),
        "narrow_cells": _plan_cells(records, plan_specs, narrow=True),
        "git_cells": git_read + git_write,
        "git_read_cells": git_read,
        "git_write_cells": git_write,
        "verify_turns": _verify_turns(turns, records),
        "first_mutation_turn": _first_mutation_turn(turns, records),
        "dupe_note_turn": note_turn,
        "dupes_after_note": after_note["exec_verb_dupes"] if note_index is not None else None,
        "paged_followups": _paged_followups(events),
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


def compare(state_dir: Path, task_ids: list[str]) -> int:
    """The same one-row dict for two task ids, side by side with deltas: a reproducible A/B."""
    rows = [summarize(state_dir, task_id) for task_id in task_ids]
    keys = [key for key in rows[0] if key != "id"
            and all(not isinstance(row.get(key), (dict, list)) for row in rows)]
    print(f"{'key':22} " + " ".join(f"{row['id']:>20}" for row in rows) + "        delta")
    for key in keys:
        values = [row.get(key) for row in rows]
        delta = (values[1] - values[0]
                 if len(values) == 2 and all(isinstance(value, int) for value in values) else "")
        print(f"{key:22} " + " ".join(f"{str(value):>20}" for value in values) + f"   {delta:>10}")
    return 0


def main() -> int:
    """CLI: `--recent [N]`, `--compare <state-dir> A B`, or `<state-dir> <task-id>...`."""
    if len(sys.argv) < 2:
        print("usage: python3 bench/task_journal.py <state-dir> <task-id>... | --compare "
              "<state-dir> A B | --recent [N]", file=sys.stderr)
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
    if sys.argv[1] == "--compare":
        if len(sys.argv) < 4:
            print("usage: python3 bench/task_journal.py --compare <state-dir> A B", file=sys.stderr)
            return 2
        return compare(Path(sys.argv[2]), sys.argv[3:5])
    state_dir = Path(sys.argv[1])
    for task_id in sys.argv[2:]:
        row = summarize(state_dir, task_id)
        print(f"{row['id']:22} status={row['status']:8} stop={str(row['stop']):12} steps={row['steps']:3}")
        print(f"   src-writes={row['untouched_src_writes']:2} edits={row['edits']:2} restarts={row['restarts']} "
              f"goal-unmet={row['goal_unmet']} refused={row['refused']} checks={row['check_runs']} tests={row['test_runs']}")
        print(f"   escape: exec-dupes={row['exec_verb_dupes']} python3-c={row['python3_c']} "
              f"shell={row['shell']} obs-chars={row['obs_chars']} "
              f"first-mutation-turn={row['first_mutation_turn']}")
        print(f"   causal: note-turn={row['dupe_note_turn']} dupes-after-note={row['dupes_after_note']} "
              f"paged-followups={row['paged_followups']} obs-chars-per-turn={row['obs_chars_per_turn']}")
        print(f"   bookkeeping: gate-cells={row['gate_cells']} narrow-cells={row['narrow_cells']} "
              f"git-cells={row['git_cells']} (read={row['git_read_cells']} "
              f"write={row['git_write_cells']}) "
              f"verify-turns={row['verify_turns']} out-tokens={row['out_tokens']} "
              f"reasoning-tokens={row['reasoning_tokens']}")
        print(f"   parent: checks_calls={row['checks_calls']} status_calls={row['status_calls']} "
              f"docs_calls={row['docs_calls']}")
        print(f"   verbs={row['verbs']}")
        print(f"   tools={row['tools']} rejected={row.get('rejected')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
