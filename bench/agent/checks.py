from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .check import Check, run_check


@dataclass
class CheckSpec:
    name: str
    cmd: str
    cheap: bool = False

    @staticmethod
    def parse(raw: Any) -> list["CheckSpec"]:
        if isinstance(raw, str):
            return [CheckSpec(name="check", cmd=raw, cheap=False)] if raw.strip() else []
        if not isinstance(raw, list):
            return []
        specs: list[CheckSpec] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            cmd = str(item.get("cmd") or item.get("command") or "").strip()
            if not cmd:
                continue
            specs.append(CheckSpec(
                name=str(item.get("name") or cmd[:40]),
                cmd=cmd,
                cheap=bool(item.get("cheap", False)),
            ))
        return specs


def run_specs(specs: Iterable[CheckSpec], cwd: str) -> Check | None:
    specs = list(specs)
    if not specs:
        return None
    for spec in specs:
        result = run_check(spec.cmd, cwd)
        if not result.ok:
            if result.state == "unrunnable":
                return Check(cmd=f"{spec.name}: {spec.cmd}", state="unrunnable",
                             stdout=result.stdout, stderr=result.stderr)
            return Check(cmd=f"{spec.name}: {spec.cmd}", state="failed",
                         exit_code=result.exit_code, duration_ms=result.duration_ms,
                         stdout=result.stdout, stderr=result.stderr, failures=result.failures)
    return Check(cmd=", ".join(spec.name for spec in specs), state="passed",
                 exit_code=0, duration_ms=0)
