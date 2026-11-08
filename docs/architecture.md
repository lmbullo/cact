# docs/architecture.md — trees, one cell end to end, loops, observations, the check plan

You are the reader. This file owns the runtime shape: which trees are input, output or generated,
how one cell travels from a loop to the kernel and back, what the loops are, how a typed
observation pages, and how the check plan and compaction gate a finish. The invariants these
mechanics satisfy are `../AGENTS.md` §2/§4; the commands that prove them are `run.md`; module
headers are the per-module source (`python3 bench/guide.py`).

## 1. Trees and fixtures

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

## 2. How one cell flows

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
          │           guide/grep/recall/verbs/diff/changes/undo/commit/help, final_answer/emit
          └─ RUN_PRELUDE: exec (argv/commands/shell) + git; run() only in repl/bare
                  │
                  ▼
              the workspace
```

What the diagram does not show:

- **Parent-side tools.** `docs(url)` (`bench/agent/docs.py`) and `delegate`
  (`bench/agent/subagent.py`) never reach the kernel: the parent loop fetches the page or runs a
  read-only child and returns one observation. `Skeptic` (`bench/agent/skeptic.py`) reuses the
  subagent as the reviewer that must falsify a finish. They are wired per run (`docs=`/`subagent=`/
  `skeptic=` on the loop; `CODE_TUI_DOCS` on unless `0`, `CODE_TUI_SUBAGENTS=1`,
  `CODE_TUI_SKEPTIC=1` in the TUI), so the typed registry stays the one catalog.
- **Model client.** `bench/agent/llm.py` posts to `https://api.deepseek.com/chat/completions`
  (`DEEPSEEK_BASE_URL` overrides) and makes four attempts, retrying 429/5xx and network errors
  with a linear backoff. Responses cache under `bench/.cache/llm/` by request hash when
  `Client(cache=True)`; the TUI worker
  constructs the client with `cache=False`.
  The reply is `content`, `reasoning` (the provider's `reasoning_content`), `tool_calls`, `usage`,
  `finish`; the loop appends `{"role": "assistant", "content", "tool_calls"}` to its history and
  keeps `reasoning` in the turn record only, so a later turn does not replay it.
- **One cell pipeline.** `Kernel.run(code)` and `Kernel.call(tool, args)` write one `CellRequest`
  JSON line (`PROTOCOL` + `kind`/`code` or `kind`/`tool`/`args`, `timeout`, `check_cache`), read
  one `@@K@@` `CellReply` line, and return a `Cell` (`stdout`, `stderr`, `result`, `final`,
  `duration_ms`, `restarted`, `answer`, `runs`, `obs_index`, `mutated`, `changed`, `deltas`,
  `delta`, `timeout`, `reads`, `tool`, `failed`, `rejected`, `envelope`). A reply whose `protocol`
  does not match `PROTOCOL` is never read through the current shape: the child restarts and the
  cell returns an error. A dead or hung kernel is killed and restarted **with an empty
  namespace**; the loop says so instead of hiding it.
- **TUI event shapes.** `tui/python/worker.py::StreamingKernel` emits `cell.start` before every
  request and a compact `cell.end` after it: `{cell, tool, ok, verb_kind, code, page:{shown,
  total, unit, has_more, truncated, complete}, chars, duration_ms, notes:[one-line]}`. `chars`
  is `len(stdout)+len(json result)` as delivered and no payload body is copied, so the journal
  stays about the same size. `turn` events (and `turn.observation`) are unchanged, so a client
  that predates `cell.end` still reads the run; the journal reader (`bench/task_journal.py`)
  prefers `cell.end` for `obs_chars`, `rejected` and `dupe_note_turn`, and counts `restarts`
  from the observation's boolean `restarted` rather than searching delivered text.
- **Results are dicts that also answer attributes.** `exec(...)` and `run()` return
  `result['stdout']` (or `result.stdout`); `read(raw=True)[:n]` and `read(...)['content']` work,
  so a natural model mistake does not cost a cell. The canonical keys are the contract.
- **Journalling.** The prelude snapshots the tree at start; every cell diffs it. That is what
  `changes()`, `diff()`, `undo()` and `[state] changed:` read.
- **Sandbox switch.** `exec`/`git`/`run()` enabled ⇒ the kernel is a plain `python3 -u -c DRIVER`
  child. Disabled (`CODE_TUI_ALLOW_RUN=0`, `worker.py --allow-run 0`) ⇒ the child runs under
  `bwrap` with a read-only `/` plus a writable bind of the workspace only, and the `exec`/`git`
  verbs are removed from the spec list.

## 3. Reading a typed result

The verb registry (`bench/agent/verbs.py`) is the only catalog: name, JSON schema, one-line
summary, `mutates`, implementing function. `bench/agent/tools.py` builds the provider specs from
it, and `help(tool)` prints the live signature — so there is no tool table in this file to keep in
sync. The registry entry and the function docstring are what an agent reads.

Paging contract, shared by every paged verb: `[page]` carries
`shown/total/offset/next/unit/has_more/truncated/complete`; `offset` is the start of what this
response delivered and `next` is the only resume cursor, a replayable argument object.
`has_more` means the file/result continues; `truncated` means a built-in default limit cut
output the caller did not ask to have cut; `complete` is exactly `not truncated`, and an
explicit window (`end`, `max_files`, `max_entries`, `max_results`, `max_chars`) is complete
with `has_more` true. A window of 0 removes the cap instead of serving a page:
`map(max_files=0)` and `search(max_results=0)` return every row/hit in one answer.
`complete` is the field to trust, not a prose tail. Every typed verb
answers a refusal with the same `{ok:false, kind, error:{code,message,use,signature}}`, and a
typed verb that returns `ok: false` is a rejected cell and counts as an error in run metrics.

## 4. The loops

Selected by `CODE_TUI_MODE` in production (default `state`); an eval case builds its loop
through `bench/evals/loops.py::run_loop`.

| mode | Loop | Action space | Rules constant |
|---|---|---|---|
| `repl` | `ReplLoop` | one fenced python block per reply | `repl.REPL_RULES` |
| `tool` | `ToolLoop` | typed verbs + `python` | `tool_loop.TOOL_RULES` |
| `state` | `StateLoop` | same, observations rendered as state cards | `state_loop.STATE_RULES` |
| `bare` | `ToolLoop` with `goal=None` and no file helpers (the archived control) | one function: `python` | `bench/archive/legacy/run.py` (`BARE_TOOL_RULES`) |

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

## 5. Check plan and compaction

`bench/agent/check_plan.py` holds an ordered list of `{name, cmd, cheap, scope}` and the `Verifier`
that owns it: the cheap subset, the `scope` filter, the `HEALTH_WINDOW` throttle, the check cache
and `Check` rendering. Results cache until the next mutating cell: a matching `run()`/`exec` is
answered from `check_cache` (`kind: "check_cached"`) instead of spawning the same command twice.

Cheap entries are health checks: they re-run after a mutating cell in their `scope` (at most every
20s) and print as the `[check]` line of the next state card. They never end a run. The whole plan
is the goal: `final_answer` is rejected (`GOAL_UNMET`) until every entry passes, then the task
lands `done` with `verified: <names> passed`.

Production passes `goal_stops=False`, so a passing health check after a mutation is evidence, not
a verdict: the model keeps working until it calls `final_answer` and the whole plan accepts that
claim. A failing check only rejects the finish. `StateLoop._compact` folds the message list once
the provider's real prompt token count reaches `config.COMPACT_AT_TOKENS` (85% of the 1M window);
it cuts only on an assistant tool-call boundary and replaces the dropped span with one
`[session state]` message carrying the changed files, the current diff, the per-file read coverage
and the last check, so no tool result is orphaned and the workspace stays authoritative.
`Budget.max_total_tokens` is a runaway guard a live case or a child agent sets, not a working
budget; production leaves it 0 (unlimited).

The TUI detects the plan per task: `CODE_TUI_CHECKS` (JSON) wins, else `CODE_TUI_CHECK`, else
`python3 bench/smoke.py`/`ruff` plus the project's `lint`/`typecheck`/`format:check`/`test`
scripts (or `build` when neither exists); `../tui/README.md` §7 owns the order. The runner follows
the lockfile (`bun.lock`/`bun.lockb` → bun, `package-lock.json` → npm). An empty
`CODE_TUI_CHECK` opts out.
