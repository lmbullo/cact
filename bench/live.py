from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from bench.agent.check import run_check
from bench import ws

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "@@TUI@@"
VERBS = ("map", "read", "grep", "write", "edit", "run", "diff", "undo", "changes", "final_answer")


def load_key() -> str:
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return os.environ["DEEPSEEK_API_KEY"].strip()
    for name in ("env", ".env"):
        path = Path.home() / ".config" / "code-tui" / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if line.strip().startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--check", default="bun run typecheck")
    parser.add_argument("--cell-timeout", type=float, default=120.0)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--max-wall", type=float, default=1800.0)
    parser.add_argument("--legacy", default="none", choices=["none", "r1", "r3", "both"])
    args = parser.parse_args()

    base = Path(args.base).resolve()
    run = ROOT / "bench" / "ws" / "runs" / args.tag
    ws.clone(base, run)
    prompt_path = run.parent / f"{args.tag}.prompt.txt"
    prompt_path.write_text(args.prompt)
    events_path = run.parent / f"{args.tag}.events.jsonl"

    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = load_key()
    if args.legacy in ("r1", "both"):
        env["CODE_TUI_LEGACY_TIMEOUT"] = "1"
    if args.legacy in ("r3", "both"):
        env["CODE_TUI_LEGACY_CHECK"] = "1"

    started = time.time()
    cmd = [
        sys.executable, str(ROOT / "tui" / "python" / "worker.py"),
        "--workspace", str(run), "--prompt-file", str(prompt_path),
        "--check", args.check, "--cell-timeout", str(args.cell_timeout),
        "--max-steps", str(args.max_steps), "--max-wall", str(args.max_wall),
    ]
    print(f"run {args.tag} legacy={args.legacy} check={args.check!r} cell={args.cell_timeout}s")
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    events = []
    final = None
    with events_path.open("w") as handle:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line.startswith(PREFIX):
                print("  worker:", line[:200])
                continue
            try:
                event = json.loads(line[len(PREFIX):])
            except json.JSONDecodeError:
                continue
            events.append(event)
            handle.write(json.dumps(event) + "\n")
            kind = event.get("kind")
            if kind == "turn":
                data = event.get("data") or {}
                obs = data.get("observation") or {}
                print(f"  turn {data.get('turn'):>3} stop={data.get('stop')} out={len(obs.get('stdout') or '')}")
            elif kind == "final":
                final = event
    proc.wait(timeout=60)
    wall = time.time() - started

    turns = [event.get("data") or {} for event in events if event.get("kind") == "turn"]
    code = "\n".join(turn.get("code") or "" for turn in turns)
    obs = "\n".join((turn.get("observation") or {}).get("stdout", "") + (turn.get("observation") or {}).get("stderr", "") for turn in turns)
    verbs = {verb: len(re.findall(rf"\b{verb}\(", code)) for verb in VERBS}
    summary = {
        "tag": args.tag,
        "legacy": args.legacy,
        "status": (final or {}).get("status"),
        "stop": (final or {}).get("stop"),
        "steps": len(turns),
        "usage": (final or {}).get("usage") or {},
        "wall_s": round(wall, 1),
        "restarts": obs.count("empty namespace"),
        "timeouts": obs.count("timeout: the cell"),
        "verbs": verbs,
        "changed": ws.changed(run),
        "check": None,
    }
    if args.check:
        check = run_check(args.check, str(run))
        summary["check"] = {"state": check.state, "exit": check.exit_code, "failures": check.failures}
    Path(run.parent / f"{args.tag}.summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({key: summary[key] for key in ("tag", "legacy", "status", "stop", "steps", "restarts", "verbs", "check")}, indent=2))
    print("changed:", json.dumps(summary["changed"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
