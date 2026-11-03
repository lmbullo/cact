# cact

An LLM coding agent, plus the benchmark that measures it and a terminal UI that runs it
against real projects.

Two halves, two languages, one agent:

| Path | Language | What it is |
|---|---|---|
| `bench/` | Python 3.10+, stdlib only | The agent (`bench/agent/`), the benchmark suites, the recorded traces |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`: a server-backed, fire-and-forget TUI that runs the same agent on your working tree |

The agent is a model in a loop with a **persistent Python kernel**. It reads, greps,
edits and runs programs through that kernel; every mutation is journalled so it can
`diff()` and `undo()`. `bench/` scores that loop against graded tasks; `tui/` puts a
task board in front of it.

There is no root `package.json`, no `pyproject.toml`, no CI. The only install step is
`bun install` inside `tui/`.

Coding agents: run `python3 bench/map.py` first — it indexes the header of every source
module (what it is, entry points, invariants, where to change it), `--query <topic>` names the
owner of a topic and `--check` fails when a header is incomplete. `AGENTS.md` is the short
version of this file plus the rules that must not break. Inside the agent's kernel the same
index is the `guide()` verb.

---

## Read these five files first

| File | Why |
|---|---|
| `bench/agent/kernel.py` | The driver + `PRELUDE` that define the agent's entire action space (`read`, `grep`, `guide`, `map`, `write`, `edit`, `apply`, `commit`, `run`, `changes`, `diff`, `undo`, `recall`, `final_answer`, `emit`) |
| `bench/agent/state_loop.py` | The loop production actually uses: tool call → kernel cell → state card, with output digests and context compaction |
| `bench/agent/tool_loop.py` | The turn machine those cards plug into (`final_answer`, goal checks, stop reasons) |
| `bench/run.py` | The benchmark CLI: suites, modes, budgets, traces, the results table |
| `tui/python/worker.py` | The seam between the TUI and `bench`: builds the loop, streams `@@TUI@@` JSON lines to the server |

---

## Layout

```
cact/
├── bench/
│   ├── run.py               benchmark CLI (also `python3 -m bench.run`)
│   ├── tasks.py             READ suite T1-T5 (answer in text) + MUTATION suite M1-M3 (grade the workspace)
│   ├── edge.py              EDGE suite E1-E10; generates edge_fixtures/ on import when missing
│   ├── hard.py              HARD suite H1-H4 with a held-out oracle; `bench/HARD.md` has the numbers
│   ├── hard_runner.py       runs a held-out test directory against a workspace (the hard oracle)
│   ├── ws.py                freeze/clone/diff a workspace via sha256 manifests
│   ├── live.py              run ONE task through tui/python/worker.py and journal it
│   ├── smoke.py             offline contract check: imports, kernel prelude, helpers, checks
│   ├── map.py               contract CLI: the header index per module (`--query`, `--module`, `--json`, `--check`)
│   ├── runs.py              summarise a task journal; `--recent` spans every workspace
│   ├── fixtures/            the small read/mutation workspace (ledger.csv, lib.py, app.py, notes.md, deep/)
│   ├── edge_fixtures/       the awkward workspace: broken tests, 20k-line log, invalid utf-8, shadowed import
│   ├── hard_fixtures/       the H1-H4 workspaces; the H3 CSV is generated and gitignored
│   ├── hard_hidden/         the held-out test tree for H1/H2/H4, outside every workspace
│   ├── traces/              recorded evidence: <task>.<mode>.jsonl + summary.<mode>.json, hard/, sweep/
│   ├── ws/                  generated: frozen TUI workspaces (tui-base, tui-broken) + runs/ journals
│   ├── .tmp/, .cache/       generated, gitignored (per-run workspaces, LLM response cache)
│   └── agent/
│       ├── kernel.py        subprocess driver (DRIVER, PRELUDE, RUN_PRELUDE) + Kernel + Cell
│       ├── llm.py           DeepSeek chat client, retries, on-disk response cache
│       ├── loop.py          shared types only: Budget, RunResult, usage accounting
│       ├── repl.py          ReplLoop = ToolLoop + fenced python blocks; observations, RUN_NOTE
│       ├── tool_loop.py     ToolLoop (a single `python` tool), goal checks, stop reasons
│       ├── state_loop.py    StateLoop = ToolLoop + state cards, read coverage, fold compaction
│       ├── reply.py         parse model text into an Action (markup repair, fences, ANSWER:)
│       ├── check.py         run_check(): runs tsc/pytest/bun test, extracts failures into a Check
│       ├── checks.py        the check plan and Verifier (goal + cheap health subset)
│       ├── trace.py         Trace JSONL writer + render() for humans
│       ├── headers.py       header index (index/guide/problems); shipped to kernel cells
│       └── config.py        production defaults + build() factory (CODE_TUI_MODE)
└── tui/
    ├── bin/code, bin/code-stop     shell entry points
    ├── src/launcher.ts             per-workspace server discovery/start, then mounts the client
    ├── src/server.ts               queue, concurrency, cancellation, HTTP + WebSocket, journals
    ├── src/client.tsx              the board, the live log, the single prompt box
    ├── src/list-model.ts           pure ordering/rows/scroll model
    ├── src/types.ts                Task, TaskEvent, ServerInfo, CheckSpec, Snapshot, WorkerMessage
    ├── src/stop.ts                 SIGTERM this workspace's server
    ├── python/worker.py            one task = one worker process = one agent loop
    ├── tests/*.test.tsx            rendering tests against a fake Bun server
    └── README.md                   exhaustive TUI reference: keys, env vars, HTTP API, states
```

---

## How a run flows

```
bench/run.py                        tui/bin/code
   │                                    │
   │ copies fixtures → bench/.tmp/<id>/ │ launcher.ts → detached server.ts (per workspace)
   │ builds Kernel + Loop               │            │ spawns python/worker.py per task
   └──────────────┬─────────────────────┘            │
                  ▼                                  ▼
        bench/agent/*.py  ── LLM (DeepSeek) ──►  tool call / fenced python block
                  │
                  ▼
        Kernel (subprocess, one per task)
          stdin/stdout JSON, @@K@@ marker per cell
          ├─ PRELUDE: read (a path or a list)/grep/guide/map/write/edit/splice/apply/commit,
          │           changes/diff/undo, recall, final_answer, emit
          └─ RUN_PRELUDE: run() — whole programs only
                  │
                  ▼
              the workspace
```

- **Model client:** `bench/agent/llm.py` posts to `https://api.deepseek.com/chat/completions`
  (`DEEPSEEK_BASE_URL` overrides). Responses are cached under `bench/.cache/llm/` by
  request hash; `--no-cache` disables it.
- **Kernel cell:** `Kernel.run(code)` writes one `CellRequest` JSON line (`PROTOCOL` plus
  `code`/`timeout`/`check_cache`), reads one `@@K@@` `CellReply` line back, and returns a
  `Cell` (`stdout`, `stderr`, `result`, `final`, `duration_ms`, `restarted`, `answer`,
  `runs`, `obs_index`, `mutated`, `changed`, `deltas`, `delta`, `timeout`, `reads`).
  `deltas` is the per-file unified diff; `delta` is only their joined text for the card. A
  reply whose `protocol` does not match `PROTOCOL` is never read through the current shape:
  the child is restarted and the cell returns an error. A dead or hung kernel is killed and
  restarted **with an empty namespace**; the loop tells the model rather than hiding it.
- **Orphan reaping:** `run()` starts each command in its own session, so killing the kernel
  group cannot reach it. The child journals the group leader (pid + `/proc` start id) and
  `Kernel.stop()`/`_restart()` kills those groups, so a timed-out cell or a crashed kernel
  does not leak a test server, browser or build process.
- **The contract index:** `bench/agent/headers.py` parses the WHAT IT IS / ENTRY POINTS /
  INVARIANTS / WHERE TO CHANGE header of every source module into rows. `bench/map.py` prints
  them; `kernel.py` ships the file's *source* to the child in `KERNEL_HEADERS`, execs it into
  a private `_HEADERS` namespace and exposes one verb, `guide(query|path)`, so an agent can ask
  which module owns a topic from inside a cell. It only answers where the repo documents
  itself; on a task workspace with no headers it returns a miss and says so.
- **Tool results are dicts that also answer attributes:** `run()` returns `result['stdout']`
  (or `result.stdout`), and `read(raw=True)[:n]` / `read(...)['content']` work, so a natural
  model mistake does not cost a cell. The canonical keys are still the contract.
- **A read is a ledger, not a firehose:** `read(limit=0)` returns the whole file only while
  it fits 400 lines / 9 KB; past that the result carries `next.offset`. `Cell.reads` records
  the delivered ranges as `{path: {ranges, total}}`, which the state card renders as
  `[read] path ranges/total`. Coverage is pure data: the loop opens no file and inverse-parses
  no card — an undigested card counts exactly the ranges `read()` recorded, a digested one only
  the numbered lines it kept. Coverage is guidance, not a finish gate: reads through
  `run()`/`open()` are equally valid and are not tracked.
- **Session recall:** every cell's full stdout/stderr/result stays in the kernel's `_OBS`
  list, persisted to disk and reloaded after a restart. `recall(pattern)` searches it —
  including turns the `[session state]` fold removed — and returns snippets plus the
  `_OBS[n]` handle to page the full text. `grep()` is for the tree; `recall()` is for the
  session, and a crash does not erase it.
- **Sandbox:** `run()` enabled ⇒ the kernel is a plain `python3 -u -c DRIVER` child.
  `run()` disabled ⇒ the child runs under `bwrap` with a read-only `/` (plus a writable
  bind of the root only when `writable=True`).
- **Journalling:** the prelude snapshots the tree at start; every cell diffs it. That is
  what `changes()`, `diff()`, `undo()` and the `[state] changed:` line read from. `write()`,
  `edit()`, `apply()` and `commit()` all funnel through the same `_commit`, so a multi-file
  `apply()`/`commit()` is one diff and one `undo()`.
- **Check plan:** `bench/agent/checks.py` holds an ordered list of `{name, cmd, cheap, scope}`
  and the `Verifier` that owns it: the cheap subset, the `scope` filter, the 20s throttle,
  the check cache and the `Check` rendering. Results are cached until the next mutating
  cell: a matching `run()` is answered from `check_cache` (`kind: "check_cached"`) instead
  of spawning the same command twice. The whole plan is the goal (`final_answer` is
  rejected until it passes); the `cheap` subset re-runs after a mutating cell (at most once
  every 20s) and appears as the `[check]` line of the next state card. A spec may carry a
  `scope` list of path prefixes that filters the cheap health run only, so a docs-only edit
  does not typecheck the TUI on every cell; the goal still runs the whole plan. `run()` is a shell, not a redirect layer:
  reads, searches, tests, builds and read-only git run as written; only verbs that mutate
  the workspace are refused (see the contract below). Mutating scratch space (`/tmp`,
  `/var/tmp`, `/dev/shm`, `$TMPDIR`) is allowed: the journal covers the tree, not scratch.
- **TUI:** `server.ts` owns task JSON + append-only event journals under
  `~/.local/state/code-tui/workspaces/<sha256-of-cwd, 16 hex>/`; `client.tsx` holds no
  persistent state of its own and reconnects every second. A `turn` message updates
  `Task.progress` (turn number plus prompt tokens), so a board row moves before the final
  answer arrives; a turn that rejected or staged a finish carries a `notice`, rendered as
  one `↺` row in the run log.

---

## The loops

Selected by `--mode` in `run.py`, by `CODE_TUI_MODE` in production (default `state`). There
are three loops; `--mode bare` is the hard suite's control, not a fourth implementation.

| mode | Loop | Action space | Rules constant |
|---|---|---|---|
| `repl` | `ReplLoop` | one fenced python block per reply, run as a kernel cell | `repl.REPL_RULES` / `BARE_RULES` |
| `tool` | `ToolLoop` | one function: `python` | `tool_loop.TOOL_RULES` |
| `state` | `StateLoop` | same as `tool`, observations rendered as state cards | `state_loop.STATE_RULES` |
| `bare` | `ToolLoop` with `goal=None` and no file helpers (benchmark control) | one function: `python` | `run.py::BARE_TOOL_RULES` |

All loops share `Budget` (`max_steps`, `max_wall_s`, `max_output_tokens`), `RunResult`, and
the `Client`. `loop.py` now holds only those shared types, after the JSON tool-calling
`AgentLoop` and its `tools.py` registry were removed.

`ToolLoop.run` is the turn machine. `StateLoop` extends it with state cards; `ReplLoop`
extends it with fenced-block reply parsing and overrides only `_reply` (plus the transcript
and the `stop`/`history` switches), so budget stops, usage accounting, goal gating and trace
writing are the same code. `FinishState` holds the staged and the accepted answer,
`TurnOutcome` is what one turn decided, and a turn record's `calls` list holds one
`CallRecord` per tool call (code, observation, runs, reads, notice, stop), so a turn that
emits several calls keeps all of them in `turns` and in the trace. The stop names are module
constants (`tool_loop.STOPS`). Stop reasons are a fixed vocabulary:

| stop | meaning | TUI status |
|---|---|---|
| `DONE` | model produced a final answer | `done` |
| `VERIFIED` | the goal check passed | `done` |
| `STEP_LIMIT` / `WALL_LIMIT` | budget exhausted (a non-empty summary still counts) | `partial` |
| `GOAL_UNMET` | model tried to finish while the check failed | continues |
| `STAGED` | `final_answer` in a cell that also printed; accepted on the next no-tool reply | continues |
| `UNREVIEWED` | ReplLoop only: `final_answer` in a printing cell is rejected | continues |
| `PROTOCOL` | ReplLoop only: reply had neither a python block nor an ANSWER line | continues |
| `TRUNCATED` / `BAD_ARGS` | cut-off or unparsable call; nothing ran | continues |
| `LLM_FAILED` | the primary model call raised `LLMError`; the run stops with whatever answer exists | `failed` |

`check.py::run_check` wraps tsc / bun test / pytest output into a `Check` with parsed
failure lines, and distinguishes `unrunnable` (broken check) from `failed` (real failure).

---

## Commands

### Benchmark

```bash
python3 bench/map.py                   # the contract index: module -> what it is
python3 bench/map.py --query checks    # which module owns "checks", with entry points
python3 bench/map.py --module bench/agent/check.py   # one module's header whole
python3 bench/map.py --json --query checks           # the same rows as JSON
python3 bench/map.py --check           # gate: every module header has its four sections

export DEEPSEEK_API_KEY=...            # or --key
python3 bench/run.py                                   # read suite, state mode, fixtures/
python3 bench/run.py --suite all --mode state          # read + mutation, production loop
python3 bench/run.py --suite edge --tasks E1_tests,E6_repair --mode state --traces
python3 bench/run.py --suite hard --mode state         # envelope vs `--mode bare`; see bench/HARD.md
python3 bench/run.py --mode repl --repl-stop call --repl-history action --repl-lib all
```

Every run prints one row per task, writes `bench/traces/<task>.<mode>.jsonl` and rewrites
`bench/traces/summary.<mode>.json`. `--traces` prints `trace.render()` of each run
(`err`/`call`/`get`/`text` lines). Useful knobs: `--suite read|mutation|all|edge|hard`,
`--mode repl|tool|state|bare`, `--tasks`, `--steps`, `--wall`, `--max-output`, `--read-chars`,
`--obs-chars`, `--map off|tool|inline`, `--context map|full`, `--context-chars`,
`--allow-run off|on`, `--cell-timeout`, `--health on|off`,
`--repl-rules|--repl-stop|--repl-history|--repl-lib`, `--model`, `--effort low|high|max|none`,
`--temperature`, `--seed`, `--no-cache`.

### Workspaces and live runs

```bash
python3 bench/smoke.py                 # offline contract check: imports, kernel, helpers, checks
python3 -m bench.ws freeze SRC DST     # clone + write DST.manifest.json
python3 -m bench.ws diff  DST          # added/modified/deleted vs the manifest
python3 bench/live.py --base bench/ws/tui-base --tag r4 --prompt "make typecheck pass" \
        --check "bun run typecheck" --legacy r3
python3 bench/runs.py ~/.local/state/code-tui/workspaces/<hash> <task-id>
python3 bench/runs.py --recent 10      # newest tasks across every workspace
```

`live.py` is the honest end-to-end test: it clones `bench/ws/tui-base`, drives
`tui/python/worker.py`, records `<tag>.events.jsonl`, `<tag>.prompt.txt`,
`<tag>.summary.json`, then runs the check itself and diffs the tree.

### TUI

```bash
cd tui && bun install
bun run typecheck      # tsc --noEmit
bun test               # tests/*.test.tsx: list model, live log, prompt box, views

./bin/code             # inside the target project; starts/reuses its server
./bin/code-stop        # SIGTERM the server for this directory
```

The agent's Python kernel is `python3` from `PATH` (override with `CODE_TUI_PYTHON`); it
imports `bench.agent` from this repo, so the checkout path matters.

---

## Contracts an agent must not break

1. **A finish is staged, then accepted.** `final_answer(v)` in a cell that also printed
   holds the answer (`STAGED`): the output is shown, and the next reply with no tool call
   accepts `v` after the goal check (when there is one). A staged answer is never dropped by
   the rest of its own turn; a non-final cell in a later turn clears it. An explicit
   `final_answer` in a silent cell finishes immediately, and an accepted finish ends that
   turn: the remaining tool calls in the same assistant message do not run (each still gets
   a paired tool result, marked skipped, so the next request stays valid). When a goal check
   exists, no answer is accepted until the check passes. `run.py` grades `result.final`; a
   prose reply with no tool call is the summary.
2. **`run()` runs commands; only mutation is gated.** Reads, searches, tests, builds and
   read-only git work as written (`cat`, `sed -n`, `rg`, `pytest`, `git diff`, `python3 -c`).
   Shell verbs that change tracked files (`tee`, `cp`, `mv`, `rm`, `sed -i`,
   `git checkout/reset/apply/...`, `>` redirection; a quoted `>` is an argument, not a
   redirect) are refused with `kind: "wrong_call"` and a `use` hint, because they would
   bypass the journal and make `diff()`/`undo()` lie. `git add`, `commit`, `push`, `fetch`,
   `init` and `clone` are allowed: they do not overwrite working-tree content. Mutations
   whose targets are absolute paths under a scratch root (`/tmp`, `/var/tmp`, `/dev/shm`,
   `$TMPDIR`) are allowed; the journal only promises the tree. Direct process spawning
   (`subprocess.Popen`, `os.system`, `os.exec*`, `os.posix_spawn`, `os.spawn`) goes through
   the same classifier, so `Popen(["rm", ...])` is refused exactly like `rm`; allowed spawns
   are listed in `Cell.runs`. A program can still write files; the journal is a
   typed-helper contract, not a sandbox. Keep `_mutating_command`, `_HINT` and the RUN_NOTE
   text honest.
3. **`write()` always replaces** (it refuses to silently drop a tail without
   `force=True`); **`edit()` reports ambiguity instead of guessing**; `apply(<unified diff>)`
   and `commit({path: text})` write several files in one cell. All of them go through the
   same journalled `_commit`.
4. **Graders hash the test files** (`edge.py::_suite`). Fixing `test_core.py` or
   `test_calc2.py` scores zero — repair the source.
5. **Ground truth is computed at import time** in `tasks.py` / `edge.py` from the
   fixtures. Editing a fixture silently redefines the expected answers. The hard suite's
   held-out tests live in `bench/hard_hidden/`, never in the workspace; `H3_bigcsv`'s CSV is
   generated and gitignored.
6. **`bench/traces/` is append-only evidence.** Add new runs; never rewrite recorded
   journals. The `sweep/` files are batch summaries. `bench/ws/` (frozen workspaces and
   `runs/` journals) is gitignored measurement output — regenerate it with `bench/ws.py` and
   `bench/live.py`.
7. **Every module explains itself.** A source module opens with WHAT IT IS / ENTRY POINTS /
   INVARIANTS / WHERE TO CHANGE; `bench/agent/headers.py` parses those into data and
   `python3 bench/map.py --check` (also in `bench/smoke.py`) fails when one is missing. The
   kernel ships that file's source to the child and exports only `guide()`, so it must stay
   stdlib-only, `__file__`-free and read-only. Task fixtures and `bench/hard_hidden/` are not
   indexed: they are graded input, not code an agent maintains.
8. **Generated directories are not source:** `bench/.tmp/` (per-run workspaces),
   `bench/.cache/` (LLM replies), `bench/ws/` (frozen workspaces), `tui/node_modules/`,
   `tmp8_w_n1sf/`, and the TUI state root `~/.local/state/code-tui/`. `bench/edge_fixtures/` is committed but is recreated
   by `edge.build()` whenever it is missing — if you add an edge task, add its fixture
   there (or extend `build()`).

---

## Where to change what

| Change | Touch |
|---|---|
| The header index, the map CLI or the `guide()` verb | `bench/agent/headers.py` (parsing, ranking, rendering), `bench/map.py` (flags), `kernel.PRELUDE` (`guide` is a one-line wrapper over `_HEADERS["guide"]`) |
| What the agent can do (new file/exec helper) | `kernel.PRELUDE` or `RUN_PRELUDE`, plus the matching rule text in `tool_loop.TOOL_RULES` / `repl.REPL_RULES` / `state_loop.STATE_RULES`; if it replaces a mutating shell verb, `_RUN_SHELL_MUTATORS` / `_RUN_GIT_MUTATORS` + `_HINT` |
| How observations look to the model | `state_loop.StateLoop._observe` / `_compact`, `repl.observation` |
| A stop reason or the finish protocol | `tool_loop.ToolLoop.run`; a different reply dialect overrides `_reply` (see `repl.ReplLoop`) |
| A new benchmark task | `tasks.py` (read/mutation) or `edge.py` (edge), then a run to record the trace |
| Grading semantics | the `grade_*` functions in `tasks.py` / `edge.py` |
| Model, temperature, caching | `llm.Client`, `run.py` flags, TUI env vars |
| Goal check parsing | `check.py::_PATTERNS`, `run_check`, `evaluate` |
| The check plan / cheap health check | `checks.py` (`CheckSpec.parse`, `Verifier`, `run_plan` / `run_specs`, `scope`), `tui/src/detect-checks.ts` |
| Task board, live log, prompt box | `tui/src/client.tsx` + `tui/src/list-model.ts` (pure, tested) |
| Queueing, cancellation, journal format | `tui/src/server.ts` |
| What the TUI sends the agent | `tui/python/worker.py` (`AUTONOMOUS`, rules, budgets, status mapping) |

---

## Gotchas

- `bench/run.py` has two import blocks: script mode (`python3 bench/run.py`) inserts the
  repo root on `sys.path`, package mode (`python3 -m bench.run`) uses relative imports.
  Keep both working.
- The default `--suite read` tasks only need text answers; `mutation`/`edge` tasks and
  `H1`/`H2`/`H4` in the hard suite set `mutates=True` and get the file helpers plus a
  writable kernel. `H3_bigcsv` is a text answer over a generated CSV.
- Budget mismatch: `run.py` defaults to 200 steps / 3600 s, the TUI defaults to 300 steps
  / 1800 s / 120 s per cell (`CODE_TUI_MAX_*`).
- The TUI server detects the check plan **when a task starts**, so a script an earlier task
  added to `package.json` is picked up without a restart: `CODE_TUI_CHECKS` (JSON list)
  wins, else `CODE_TUI_CHECK`, else `python3 bench/smoke.py` plus `typecheck`/`test` (or
  `build` when neither exists) from the root and `tui/package.json`. The runner follows the
  lockfile (`bun.lock` -> bun, `package-lock.json` -> npm). Entries may carry a `scope` path
  prefix list; the cheap ones run after a mutating cell that touched their scope (at most
  every 20s), and the whole plan gates `final_answer`. An empty `CODE_TUI_CHECK` opts out.
- `bench/ws/tui-base` and `tui-broken` are frozen snapshots with manifests — do not edit
  them in place; clone with `bench.ws.clone` first. `bench/ws/` is gitignored: recreate
  `tui-base` with `python3 -m bench.ws freeze tui bench/ws/tui-base`, and `tui-broken` as
  that tree with `tests/log.test.tsx` failing.
- Tests are TS only (`bun test` = 13 passing tests across 6 files). Python has no test
  framework: `python3 bench/smoke.py` is the offline contract check (kernel prelude, `Cell`
  fields, tool envelopes, reply parsing), and a real change is then confirmed by running a
  benchmark task. The React `act(...)` warnings in the test output are expected noise.
- Commit style in this repo: `area(scope): lowercase summary`, e.g.
  `bench(agent/kernel): add grep, write, edit` or `tui(tests): add log and prompt tests`.