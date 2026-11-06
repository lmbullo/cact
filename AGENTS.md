# AGENTS.md — operating manual for coding agents

You are the reader. No human reads these files; every document in this repo is an interface
specification for an agent that will edit the code without supervision. Write and edit them the
same way: facts, entry points, invariants, failure modes, exact commands. No onboarding prose,
no persuasion, no screenshots-for-humans.

Precedence when sources disagree:

1. the module header / the code (`python3 bench/map.py --query <topic>`)
2. this file (the short contract)
3. `README.md` (the full reference)
4. every other document

Fix the lower layer in the same change that breaks it.

## 0. Fast path

```bash
python3 bench/map.py --check                 # gate: every module header is complete
python3 bench/map.py --query "<topic>"      # which module owns this
python3 bench/map.py --module <path>        # one module's header whole
python3 -m ruff check .                     # gate: the code contract (ruff.toml)
python3 bench/smoke.py                      # offline contract check, no key, no network
```

Then read only the header and the line range you need (`read(path, section=...)` inside a code
cell, `read`/`outline`/`search` as tools). Do not read a 1000-line module to learn its entry
points; the header says them and every public name carries a one-line docstring.

## 1. What this repo is

An LLM coding agent in two halves, one language each:

| Path | Language | What it is |
|---|---|---|
| `bench/` | Python 3.10+, stdlib only | The agent (`bench/agent/`), the benchmark suites, fixtures, recorded traces |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`: a server-backed TUI that runs the same agent on the current working tree |

The agent is a model in a loop with a **persistent Python kernel**. The action space is 15 typed
tools generated from one registry (`bench/agent/verbs.py`): `guide`, `map`, `read`,
`session_diff`, `search`, `recall`, `outline`, `edit`, `splice`, `write`, `delete`, `mkdir`,
`apply`, `exec`, `git`. `python` is the escape hatch for computation, not a verb. `run()` is a
python-only helper, present in `repl`/`bare` modes and absent from typed modes.

No root `package.json`, no `pyproject.toml`, no CI. The only install step is `bun install` in
`tui/`.

## 2. Load-bearing properties

These are architecture, not style. A change that breaks one is a regression even if tests pass.

- **Self-describing surface.** Every source module opens with WHAT IT IS / ENTRY POINTS /
  INVARIANTS / WHERE TO CHANGE. `bench/agent/headers.py` parses those sections into rows;
  `bench/map.py` and the in-kernel `guide()` verb both read them. `help(tool)` returns the live
  signature, and the provider schema comes from the same registry entry. An agent must never
  need to read an implementation to learn how to call it.
- **Self-verifying work.** `final_answer` is staged, then rejected until the goal `Verifier`
  plan passes. A failed check is a repair instruction, not a question. There are no interactive
  prompts in production (`tui/python/worker.py` is unattended).
- **One fact, one place.** Adding a verb is exactly: registry entry in `verbs.py`, function in
  `kernel/prelude.py` or `kernel/run_prelude.py`, a `Kernel.call` case in
  `bench/smoke_checks/`, and one sentence in the rules text. Schemas and `help()` are generated;
  the rules prose is the only hand-written catalog. Never add a second catalog to a prompt or a
  README.
- **Machine-readable failure.** Unknown tools, bad arguments (`kind: "bad_args"`,
  `error.code: "bad_arguments"`, plus the signature hint), refused mutations (`wrong_call`) and
  check failures return structured dicts (`kind`, `error`, `use`, `_OBS[n]`). Do not replace a
  structured answer with prose only a human can parse.
- **Legibility is enforced, not requested.** Three cheap offline gates: `bench/map.py --check`
  for the module header, `ruff.toml` (E/F/W/B, blank-line convention included) for the code,
  and a one-line contract docstring on every public function and class. A public name without
  a docstring is a defect; so is a name one character away from another (`check` vs `checks`,
  `run` vs `runs`, `ws.py` next to `ws/`). Names come in prefix families, never near-duplicates.
- **Design the tool for the job.** `exec` takes argv + cwd, not a command string: no quoting, no
  `cd` prefix, no shell grammar. `search` takes `include`/`context`, not a pipeline. When the
  common path is a typed tool, the shell stops being the catch-all. Use `exec(shell=...)` only
  for syntax that genuinely needs a shell.
- **The bottleneck is reasoning and turns.** Never fix an efficiency problem by lowering
  `max_steps`, truncating context, or weakening the model. Reduce wasted turns (typed results,
  batching, idempotent reads) and wasted verification (the check plan and its `[check]` line).

## 3. Read these first

| File | Owns |
|---|---|
| `bench/agent/kernel/` | The action space, split by lifecycle: `protocol.py` (PROTOCOL + Cell wire shapes), `driver.py` (child loop), `prelude.py` (assembled PRELUDE), `prelude_setup.py` / `prelude_journal.py` / `prelude_index.py` / `prelude_read.py` / `prelude_verbs.py` (the five fragments), `run_prelude.py` (exec/git + gates), `process.py` (`Kernel`: spawn, supervise, restart, reap) |
| `bench/agent/verbs.py` | The typed verb registry: name, schema, summary, `mutates`, implementing function. `tools.py` builds provider specs from it; the child receives it as `KERNEL_VERBS` |
| `bench/agent/tools.py` | `TYPE_TOOLS`, generated from `verbs.VERBS`; `python` stays the escape hatch in `tool_loop.py` |
| `bench/agent/headers.py` | The header index (`index`, `guide`, `problems`). Shipped as source to every kernel child, so the CLI and the verb run the same code |
| `bench/agent/state_loop.py` | The production loop: tool call → kernel cell → typed observation or state card, context compaction |
| `bench/agent/envelope.py` | The one typed observation shape: `ok/op/kind/data/page/handle/notes/error`; `page` is the only paging contract |
| `bench/agent/tool_loop.py` | The turn machine: `final_answer`, goal checks, stop reasons, call pairing |
| `bench/agent/check_plan.py` | The check plan and `Verifier`: goal, cheap subset, scope filter, throttle, cache (one command: `check_run.py`) |
| `bench/run.py` | Benchmark CLI: suites, modes, budgets, traces, results table |
| `bench/smoke.py` + `bench/smoke_checks/` | The offline contract check; one group per area |
| `bench/HARD.md` | Measured evidence for why the envelope exists; read before simplifying the tool surface |
| `tui/python/worker.py` | The seam between the TUI and `bench`: builds the loop, streams `@@TUI@@` JSON lines to `server.ts` |
| `tui/README.md` | The TUI wire contract: state root, HTTP/WS API, events, statuses, env vars |

## 4. Invariants

1. **A finish is staged, then accepted.** `final_answer(v)` always holds the answer (`STAGED`).
   The next reply with no tool call accepts it after the goal check (when one exists). For a
   silent stage that reply's content wins over `v`, so a short final value cannot erase the
   report written next. An accepted finish ends the turn: the remaining calls in the same
   message do not run, but each still gets a paired (skipped) tool result. With a goal check no
   answer is accepted until it passes. `run.py` grades `result.final`; a prose reply with no
   tool call is the summary.
2. **`exec` is the one process tool; only mutation is gated.** `exec(argv)` runs a program with
   `cwd`, `env`, `stdin` and `commands=[[...]]`; `exec(shell=<string>)` is the explicit shell
   escape hatch. `bash -lc` inside argv is refused (`shell_via_exec`) and points at `shell`.
   `run()` exists only in python-only modes. The typed call is bound to the verb's real
   signature before dispatch, so a wrong or missing keyword is `bad_args`/`bad_arguments`
   (`help(tool)` prints the signature), never a traceback. A typed verb returning `ok: false`
   is `Cell.rejected` and counts as an error, so a bad argument shape is visible in run metrics.
   Programs/verbs that change tracked files (`tee`, `cp`, `mv`, `rm`, `sed -i`,
   `git apply/checkout/...`, `>` redirection; a quoted `>` is an argument) are refused as
   `kind: "wrong_call"` with a `use` hint, because they bypass the journal. `git
   add/commit/push/fetch/init/clone` are allowed, as is mutation of a scratch path (`/tmp`,
   `/var/tmp`, `/dev/shm`, `$TMPDIR`). Direct spawning (`subprocess.Popen`, `os.system`,
   `os.exec*`, `os.posix_spawn`, `os.spawn`) runs through the same classifier (`_audit_spawn`).
   A program can still write files: the journal is a typed-helper contract, not a sandbox. Keep
   `_mutating_command`, `_HINT` and `RUN_NOTE` honest.
3. **`write()` replaces** and refuses to silently drop a tail without `force=True`.
   **`edit(edits=[{path, old, new}, ...])`** is the typed batch form (the python `edit(path,
   old, new)` helper remains); the list is atomic — all old strings must match, or nothing
   changes. **`delete(path)`** and **`mkdir(path)`** cover the shell mutations that would
   bypass the journal. **`splice(path, start, end, text)`** replaces 1-based inclusive lines.
   **`apply(<unified diff>)`** writes several files in one cell. All go through `_commit`, which
   is what makes the journal honest; `commit()` is a python helper.
4. **A repeated read is a pointer, not a re-delivery.** `read` tracks delivered ranges and the
   file digest; a contained range again returns `already_read` with the `_OBS[n]` handle.
   `read(..., repeat=True)` re-delivers. `_commit` invalidates the entry; an external write is
   caught by the digest. This is context hygiene, not a read cap. A typed result is normalized
   into `Cell.envelope` and rendered verbatim; only python/exec output is digested. `has_more`
   means the file continues after `shown`; `truncated` means the default page limit cut the
   requested output — an explicit `start`/`end` before EOF is complete and only has
   `has_more`.
5. **Map and search skip generated trees.** `map`/`search` use `_walk`, which applies root
   `.gitignore` patterns plus `_SKIP_DIRS`; `bench/ws`, `.tmp`, `.cache` and `node_modules` do
   not pollute a repo map. `search` returns exact line text (no 200-char clip), merges
   overlapping context so each line appears once, and keeps hit text in stdout while the result
   carries locators. Changing `.gitignore` resets the pattern cache.
6. **Graders hash the test files** (`edge.py::_suite`). Repairing `test_core.py` or
   `test_calc2.py` scores zero; repair the source. The hard suite's oracle lives outside the
   workspace (`bench/hard_hidden/`) and cannot be read or edited by the agent.
7. **The check plan is the goal, and one `Verifier` owns it.** `run_plan`/`run_specs` run the
   ordered plan and return the first failure; a spec's `scope` prefixes filter the *cheap*
   health run to the paths a cell changed (the goal still runs every spec). The `Verifier` owns
   the goal, the cheap subset, the throttle, the cache and rendering: the cheap subset re-runs
   after a mutating cell (at most every 20s) and appears as the `[check]` line of the state
   card. `final_answer` is rejected (`GOAL_UNMET`) until the whole plan passes. Production
   passes `goal_stops=False`, so a failing check never ends the run by itself.
8. **Ground truth is computed at import time** in `tasks.py` / `edge.py` / `harness.py` /
   `hard.py` from the fixtures. Editing a fixture silently redefines the expected answers;
   `H3_bigcsv`'s CSV is generated and gitignored, and the H1/H2/H4 oracle lives in
   `hard_hidden/`. Fixture Markdown (`SPEC.md`, `docs/CURRENT.md`, `fixtures/notes.md`) is
   graded input, not documentation: do not rewrite it as prose.
9. **`bench/traces/` is append-only evidence.** Add runs; never rewrite a recorded journal.
   `bench/ws/` (frozen workspaces + `runs/` journals) is gitignored measurement output: rebuild
   `tui-base` with `python3 -m bench.snapshot freeze tui bench/ws/tui-base`, and let `bench/live.py`
   create `runs/<tag>`; do not expect either in a fresh clone.
10. **Generated, not source:** `bench/.tmp/`, `bench/.cache/`, `bench/ws/`, `tui/node_modules/`,
    and the TUI state root (`~/.local/state/code-tui/` by default). `bench/edge_fixtures/`,
    `bench/harness_fixtures/`, `bench/sandbox_fixtures/` and `bench/sandbox_hidden/` are
    committed but rebuilt by their `build()` when missing.
11. **`bench/run.py` has two import blocks** — script mode (`python3 bench/run.py`) and package
    mode (`python3 -m bench.run`). Keep both working.
12. **Every module explains itself, and something checks.** A source module carries all four
    header sections; `bench/agent/headers.py` parses them and `python3 bench/map.py --check`
    (also a smoke check) exits 1 when one is missing. `kernel/prelude.py` ships `headers.py`'s
    *source* to the child in `KERNEL_HEADERS` and execs it into a private `_HEADERS` namespace,
    exporting only `guide()`: keep that file stdlib-only, free of `__file__`, and read-only, or
    the verb breaks where `bench` is not importable. Never inline the index into `PRELUDE`
    again — a shared namespace let its `_walk` shadow the prelude's.
13. **`exec`/`run()` children are journalled and reaped.** Each program starts in its own
    session; the child writes its group leader to `KERNEL_ORPHAN_JOURNAL`, and `Kernel.stop()`
    / `_restart()` kill those groups. Removing the journal or the reap leaks processes when a
    cell times out or the kernel dies. `recall(pattern)` is the session-side search over `_OBS`;
    `search` (`grep()` in python) is the tree-side search. `_OBS` is persisted to
    `KERNEL_OBS_LOG` and reloaded on restart, so `recall()` survives a crash; variables do not.

## 5. Verification gates

Run in this order. A failure is a contract change, not a flaky test.

| Command | Covers | Expected |
|---|---|---|
| `python3 bench/map.py --check` | every module header has its four sections | exit 0 |
| `python3 -m ruff check .` | code: E/F/W/B against `ruff.toml` (fixtures and generated trees are excluded there) | exit 0 |
| `python3 bench/smoke.py` | imports, kernel prelude, `run()` allow/refuse, spawn gate, apply/commit, recall across restart, envelopes, reply/check parsing, header index, `guide()`, ruff | exit 0 |
| `python3 -m compileall -q bench tui/python` | syntax | exit 0 |
| `cd tui && bun run typecheck` | TS types | exit 0 |
| `cd tui && bun test` | rendering/detection tests, 6 files | 13 pass; React `act(...)` warnings are expected noise |

Smoke is the fast signal: no key, no network, nothing written outside a temp dir. Python has no
unit-test framework; smoke plus one real task is the whole story:

```bash
DEEPSEEK_API_KEY=... python3 bench/run.py --suite edge --tasks E1_tests --mode state --traces
DEEPSEEK_API_KEY=... python3 bench/run.py --suite harness --mode state   # tool-path evals L1-L8
DEEPSEEK_API_KEY=... python3 bench/run.py --suite sandbox --tasks S1_feature --mode state --traces
```

Needs network and a key. Without one, stop at the offline gates and say so.

## 6. Change recipes

| Change | Touch | Verify |
|---|---|---|
| A typed action | `verbs.py` (entry), `kernel/prelude.py` or `kernel/run_prelude.py` (function), `bench/smoke_checks/verbs.py` (a `Kernel.call` case), rules text | smoke |
| A process/git gate | `kernel/run_prelude.py` (`_mutating_command`, `_HINT`, `RUN_NOTE`) | `smoke_checks/gates.py` |
| Observation shape/paging | `envelope.py`, `state_loop._observe`, `repl.observation` | smoke envelopes + a run |
| Finish protocol or stop reason | `tool_loop.ToolLoop.run` (a reply dialect overrides `_reply`, see `repl.py`) | `smoke_checks/turns.py` |
| A check command / plan | `check_run.py` (one command), `check_plan.py` (`CheckSpec.parse`, `Verifier`), `tui/src/detect-checks.ts` | smoke plans + `bun test` |
| A benchmark task | `tasks.py` (read/mutation), `edge.py`, `harness.py`, `sandbox.py`, then a recorded run | suite import + trace |
| Grading semantics | the `grade_*` functions in the same module | re-run the suite |
| Model/temperature/cache | `llm.Client`, `run.py` flags, TUI env | a live run |
| TUI board/log/keys | `tui/src/client.tsx`, `board-view.tsx`, `log-view.tsx`, `theme.ts`, `list-model.ts` | `bun test` |
| TUI queue/journal/API | `tui/src/server.ts`, `tui/src/types.ts` | `bun test`, `bench/live.py` |

Adding a module means adding its four-section header before its first line of code, a one-line
contract docstring on every public function and class, then `python3 bench/map.py --check` and
`python3 -m ruff check .`. Name it so a `search` for the concept finds one family of files
(`check_run.py` / `check_plan.py`), never a name one character away from an existing module.
Adding a kernel helper means also adding the matching rule text
to `tool_loop.TOOL_RULES` / `repl.REPL_RULES` / `state_loop.STATE_RULES`, or the model will not
know it exists. If it replaces a mutating shell verb, add it to `_RUN_SHELL_MUTATORS` /
`_RUN_GIT_MUTATORS` and `_HINT` too.

## 7. Working rules

- State the contract, the entry point and the failure mode. Delete tutorials.
- Every public function, method, class and exported TS symbol carries a one-line docstring
  (Python) or JSDoc line (TS): that line is the contract an agent reads through `inspect`,
  `help(tool)`, `outline` and editor hover. It states what the thing does, not how. A private
  helper gets one when its name is not obvious. Python is gated by `smoke_checks/style.py`;
  TS conventions are not gated, so match the file you are editing.
- Prefer one call that does the job over a pipeline of calls; prefer a typed tool over
  `exec(shell=...)`; prefer deleting a duplicate catalog over syncing two copies.
- A doc claim must be executable or checkable. If you cannot verify it, cut it.
- Commit style: `area(scope): lowercase summary`, e.g.
  `bench(agent/kernel): add grep, write, edit`.
- Commit and push only after the gates in §5 pass. Report what changed, what you verified, and
  what is still unverified.

## 8. Failure modes you will actually hit

| Symptom | Cause | Fix |
|---|---|---|
| `bad_args` / `bad_arguments` | keyword does not match the verb signature | `help(tool)`; the schema and signature must agree |
| `wrong_call` with a `use` hint | a shell program/verb would mutate tracked files | use `write`/`edit`/`splice`/`delete`/`mkdir`/`apply`; scratch paths are allowed |
| `shell_via_exec` | `bash -lc` inside `argv` | `exec(shell=<string>)` |
| cell `restarted` with an empty namespace | protocol mismatch, timeout, or dead child | re-read the state; variables are gone, `_OBS` survived |
| `GOAL_UNMET` | the check plan still fails | repair the source; do not weaken the plan |
| `already_read` pointer | range was already delivered | page the `_OBS` handle or pass `repeat=True` |
| map/search misses a file | generated tree, `.gitignore`, or an unmatched include | the result names unmatched patterns; adjust |
