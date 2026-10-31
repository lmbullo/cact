# AGENTS.md

Orientation for coding agents. The human owner does not read this code; you do. Read this
file first, then the docstring at the top of whatever module you are about to change — every
module in `bench/` and `tui/` now opens with WHAT IT IS / ENTRY POINTS / INVARIANTS /
WHERE TO CHANGE. `README.md` is the full prose reference; this file is the short version.

## What this repo is

An LLM coding agent in two halves, one language each:

| Path | Language | What it is |
|---|---|---|
| `bench/` | Python 3.10+, stdlib only | The agent itself (`bench/agent/`) plus the benchmark suites, fixtures and recorded traces |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`: a server-backed TUI that runs the same agent on the current working tree |

The agent is a model in a loop with a **persistent Python kernel**. A cell can read, grep,
map, write, edit and run whole programs; every mutation is journalled so `changes()`,
`diff()` and `undo()` work. `bench/` scores that loop against graded tasks; `tui/` puts a
task board in front of it.

No root `package.json`, no `pyproject.toml`, no CI. The only install step is `bun install`
inside `tui/`.

## Read these five first

| File | Why |
|---|---|
| `bench/agent/kernel.py` | `DRIVER` + `PRELUDE` + `RUN_PRELUDE` define the agent's entire action space, and `Kernel` is the subprocess that enforces it |
| `bench/agent/state_loop.py` | The loop production actually uses: tool call → kernel cell → state card, output digests, context compaction |
| `bench/agent/tool_loop.py` | The turn machine those cards plug into: `final_answer`, goal checks, stop reasons |
| `bench/run.py` | The benchmark CLI: suites, modes, budgets, traces, the results table |
| `tui/python/worker.py` | The seam between the TUI and `bench`: builds the loop, streams `@@TUI@@` JSON lines to `server.ts` |

## Rules that must not break

1. **`final_answer(value)` is only accepted from a cell that prints nothing.** If the cell
   printed, the answer is rejected and the output is returned to the model. With a goal
   check, `final_answer` is also rejected until the check passes. `run.py` grades
   `result.final`; a prose reply with no tool call is the summary.
2. **`run()` is not a file interface.** `_run_protocol_error` in `kernel.py` refuses
   `cat head tail wc grep rg ag sed awk find ls tree du tee touch cp mv rm truncate`, output
   redirection, `git ls-files/grep/show/...`, and `python3 -c` that inspects project files.
   Keep that list honest: the model must use `read()`, `grep()`, `map()`, `write()`, `edit()`.
3. **`write()` replaces** (it refuses to silently drop a tail without `force=True`);
   **`edit()` reports ambiguity rather than guessing**; both are journalled.
4. **Graders hash the test files** (`edge.py::_suite`). Repairing `test_core.py` or
   `test_calc2.py` scores zero; the source must be repaired.
5. **Ground truth is computed at import time** in `tasks.py` / `edge.py` from the fixtures.
   Editing a fixture silently redefines the expected answers.
6. **`bench/traces/` and `bench/ws/runs/` are append-only evidence.** Add runs; never rewrite
   recorded journals.
7. **Generated, not source:** `bench/.tmp/` (per-run workspaces), `bench/.cache/` (cached LLM
   replies), `tui/node_modules/`, and the TUI state root `~/.local/state/code-tui/`.
   `bench/edge_fixtures/` is committed but is rebuilt by `edge.build()` when missing.
8. **`bench/run.py` has two import blocks** — script mode (`python3 bench/run.py`) and package
   mode (`python3 -m bench.run`). Keep both working.

## Verify a change

```bash
python3 -c "import bench.run, bench.tasks, bench.edge, bench.ws"   # imports still resolve
python3 -m compileall -q bench tui/python                          # syntax
cd tui && bun run typecheck && bun test                            # TS: 5 tests, 4 files
```

The React `act(...)` warnings in `bun test` are expected noise. Python has no unit tests: a
Python change is verified by running a benchmark task, e.g.

```bash
DEEPSEEK_API_KEY=... python3 bench/run.py --suite edge --tasks E1_tests --mode state --traces
```

which needs network and a key; without one, fall back to the smoke checks above plus reading
the trace format.

## Conventions

- Commit style: `area(scope): lowercase summary`, e.g. `bench(agent/kernel): add grep, write, edit`.
- Prose is for agents: state the contract, the entry point and the failure mode; skip
  tutorials.
- When you add a helper to the kernel, add the matching rule text to `tool_loop.TOOL_RULES` /
  `repl.REPL_RULES` / `state_loop.STATE_RULES`, or the model will not know it exists.
- When you add an edge task, also add its fixture to `edge.build()`.
