"""runner: execute one eval pass, aggregate by failure tag, append the evidence.

WHAT IT IS
  The one place that knows the registry and both execution modes.  run() selects cases, gives each
  a fresh temp workspace, builds the client (scripted offline, llm.Client live), turns a case
  exception into a failed row, and returns a Report; Report.text() is the table, write() appends.

ENTRY POINTS
  REGISTRY                         id -> Case (loop + live)
  run(ids=None, live=False, *, api_key="", model="deepseek-flash", effort="low") -> Report
  Report.rows/.passed/.failed/.usage/.by_failure, Report.text()/.write(tag="")  view, persist
  main(argv=None) -> int           the CLI behind bench/eval.py

INVARIANTS
  offline is the default and needs no key; live=True with no key is an error before any case
  runs, never a green pass over an empty live set.
  a case gets tempfile.mkdtemp(), removed after the row is recorded; an exception is ok=False
  with its text, so one broken eval cannot hide the rest.
  tokens_per_pass = total tokens / passes, None when nothing passed; write() names the file by
  UTC timestamp and mode; results/ is append-only.

WHERE TO CHANGE
  a new case .. its module's CASES, then REGISTRY;  CLI flags .. main;  row .. _row
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .live import LIVE_CASES
from .loops import LOOP_CASES
from .types import Case, CaseResult, Context

RESULTS = Path(__file__).resolve().parent / "results"
REGISTRY: dict[str, Case] = {case.id: case for case in (*LOOP_CASES, *LIVE_CASES)}


@dataclass
class Report:
    """One eval pass: the rows, the counters, and the aggregate cost per pass."""

    mode: str
    rows: list[tuple[Case, CaseResult]] = field(default_factory=list)
    wall_ms: float = 0.0

    @property
    def passed(self) -> int:
        """Cases whose oracle and loop contract both held."""
        return sum(1 for _, result in self.rows if result.ok)

    @property
    def failed(self) -> int:
        """Cases that did not pass."""
        return len(self.rows) - self.passed

    @property
    def usage(self) -> dict:
        """Summed provider usage across every case, including delegated children."""
        total: dict = {}
        for _, result in self.rows:
            for key, value in (result.usage or {}).items():
                if isinstance(value, (int, float)):
                    total[key] = total.get(key, 0) + value
        return total

    @property
    def tokens_per_pass(self) -> float | None:
        """Total tokens divided by passes, or None when nothing passed."""
        tokens = int(self.usage.get("total_tokens") or 0)
        return round(tokens / self.passed, 1) if self.passed else None

    def by_failure(self) -> dict[str, dict]:
        """Group rows by failure tag; the tag is the diagnostic axis, not the task id."""
        out: dict[str, dict] = {}
        for case, result in self.rows:
            bucket = out.setdefault(case.failure, {"n": 0, "pass": 0, "tokens": 0})
            bucket["n"] += 1
            bucket["pass"] += 1 if result.ok else 0
            bucket["tokens"] += int((result.usage or {}).get("total_tokens") or 0)
        return out

    def text(self) -> str:
        """The pass/fail table plus the failure-tag and cost summary."""
        lines = [f"mode={self.mode} cases={len(self.rows)} pass={self.passed} "
                 f"fail={self.failed} tokens={int(self.usage.get('total_tokens') or 0):,} "
                 f"tokens/pass={self.tokens_per_pass}"]
        for case, result in self.rows:
            mark = "PASS" if result.ok else "FAIL"
            lines.append(f"{mark} {case.id:18} {case.failure:12} "
                         f"stop={result.stop or '-':12} steps={result.steps:<3} "
                         f"err={result.errors:<3} "
                         f"tok={int((result.usage or {}).get('total_tokens') or 0):<7} "
                         f"{result.detail[:120]}")
        for tag, bucket in sorted(self.by_failure().items()):
            lines.append(f"[{tag}] {bucket['pass']}/{bucket['n']} passed, "
                         f"{bucket['tokens']:,} tokens")
        return "\n".join(lines)

    def write(self, tag: str = "") -> Path:
        """Append this report as a JSON evidence file; never rewrite an earlier one."""
        RESULTS.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        name = f"{stamp}-{self.mode}{('-' + tag) if tag else ''}.json"
        path = RESULTS / name
        payload = {
            "mode": self.mode,
            "wall_ms": round(self.wall_ms),
            "passed": self.passed,
            "failed": self.failed,
            "tokens_per_pass": self.tokens_per_pass,
            "usage": self.usage,
            "by_failure": self.by_failure(),
            "rows": [
                {
                    "id": case.id,
                    "failure": case.failure,
                    "title": case.title,
                    "kind": case.kind,
                    "ok": result.ok,
                    "detail": result.detail,
                    "stop": result.stop,
                    "steps": result.steps,
                    "errors": result.errors,
                    "usage": result.usage,
                    "final": result.final,
                    "meta": result.meta,
                }
                for case, result in self.rows
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path


def run(ids: list[str] | None = None, *, live: bool = False, api_key: str = "",
        model: str = "deepseek-flash", effort: str = "low",
        client_factory=None) -> Report:
    """Run the selected cases once; offline by default, live only with a key or a factory."""
    selected = [REGISTRY[name] for name in ids if name in REGISTRY] if ids else list(REGISTRY.values())
    selected = [case for case in selected if (case.kind == "live") == live]
    if not selected:
        raise ValueError("no cases selected for this mode")
    if live and client_factory is None and not api_key:
        raise ValueError("live evals need an API key")

    def factory():
        """A fresh client per case: one live case cannot reuse another's request history."""
        if client_factory is not None:
            return client_factory()
        from ..agent.llm import Client

        return Client(api_key, model=model, effort=effort)

    report = Report(mode="live" if live else "loop")
    started = time.perf_counter()
    for case in selected:
        root = Path(tempfile.mkdtemp(prefix=f"eval-{case.id}-"))
        try:
            ctx = Context(workspace=root, client_factory=factory,
                          options={"model": model, "effort": effort})
            result = case.fn(ctx)
        except Exception as exc:  # noqa: BLE001 - one case cannot abort the pass
            result = CaseResult(ok=False, detail=f"case raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(root, ignore_errors=True)
        result.meta = {**(result.meta or {}), "workspace_removed": not root.exists()}
        report.rows.append((case, result))
    report.wall_ms = (time.perf_counter() - started) * 1000.0
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI: list cases, run one offline pass, or run the live failure-mode suite."""
    parser = argparse.ArgumentParser(prog="bench/eval.py",
                                     description="Agent evals: loop contracts and live failures.")
    parser.add_argument("--list", action="store_true", help="print the registry")
    parser.add_argument("--live", action="store_true", help="run the model-backed cases")
    parser.add_argument("--tasks", default="", help="comma-separated case ids")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--tag", default="", help="suffix for the persisted result file")
    parser.add_argument("--no-write", action="store_true", help="print only; persist nothing")
    args = parser.parse_args(argv)

    if args.list:
        for case in REGISTRY.values():
            print(f"{case.kind:5} {case.id:18} {case.failure:12} {case.title}")
        return 0
    api_key = ""
    if args.live:
        from ..live import load_key
        api_key = load_key()
        if not api_key:
            print("live evals need DEEPSEEK_API_KEY", file=sys.stderr)
            return 2
    ids = [name.strip() for name in args.tasks.split(",") if name.strip()] or None
    try:
        report = run(ids, live=args.live, api_key=api_key, model=args.model,
                     effort=args.effort)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(report.text())
    if not args.no_write:
        print(f"\nresults: {report.write(args.tag)}")
    return 0 if report.failed == 0 else 1
