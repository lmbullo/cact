# how to — run it, check it, extend it, debug it

`README.md` says what cact is; `docs/how-it-works.md` says why it is built this way. This file is
the recipes: one job per section, the command that does it and what a normal result looks like.
Every command below is offline unless it says `run`, `live` or needs a key.

## 1. Run the agent on a repository

```bash
make -C rust build                              # rust/target/release/cact
cd /path/to/cact/tui && bun install             # once
export DEEPSEEK_API_KEY=...                     # or put it in ~/.config/code-tui/env
cd /path/to/the/project
/path/to/cact/tui/bin/code
```

The launcher hashes the current directory, takes a lock and starts or reuses one detached server
for that directory. Closing the UI leaves the server and its tasks running; `tui/bin/code-stop`
stops the server. The state root is `$CODE_TUI_STATE_ROOT`, else `$XDG_STATE_HOME/code-tui`, else
`~/.local/state/code-tui/`; the TUI wire contract, keys and env table are `tui/README.md`.

The server spawns `rust/target/release/cact worker` (or the debug binary) and there is no Python
fallback: a missing binary is a spawn error, so `make -C rust build` first.

## 2. Make the task finishable

The agent's definition of done is the check plan, so give it one before you give it work.

```bash
export CODE_TUI_CHECK="bun test"                                  # one command
export CODE_TUI_CHECKS='[{"name":"lint","cmd":"bun run lint","cheap":true},{"name":"test","cmd":"bun test"}]'
```

Without an override, `tui/src/detect-checks.ts` builds the plan from the tree: `ruff.toml` when
present, a `Cargo.toml` root (`cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test --workspace`), or the `package.json` scripts (`lint`, `typecheck`,
`format:check`, `test`). A task with no runnable checks still runs; its verifier is weak and its
report says so.

Name the outcome and where it applies, and let the checks carry the acceptance criteria. "Make
`bun run typecheck` pass and keep the suite green" is a complete task. "Do something nice with
the header" is not, and the agent will spend its budget guessing.

## 3. Watch a run

The board is append-only history: one row per task, live status, steps and cost. `Enter` on an
empty prompt opens the highlighted task's log; `Ctrl+Enter` queues the prompt; `Ctrl+K` cancels;
`Ctrl+O` cycles the board order. The full key table is `tui/README.md`. Press `?` for the in-app
help card: the same keys plus four starter use cases a digit loads into the prompt. Each task row
links to what the run is doing now; the counters are cells, steps and tokens, and the log page is
the state cards as they arrived.

## 4. Read a run afterwards

```bash
state=~/.local/state/code-tui/workspaces/<hash>
ls "$state"/tasks          # <id>.json: one snapshot per task
cat "$state"/events/<id>.jsonl   # the append-only event stream the log view reads
cat "$state"/logs/<id>.jsonl     # the machine log: event, stdout, stderr, prompt, usage, error records
cat "$state"/prompts/<id>.txt    # the prompt exactly as it was queued
```

The engine also keeps cross-task memory and the worker's state dir is `$CODE_TUI_STATE`, else
`$XDG_STATE_HOME/code-tui/<hash>`: `history.jsonl` (last 50 rows) is what the next task in the
same workspace receives as its `[workspace history]` prefix. A run's structured handoff — summary,
changed paths, gates, deferred, untested — is written there too, so the next task inherits it.

## 5. Run the gates

```bash
make -C rust fmt                      # cargo fmt --all --check
make -C rust check                    # clippy -D warnings, cargo test, the 19-case selfcheck
cd tui && bun run typecheck && bun test
```

`docs/run.md` owns the order, each expected exit code and the fact it proves. A failure is a
contract change, not a flaky test: fix the code or the document in the same change. Inside an
agent task the same plan is `checks()`.

## 6. Run one live task

```bash
export DEEPSEEK_API_KEY=...
make -C rust live                     # rust/scripts/live.sh: a throwaway repo, one small task
rust/target/release/cact run --workspace /tmp/demo --prompt 'Add fib.py and a pytest' \
    --checks '[{"name":"test","cmd":"python3 -m pytest -q"}]'
```

`rust/scripts/live.sh` prints `live: ok (exit <code>, fib.py present, pytest green)` only when the
file exists and the suite passes; `BIN` and `MODEL` override the binary and the model. Live runs
cost money; the offline gates are the regression gate.

## 7. Change the check plan

- One project's plan: set `CODE_TUI_CHECK` or `CODE_TUI_CHECKS` as above. `cheap: true` marks the
  specs that re-run after a mutation; `scope` is a list of path prefixes.
- A new convention for every project: `tui/src/detect-checks.ts` (its header names the two places
  to change), then `cd tui && bun test`.
- The engine side of a spec is `rust/crates/cact/src/checks.rs`; it owns the order, the cache key
  and the first failing line.

## 8. Add a typed verb

Three edits, one catalog:

1. `rust/crates/cact/src/spec.rs` — the `tool(name, description, properties, required)` entry. That
   string is what the model reads.
2. The implementation in the owning `rust/crates/cact/src/tools/*.rs` module, plus one arm in the
   `match name` of `tools::call`.
3. A case in `rust/crates/cact/src/selfcheck.rs`, then `make -C rust check`.

Do not add a tool table to a prompt or a document: the provider specs are generated from
`spec.rs`. If the verb can change tracked files, it goes through the journal (`mutate.rs`) so the
`changed` list and `undo` keep working.

## 9. Add a skill

A skill is a procedure the agent loads only when the task names it. Put it at
`.agent/skills/<name>.md` in the workspace the agent runs on; `skill()` lists the names and
`skill(name)` returns the text. Keep it short and imperative: steps, commands, stop conditions. It
is read, not executed.

## 10. Add a module or a document

A module opens with the four-section header (`WHAT IT IS` / `ENTRY POINTS` / `INVARIANTS` /
`WHERE TO CHANGE`), and every public name carries a one-line docstring that states its contract.
Then `make -C rust check`; the engine's `guide` verb indexes the header, and the selfcheck
proves the verb the module adds still behaves.

A document is added to the trigger map `docs/README.md` and to the docs list in `AGENTS.md`. A
backticked path must exist, and a document that names a removed file is a bug.

## 11. When a run goes wrong

Start with the structured answer, not the prose. `AGENTS.md` maps the common symptoms
(`bad_arguments`, `wrong_call`, `outside_root`, `GOAL_UNMET`, `green_plan`, a restarted cell) to a
cause. Three other cases are worth naming:

- The result is wrong: the checks are too weak — that is a fixture problem, and the fix belongs in
  the plan, not in a longer prompt.
- The run burned its budget: read the task log for the turn mix before changing the engine; the
  cause is usually a retry loop or a re-verification, not the model.
- The engine's own behaviour is in question: reproduce it as a selfcheck case in
  `rust/crates/cact/src/selfcheck.rs` instead of arguing about one transcript.
