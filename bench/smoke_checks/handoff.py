"""handoff: the structured finish answer survives into the next task's prompt.
WHAT IT IS
  Cross-task half: worker.py appends history.jsonl and prepends a compact `[workspace history]`.
  Checks prove a dict final_answer reaches record, prefix, legacy and cap.
ENTRY POINTS
  run(check)   called by bench/smoke.py::main after gates
INVARIANTS
  pure python + TemporaryDirectory: no kernel, no network, nothing in the real state dir.
  XDG_STATE_HOME is set and restored in finally; CODE_TUI_STATE/CODE_TUI_STATE_ROOT cleared.
  drives real record_history / history_brief, never a copy of their format.
  the fake result has the loop fields: final, stop, handoff.
WHERE TO CHANGE
  row format ........ tui/python/worker_history.py::record_history
  prompt prefix ..... tui/python/worker_history.py::history_brief
  rendered block .... bench/agent/handoff.py
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from tui.python import worker


def _result(final="", handoff=None, stop="DONE"):
    """The loop-shaped result record_history consumes."""
    return SimpleNamespace(final=final, stop=stop, handoff=handoff or {})


def run(check):
    """Record a structured answer, then read it back through the next task's prefix."""
    old = os.environ.get("XDG_STATE_HOME")
    explicit = {name: os.environ.pop(name, None)
                for name in ("CODE_TUI_STATE", "CODE_TUI_STATE_ROOT")}
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_STATE_HOME"] = tmp
        try:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            worker.record_history(workspace, "first task", _result(
                final="rendered block",
                handoff={
                    "summary": "added the thing",
                    "changed": ["a.py", "b.py"],
                    "gates": [{"name": "smoke", "ok": True, "exit": 0}],
                    "deferred": ["next task: wire the flag"],
                    "untested": ["the retry path"],
                },
            ), ["a.py", "b.py"])
            path = worker.state_dir(workspace) / "history.jsonl"
            row = json.loads(path.read_text().splitlines()[-1])
            check("handoff: history.jsonl keeps the structured fields",
                  row.get("deferred") == ["next task: wire the flag"]
                  and row.get("untested") == ["the retry path"]
                  and row.get("changed") == ["a.py", "b.py"]
                  and row.get("gates") == [{"name": "smoke", "ok": True, "exit": 0}],
                  json.dumps(row)[:300])

            brief = worker.history_brief(workspace)
            check("handoff: the next task's prefix carries the deferred line",
                  "deferred: next task: wire the flag" in brief, brief[:400])
            check("handoff: the prefix also carries the untested line",
                  "untested: the retry path" in brief, brief[:400])

            worker.record_history(workspace, "mixed gates", _result(
                final="mixed",
                handoff={"summary": "one gate passed, one failed",
                        "changed": ["c.py"],
                        "gates": [{"name": "smoke", "ok": True, "exit": 0},
                                  {"cmd": "ruff check .", "result": "3 errors", "exit": 1}]},
            ), ["c.py"])
            row = json.loads(path.read_text().splitlines()[-1])
            check("handoff: a mixed gate list persists each row's failure",
                  [gate.get("ok") for gate in row.get("gates") or []] == [True, False]
                  and (row.get("gates") or [{}])[1].get("exit") == 1,
                  json.dumps(row.get("gates"))[:300])

            path.write_text(
                json.dumps({"when": "09:00", "prompt": "legacy", "summary": "old row",
                            "files": ["old.py"], "stop": "DONE"}) + "\n"
                + "\n".join(path.read_text().splitlines()) + "\n"
            )
            legacy = worker.history_brief(workspace)
            check("handoff: an old history line still parses",
                  "old row" in legacy and "deferred: next task: wire the flag" in legacy,
                  legacy[:400])

            for index in range(8):
                worker.record_history(workspace, "task %d" % index, _result(
                    final="done %d" % index,
                    handoff={"summary": "task %d" % index,
                             "deferred": ["open item %d" % index],
                             "untested": ["untested item %d" % index]},
                ), [])
            capped = worker.history_brief(workspace)
            opens = [line for line in capped.splitlines()
                     if "deferred:" in line or "untested:" in line]
            check("handoff: the prefix caps open items and keeps the newest first",
                  len(opens) <= 6 and "open item 7" in capped and "open item 0" not in capped,
                  "\n".join(opens))
        finally:
            if old is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = old
            for name, value in explicit.items():
                if value is not None:
                    os.environ[name] = value
