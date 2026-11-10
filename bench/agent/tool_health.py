"""tool_health: ToolHealth: the checks cell, the cheap check and the queued _OBS entry.
WHAT IT IS
  The verification half of ToolLoop, as a mixin: _checks_cell builds the parent-side `checks` cell
  from the Verifier's specs and cache; _refresh_health runs the cheap check after a mutation and
  records it as the next card's `[check]` line; _queue_check_obs turns a failing check's full text
  into the next _OBS entry; _check_cache/_take_extra_obs carry that state across a request.
ENTRY POINTS
  ToolHealth  mixin base of tool_loop.ToolLoop
  _checks_cell(args) -> Cell .............. `checks(name=None)`: the plan's rows, cached
  _refresh_health(record, cell) ........... the cheap check after a mutation
  _queue_check_obs(cell) ................. a failing check's text -> the next _OBS handle
  _check_cache() -> dict; _take_extra_obs() -> list[dict]
INVARIANTS
  checks(name) never shells out a command the cheap check already passed: both read the same
  Verifier cache, and a mutation drops it.
  a failing row is data (kind:"checks", rows, failed), not a refusal; an unknown name is a refusal
  whose error.use lists the plan's names.
  the cheap check never ends the run and never raises: a broken one leaves last_check None and
  the turn continues; a failing check is one card line, its full text queued as extra_obs.
WHERE TO CHANGE
  the checks cell .................. _checks_cell (check_plan.run_each runs the specs)
  cheap-check policy ............... _refresh_health (throttle and scope live in check_plan.py)
  the queued handle ................ _queue_check_obs
"""

from __future__ import annotations

import json
import time

from .check_plan import run_each
from .check_run import Check
from .kernel.protocol import Cell, CellReply
from .records import CallRecord


class ToolHealth:
    """The checks cell, the cheap check and the queued observation; mixed into ToolLoop."""

    def _check_cache(self) -> dict:
        return self.verifier.cache() if self.verifier is not None else self.check_cache

    def _take_extra_obs(self) -> list[dict]:
        """The parent-side observations queued for the next kernel request; empties the queue."""
        extra = self._extra_obs
        self._extra_obs = []
        return extra

    def _checks_cell(self, args: dict) -> Cell:
        """`checks(name=None)`: the plan's rows, reusing every cached green spec.

        Parent-side, like `docs`: it needs the Verifier the worker built, not the kernel. A
        failing row is data, not a refusal; an unknown `name` is a refusal with the plan's
        names. The synthetic Cell goes through the same observation renderer as a typed call.
        """
        started = time.perf_counter()
        name = str(args.get("name") or "").strip() or None
        specs = list(getattr(self.verifier, "specs", ()) or ()) if self.verifier is not None else []
        payload: dict = {"ok": True, "kind": "checks", "rows": [], "passed": 0, "failed": 0}
        rejected = False
        if not specs:
            payload["text"] = "no check plan is configured for this run"
        elif name is not None and name not in [spec.name for spec in specs]:
            rejected = True
            payload.update({"ok": False, "kind": "checks", "error": {
                "code": "unknown_check",
                "message": "no check spec named %r" % name,
                "use": "available: " + ", ".join(spec.name for spec in specs)}})
            payload["text"] = "no check spec named %r; available: %s" % (
                name, ", ".join(spec.name for spec in specs))
        else:
            rows = run_each(specs, self.verifier.cwd, cache=self.verifier.cache(), name=name)
            passed = sum(1 for row in rows if row["ok"])
            failed = len(rows) - passed
            lines = ["[checks] %d spec(s): %d passed, %d failed" % (len(rows), passed, failed)]
            for row in rows:
                if row["ok"]:
                    detail = " | " + row["detail"] if row.get("detail") else ""
                    lines.append("- %s: ok exit=%s%s" % (row["name"], row["exit"], detail))
                else:
                    lines.append("- %s: FAIL exit=%s: %s" % (
                        row["name"], row["exit"], row["first_line"] or "failed"))
            payload.update({"rows": rows, "passed": passed, "failed": failed,
                            "ok": failed == 0, "text": "\n".join(lines)})
        reply = CellReply(tool="checks", stdout=str(payload.get("text") or ""),
                          result=json.dumps(payload))
        cell = Cell.from_reply(reply, duration_ms=int((time.perf_counter() - started) * 1000))
        cell.rejected = rejected
        return cell

    def _refresh_health(self, record: CallRecord, cell) -> None:
        """Refresh the cheap check after a mutation, at most once every 20s."""
        if self.verifier is not None:
            paths = sorted(cell.changed or ())
            if not self.verifier.should_run(paths, time.perf_counter()):
                return
            try:
                self.verifier.run(paths)
            except Exception:  # noqa: BLE001 - a broken health check never stops the run
                self.last_check = None
            else:
                self.last_check = self.verifier.last
            self.check_cache = self.verifier.cache()
            self._queue_check_obs(cell)
            if self.last_check is not None:
                record.check = {"ok": bool(self.last_check.ok), "cmd": self.last_check.cmd}
                self._surprise = self._surprise or not self.last_check.ok
            return
        if self.health is None:
            return
        now = time.perf_counter()
        if now - self._health_at < 20.0:
            return
        self._health_at = now
        try:
            outcome = self.health(sorted(cell.changed))
        except Exception:  # noqa: BLE001 - a broken health check never stops the run
            outcome = None
        if isinstance(outcome, tuple):
            self.last_check, results = outcome
            self.check_cache = {cmd: {"text": item.text(600), "ok": bool(item.ok)}
                                for cmd, item in results.items() if item is not None}
        else:
            self.last_check = outcome
        if isinstance(self.last_check, bool):
            self.last_check = Check(cmd="health", state="passed" if self.last_check else "failed")
        self._queue_check_obs(cell)
        if self.last_check is not None:
            record.check = {"ok": bool(self.last_check.ok), "cmd": self.last_check.cmd}

    def _queue_check_obs(self, cell) -> None:
        """A failing check's full text becomes the next `_OBS` entry; the card names its index."""
        self.last_check_handle = None
        if self.last_check is None or self.last_check.ok:
            return
        if getattr(cell, "obs_index", None) is None:
            return
        index = int(cell.obs_index) + 1 + len(self._extra_obs)
        self._extra_obs.append({"stdout": self.last_check.text(), "stderr": "", "result": None})
        self.last_check_handle = "_OBS[%d]" % index
