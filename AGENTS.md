# AGENTS.md

Orientation for coding agents. The human owner does not read this code; you do. Read this
file first, then the docstring at the top of whatever module you are about to change — every
module under `bench/agent/` and `tui/src/` opens with WHAT IT IS / ENTRY POINTS / INVARIANTS /
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

## Read these first

| File | Why |
|---|---|
| `bench/agent/kernel.py` | `DRIVER` + `PRELUDE` + `RUN_PRELUDE` define the agent's entire action space, and `Kernel` is the subprocess that enforces it |
| `bench/agent/state_loop.py` | The loop production actually uses: tool call → kernel cell → state card, output digests, context compaction |
| `bench/agent/tool_loop.py` | The turn machine those cards plug into: `final_answer`, the goal check plan, the `[check]` health line, stop reasons |
| `bench/run.py` | The benchmark CLI: suites, modes, budgets, traces, the results table |
| `bench/HARD.md` | Why the envelope exists: hard-suite results against `--mode bare` |
| `tui/python/worker.py` | The seam between the TUI and `bench`: builds the loop, streams `@@TUI@@` JSON lines to `server.ts` |

## Rules that must not break

1. **A finish is staged, then accepted.** `final_answer(v)` in a cell that also printed
   holds the answer (`STAGED`); the next reply with no tool call accepts `v` after the goal
   and read gates. With a goal check, no answer is accepted until the check passes. `run.py`
   grades `result.final`; a prose reply with no tool call is the summary.
2. **`run()` runs commands; only mutation is gated.** Reads, searches, tests, builds and
   read-only git work (`cat`, `sed -n`, `rg`, `pytest`, `git diff`, `python3 -c`). Shell verbs
   that change tracked files (`tee`, `cp`, `mv`, `rm`, `sed -i`, `git apply/checkout/...`,
   `>` redirection) are refused by `_mutating_command` as `kind: "wrong_call"` with a `use`
   hint, because they bypass the journal. `git add/commit/push/fetch/init/clone` are allowed,
   as is mutating an absolute path outside the workspace (`/tmp`), which the journal never
   covered. A program can still write files; the journal is a
   typed-helper contract, not a sandbox. Keep `_mutating_command`, `_HINT` and RUN_NOTE honest.
3. **`write()` replaces** (it refuses to silently drop a tail without `force=True`);
   **`edit()` reports ambiguity rather than guessing**; **`apply(<unified diff>)` and
   `commit({path: text})`** write several files in one cell, so one `diff()`/`undo()` covers
   them. All of them go through `_commit`, which is what makes the journal honest.
4. **Graders hash the test files** (`edge.py::_suite`). Repairing `test_core.py` or
   `test_calc2.py` scores zero; the source must be repaired. The hard suite's oracle lives
   outside the workspace (`bench/hard_hidden/`), so the agent cannot read or edit it.
5. **The check plan is the goal** (`bench/agent/checks.py`). `run_plan` / `run_specs` run the
   ordered plan and return the first failure; a spec's `scope` path prefixes filter the cheap
   health run to the files a cell changed (the goal still runs every spec). The `cheap`
   subset re-runs after a mutating cell (at most every 20s) and is printed as the `[check]`
   line of the state card. `final_answer` is rejected
   (`GOAL_UNMET`) until the whole plan passes. Production passes `goal_stops=False`, so a
   failing check never ends the run by itself — it only rejects the finish.
6. **Ground truth is computed at import time** in `tasks.py` / `edge.py` / `hard.py` from the
   fixtures. Editing a fixture silently redefines the expected answers; `H3_bigcsv`'s CSV is
   generated and gitignored, and the H1/H2/H4 oracle lives in `hard_hidden/`.
7. **`bench/traces/` is append-only evidence.** Add runs; never rewrite a recorded journal.
   `bench/ws/` (frozen workspaces + `runs/` journals) is gitignored measurement output:
   rebuild `tui-base` with `python3 -m bench.ws freeze tui bench/ws/tui-base`, and let
   `bench/live.py` create `runs/<tag>`; do not expect either in a fresh clone.
8. **Generated, not source:** `bench/.tmp/` (per-run workspaces), `bench/.cache/` (cached LLM
   replies), `bench/ws/` (frozen workspaces, rebuilt on demand), `tui/node_modules/`, and the
   TUI state root `~/.local/state/code-tui/`. `bench/edge_fixtures/` is committed but is
   rebuilt by `edge.build()` when missing.
9. **`bench/run.py` has two import blocks** — script mode (`python3 bench/run.py`) and package
   mode (`python3 -m bench.run`). Keep both working.

## Verify a change

```bash
python3 bench/smoke.py                     # offline contract check: imports, kernel prelude,
                                           # run() allow + refusal, apply/commit,
                                           # envelopes, reply parsing, check parsing
python3 -m compileall -q bench tui/python  # syntax
cd tui && bun run typecheck && bun test    # TS: 5 tests, 4 files
```

`bench/smoke.py` is the fast signal: no key, no network, nothing written outside a temp dir.
A failure there means the contract an agent relies on changed (kernel prelude, `Cell` fields,
tool envelopes) — not that a model misbehaved.

The React `act(...)` warnings in `bun test` are expected noise. Python has no unit test
framework: `bench/smoke.py` plus a real benchmark task is the whole story, e.g.

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
  `repl.REPL_RULES` / `state_loop.STATE_RULES`, or the model will not know it exists. If it
  replaces a mutating shell verb, add it to `_RUN_SHELL_MUTATORS` / `_RUN_GIT_MUTATORS` and
  `_HINT` in `RUN_PRELUDE` too.
- When you add a check command, give it a name and mark it `cheap` only if it takes seconds;
  the cheap ones run after a mutating cell in their `scope` (at most every 20s).
- When you add an edge task, also add its fixture to `edge.build()`.