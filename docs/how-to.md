# how to — run it, check it, extend it, debug it

`README.md` says what cact is; `docs/how-it-works.md` says why it is built this way. This file is
the recipes: one job per section, the command that does it and what a normal result looks like.
Every command below is offline unless it says `--live` or needs a key.

## 1. Run the agent on a repository

```bash
cd /path/to/cact && cd tui && bun install        # once
export DEEPSEEK_API_KEY=...                     # or put it in ~/.config/code-tui/env
cd /path/to/the/project
/path/to/cact/tui/bin/code
```

The launcher hashes the current directory, takes a lock and starts or reuses one detached server
for that directory. Closing the UI leaves the server and its tasks running; `tui/bin/code-stop`
stops the server. The state root is `$CODE_TUI_STATE_ROOT`, else `$XDG_STATE_HOME/code-tui`, else
`~/.local/state/code-tui/`; the TUI wire contract, keys and env table are `tui/README.md`.

## 2. Make the task finishable

The agent's definition of done is the check plan, so give it one before you give it work.

```bash
export CODE_TUI_CHECK="bun test"                                  # one command
export CODE_TUI_CHECKS='[{"name":"lint","cmd":"bun run lint","cheap":true},{"name":"test","cmd":"bun test"}]'
```

Without an override, `tui/src/detect-checks.ts` builds the plan from the tree: `bench/smoke.py`
and `ruff.toml` in this repo, a Rust crate, or the `package.json` scripts (`lint`, `typecheck`,
`format:check`, `test`). A task with no runnable checks still runs; its verifier is weak and its
report says so.

Name the outcome and where it applies, and let the checks carry the acceptance criteria. "Make
`bun run typecheck` pass and keep the suite green" is a complete task. "Do something nice with
the header" is not, and the agent will spend its budget guessing.

## 3. Watch a run

The board is append-only history: one row per task, live status, steps and cost. `Enter` on an
empty prompt opens the highlighted task's log; `Ctrl+Enter` queues the prompt; `Ctrl+K` cancels;
`Ctrl+O` cycles the board order. The full key table is `tui/README.md` §5. Press `?` for the
in-app help card: the same keys plus four starter use cases a digit loads into the prompt. Each
task row links to what the run is doing now; the counters are cells, steps and tokens, and the
log page is the state cards as they arrived.

## 4. Read a run afterwards

```bash
python3 bench/task_journal.py ~/.local/state/code-tui/workspaces/<hash> <task-id>
python3 bench/task_journal.py --compare ~/.local/state/code-tui/workspaces/<hash> A B
python3 bench/task_journal.py --recent 10
```

A journal row carries status, stop reason, steps, writes and edits, restarts and rejections, the
tool mix, and the escape metrics (`exec_verb_dupes`, `python3_c`, `shell`, `first_mutation_turn`,
`obs_chars_per_turn`). `bench/task_journal.py` owns the key list. The same data is on disk as
`<tag>.events.jsonl` next to each task.

## 5. Run the gates

```bash
python3 bench/guide.py --check     # module headers complete and within budget
python3 -m ruff check .            # code contract
python3 bench/smoke.py             # offline contract check, one group per area
python3 bench/eval.py              # 16 scripted loop contracts
cd tui && bun run typecheck && bun test
```

`docs/run.md` §1 owns the order, each expected exit code and the fact it proves. A failure is a
contract change, not a flaky test: fix the code or the document in the same change. Inside an
agent task the same plan is `checks()`.

## 6. Run one eval case

```bash
python3 bench/eval.py --list                          # ids, kinds, failure tags
python3 bench/eval.py --tasks L04_bad_args,L15_autonomy
python3 bench/eval.py --tag after-call-shape           # writes a result row, prints the report
DEEPSEEK_API_KEY=... python3 bench/eval.py --live --tasks A05_skeptic
```

Loop cases are scripted: no key, no network, deterministic. A live case spends tokens and needs
network; without a key, stop at the offline gates and say so. Use `--no-write` to skip the row
under `bench/evals/results/`.

## 7. Add a check command or a plan

- One project-wide command: set `CODE_TUI_CHECK`; it replaces detection for every task in that
  workspace.
- A plan with a cheap subset: set `CODE_TUI_CHECKS` as the JSON list above. `cheap: true` marks
  the specs that re-run after a mutation (the `[check]` line); `scope` is a list of path prefixes.
- A new convention for every project: `tui/src/detect-checks.ts` (its header names the two places
  to change), then `cd tui && bun test`.
- The Python side of a spec is `bench/agent/check_spec.py`; the runner is
  `bench/agent/check_run.py`; the plan and the goal gate are `bench/agent/check_plan.py`.
  `bench/smoke_checks/plans.py` covers the plan contract.

## 8. Add a typed verb

Four edits, one registry:

1. `bench/agent/verbs.py` — the entry: name, schema, `summary` (that string is what the model
   reads), `mutates`, function.
2. The implementing function in the owning `kernel/prelude_*.py` fragment, or in
   `kernel/run_prelude.py` for a process or git verb. `bench/agent/kernel/prelude.py` names the
   five fragments.
3. A `Kernel.call` case in `bench/smoke_checks/verbs.py` (plus `gates.py` if it can mutate).
4. `python3 bench/guide.py --check` and `python3 bench/smoke.py`.

Do not add a tool table to a prompt or a document: schemas, `help(tool)` and the provider specs
are generated from the same registry entry. If the verb can change tracked files, it goes through
the journal (`_commit`) and the gate lists in `kernel/run_prelude.py` name it.

## 9. Add a skill or a one-off tool

A skill is a procedure the agent loads only when the task names it. Put it at
`.agent/skills/<name>.md` in the workspace the agent runs on; `skill()` lists the names and
`skill(name)` returns the text. Keep it short and imperative: steps, commands, stop conditions. It
is read, not executed.

For an experiment, pass a manifest instead of changing the registry:

```bash
CODE_TUI_TOOLS=/path/to/manifest.json /path/to/cact/tui/bin/code
```

The manifest is JSON: a list of `{name, summary, schema, source}` where `source` defines exactly
the named callable and may use the prelude helpers. `bench/agent/custom.py` validates it and
rejects a name that collides with a built-in verb. Experiments add actions; they never weaken a
gate.

## 10. Add a loop eval case

1. Write the scripted model reply and its assertion in `bench/evals/loops.py`.
2. Register the case in `bench/evals/runner.py::REGISTRY` with a stable id and a `failure` tag.
3. `python3 bench/eval.py --tasks <id>` — a case passes only when the stop, the observation and
   the oracle agree.

A live case is a workspace fixture in `bench/evals/fixtures.py` plus a hidden answer in
`bench/evals/oracle.py`; never both in one file, and never a visible test as the oracle.

## 11. Add a module or a document

A module opens with the four-section header (WHAT IT IS / ENTRY POINTS / INVARIANTS / WHERE TO
CHANGE), at most 24 lines, and every public name carries a one-line contract docstring. Then
`python3 bench/guide.py --check`, `python3 -m bench.agent.pages --write` to regenerate the pages,
and `python3 bench/smoke.py`.

A document is created, added to `DOCS` in `bench/smoke_checks/docs.py`, and given a row in the
trigger map `docs/README.md`. The docs gate then resolves every path it names and rejects a
paragraph copied from another document.

## 12. When a run goes wrong

Start with the structured answer, not the prose. `AGENTS.md` §8 maps the common symptoms
(`bad_args`, `wrong_call`, `shell_via_exec`, a restarted cell, a rejected finish) to a cause. Three
other cases are worth naming:

- The result is wrong: the checks are too weak — that is a fixture problem, and the fix belongs in
  the plan, not in a longer prompt.
- The run burned its budget: read `bench/task_journal.py` for the turn mix and the escape metrics
  before changing the loop; the cause is usually a retry loop or a re-verification, not the model.
- The loop's own behaviour is in question: reproduce it as a loop case (§10) instead of arguing
  about one transcript.
