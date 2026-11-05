"""The benchmark CLI. Scores the agent on the read, mutation, edge, harness and hard suites.

WHAT IT IS
  For each selected task: copy the task's fixtures into `bench/.tmp/<task>/fixtures`, build
  the loop for `--mode`, run the task, grade `result.final` (or the workspace, for mutation
  and hard tasks), print one row, and write `bench/traces/<task>.<mode>.jsonl` plus
  `bench/traces/summary.<mode>.json`.

ENTRY POINTS
  python3 bench/run.py ...            script mode (inserts the repo root on sys.path)
  python3 -m bench.run ...            package mode (relative imports)
  digest(root, budget)                whole-tree text dump for --context full
  workspace(task) -> Path             fresh copy under bench/.tmp
  main() -> int                       0 only when every selected task passes

  Flags: --suite read|mutation|all|edge|harness|hard, --mode repl|tool|state|bare, --tasks a,b,
  --steps, --wall, --max-output, --obs-chars, --map off|tool|inline, --context map|full,
  --context-chars, --allow-run off|on, --cell-timeout, --health on|off, --repl-rules,
  --repl-stop, --repl-history, --repl-lib, --model, --effort, --temperature, --seed,
  --no-cache, --traces.

WHAT IT IS NOT
  It is not the production path. The TUI builds loops through bench/agent/config.build; this
  CLI wires each suite by hand, so a change here does not change production behaviour.

INVARIANTS
  - Two import blocks (see `if __package__ in (None, "")`), both must keep working. Anything
    new that needs the repo root must be imported in both.
  - Mutation/edge tasks and the mutating hard tasks (H1/H2/H4) set mutates=True: the kernel
    gets the file helpers, a writable root, and `--allow-run` decides whether the child is
    sandboxed under bwrap.
  - `Trace(trace_path)` is opened with mode "w" per task per mode; `summary.<mode>.json` is
    rewritten on every run. Never hand-edit either.
  - Exit code is 0 only on a full pass: use it as the CI signal when a key is available.
  - Grading happens in tasks.py / edge.py `grade_*`; this file only calls it. Do not grade
    here.

WHERE TO CHANGE
  New suite .......................... the `pool` dict + the matching module's TASKS list
  New flag / budget wiring .......... the argparse block in main()
  What a run records ................. the `rows.append({...})` dict and summary write
  Which loop a mode builds ........... the `if args.mode in ("tool", "state")` / `bare` branch
  The per-task goal and cheap health ... the `Verifier` built from `task.check`
  The hard suite and its held-out oracle ... bench/hard.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from bench.agent.checks import Verifier
    from bench.agent.kernel import Kernel
    from bench.agent.llm import Client
    from bench.agent.loop import Budget
    from bench.agent.repl import BARE_RULES, REPL_RULES, RUN_NOTE, RUN_NOTE_MUT, ReplLoop
    from bench.agent.state_loop import StateLoop
    from bench.agent.tool_loop import PYTHON_TOOL, TOOL_RULES, ToolLoop
    from bench import edge as bench_edge
    from bench import hard as bench_hard
    from bench import harness as bench_harness
    from bench.agent.trace import Trace, render
    from bench import tasks as bench_tasks
else:
    from .agent.checks import Verifier
    from .agent.kernel import Kernel
    from .agent.llm import Client
    from .agent.loop import Budget
    from .agent.repl import BARE_RULES, REPL_RULES, RUN_NOTE, RUN_NOTE_MUT, ReplLoop
    from .agent.state_loop import StateLoop
    from .agent.tool_loop import PYTHON_TOOL, TOOL_RULES, ToolLoop
    from . import edge as bench_edge
    from . import hard as bench_hard
    from . import harness as bench_harness
    from .agent.trace import Trace, render
    from . import tasks as bench_tasks

def _map_text(root: Path) -> str:
    lines = []
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    for path in sorted(root.rglob("*")):
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            lines.append(f"{path.stat().st_size:>7}  {path.relative_to(root)}")
    return "\n".join(lines)


LIB_NOTE = (
    "\nPRELOADED HELPERS (plain functions, each returns a dict with ok/kind and a note when action is needed): "
    "map(), read(path, offset=1, limit=2000), grep(pattern, path='.', max_results=50), "
    "write(path, content), edit(path, old, new, all=False). write always replaces.\n"
)

LIB_MUT_NOTE = (
    "\nPRELOADED HELPERS for mutation (plain functions, each returns a dict with ok/kind and a note when action is needed): "
    "write(path, content), edit(path, old, new, all=False). write always replaces. "
    "Reading is ordinary Python; open() decodes with errors='replace'.\n"
)

BARE_TOOL_RULES = """You act by calling the python tool, which executes the code in a persistent kernel. Variables and imports persist between calls; stdout, stderr and the last expression come back as the tool result. To finish, call final_answer(<value>) in a cell that prints nothing, or reply with no tool call."""

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
TRACES = HERE / "traces"


def digest(root: Path, budget: int = 60_000) -> str:
    blocks, used = [], 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()
        body = path.read_text(encoding="utf-8", errors="replace")
        lines = body.splitlines()
        if used + len(body) > budget:
            head = "\n".join(lines[:30])
            tail = "\n".join(lines[-10:])
            block = f"### {rel} ({len(lines)} lines, {len(body)} bytes, truncated)\n{head}\n...\n{tail}"
        else:
            block = f"### {rel} ({len(lines)} lines)\n{body}"
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def workspace(task) -> Path:
    base = HERE / ".tmp"
    base.mkdir(exist_ok=True)
    dest = base / task.id / "fixtures"
    if dest.parent.exists():
        shutil.rmtree(dest.parent)
    dest.parent.mkdir(parents=True)
    shutil.copytree(getattr(task, "source", FIXTURES), dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--effort", default="low", choices=["low", "high", "max", "none"])
    ap.add_argument("--mode", default="state", choices=["repl", "tool", "state", "bare"])
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--steps", type=int, default=200)
    ap.add_argument("--wall", type=float, default=3600.0)
    ap.add_argument("--max-output", type=int, default=393216)
    ap.add_argument("--read-chars", type=int, default=16000)
    ap.add_argument("--repl-rules", default="full", choices=["full", "bare", "none"])
    ap.add_argument("--allow-run", default="on", choices=["off", "on"])
    ap.add_argument("--cell-timeout", type=float, default=0.0)
    ap.add_argument("--repl-stop", default="call", choices=["call", "var"])
    ap.add_argument("--repl-history", default="full", choices=["full", "obs", "drop", "action"])
    ap.add_argument("--suite", default="read",
                    choices=["read", "mutation", "all", "edge", "hard", "harness"])
    ap.add_argument("--health", default="on", choices=["on", "off"])
    ap.add_argument("--repl-lib", default="off", choices=["off", "all", "mut"])
    ap.add_argument("--obs-chars", type=int, default=4000)
    ap.add_argument("--map", default="off", choices=["off", "tool", "inline"])
    ap.add_argument("--context", default="map", choices=["map", "full"])
    ap.add_argument("--context-chars", type=int, default=60_000)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--traces", action="store_true")
    args = ap.parse_args()

    if not args.key:
        print("no api key (use --key or DEEPSEEK_API_KEY)", file=sys.stderr)
        return 2

    client = Client(
        api_key=args.key,
        model=args.model,
        effort=None if args.effort == "none" else args.effort,
        cache=not args.no_cache,
        temperature=args.temperature,
        seed=args.seed,
    )
    budget = Budget(max_steps=args.steps, max_wall_s=args.wall, max_output_tokens=args.max_output)

    pool = {
        "read": bench_tasks.TASKS,
        "mutation": bench_tasks.MUTATION_TASKS,
        "all": bench_tasks.ALL_TASKS,
        "edge": bench_edge.EDGE_TASKS,
        "hard": bench_hard.HARD_TASKS,
        "harness": bench_harness.HARNESS_TASKS,
    }[args.suite]
    lookup = {"edge": bench_edge.by_id, "hard": bench_hard.by_id,
              "harness": bench_harness.by_id}.get(args.suite, bench_tasks.by_id)
    selected = pool if args.tasks == "all" else [lookup(t) for t in args.tasks.split(",")]

    TRACES.mkdir(parents=True, exist_ok=True)
    rows = []
    cache_turns = {1: [0, 0], "2+": [0, 0]}
    print(f"model={args.model} effort={args.effort} mode={args.mode} root=fixtures/ steps<={args.steps}\n")
    header = f"{'task':<12} {'ok':<3} {'stop':<12} {'steps':>5} {'llm':>5} {'cells':>5} {'err':>4} {'in':>7} {'out':>6} {'ms':>6}"
    print(header)
    print("-" * len(header))

    for task in selected:
        trace_path = TRACES / f"{task.id}.{args.mode}.jsonl"
        ws = workspace(task)
        mutate = task in bench_tasks.MUTATION_TASKS or getattr(task, "mutates", False)
        if args.context == "full":
            prefix = "WORKSPACE\n" + digest(ws, args.context_chars) + "\n\n"
        else:
            prefix = _map_text(ws) + "\n\n" if args.map == "inline" else ""
        if True:
            allow_run = args.allow_run == "on"
            timeout = args.cell_timeout or (120.0 if allow_run else 15.0)
            lib = args.repl_lib
            if args.mode in ("tool", "state") and lib == "off":
                lib = "all"
            kernel = Kernel(ws, timeout=timeout, writable=mutate, prelude=lib,
                            allow_run=allow_run,
                            allow_shell=args.mode not in ("tool", "state"))
            try:
                base = (TOOL_RULES if args.mode in ("tool", "state")
                        else BARE_TOOL_RULES if args.mode == "bare"
                        else {"full": REPL_RULES, "bare": BARE_RULES, "none": ""}[args.repl_rules])
                rules = base
                if args.mode not in ("tool", "state") and lib == "all":
                    rules = rules + LIB_NOTE
                elif args.mode not in ("tool", "state") and lib == "mut":
                    rules = rules + LIB_MUT_NOTE
                if allow_run and args.repl_rules != "none" and args.mode not in ("tool", "state"):
                    rules = rules + (RUN_NOTE_MUT if lib == "mut" else RUN_NOTE)
                check = getattr(task, "check", None)
                verifier = None
                if check and args.mode != "bare":
                    verifier = Verifier(check=lambda: check(ws), cwd=str(ws),
                                        cheap_enabled=args.health == "on")
                if args.mode in ("tool", "state"):
                    loop = (StateLoop if args.mode == "state" else ToolLoop)(
                        client, kernel, budget,
                        rules=rules + prefix,
                        goal_stops=True,
                        obs_chars=args.obs_chars,
                        verifier=verifier,
                    )
                elif args.mode == "bare":
                    loop = ToolLoop(
                        client, kernel, budget,
                        rules=rules + prefix,
                        tools=[PYTHON_TOOL],
                        goal=None,
                        goal_stops=False,
                        obs_chars=args.obs_chars,
                    )
                else:
                    loop = ReplLoop(
                        client, kernel, budget,
                        rules=rules + prefix,
                        stop=args.repl_stop,
                        history=args.repl_history,
                        obs_chars=args.obs_chars,
                        goal=verifier.goal_value if verifier is not None else None,
                        goal_stops=True,
                    )
                result = loop.run(task.id, task.prompt, Trace(trace_path))
            finally:
                kernel.stop()

        for turn in result.turns:
            c = turn.get("cache") or {}
            bucket = 1 if turn.get("turn") == 1 else "2+"
            cache_turns[bucket][0] += int(c.get("hit", 0))
            cache_turns[bucket][1] += int(c.get("hit", 0)) + int(c.get("miss", 0))
        result.workspace = str(ws)
        ok, detail = task.grade(result.final, result)
        usage = result.usage
        print(
            f"{task.id:<12} {'ok' if ok else 'FAIL':<3} {result.stop:<12} "
            f"{result.steps:>5} {result.llm_calls:>5} {result.calls:>5} {result.errors:>4} "
            f"{usage.get('prompt_tokens', 0):>7} {usage.get('completion_tokens', 0):>6} "
            f"{result.wall_ms:>6.0f}"
        )
        if not ok:
            print(f"    -> {detail}")
        mix: dict = {}
        for turn in result.turns:
            for call in turn.get("calls") or []:
                name = call.get("name")
                if name:
                    mix[name] = mix.get(name, 0) + 1
        rows.append({
            "task": task.id,
            "mix": mix,
            "mode": args.mode,
            "shape": task.shape,
            "ok": ok,
            "detail": detail,
            "stop": result.stop,
            "steps": result.steps,
            "llm_calls": result.llm_calls,
            "calls": result.calls,
            "errors": result.errors,
            "usage": usage,
            "wall_ms": round(result.wall_ms),
            "runs": [c for t in result.turns for c in (t.get("runs") or [])],
            "final": result.final,
            "trace": str(trace_path),
        })
        if args.traces:
            print()
            print(render(trace_path, reasoning_chars=300))
            print()

    passed = sum(1 for r in rows if r["ok"])
    errors = sum(r["errors"] for r in rows)
    tot = {k: sum(r["usage"].get(k, 0) for r in rows) for k in (
        "total_tokens", "prompt_tokens", "completion_tokens",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "local_hits")}
    prompt = tot["prompt_tokens"] or 1
    print(
        f"\npass {passed}/{len(rows)} | cell errors {errors} | "
        f"steps {sum(r['steps'] for r in rows)} | tokens {tot['total_tokens']}"
    )
    print(
        f"cache: prompt {tot['prompt_tokens']} = hit {tot['prompt_cache_hit_tokens']} "
        f"({100 * tot['prompt_cache_hit_tokens'] / prompt:.0f}%) + miss {tot['prompt_cache_miss_tokens']} | "
        f"local hits {tot['local_hits']}/{sum(r['steps'] for r in rows)} calls"
    )

    for bucket in (1, "2+"):
        hit, total = cache_turns[bucket]
        share = 100 * hit / total if total else 0
        print(f"  turn {str(bucket):<2} cache hit {share:>5.1f}%  ({hit}/{total} input tokens)")

    (TRACES / f"summary.{args.mode}.json").write_text(
        json.dumps({"model": args.model, "effort": args.effort, "mode": args.mode, "rows": rows}, indent=2)
    )
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
