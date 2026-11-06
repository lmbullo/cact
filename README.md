# cact — technical reference for coding agents

You are the reader. This repository is maintained by agents, not humans: every document is an
interface specification, and every claim in it should be checkable from the tree. The short
contract is `AGENTS.md`; this file is the full reference. For source contracts, prefer the
machine index:

```bash
python3 bench/map.py                    # module -> what it is
python3 bench/map.py --query <topic>    # which module owns it
python3 bench/map.py --module <path>    # one header, whole
python3 bench/map.py --check            # gate: a module header is incomplete -> exit 1
```

Inside a kernel cell the same data is `guide(query)` / `guide(path)`. `map`, `read`, `outline`
and `search` cover docs and files; `guide` covers source headers only.

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

## 2. Repository map

```
cact/
├── AGENTS.md                 short contract for agents (read before editing)
├── README.md                 this file: full reference
├── ruff.toml                 the code contract: E/F/W/B + blank lines, fixtures excluded
├── bench/
│   ├── run.py                benchmark CLI (script and `-m bench.run` modes)
│   ├── tasks.py              READ T1-T5 (text answers) + MUTATION M1-M3 (grade the tree)
│   ├── edge.py               EDGE E1-E10; generates edge_fixtures/ on import when missing
│   ├── harness.py            HARNESS L1-L8: grades the answer and the tool path
│   ├── sandbox.py            SANDBOX S1: docs -> inspect -> atomic edit -> tests -> git commit
│   ├── hard.py               HARD H1-H4 with a held-out oracle; numbers in bench/HARD.md
│   ├── hard_runner.py        runs a held-out test dir against a workspace
│   ├── snapshot.py           freeze/clone/diff a workspace via sha256 manifests
│   ├── live.py               run ONE task through tui/python/worker.py and journal it
│   ├── smoke.py              offline contract runner, one call per group below
│   ├── smoke_support.py      its harness: check(), fakes, temp workspace, kernel_for()
│   ├── smoke_checks/         suites, helpers, index, plans, protocol, files, verbs, gates, turns, repl, style
│   ├── map.py                header-index CLI (`--query`, `--module`, `--json`, `--check`)
│   ├── task_journal.py       summarise a task journal; `--recent` spans workspaces
│   ├── fixtures/             small read/mutation workspace (ledger.csv, lib.py, app.py, notes.md, deep/)
│   ├── edge_fixtures/        broken tests, 20k-line log, invalid utf-8, shadowed import
│   ├── harness_fixtures/     HTML-in-JS, regex hits, local checksum, current docs, edit targets
│   ├── sandbox_fixtures/     changeable feature workspace; sandbox_hidden/ owns its oracle
│   ├── hard_fixtures/        H1-H4 workspaces; the H3 CSV is generated and gitignored
│   ├── hard_hidden/          held-out tests for H1/H2/H4, outside every workspace
│   ├── traces/               append-only evidence: <task>.<mode>.jsonl, summary.<mode>.json
│   ├── ws/                   generated: frozen TUI workspaces + runs/ journals
│   ├── .tmp/, .cache/        generated, gitignored
│   └── agent/
│       ├── kernel/           the action space + the child process, one file per lifecycle:
│       │   ├── protocol.py   PROTOCOL, MARKER, CellRequest/CellReply/ExtendNotice, Cell
│       │   ├── driver.py     DRIVER: the child loop (stdin JSON -> @@K@@ line out)
│       │   ├── prelude.py    HEADERS_SOURCE + the assembled PRELUDE
│       │   ├── prelude_setup.py    namespace, read ledgers, path matching
│       │   ├── prelude_journal.py  _commit, write-through open(), snapshot/diff/undo
│       │   ├── prelude_index.py    _walk, map(), outline()
│       │   ├── prelude_read.py     read(), grep()/search(), recall()
│       │   ├── prelude_verbs.py    write/delete/mkdir/edit/splice/apply/commit
│       │   ├── run_prelude.py      RUN_PRELUDE: exec/git/run + mutation and spawn gates
│       │   └── process.py    Kernel: spawn, supervise, restart, reap; Cell.from_reply
│       ├── verbs.py          typed verb registry: schema, summary, mutates, function
│       ├── tools.py          TYPE_TOOLS generated from verbs.VERBS
│       ├── llm.py            DeepSeek client, retries, on-disk response cache
│       ├── loop.py           shared types only: Budget, RunResult, usage accounting
│       ├── repl.py           ReplLoop = ToolLoop + fenced python blocks
│       ├── tool_loop.py      ToolLoop: typed verbs + `python`, goal checks, stop reasons
│       ├── state_loop.py     StateLoop = ToolLoop + typed observations/state cards, compaction
│       ├── envelope.py       normalized typed observation: data/page/handle/notes/error
│       ├── reply.py          parse model text into an Action (markup repair, fences, ANSWER:)
│       ├── check_run.py      run_check(): runs tsc/pytest/bun test, extracts failures
│       ├── check_plan.py     the check plan and Verifier (goal + cheap health subset)
│       ├── trace.py          Trace JSONL writer + render()
│       ├── headers.py        header index (index/guide/problems); shipped to kernel cells
│       └── config.py         production defaults + build() factory (CODE_TUI_MODE)
└── tui/
    ├── bin/code, bin/code-stop   shell entry points
    ├── src/launcher.ts           workspace hash, server discovery/reuse, detached spawn
    ├── src/server.ts             queue, concurrency, cancellation, HTTP + WS, journals
    ├── src/client.tsx            App: state, keys, layout; mounts the app
    ├── src/theme.ts              palette, status words, one-line helpers (pure)
    ├── src/board-view.tsx        Rule, SectionHeader, TaskRow
    ├── src/log-view.tsx          EventView, LogView (one event, one row)
    ├── src/detect-checks.ts      verification plan for a workspace (env + package.json)
    ├── src/list-model.ts         pure ordering/rows/scroll model
    ├── src/types.ts              Task, TaskEvent, ServerInfo, CheckSpec, Snapshot, WorkerMessage
    ├── src/stop.ts               SIGTERM this workspace's server
    ├── python/worker.py          one task = one worker = one agent loop
    ├── tests/*.test.ts(x)        rendering/detection tests (fake Bun server where needed)
    └── README.md                 TUI wire contract: state root, API, events, env
```

## 3. Runtime architecture

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

- **Model client.** `bench/agent/llm.py` posts to `https://api.deepseek.com/chat/completions`
  (`DEEPSEEK_BASE_URL` overrides). Responses cache under `bench/.cache/llm/` by request hash;
  `--no-cache` disables it.
- **One cell pipeline.** `Kernel.run(code)` and `Kernel.call(tool, args)` write one
  `CellRequest` JSON line (`PROTOCOL` + `kind`/`code` or `kind`/`tool`/`args`, `timeout`,
  `check_cache`), read one `@@K@@` `CellReply` line, and return a `Cell` (`stdout`, `stderr`,
  `result`, `final`, `duration_ms`, `restarted`, `answer`, `runs`, `obs_index`, `mutated`,
  `changed`, `deltas`, `delta`, `timeout`, `reads`, `tool`, `failed`, `rejected`, `envelope`).
  A typed result is normalized into an envelope and delivered verbatim; only python/exec output
  is digested. A reply whose `protocol` does not match `PROTOCOL` is never read through the
  current shape: the child restarts and the cell returns an error. A dead or hung kernel is
  killed and restarted **with an empty namespace**; the loop says so instead of hiding it.
- **Orphan reaping.** `exec`/`run()` start each program in its own session, so killing the
  kernel group cannot reach it. The child journals the group leader (pid + `/proc` start id) and
  `Kernel.stop()`/`_restart()` kill those groups. A timed-out cell or a crashed kernel must not
  leak a test server, browser or build process.
- **Contract index.** `bench/agent/headers.py` parses the four-section header of every source
  module into rows. `bench/map.py` prints them; `kernel/prelude.py` ships the file's *source* to
  the child in `KERNEL_HEADERS`, execs it into a private `_HEADERS` namespace and exposes one
  verb, `guide(query|path)`. It only answers where the repo documents itself; on a task
  workspace with no headers it returns a miss and says so.
- **Results are dicts that also answer attributes.** `exec(...)` and `run()` return
  `result['stdout']` (or `result.stdout`); `read(raw=True)[:n]` and `read(...)['content']` work,
  so a natural model mistake does not cost a cell. The canonical keys are the contract.
- **Bad typed call = answer, not traceback.** Before dispatch the driver binds arguments to the
  verb function's live signature. An unknown keyword or missing argument returns
  `{ok: false, kind: "bad_args", error: {code: "bad_arguments", message, use}}` (the signature
  failure's `use` points at `help(tool)`); a malformed `argv`/`commands`/`env` returns the same
  envelope with its own `code`. A structured `ok: false` is `Cell.rejected`, not `Cell.failed`:
  it renders `exit: "error"` and counts in run errors.
- **Journalling.** The prelude snapshots the tree at start; every cell diffs it. That is what
  `changes()`, `diff()`, `undo()` and `[state] changed:` read. `write()`, `edit()`, `splice()`,
  `delete()`, `mkdir()` and `apply()` all funnel through the same `_commit`, so a multi-file
  `apply()` is one diff and one `undo()`.
- **Sandbox switch.** `exec`/`git`/`run()` enabled ⇒ the kernel is a plain
  `python3 -u -c DRIVER` child. Disabled (`CODE_TUI_ALLOW_RUN=0`) ⇒ the child runs under
  `bwrap` with a read-only `/` plus a writable bind of the workspace only.

## 4. Tool surface

Generated from `bench/agent/verbs.py`; the same registry feeds the provider schema and
`help()`.

| Tool | Contract | Do not use for |
|---|---|---|
| `guide` | ranked module owners from source headers; `path` prints one header | docs/files — use `map`/`search` |
| `map` | files + line counts; `include` is literal or glob; ignored trees skipped | source contracts — use `guide` |
| `read` | canonical `start`/`end`; `path=[...]` one window; `ranges=[...]` many; `section=` resolves through outline; repeated range is a pointer unless `repeat=True` | a whole huge file in one call |
| `session_diff` | unified diff of everything this session journalled | repository history — use `git` |
| `search` | regex by default, exact lines, merged context, `include`/`within`/`section`, hit map + `[more]` | session history — use `recall` |
| `recall` | search earlier cell output (`_OBS`), survives restart | the tree |
| `outline` | Markdown headings, py/ts/js defs, HTML sections; subtree ranges | file contents |
| `edit` | atomic batch `[{path, old, new}]`; all old strings must match | creating a file — use `write` |
| `splice` | replace 1-based inclusive lines | whole-file rewrites |
| `write` | create or replace; refuses to drop a tail without `force` | surgical edits |
| `delete` / `mkdir` | the journalled shell mutations | anything else |
| `apply` | unified diff, several files, one journal entry | ordinary replacements — use `edit` |
| `exec` | one process: `argv`, `commands=[[...]]`, `shell=<string>`, `cwd`, `env`, `stdin` | mutating tracked files; `bash -lc` in argv |
| `git` | status/diff/show/log/files/add/commit/push/remote/branch; no pager | working-tree mutation not in that list |
| `python` | persistent kernel cell; computation, `run()` in repl/bare | file mutation that the journal must see |

Paging contract: `[page]` carries `shown/total/next/unit/has_more/complete`; `next` is a
replayable argument object. `has_more` means the file continues; `truncated` only means the
default page limit cut the requested output. `complete` means this request was served.

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
usage accounting, goal gating and trace writing are one implementation. The base machine adds
one wrap-up nudge at 80% of `max_steps` and one last-turn nudge at `max_steps - 1`; subclasses
change the wording, not the timing. This is the stop signal an open-ended refactor needs, not a
lower step cap. A turn record's `calls`
holds one `CallRecord` per tool call (code, observation, runs, reads, notice, stop).

Stop vocabulary (`tool_loop.STOPS`):

| stop | meaning | TUI status |
|---|---|---|
| `DONE` | model produced a final answer | `done` |
| `VERIFIED` | the goal check passed | `done` |
| `STEP_LIMIT` / `WALL_LIMIT` | budget exhausted; a non-empty summary still counts | `partial` |
| `GOAL_UNMET` | finish attempted while the check failed | continues |
| `STAGED` | `final_answer` staged; accepted on the next no-tool reply | continues |
| `UNREVIEWED` | ReplLoop only: `final_answer` in a printing cell rejected | continues |
| `PROTOCOL` | ReplLoop only: no python block and no ANSWER line | continues |
| `TRUNCATED` / `BAD_ARGS` | cut-off or unparsable call; nothing ran | continues |
| `LLM_FAILED` | primary model call raised `LLMError` | `failed` |

`check_run.py::run_check` wraps tsc / bun test / pytest output into a `Check` with parsed failure
lines and distinguishes `unrunnable` (broken check) from `failed` (real failure).

## 6. Check plan

`bench/agent/check_plan.py` holds an ordered list of `{name, cmd, cheap, scope}` and the `Verifier`
that owns it: the cheap subset, the `scope` filter, the 20s throttle, the check cache and `Check`
rendering. Results cache until the next mutating cell: a matching `run()`/`exec` is answered
from `check_cache` (`kind: "check_cached"`) instead of spawning the same command twice.

- **Cheap entries are health checks.** They re-run after a mutating cell in their `scope` (at
  most every 20s) and print as the `[check]` line of the next state card. They never end a run.
- **The whole plan is the goal.** `final_answer` is rejected (`GOAL_UNMET`) until every entry
  passes; then the task lands `done` with `verified: <names> passed`.

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

Ground truth is computed at import time in each suite module from its fixture. Editing a
fixture silently redefines the expected answer. Test-file hashes are the anti-cheat; the hard
oracle is outside the workspace.

## 8. Commands

### Benchmark

```bash
python3 bench/map.py
python3 bench/map.py --query checks
python3 bench/map.py --module bench/agent/check_plan.py
python3 bench/map.py --json --query checks
python3 bench/map.py --check

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
`--mode repl|tool|state|bare`, `--tasks`, `--steps`, `--wall`, `--max-output`, `--read-chars`,
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

## 9. Contracts an agent must not break

1. **A finish is staged, then accepted.** `final_answer(v)` always stages (`STAGED`): a printing
   cell shows its output; a silent cell still waits for the next no-tool reply, so a short
   `final_answer("done")` cannot erase the report the model writes next. That reply accepts the
   finish after the goal check (when one exists); for a silent stage its content wins over `v`.
   A staged answer is not dropped by the rest of its own turn; a non-final cell in a later turn
   clears it. A silent `final_answer` is terminal intent for its turn: remaining calls do not
   run, but each still gets a paired (skipped) result so the next request stays valid. With a
   goal, nothing is accepted until it passes. `run.py` grades `result.final`; a prose reply with
   no tool call is the summary.
2. **`exec` is the one process tool; only mutation is gated.** `exec(argv)` runs from the
   workspace root; `cwd=` replaces `cd`, `commands=[[...]]` runs several, `env`/`stdin` are
   explicit, `shell=<string>` is the escape hatch. `bash -lc` in argv is `shell_via_exec`,
   pointing at `shell`. `run()` is python-only. Wrong keywords are `bad_args`/`bad_arguments`,
   never a traceback. Mutating tracked files (`tee`, `cp`, `mv`, `rm`, `sed -i`,
   `git checkout/reset/apply/...`, `>`; a quoted `>` is an argument) is `kind: "wrong_call"`
   with a `use` hint, because it would make `diff()`/`undo()` lie. `git add/commit/push/fetch/
   init/clone` are allowed. Mutations whose targets are under a scratch root (`/tmp`,
   `/var/tmp`, `/dev/shm`, `$TMPDIR`) are allowed; the journal only promises the tree. Direct
   spawning goes through the same classifier, so `Popen(["rm", ...])` is refused like `rm`;
   allowed spawns are in `Cell.runs`. A program can still write files: the journal is a
   typed-helper contract, not a sandbox. Keep `_mutating_command`, `_HINT` and `RUN_NOTE`
   honest.
3. **`write()` always replaces** (no silent tail drop without `force=True`). The typed
   `edit(edits=[...])` validates the whole batch before writing (the python `edit(path, old,
   new)` helper remains). `delete(path)` and `mkdir(path)` are the journalled shell mutations.
   `splice(path, start, end, text)` replaces a 1-based inclusive line range.
   `apply(<unified diff>)` writes several files in one cell. All go through `_commit`;
   `commit()` is a python helper.
4. **Graders hash the test files.** Fixing `test_core.py` or `test_calc2.py` scores zero — fix
   the source. The hard oracle is in `bench/hard_hidden/`, never in the workspace.
5. **Ground truth is computed at import time** from fixtures. Editing a fixture silently
   redefines expected answers. `H3_bigcsv`'s CSV is generated and gitignored.
6. **`bench/traces/` is append-only.** Add runs; never rewrite recorded journals. `sweep/`
   files are batch summaries. `bench/ws/` is regenerated measurement output.
7. **Every module explains itself.** Four sections; `headers.py` parses; `map.py --check` and
   smoke fail on a gap. `headers.py` ships to the child: stdlib-only, `__file__`-free,
   read-only, and only `guide()` is exported. Fixtures and `hard_hidden/` are graded input, not
   indexed code.
8. **Generated directories are not source:** `bench/.tmp/`, `bench/.cache/`, `bench/ws/`,
   `tui/node_modules/`, the TUI state root. `edge_fixtures/`, `harness_fixtures/`,
   `sandbox_fixtures/`, `sandbox_hidden/` are committed but rebuilt by `build()` when missing —
   add a task and its fixture in the same module.

## 10. Where to change what

| Change | Touch |
|---|---|
| Header index, map CLI, `guide()` | `bench/agent/headers.py`, `bench/map.py`, `kernel/prelude.py` (`guide` is a wrapper over `_HEADERS["guide"]`) |
| A typed action | `bench/agent/verbs.py` (schema + summary + function), `kernel/prelude.py` or `kernel/run_prelude.py`, `bench/smoke_checks/` (`Kernel.call` case), rules text in `tool_loop.TOOL_RULES` / `repl.REPL_RULES` / `state_loop.STATE_RULES` |
| How observations look | `state_loop.StateLoop._observe` / `_compact`, `repl.observation` |
| A stop reason or the finish protocol | `tool_loop.ToolLoop.run`; a new dialect overrides `_reply` |
| A benchmark task | `tasks.py`, `edge.py`, `harness.py` or `sandbox.py`, then a recorded run |
| Grading semantics | the `grade_*` functions in the same module |
| Model, temperature, caching | `llm.Client`, `run.py` flags, TUI env |
| Check parsing | `check_run.py::_PATTERNS`, `run_check`, `evaluate` |
| Check plan / cheap health | `check_plan.py` (`CheckSpec.parse`, `Verifier`, `run_plan`/`run_specs`, `scope`), `tui/src/detect-checks.ts` |
| Board, log, prompt | `tui/src/client.tsx`, `board-view.tsx`, `log-view.tsx`, `theme.ts`, `list-model.ts` |
| Queue, cancellation, journal format | `tui/src/server.ts` |
| What the TUI sends the agent | `tui/python/worker.py` (AUTONOMOUS, rules, budgets, status mapping) |

## 11. Gotchas

- `bench/run.py` has two import blocks: script mode inserts the repo root on `sys.path`; package
  mode uses relative imports. Keep both working.
- `--suite read` tasks only need text answers. `mutation`/`edge`, `harness.L4_edit_batch` and
  `H1`/`H2`/`H4` set `mutates=True` and get file helpers plus a writable kernel.
- Budget mismatch: `run.py` 200 steps / 3600s; TUI 300 steps / 1800s / 120s per cell.
- The TUI server detects the check plan **when a task starts**, so a script an earlier task
  added to `package.json` is picked up without a restart.
- `bench/ws/tui-base` and `tui-broken` are frozen snapshots with manifests — clone before
  editing. `bench/ws/` is gitignored: recreate `tui-base` with
  `python3 -m bench.snapshot freeze tui bench/ws/tui-base`.
- Tests are TS only (`bun test` = 13 tests, 6 files). React `act(...)` warnings are expected
  noise. Python has no test framework: `bench/smoke.py` is the offline contract check, and its
  `style` group runs `ruff.toml` plus the "every public name has a docstring" check.
- Commit style: `area(scope): lowercase summary`, e.g.
  `bench(agent/kernel): add grep, write, edit` or `tui(tests): add log and prompt tests`.

## 12. Documents

Docs are interface specifications for agents. Keep them executable and checkable; delete a
claim you cannot verify.

| Document | Audience | Owns |
|---|---|---|
| `AGENTS.md` | an agent about to edit the repo | short contract, invariants, gates, recipes |
| `README.md` | an agent that needs the full picture | architecture, module map, suites, commands, contracts |
| `tui/README.md` | an agent maintaining/running the TUI | wire contract: state root, HTTP/WS API, events, statuses, env |
| `bench/HARD.md` | an agent about to simplify the tool surface | measured evidence and what earned its keep |
| `bench/agent/*` headers | an agent locating an owner | parsed by `headers.py`; `map.py --check` gates them |
| fixture `*.md` / `SPEC.md` | the agent **under test** | graded task input. Do not rewrite as documentation: it defines expected answers, and `harness.py`/`sandbox.py` regenerate theirs from `build()` |
