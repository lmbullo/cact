# docs/architecture.md — trees, one cell end to end, cards, the check plan

This file owns the runtime shape: which trees are input, output or generated, how one cell travels
from the engine to the kernel and back, how a typed observation pages, and how the check plan and
the card budget gate a finish. The design argument is `how-it-works.md`; the invariants are
`../AGENTS.md`; the commands that prove them are `run.md`; the module map is `../rust/README.md`.

## 1. Trees

A module's one-line contract is its `//!` header, not this file; the engine's `guide` verb prints
every module with that line. What a header cannot say is which trees are input, output or rebuilt:

| Tree | Kind | Note |
|---|---|---|
| `rust/crates/cact/src/` | input, source | one module per file, each opening with the four-section header |
| `rust/crates/cact/src/kernel_driver.py` | input, source | the cell child: the only Python the project ships |
| `rust/crates/cact/src/selfcheck.rs` | input, source | the 19 offline cases, each in its own scratch tree |
| `rust/target/`, `tui/node_modules/` | output, gitignored | the build and the install tree |
| `~/.local/state/code-tui/` | output, gitignored | the TUI state root; `$CODE_TUI_STATE_ROOT` or `$XDG_STATE_HOME/code-tui` move it |
| `<state>/.cache/llm/` | output, gitignored | the reply cache; `BENCH_LLM_CACHE` moves it |
| the engine's state dir | output, gitignored | `$CODE_TUI_STATE`, else `$XDG_STATE_HOME/code-tui/<hash>`: `history.jsonl` and the task logs |

## 2. How one cell flows

```
cact worker (the TUI's child)            cact run (a terminal)
   │  @@TUI@@{json} events on stdout        │  the cards, then the report
   └────────────────┬────────────────────────┘
                    ▼
        engine.rs: one model reply = one turn
          ├─ llm.rs ── one POST /chat/completions through curl ──► the provider
          ├─ shape.rs: repair a generic call shape before dispatch
          ├─ spec.rs::Scope::filter + tools::call: dispatch
          │     ├─ the typed verb, in process (read, search, map, outline, guide, skill,
          │     │   mutate, exec, git, checks, status, page, final_answer)
          │     └─ code/python ──► kernel.rs ──► kernel_driver.py (the persistent child)
          └─ card.rs: one state card per call
                    │
                    ▼
                the workspace
```

What the diagram does not show:

- **One engine.** `worker` and `run` share `Engine::run`; they differ only in the event sink and
  the output. The rules the model sees are `TOOL_RULES` + `STATE_RULES` + `AUTONOMOUS`, plus
  `RUN_NOTE` when `--allow-run 1`.
- **The cell is a child, not a second agent.** `kernel.rs` spawns `python3 -u -c
  kernel_driver.py`. A typed verb called inside a cell sends one `rpc` line to the parent, the
  parent runs the real Rust verb, and the cell receives the same observation text a typed call
  would have produced. A timeout kills the child; the next cell restarts it and the card says
  `[state] kernel restarted`.
- **The journal owns mutation.** `journal.rs` records every tracked edit with its diff.
  `mutate.rs` reads the before-body, writes, and records; `session_diff` and `status` read the
  journal back; `undo` reverts the last entry. "What changed" is a fact, not a reconstruction.
- **Memory is per workspace.** `history.rs` keeps one `history.jsonl` (last 50 rows) and renders
  the `[workspace history]` prefix the worker prepends to the next task's prompt.
- **The first message carries a probe.** `env.rs` renders one `[env]` card: the resolved root,
  the git root or `none`, a capped top-level listing, files at depth 2, the check plan, and any
  task-mentioned path that does not exist with the closest path that does.

## 3. Reading a typed result

Every verb returns one envelope (`envelope.rs`): `ok`, `op`, `kind`, `data`, `page`, `handle`,
`notes`, `error`, `changed`. `card.rs` renders it as `[s]`/`[obs]`/`[page]`/`[data]`/`[read]`,
never as a transcript.

The paging contract is shared by every paged verb: `[page]` carries
`shown/total/offset/next/unit/has_more/truncated/complete`; `offset` is the start of what this
response delivered and `next` is the only resume cursor, a replayable argument object. `has_more`
means the file or result continues; `truncated` means a built-in default limit cut output the
caller did not ask to have cut; `complete` is exactly `not truncated`, and an explicit window
(`end`, `max_files`, `max_entries`, `max_results`, `max_chars`) is complete with `has_more` true.
A window of 0 removes the cap instead of serving a page: `map(max_files=0)` and
`search(max_results=0)` return every row or hit in one answer. Trust `complete`, not a prose tail.

Large output goes to the session's `_OBS[n]` store (`obs.rs`) and is read back with `page`. A
repeat read returns a pointer unless the caller passes `repeat=true`.

Every refusal has the same shape: `{ok:false, kind, error:{code, message, use, signature}}`, and a
verb that answers `ok: false` is a rejected cell.

One more limit sits above the verb page: `Budget.card_budget` (`CODE_TUI_CARD_BUDGET`, default
5000) caps the card chars one turn may deliver. A card that exceeds it is cut, its `[read]` line is
recomputed from the lines that survived, and the cut names the exact resume: `read(path, start=N)`
for a read, `page(_OBS[n])` otherwise. The verb's `[page]` still describes the whole result; the
`[card]` line says what this turn actually received.

## 4. The turn machine

`Engine::run` is the only loop: send the transcript, read one reply, dispatch every call, append
one state card per call, and stop on the finish protocol.

- The turn is bounded: at most `max_calls_per_turn` calls, and the run is bounded by steps, wall
  clock, total tokens and the card budget. A call the per-turn budget skips still gets its paired
  `tool` message, because an unpaired call is a provider 400.
- The stop vocabulary is one closed set in `engine.rs::stops`: `DONE`, `VERIFIED`, `STEP_LIMIT`,
  `WALL_LIMIT`, `GOAL_UNMET`, `TRUNCATED`, `LLM_FAILED`, `TOKEN_LIMIT`.
- The worker maps that set to a task status: `DONE`/`VERIFIED` are `done`, a spent budget with a
  summary is `partial`, everything else is `failed`. The exit code is 0 only for `done`.

## 5. The check plan

`checks.rs` holds `Spec {name, cmd, cheap, scope}`, built from the worker's `--checks` JSON.
`tui/src/detect-checks.ts` builds that JSON from the tree when the user sets no override:
`CODE_TUI_CHECKS` (a JSON list) wins over `CODE_TUI_CHECK` (one command), and an empty
`CODE_TUI_CHECK` disables verification. Otherwise it reads `ruff.toml` or a `Cargo.toml` root and
the `lint`, `typecheck`, `format:check` and `test` scripts of the root and `tui/package.json`, and
marks `lint`, `typecheck` and `format` cheap so a mutating cell cannot leave a tree the project
itself would reject.

- Order is contract: cheap specs run first, so a broken tree is reported before the slow suite.
- "Unrunnable" (spawn failure, timeout) is a broken check, never a failed project.
- A green spec is cached for the run by `(cmd, digest of the scope files)`, so a repeat is free.
- Once the plan is green, `checks` answers from that cache and a gate re-run through `exec` is
  refused with `green_plan`; a mutation or a cell that changed files reopens verification. A
  read-only `exec` stays open, because the model often checks a value the tests do not cover.
- `final_answer` is rejected until the plan passes and the prompt's explicit imperatives are
  supported. `contract.rs` parses those imperatives (`commit`, `branch`, `push`, `verify`,
  `change`, and their negations) into plan items, and `Evidence` records what the run actually
  did; prose is never evidence.
