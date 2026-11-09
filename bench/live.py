"""End-to-end run through the real TUI worker: clone a frozen workspace, drive the agent,
journal everything, then check and diff it.

WHAT IT IS
  The honest test. It clones `bench/archive/results/ws/tui-base` to `bench/ws/runs/<tag>`, writes
  `<tag>.prompt.txt`, spawns `tui/python/worker.py` against it, records `<tag>.events.jsonl`
  (the worker's `@@TUI@@` lines), then runs the check itself and diffs the tree against the
  base. `bench/task_journal.py` reads the result back. `--sandbox daytona` runs the same case
  on a disposable Daytona box and copies the same artifacts back; `local` stays the default.

ENTRY POINTS
  python3 bench/live.py --base bench/archive/results/ws/tui-base --tag r4 --prompt "..." \
          --check "bun run typecheck" [--checks '[{"name":"smoke","cmd":"...","cheap":true}]']
          [--legacy r1|r3|both] [--sandbox local|daytona]
  load_key()      DEEPSEEK_API_KEY, else ~/.config/code-tui/{env,.env}
  _daytona(args, base)  the sandbox path: snapshot, upload, worker, check, download, delete
  _summary(args, events, final, wall, run, check)  the one summary both paths write

  `--sandbox daytona` uses the snapshot named by `CODE_TUI_DSNAPSHOT` (default
  `code-tui-py-bun`), built once from `python:3.12` + bun (bench/remote.py::create_snapshot)
  and reused by name afterwards. The key is passed only in the worker's exec `env=`, never to
  `create(env=...)` and never to a file.

  The base must already exist: `bench/ws/` is gitignored, so build it first with
  `python3 -m bench.snapshot freeze tui bench/ws/tui-base` or point --base at the archived
  `bench/archive/results/ws/tui-base`.

INVARIANTS
  - `--tag` names a new directory under bench/ws/runs/. That tree and its journals are
    append-only evidence: never reuse a tag, never rewrite an old run.
  - Everything is driven through the worker's public interface (`@@TUI@@` JSON lines,
    `VERBS` list), so this file doubles as the contract test for worker.py: if the worker
    changes its messages, this breaks first.
  - `--legacy` toggles the worker's legacy env flags (`CODE_TUI_LEGACY_TIMEOUT`,
    `CODE_TUI_LEGACY_CHECK`) so old behaviour stays reproducible. On Daytona, every
    `CODE_TUI_*` override except host-path settings is forwarded to the worker, so a profile
    or scope experiment is the same command locally and in the sandbox.
  - `--check` is a single command; `--checks` is a JSON plan (see bench/agent/check_plan.py)
    and wins when set. The worker treats the plan as its goal and runs the `cheap` subset
    after mutating cells.
  - The check runs twice by design: once inside the agent as its goal, once here as the
    independent verdict printed in the summary. On `daytona` the second run happens inside
    `/work/ws` and its output is classified by `check_run.from_output`.
  - The sandbox is disposable: it is deleted in a `finally`, so no exception path leaves one
    billing. Only the snapshot (the slow build) survives.

WHERE TO CHANGE
  What a live run records ..... _summary(), the one events/summary writer
  How the worker is invoked ... the argv/env block in _local() and _daytona()
  The sandbox layout ......... _daytona(), HARNESS_EXCLUDE, SNAPSHOT_ENV
  Key discovery ............... load_key()
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # script mode: make `bench` importable before the package imports
    sys.path.insert(0, str(ROOT))

from bench import snapshot  # noqa: E402
from bench.agent.check_run import Check, from_output, run_check  # noqa: E402

PREFIX = "@@TUI@@"
VERBS = ("map", "read", "outline", "search", "grep", "write", "edit", "splice", "apply", "commit",
         "exec", "git", "run", "diff", "undo", "changes", "help", "final_answer")
SNAPSHOT_ENV = "CODE_TUI_DSNAPSHOT"
DEFAULT_SNAPSHOT = "code-tui-py-bun"
SANDBOXES = ("local", "daytona")
# The harness is this repo minus the evidence archive and generated trees; the harness needs
# its own source, `tui/python/worker.py`, and the gates, nothing else.
HARNESS_EXCLUDE = ("bench/archive", "bench/ws", "bench/.cache", "bench/.tmp", ".git",
                   "node_modules")
SANDBOX_HARNESS = "/work/harness"
SANDBOX_WS = "/work/ws"
SANDBOX_STATE = "/work/state"
SANDBOX_PATH = "/usr/local/bin:/usr/local/sbin:/usr/bin:/bin"


def load_key() -> str:
    """DEEPSEEK_API_KEY from the env, else ~/.config/code-tui/{env,.env}; "" when absent."""
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


def _refuse(kind: str, message: str) -> int:
    """A structured refusal on stdout, exit 2: no network, no artifact."""
    print(json.dumps({"ok": False, "error": {"kind": kind, "message": message}}))
    return 2


def _consume(lines, handle, echo: bool = True) -> tuple[list, dict | None]:
    """Parse the worker's `@@TUI@@` lines into events, writing each to `handle`; return them."""
    events: list = []
    final = None
    for line in lines:
        line = line.rstrip("\n")
        if not line.startswith(PREFIX):
            if echo:
                print("  worker:", line[:200])
            continue
        try:
            event = json.loads(line[len(PREFIX):])
        except json.JSONDecodeError:
            continue
        events.append(event)
        handle.write(json.dumps(event) + "\n")
        kind = event.get("kind")
        if kind == "turn" and echo:
            data = event.get("data") or {}
            obs = data.get("observation") or {}
            print(f"  turn {data.get('turn'):>3} stop={data.get('stop')} out={len(obs.get('stdout') or '')}")
        elif kind == "final":
            final = event
    return events, final


def _summary(args, events, final, wall: float, run: Path, check: Check | None) -> dict:
    """Write `<tag>.summary.json` and print the verdict; the one writer for both sandboxes."""
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
        "restarts": sum(1 for turn in turns
                        if (turn.get("observation") or {}).get("restarted")),
        "timeouts": obs.count("timeout: the cell"),
        "verbs": verbs,
        "changed": snapshot.changed(run),
        "check": None,
    }
    if check is not None:
        summary["check"] = {"state": check.state, "exit": check.exit_code,
                            "failures": check.failures}
    Path(run.parent / f"{args.tag}.summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({key: summary[key] for key in ("tag", "legacy", "status", "stop", "steps", "restarts", "verbs", "check")}, indent=2))
    print("changed:", json.dumps(summary["changed"]))
    return summary


def _local(args, base: Path) -> int:
    """Clone --base, drive the worker, journal the events, run the check and diff the tree."""
    run = ROOT / "bench" / "ws" / "runs" / args.tag
    snapshot.clone(base, run)
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
        "--check", args.check, "--checks", args.checks, "--cell-timeout", str(args.cell_timeout),
        "--max-steps", str(args.max_steps), "--max-wall", str(args.max_wall),
    ]
    print(f"run {args.tag} legacy={args.legacy} check={args.check!r} cell={args.cell_timeout}s")
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with events_path.open("w") as handle:
        assert proc.stdout is not None
        events, final = _consume(proc.stdout, handle)
    proc.wait(timeout=60)
    wall = time.time() - started

    check = run_check(args.check, str(run)) if args.check else None
    _summary(args, events, final, wall, run, check)
    return 0


def _sandbox_tree(base: Path) -> Path:
    """A staging copy of `base`, with generated trees (target, caches) left out of the upload."""
    staging = Path(tempfile.mkdtemp(prefix="bench-ws-")) / "ws"
    shutil.copytree(base, staging, symlinks=False,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", ".cache", ".tmp",
                                                  "target"))
    return staging


def _sandbox_env(base: dict) -> dict:
    """`base` plus the caller's CODE_TUI_* overrides, minus host-path settings."""
    local_only = {"CODE_TUI_DSNAPSHOT", "CODE_TUI_STATE_ROOT",
                  "CODE_TUI_SERVER", "CODE_TUI_PYTHON"}
    env = dict(base)
    for name, value in os.environ.items():
        if name.startswith("CODE_TUI_") and name not in local_only:
            env[name] = value
    return env


def _daytona(args, base: Path) -> int:
    """Run the same case on a Daytona box, copy the artifacts home, delete the box."""
    from bench import remote  # lazy: `--sandbox local` never imports the SDK

    report = remote.status()
    if not report["sdk"] or not report["key"]:
        return _refuse("sandbox_unavailable",
                       report["hint"] or "the daytona sandbox needs the SDK and an API key")
    key = load_key()
    if not key:
        return _refuse("sandbox_unavailable",
                       "no DEEPSEEK_API_KEY for the worker inside the sandbox")
    snapshot_name = (os.environ.get(SNAPSHOT_ENV) or DEFAULT_SNAPSHOT).strip() or DEFAULT_SNAPSHOT
    run = ROOT / "bench" / "ws" / "runs" / args.tag
    started = time.time()
    box = None
    deleted = False
    try:
        built = remote.create_snapshot(snapshot_name)
        print(f"snapshot {built['name']} id={built['id']} created={built['created']}")
        box = remote.create(snapshot=built["name"], auto_stop=120, auto_delete=240)
        print(f"sandbox {box.id}")

        made = box.run(["mkdir", "-p", SANDBOX_HARNESS, SANDBOX_WS, SANDBOX_STATE], timeout=60)
        if not made["ok"]:
            raise remote.RemoteError(f"mkdir /work failed: {made['output'][:200]}")
        print(f"upload harness {ROOT} -> {SANDBOX_HARNESS} (exclude {', '.join(HARNESS_EXCLUDE)})")
        box.upload_tree(ROOT, SANDBOX_HARNESS, exclude=HARNESS_EXCLUDE)
        staging = _sandbox_tree(base)
        try:
            print(f"upload base {base} -> {SANDBOX_WS}")
            box.upload_tree(staging, SANDBOX_WS, exclude=(".git",))
        finally:
            shutil.rmtree(staging.parent, ignore_errors=True)
        box.upload(args.prompt.encode("utf-8"), "/work/prompt.txt")

        base_env = {"PYTHONPATH": f"{SANDBOX_HARNESS}:{SANDBOX_WS}", "PATH": SANDBOX_PATH}
        probe = box.run(["python3", "-c", "import bench; print(bench.__file__)"],
                        cwd=SANDBOX_HARNESS, env=base_env, timeout=120)
        print(f"import bench: exit={probe['exit']} {probe['output'].strip()[:200]}")
        if not probe["ok"]:
            probe = box.run(["python3", "-c",
                             f"import sys; sys.path[:0]=[{SANDBOX_HARNESS!r}, {SANDBOX_WS!r}]; "
                             "import bench; print(bench.__file__)"],
                            cwd=SANDBOX_HARNESS, env=base_env, timeout=120)
            print(f"import bench (retry): exit={probe['exit']} {probe['output'].strip()[:200]}")
        if not probe["ok"]:
            raise remote.RemoteError(
                "cannot import bench inside the sandbox after two attempts: "
                f"layout {SANDBOX_HARNESS}=repo, {SANDBOX_WS}=base, cwd={SANDBOX_HARNESS}; "
                f"error: {probe['output'].strip()[:600]}")

        worker = [
            "python3", f"{SANDBOX_HARNESS}/tui/python/worker.py",
            "--workspace", SANDBOX_WS, "--prompt-file", "/work/prompt.txt",
            "--check", args.check, "--checks", args.checks,
            "--cell-timeout", str(args.cell_timeout), "--max-steps", str(args.max_steps),
            "--max-wall", str(args.max_wall),
        ]
        env = _sandbox_env(base_env)
        env["DEEPSEEK_API_KEY"] = key
        env["XDG_STATE_HOME"] = SANDBOX_STATE
        if args.legacy in ("r1", "both"):
            env["CODE_TUI_LEGACY_TIMEOUT"] = "1"
        if args.legacy in ("r3", "both"):
            env["CODE_TUI_LEGACY_CHECK"] = "1"
        script = " ".join(shlex.quote(part) for part in worker) + " > /work/out.jsonl 2>&1"
        print(f"run {args.tag} sandbox={box.id} legacy={args.legacy} check={args.check!r}")
        worker_started = time.time()
        worked = box.run(["bash", "-c", script], cwd=SANDBOX_HARNESS, env=env,
                         timeout=int(args.max_wall) + 600)
        wall = time.time() - worker_started
        print(f"worker exit={worked['exit']} wall={wall:.1f}s")

        check = None
        if args.check:
            check_started = time.time()
            checked = box.run(["bash", "-c", args.check], cwd=SANDBOX_WS,
                              env={"PATH": SANDBOX_PATH}, timeout=600)
            check = from_output(args.check, checked["exit"], checked["output"], "",
                                int((time.time() - check_started) * 1000))
            print(f"check {args.check!r}: {check.state} exit={check.exit_code}")

        run.parent.mkdir(parents=True, exist_ok=True)
        (run.parent / f"{args.tag}.prompt.txt").write_text(args.prompt)
        payload = box.download("/work/out.jsonl") or b""
        with (run.parent / f"{args.tag}.events.jsonl").open("w") as handle:
            events, final = _consume(payload.decode("utf-8", "replace").splitlines(), handle)

        base_manifest = Path(str(base) + ".manifest.json")
        if base_manifest.exists():  # same reference state snapshot.clone carries locally
            Path(str(run) + ".manifest.json").write_text(base_manifest.read_text())
        box.download_tree(SANDBOX_WS, run, exclude=("node_modules",))
        modules = base / "node_modules"
        if modules.exists() and not (run / "node_modules").exists():
            (run / "node_modules").symlink_to(modules.resolve())
        try:  # the worker's journal/state, kept beside the run as evidence
            box.download_tree(SANDBOX_STATE, run.parent / f"{args.tag}.state",
                             exclude=("docs",))
        except remote.RemoteError as exc:
            print(f"  journal: {exc}")

        _summary(args, events, final, wall, run, check)
        return 0
    except remote.RemoteError as exc:
        print(json.dumps({"ok": False, "error": {"kind": "sandbox_failed",
                                                 "message": str(exc)}}))
        return 1
    finally:
        if box is not None:
            try:
                box.delete()
                deleted = True
            except remote.RemoteError as exc:
                print(json.dumps({"ok": False, "error": {"kind": "delete_failed",
                                                         "message": str(exc)}}))
        print(json.dumps({"sandbox": box.id if box is not None else None,
                          "snapshot": snapshot_name,
                          "wall_s": round(time.time() - started, 1),
                          "deleted": deleted}))


def main() -> int:
    """Parse the case, then run it locally (default) or in a Daytona sandbox."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--check", default="bun run typecheck")
    parser.add_argument("--checks", default="")
    parser.add_argument("--cell-timeout", type=float, default=120.0)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--max-wall", type=float, default=1800.0)
    parser.add_argument("--legacy", default="none", choices=["none", "r1", "r3", "both"])
    parser.add_argument("--sandbox", default="local",
                        help="local (default) or daytona; nothing else is accepted")
    args = parser.parse_args()
    if args.sandbox not in SANDBOXES:
        return _refuse("bad_args",
                       f"unknown --sandbox {args.sandbox!r}: choose local or daytona")
    base = Path(args.base).resolve()
    if args.sandbox == "daytona":
        return _daytona(args, base)
    return _local(args, base)


if __name__ == "__main__":
    raise SystemExit(main())
