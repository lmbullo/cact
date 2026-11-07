# cact — technical reference for coding agents

You are the reader. The short contract is `AGENTS.md`: invariants, gates, change recipes. This file
is the reference behind it — what runs where, how one cell flows, what the evals grade, and the
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
| `bench/` | Python 3.10+, stdlib only | The agent (`bench/agent/`), the failure-mode evals (`bench/evals/`), archived legacy suites and results (`bench/archive/`) |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`: a server-backed, fire-and-forget TUI that runs the same agent on a working tree |

Two entry points matter:

- `python3 bench/eval.py` — the evals. Offline loop contracts by default; `--live` runs the model-backed failure suite.
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
| `bench/evals/fixtures.py` | input, built per case | the eval workspaces, never a committed tree that can drift |
| `bench/evals/oracle.py` | oracle, source | the hidden answers, outside every workspace |
| `bench/evals/results/` | output, append-only | one JSON row per eval pass, with `tokens/pass` |
| `bench/archive/legacy/` | archived input | the old read/mutation/edge/harness/hard/sandbox suites and their fixtures; a read-only record, not runnable in place (its modules still import the pre-archive `bench.<suite>` names) |
| `bench/archive/results/traces/` | archived output | the recorded legacy traces and sweep summaries |
| `bench/archive/results/ws/` | archived output | the old frozen TUI workspaces + `runs/` journals |
| `bench/.tmp/`, `bench/.cache/` | output, gitignored | eval copies, LLM reply cache |
| `bench/archive/results/tmp/` | output, gitignored | scratch workspaces from the retired suites; the archived H3 CSV is likewise regenerated and ignored |
| `tui/node_modules/`, TUI state root | output, gitignored | install tree, `~/.local/state/code-tui/` |

`bench/agent/kernel/` is the one package split by lifecycle rather than by feature: `protocol.py`
(the wire), `driver.py` (child loop), five `prelude_*.py` fragments, `prelude.py` (assembled
`PRELUDE`), `run_prelude.py` (exec/git + gates), `process.py` (the parent-side supervisor). To
change a verb, the header of the fragment that owns it says so.

## 3. How one cell flows

```
bench/eval.py                       tui/bin/code
   │                                    │
   │ builds a temp workspace per case    │ launcher.ts -> detached server.ts (per workspace)
   │ builds Kernel + Loop               │            │ spawns python/worker.py per task
   └──────────────┬─────────────────────┘            │
                  ▼                                  ▼
        bench/agent/*.py  ── LLM (DeepSeek) ──►  typed tool call / python cell
                  │
                  ▼
        Kernel (subprocess, one per task)
          stdin/stdout JSON, @@K@@ marker per request
          ├─ PRELUDE: guide/skill/map/read/session_diff/search/recall/outline/
          │           edit/splice/write/delete/mkdir/apply + python helpers
          │           guide/grep/skill/diff/changes/undo/commit/help, final_answer/emit
          └─ RUN_PRELUDE: exec (argv/commands/shell) + git; run() only in repl/bare
                  │
                  ▼
              the workspace
```

What the diagram does not show:

- **Parent-side tools.** `docs(url)` (`bench/agent/docs.py`) and `delegate`
  (`bench/agent/subagent.py`) never reach the kernel: the parent loop fetches the page or runs a
  read-only child and returns one observation. `Skeptic` (`bench/agent/skeptic.py`) reuses the
  subagent as the reviewer that must falsify a finish. They are opt-in (`docs=`/`subagent=`/
  `skeptic=` on the loop, `CODE_TUI_DOCS`/`CODE_TUI_SUBAGENTS`/`CODE_TUI_SKEPTIC` in the TUI),
  so the typed registry stays the one catalog.
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

Selected by `CODE_TUI_MODE` in production (default `state`); an eval case builds its loop
through `bench/evals/loops.py::run_loop`.

| mode | Loop | Action space | Rules constant |
|---|---|---|---|
| `repl` | `ReplLoop` | one fenced python block per reply | `repl.REPL_RULES` / `BARE_RULES` |
| `tool` | `ToolLoop` | typed verbs + `python` | `tool_loop.TOOL_RULES` |
| `state` | `StateLoop` | same, observations rendered as state cards | `state_loop.STATE_RULES` |
| `bare` | `ToolLoop` with `goal=None` and no file helpers (the archived control) | one function: `python` | `repl.BARE_RULES` |

All loops share `Budget` (`max_steps`, `max_wall_s`, `max_output_tokens`, `max_total_tokens`,
where 0 means unlimited), `RunResult` and the
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
`python3 bench/smoke.py`/`ruff` plus the project's `lint`/`typecheck`/`format:check`/`test`
scripts (or `build` when neither exists); `tui/README.md` §7 owns the order. The runner follows
the lockfile (`bun.lock`/`bun.lockb` → bun, `package-lock.json` → npm). An empty
`CODE_TUI_CHECK` opts out.

## 7. Evals and grading

One registry (`bench/evals/runner.py::REGISTRY`) over two disjoint lists. A case id is stable and
names the mechanism it guards; the `failure` tag is the diagnostic axis the report aggregates by.

| Suite | Cases | Grades |
|---|---|---|
| loop (default) | `L01`-`L16` | the loop contract under a scripted model: staged finish, goal rejection, bad arguments, repeated reads, the journal, compaction, custom tools, skills, delegation, the skeptic, the token cap, docs |
| live (`--live`) | `A01`-`A10`, 14 cases | workspace behavior against a hidden oracle: the contract behind a green visible test, five latent bugs, a fact across compaction, an on-demand skill, a skeptical finish, delegation, a repair, the real worker path, docs grounding, the project's own lint |

Loop cases pass only when the stop, the observation and the oracle agree; a live case passes only
when its oracle passes, so the model's prose is never the verdict. Fixtures are built per case in a
temp workspace (`bench/evals/fixtures.py`) and the answers live outside it (`bench/evals/oracle.py`).
The failure tags in use are `finish`, `tooling`, `mutation`, `context`, `verification`, `cost` and
`autonomy`.

## 8. Commands

### Evals

```bash
python3 bench/guide.py
python3 bench/guide.py --query checks
python3 bench/guide.py --module bench/agent/check_plan.py
python3 bench/guide.py --json --query checks
python3 bench/guide.py --check

python3 bench/eval.py                      # 16 scripted loop contracts, no key, no network
python3 bench/eval.py --list               # ids, kinds, failure tags
python3 bench/eval.py --tasks L04_bad_args,L15_autonomy
DEEPSEEK_API_KEY=... python3 bench/eval.py --live   # 14 model-backed failure cases
```

Every pass prints one row per case and appends a JSON file under `bench/evals/results/` with
`passed`, `tokens/pass`, the per-failure-tag summary and the per-case stop/steps/usage. The
offline suite is the regression gate: finish staging, goal rejection, bad arguments, repeated
reads, the journal, compaction, custom tools, skills, delegation, the skeptic, the
unattended-ask gate and the token cap. The live suite grades workspace behavior with hidden
oracles: the contract behind a green visible test, latent bugs, a fact across compaction, an
on-demand skill, a skeptical finish, delegation and a normal repair.

Knobs: `--tasks`, `--model`, `--effort` (the provider's `reasoning_effort`, default `low`; `--effort ""` omits it, `CODE_TUI_EFFORT=none` in the TUI), `--tag`, `--no-write`.
The TUI uses 300 steps / 1800s / 120s per cell; a live eval per case uses the `Budget` in its
case definition, including `max_total_tokens`.

### Workspaces and live runs

```bash
python3 bench/smoke.py                 # offline contract check
python3 -m bench.snapshot freeze SRC DST   # clone + write DST.manifest.json
python3 -m bench.snapshot diff  DST        # added/modified/deleted vs the manifest
python3 bench/live.py --base bench/archive/results/ws/tui-base --tag r4 --prompt "make typecheck pass" \
        --check "bun run typecheck" --legacy r3
python3 bench/task_journal.py ~/.local/state/code-tui/workspaces/<hash> <task-id>
python3 bench/task_journal.py --recent 10  # newest tasks across every workspace
```

`live.py` is the honest end-to-end test: it clones `bench/archive/results/ws/tui-base`, drives
`tui/python/worker.py`, records `<tag>.events.jsonl`, `<tag>.prompt.txt`,
`<tag>.summary.json`, runs the check and diffs the tree.

### TUI

```bash
cd tui && bun install
bun run typecheck      # tsc --noEmit; noUnusedLocals/noUnusedParameters are on
bun test               # 16 tests, 7 files
./bin/code             # inside the target project; starts/reuses its server
./bin/code-stop        # SIGTERM the server for this directory
```

The agent's Python kernel is `python3` from `PATH` (`CODE_TUI_PYTHON` overrides); it imports
`bench.agent` from this repo, so the checkout path matters.

## 9. Gotchas

- `bench/eval.py` has two import blocks: script mode inserts the repo root on `sys.path`;
  package mode uses relative imports. Keep both working.
- A live eval's oracle is the verdict. The model's prose is never the pass condition, and
  `autonomy=True` rejects a finish that asks a human.
- The TUI server detects the check plan **when a task starts**, so a script an earlier task added
  to `package.json` is picked up without a restart.
- `bench/archive/results/ws/tui-base` and `tui-broken` are archived frozen snapshots.
  Recreate a base with `python3 -m bench.snapshot freeze tui <dst>`; `bench/ws/` is generated
  and gitignored.
- Tests are TS only (`bun test` = 16 tests, 7 files). React `act(...)` warnings are expected
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
| `README.md` | an agent that needs the machinery | runtime flow, tree kinds, evals, commands, gotchas |
| `tui/README.md` | an agent maintaining/running the TUI | wire contract: state root, HTTP/WS API, events, statuses, env |
| `bench/HARD.md` | an agent about to simplify the tool surface | measured evidence and what earned its keep |
| `bench/agent/*` headers | an agent locating an owner | parsed by `headers.py`; `guide.py --check` gates them |
| `bench/evals/fixtures.py` / `bench/evals/oracle.py` | an agent **under test** | graded eval input and answer. Do not rewrite as documentation: `fixtures.py` builds the workspace and `oracle.py` owns the expected result, never both in one file |
| `bench/archive/legacy/` fixtures / `SPEC.md` | the agent **under test** in the retired suites | graded task input and the record behind `HARD.md`; do not rewrite as documentation |
