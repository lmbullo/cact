# cact — technical reference for coding agents

You are the reader. The short contract is `AGENTS.md`: invariants, gates, change recipes. This file
is the reference behind it — what runs where, how one cell flows, what the suites grade, and the
exact commands. Neither document restates a fact the other owns, and neither restates a module's
header.

Source contracts are not listed here; they are the machine index:

```bash
python3 bench/guide.py                  # module -> what it is (one line each)
python3 bench/guide.py --query <topic>  # which module owns it
python3 bench/guide.py --module <path>  # one header, whole
python3 bench/guide.py --check          # gate: a module header is incomplete -> exit 1
```

Inside a kernel cell the same data is `guide(query)` / `guide(path)`. `map`, `read`, `outline` and
`search` cover docs and files; `guide` covers source headers only.

---

## 1. What runs where

| Path | Language | Role |
|---|---|---|
| `bench/` | Python 3.10+, stdlib only | The agent (`bench/agent/`), the graded suites, fixtures, recorded traces |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`: a server-backed, fire-and-forget TUI that runs the same agent on a working tree |

Two entry points matter:

- `python3 bench/run.py` — the benchmark. Copies fixtures, builds a `Kernel` + loop, grades.
- `tui/bin/code` — the product. Launches a per-workspace server; `tui/python/worker.py` builds
  the same loop through `bench/agent/config.py`.

There is no root `package.json`, no `pyproject.toml`, no CI. The only install step is
`bun install` in `tui/`.

## 2. Trees and fixtures

A module's one-line contract is its header, not this file: `python3 bench/guide.py` prints every
module under `bench/` and `tui/` with that line. What a header cannot say is which trees are
*input*, which are *output*, and which are rebuilt:

| Tree | Kind | Note |
|---|---|---|
| `bench/fixtures/` | input, committed | small read/mutation workspace (ledger.csv, lib.py, app.py, notes.md, deep/) |
| `bench/edge_fixtures/` | input, rebuilt by `build()` | broken tests, 20k-line log, invalid utf-8, shadowed import |
| `bench/harness_fixtures/` | input, rebuilt by `build()` | HTML-in-JS, regex hits, local checksum, current docs, edit targets |
| `bench/sandbox_fixtures/` | input, rebuilt by `build()` | changeable feature workspace |
| `bench/sandbox_hidden/` | oracle, committed | held-out tests for `sandbox`; never inside the task workspace |
| `bench/hard_fixtures/` | input, rebuilt by `build()` | H1-H4 workspaces; the H3 CSV is generated and gitignored |
| `bench/hard_hidden/` | oracle, committed | held-out tests for H1/H2/H4, outside every workspace |
| `bench/traces/` | output, append-only | `<task>.<mode>.jsonl` per run, `summary.<mode>.json`, `sweep/` batch summaries, `hard/` per-run summaries |
| `bench/ws/` | output, gitignored | frozen TUI workspaces + `runs/` journals |
| `bench/.tmp/`, `bench/.cache/` | output, gitignored | suite copies, LLM reply cache |
| `tui/node_modules/`, TUI state root | output, gitignored | install tree, `~/.local/state/code-tui/` |

`bench/agent/kernel/` is the one package split by lifecycle rather than by feature: `protocol.py`
(the wire), `driver.py` (child loop), five `prelude_*.py` fragments, `prelude.py` (assembled
`PRELUDE`), `run_prelude.py` (exec/git + gates), `process.py` (the parent-side supervisor). To
change a verb, the header of the fragment that owns it says so.

## 3. How one cell flows

```
bench/run.py                        tui/bin/code
   │                                    │
   │ copies fixtures -> bench/.tmp/<id>/│ launcher.ts -> detached server.ts (per workspace)
   │ builds Kernel + Loop               │            │ spawns python/worker.py per task
   └──────────────┬─────────────────────┘            │
                  ▼                                  ▼
        bench/agent/*.py  ── LLM (DeepSeek) ──►  typed tool call / python cell
                  │
                  ▼
        Kernel (subprocess, one per task)
          stdin/stdout JSON, @@K@@ marker per request
          ├─ PRELUDE: guide/map/read/session_diff/search/recall/outline/
          │           edit/splice/write/delete/mkdir/apply + python helpers
          │           guide/grep/diff/changes/undo/commit/help, final_answer/emit
          └─ RUN_PRELUDE: exec (argv/commands/shell) + git; run() only in repl/bare
                  │
                  ▼
              the workspace
```

What the diagram does not show:

- **Model client.** `bench/agent/llm.py` posts to `https://api.deepseek.com/chat/completions`
  (`DEEPSEEK_BASE_URL` overrides). Responses cache under `bench/.cache/llm/` by request hash;
  `--no-cache` disables it.
- **One cell pipeline.** `Kernel.run(code)` and `Kernel.call(tool, args)` write one `CellRequest`
  JSON line (`PROTOCOL` + `kind`/`code` or `kind`/`tool`/`args`, `timeout`, `check_cache`), read
  one `@@K@@` `CellReply` line, and return a `Cell` (`stdout`, `stderr`, `result`, `final`,
  `duration_ms`, `restarted`, `answer`, `runs`, `obs_index`, `mutated`, `changed`, `deltas`,
  `delta`, `timeout`, `reads`, `tool`, `failed`, `rejected`, `envelope`). A reply whose `protocol`
  does not match `PROTOCOL` is never read through the current shape: the child restarts and the
  cell returns an error. A dead or hung kernel is killed and restarted **with an empty
  namespace**; the loop says so instead of hiding it.
- **Results are dicts that also answer attributes.** `exec(...)` and `run()` return
  `result['stdout']` (or `result.stdout`); `read(raw=True)[:n]` and `read(...)['content']` work,
  so a natural model mistake does not cost a cell. The canonical keys are the contract.
- **Journalling.** The prelude snapshots the tree at start; every cell diffs it. That is what
  `changes()`, `diff()`, `undo()` and `[state] changed:` read.
- **Sandbox switch.** `exec`/`git`/`run()` enabled ⇒ the kernel is a plain `python3 -u -c DRIVER`
  child. Disabled (`CODE_TUI_ALLOW_RUN=0`) ⇒ the child runs under `bwrap` with a read-only `/`
  plus a writable bind of the workspace only.

## 4. Reading a typed result

The verb registry (`bench/agent/verbs.py`) is the only catalog: name, JSON schema, one-line
summary, `mutates`, implementing function. `bench/agent/tools.py` builds the provider specs from
it, and `help(tool)` prints the live signature — so there is no tool table in this file to keep in
sync. The registry entry and the function docstring are what an agent reads.

Paging contract, shared by every paged verb: `[page]` carries
`shown/total/next/unit/has_more/complete`; `next` is a replayable argument object. `has_more`
means the file continues; `truncated` only means the default page limit cut the requested output;
`complete` means this request was served. `complete` is the field to trust, not a prose tail.

## 5. The loops

Selected by `--mode` in `run.py`, by `CODE_TUI_MODE` in production (default `state`).

| mode | Loop | Action space | Rules constant |
|---|---|---|---|
| `repl` | `ReplLoop` | one fenced python block per reply | `repl.REPL_RULES` / `BARE_RULES` |
| `tool` | `ToolLoop` | typed verbs + `python` | `tool_loop.TOOL_RULES` |
| `state` | `StateLoop` | same, observations rendered as state cards | `state_loop.STATE_RULES` |
| `bare` | `ToolLoop` with `goal=None` and no file helpers (control) | one function: `python` | `run.py::BARE_TOOL_RULES` |

All loops share `Budget` (`max_steps`, `max_wall_s`, `max_output_tokens`), `RunResult` and the
`Client`. `ToolLoop.run` is the turn machine; `StateLoop` adds cards and compaction; `ReplLoop`
overrides `_reply`, `_observe`, `_before_turn` and the stop/history switches, so budget stops,
usage accounting, goal gating and trace writing are one implementation. The base machine adds one
wrap-up nudge at 80% of `max_steps` and one last-turn nudge at `max_steps - 1`; subclasses change
the wording, not the timing. This is the stop signal an open-ended refactor needs, not a lower
step cap. A turn record's `calls` holds one `CallRecord` per tool call (code, observation, runs,
reads, notice, stop).

The stop vocabulary is `tool_loop.STOPS` (one comment per value says what sets it); this file
does not repeat it, and the TUI maps it to a status word in `tui/python/worker.py`.

`check_run.py::run_check` wraps tsc / bun test / pytest output into a `Check` with parsed failure
lines and distinguishes `unrunnable` (broken check) from `failed` (real failure).

## 6. Check plan

`bench/agent/check_plan.py` holds an ordered list of `{name, cmd, cheap, scope}` and the `Verifier`
that owns it: the cheap subset, the `scope` filter, the 20s throttle, the check cache and `Check`
rendering. Results cache until the next mutating cell: a matching `run()`/`exec` is answered
from `check_cache` (`kind: "check_cached"`) instead of spawning the same command twice.

Cheap entries are health checks: they re-run after a mutating cell in their `scope` (at most every
20s) and print as the `[check]` line of the next state card. They never end a run. The whole plan
is the goal: `final_answer` is rejected (`GOAL_UNMET`) until every entry passes, then the task
lands `done` with `verified: <names> passed`.

Production passes `goal_stops=False`: a failing check only rejects the finish. The TUI detects
the plan per task: `CODE_TUI_CHECKS` (JSON) wins, else `CODE_TUI_CHECK`, else
`python3 bench/smoke.py` plus `typecheck`/`test` (or `build`) from the root and
`tui/package.json`. The runner follows the lockfile (`bun.lock`/`bun.lockb` → bun,
`package-lock.json` → npm). An empty `CODE_TUI_CHECK` opts out.

## 7. Suites and grading

| Suite | Tasks | Grades |
|---|---|---|
| `read` (default) | T1-T5 | the text answer |
| `mutation` | M1-M3 | the workspace after the run |
| `edge` | E1-E10 | repaired source; test files are hashed, editing them scores zero |
| `harness` | L1-L8 | answer **and** call record: HTML via map/read/outline, regex search, exec batch, atomic edit batch, explicit shell, batched read, local checksum, docs grounding |
| `sandbox` | S1_feature | docs → inspect → atomic edit → tests → typed git commit; oracle in `sandbox_hidden/` |
| `hard` | H1-H4 | held-out tests or exact answer; `--mode bare` is the control |

Ground truth is computed at import time in each suite module from its fixture (see §2 for which
trees that means). Test-file hashes are the anti-cheat; the hard oracle is outside the workspace.

## 8. Commands

### Benchmark

```bash
python3 bench/guide.py
python3 bench/guide.py --query checks
python3 bench/guide.py --module bench/agent/check_plan.py
python3 bench/guide.py --json --query checks
python3 bench/guide.py --check

export DEEPSEEK_API_KEY=...            # or --key
python3 bench/run.py                                   # read suite, state mode, fixtures/
python3 bench/run.py --suite all --mode state          # read + mutation
python3 bench/run.py --suite edge --tasks E1_tests,E6_repair --mode state --traces
python3 bench/run.py --suite harness --mode state      # L1-L8
python3 bench/run.py --suite sandbox --tasks S1_feature --mode state --traces
python3 bench/run.py --suite hard --mode state         # envelope vs `--mode bare`
python3 bench/run.py --mode repl --repl-stop call --repl-history action --repl-lib all
```

Every run prints one row per task, writes `bench/traces/<task>.<mode>.jsonl` and rewrites
`bench/traces/summary.<mode>.json`. `--traces` prints `trace.render()` (`err`/`call`/`get`/`text`
lines). Knobs: `--suite read|mutation|all|edge|harness|hard|sandbox`,
`--mode repl|tool|state|bare`, `--tasks`, `--steps`, `--wall`, `--max-output`,
`--obs-chars`, `--map off|tool|inline`, `--context map|full`, `--context-chars`,
`--allow-run off|on`, `--cell-timeout`, `--health on|off`,
`--repl-rules|--repl-stop|--repl-history|--repl-lib`, `--model`,
`--effort low|high|max|none`, `--temperature`, `--seed`, `--no-cache`.

Defaults: 200 steps / 3600s per task. The TUI uses 300 / 1800s / 120s per cell.

### Workspaces and live runs

```bash
python3 bench/smoke.py                 # offline contract check
python3 -m bench.snapshot freeze SRC DST   # clone + write DST.manifest.json
python3 -m bench.snapshot diff  DST        # added/modified/deleted vs the manifest
python3 bench/live.py --base bench/ws/tui-base --tag r4 --prompt "make typecheck pass" \
        --check "bun run typecheck" --legacy r3
python3 bench/task_journal.py ~/.local/state/code-tui/workspaces/<hash> <task-id>
python3 bench/task_journal.py --recent 10  # newest tasks across every workspace
```

`live.py` is the honest end-to-end test: it clones `bench/ws/tui-base`, drives
`tui/python/worker.py`, records `<tag>.events.jsonl`, `<tag>.prompt.txt`,
`<tag>.summary.json`, runs the check and diffs the tree.

### TUI

```bash
cd tui && bun install
bun run typecheck      # tsc --noEmit; noUnusedLocals/noUnusedParameters are on
bun test               # 13 tests, 6 files
./bin/code             # inside the target project; starts/reuses its server
./bin/code-stop        # SIGTERM the server for this directory
```

The agent's Python kernel is `python3` from `PATH` (`CODE_TUI_PYTHON` overrides); it imports
`bench.agent` from this repo, so the checkout path matters.

## 9. Gotchas

- `bench/run.py` has two import blocks: script mode inserts the repo root on `sys.path`; package
  mode uses relative imports. Keep both working.
- `--suite read` tasks only need text answers. `mutation`/`edge`, `harness.L4_edit_batch` and
  `H1`/`H2`/`H4` set `mutates=True` and get file helpers plus a writable kernel.
- The TUI server detects the check plan **when a task starts**, so a script an earlier task added
  to `package.json` is picked up without a restart.
- `bench/ws/tui-base` and `tui-broken` are frozen snapshots with manifests — clone before
  editing. `bench/ws/` is gitignored: recreate `tui-base` with
  `python3 -m bench.snapshot freeze tui bench/ws/tui-base`.
- Tests are TS only (`bun test` = 13 tests, 6 files). React `act(...)` warnings are expected
  noise. Python has no test framework: `bench/smoke.py` is the offline contract check; its
  `style` group runs `ruff.toml` plus the "every public name has a docstring" check, and its
  `docs` group fails when a document names a path that no longer exists.
- Commit style: `area(scope): lowercase summary`, e.g.
  `bench(agent/kernel): add grep, write, edit` or `tui(tests): add log and prompt tests`.

## 10. Documents

Docs are interface specifications for agents. Keep them executable and checkable; delete a claim
you cannot verify, and never copy a fact from the layer that owns it.

| Document | Audience | Owns (and does not repeat) |
|---|---|---|
| `AGENTS.md` | an agent about to edit the repo | short contract, invariants, gates, change recipes |
| `README.md` | an agent that needs the machinery | runtime flow, tree kinds, suites, commands, gotchas |
| `tui/README.md` | an agent maintaining/running the TUI | wire contract: state root, HTTP/WS API, events, statuses, env |
| `bench/HARD.md` | an agent about to simplify the tool surface | measured evidence and what earned its keep |
| `bench/agent/*` headers | an agent locating an owner | parsed by `headers.py`; `guide.py --check` gates them |
| fixture `*.md` / `SPEC.md` | the agent **under test** | graded task input. Do not rewrite as documentation: it defines expected answers, and `harness.py`/`sandbox.py` regenerate theirs from `build()` |
