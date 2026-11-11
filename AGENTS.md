# AGENTS.md — operating manual for coding agents

This is the short contract for an agent that will edit the code without supervision. Every
document in the repo is an interface specification written for that agent; a human can read the
same text and get the same facts, which is why the public entry (`README.md`) points here instead
of restating it. Write and edit them the same way: facts, entry points, invariants, failure modes,
exact commands. No onboarding prose, no persuasion, no screenshot-as-argument.

Precedence when sources disagree:

1. the module header / the code (`python3 bench/guide.py --query <topic>`)
2. this file (the short contract)
3. `docs/` (the document map, task recipes, the design argument, invocation, runtime shape) and
   `README.md` (identity, the eval registry, status)
4. `tui/README.md`, `bench/HARD.md`

Fix the lower layer in the same change that breaks it.

## 0. Fast path

```bash
python3 bench/guide.py --check           # gate: every module header is complete
python3 bench/guide.py --query "<topic>" # which module owns this
python3 bench/guide.py --module <path>   # one module's header whole
python3 -m ruff check .                  # gate: the code contract (ruff.toml)
python3 bench/smoke.py                   # offline contract check, no key, no network
```

Inside a task the same plan is one call: `checks()`. The tree-state equivalent of `git status`
+ `git log` + `session_diff` is `status()`. Run a gate ladder by hand only when debugging
`checks()` itself.

Then read only the header and the line range you need (`read(path, section=...)` inside a code
cell, `read`/`outline`/`search` as tools). Do not read a 1000-line module to learn its entry
points; the header says them and every public name carries a one-line docstring.

## 1. What this repo is

An LLM coding agent in two halves, one language each:

| Path | Language | What it is |
|---|---|---|
| `bench/` | Python 3.10+, stdlib only | The agent (`bench/agent/`), the failure-mode evals (`bench/evals/`), archived legacy suites and results (`bench/archive/`) |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`: a server-backed TUI that runs the same agent on the current working tree |

The agent is a model in a loop with a **persistent Python kernel**. The action space is 21 typed
tools generated from one registry (`bench/agent/verbs.py`): `guide`, `skill`, `map`, `read`,
`session_diff`, `checks`, `revert_check`, `serve`, `status`, `search`, `recall`, `page`, `outline`,
`edit`, `splice`, `write`, `delete`, `mkdir`, `apply`, `exec`, `git`. `run` stays in the registry as
a python-only helper (never a provider tool); `limits.DEFAULT_TOOLS` is the production scope — every
typed verb except the operational `serve`, plus `code`/`delegate`/`docs`/`plan`/`task` — and
`CODE_TUI_ALLOWED_TOOLS` replaces that set (the only way to grant `serve`). `code` is the
orchestration cell (a python cell; `python` is its alias), not a verb. `run()` is present in
`repl`/`bare` modes and absent from typed modes.

No root `package.json`, no `pyproject.toml`, no CI. The only install step is `bun install` in
`tui/`.

## 2. Load-bearing properties

These are architecture, not style. A change that breaks one is a regression even if tests pass.

- **Self-describing surface.** Every source module opens with WHAT IT IS / ENTRY POINTS /
  INVARIANTS / WHERE TO CHANGE. `bench/agent/headers.py` parses those sections into rows;
  `bench/guide.py` and the in-kernel `guide()` verb both read them. `help(tool)` returns the live
  signature, and the provider schema comes from the same registry entry. An agent must never
  need to read an implementation to learn how to call it. A header is at most 24 lines
  (`headers.MAX_HEADER`, `HEADER_ALLOW` empty), so every in-scope module meets the same budget.
  `bench/smoke.py` checks that every ENTRY POINTS callable resolves to a declared symbol, so a
  header cannot advertise a deleted function.
- **Self-verifying work.** `final_answer` is staged, then rejected until the goal `Verifier`
  plan passes. A failed check is a repair instruction, not a question. There are no interactive
  prompts in production (`tui/python/worker.py` is unattended).
- **One fact, one place.** Adding a verb is exactly: registry entry in `verbs.py` (its
  `summary` is the provider description), function in the owning `kernel/prelude_*.py` fragment
  (the `kernel/prelude.py` header names the five) or `kernel/run_prelude.py`, and a
  `Kernel.call` case in `bench/smoke_checks/`. Schemas and `help()` are generated from the
  same registry. The system rules carry agent policy (how to work, verify and finish), not a
  second tool catalog; never add a tool table to a prompt or a README.
- **Machine-readable failure.** Unknown tools, bad arguments (`kind: "bad_args"`,
  `error.code: "bad_arguments"`, plus the signature hint), refused mutations (`wrong_call`) and
  check failures return structured dicts (`kind`, `error`, `use`, `_OBS[n]`). Do not replace a
  structured answer with prose only a human can parse.
- **Legibility is enforced, not requested.** Four cheap offline gates: `bench/guide.py --check`
  for the module header, `ruff.toml` (E/F/W/B, blank-line convention included) for the code,
  a one-line contract docstring on every public function and class, and
  `bench/smoke_checks/docs.py`, which resolves every repository path these documents name (a
  rename cannot leave a dead pointer) and fails when one document copies a paragraph from
  another (a copied fact drifts until one copy lies). A public name without
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
| `docs/README.md` | The trigger→file map for every document, and the one-owner/path rules the docs gate enforces |
| `docs/how-it-works.md` | The design argument: the control loop, what replaced each human-shaped affordance, verification, autonomy, non-goals |
| `docs/how-to.md` | Task recipes: run it, make a task finishable, read a run, the gates, eval cases, adding a verb/plan/case/document, debugging |
| `docs/run.md` | Invocation: gate ladder, expected exit codes, eval/workspace/TUI commands, knobs, gotchas |
| `docs/architecture.md` | Runtime shape: tree kinds, one cell end to end, loops, paging, the check plan and compaction |
| `bench/agent/kernel/` | The action space, split by lifecycle: `protocol.py` (PROTOCOL + Cell wire shapes), `driver.py` (child loop), `prelude.py` (assembled PRELUDE over five fragment assemblers), `run_prelude.py` (assembled RUN_PRELUDE over the `run_prelude_*.py` fragments: exec/git/serve + the mutation and spawn gates), `process.py` (`Kernel`: spawn, supervise, restart, reap) |
| `bench/agent/verbs.py` | The typed verb registry: name, schema, summary, `mutates`, implementing function. `tools.py` builds provider specs from it; the child receives it as `KERNEL_VERBS` |
| `bench/agent/tools.py` | `TYPE_TOOLS`, generated from `verbs.VERBS`; `code` is the orchestration cell in `tool_loop.py`, `python` its alias |
| `bench/agent/headers.py` | The header index (`index`, `guide`, `problems`). Shipped as source to every kernel child, so the CLI and the verb run the same code |
| `bench/index.py` | The generated symbol index: `build(root)` plus the `--write`/`--check`/`--json` CLI for `agents.index.json` |
| `bench/agent/state_loop.py` | The production loop: tool call → kernel cell → typed observation or state card, context compaction |
| `bench/agent/envelope.py` | The one typed observation shape: `ok/op/kind/data/page/handle/notes/error`; `page` is the only paging contract |
| `bench/agent/tool_loop.py` | The turn machine: `final_answer`, goal checks, stop reasons, call pairing, the unattended-ask gate |
| `bench/agent/call_shape.py` | The one pure rewrite before dispatch: generic `exec`/`read _OBS`/`git add`/`edit` shapes to their typed signature, one `[shape]` note |
| `bench/agent/subagent.py` | `delegate`: a read-only child loop with its own context and usage |
| `bench/agent/skeptic.py` | The read-only reviewer that must falsify a finish before it is accepted |
| `bench/agent/custom.py` | Per-run tools from a JSON manifest, shipped to the child as extra verbs and prelude |
| `bench/agent/docs.py` | `docs(url)`: fetch one documentation page, strip it to clean text with a char budget, cache it |
| `bench/agent/check_plan.py` | The check plan and `Verifier`: goal, cheap subset, scope filter, throttle, cache (one command: `check_run.py`) |
| `bench/eval.py` + `bench/evals/` | The eval CLI, the scripted loop contracts, the live failure suite, the oracles and the append-only result rows |
| `bench/smoke.py` + `bench/smoke_checks/` | The offline contract check; one group per area |
| `bench/HARD.md` | Historical evidence for the envelope, with its archived suite and traces |
| `tui/python/worker.py` | The seam between the TUI and `bench`: builds the loop, streams `@@TUI@@` JSON lines to `server.ts` |
| `tui/README.md` | The TUI wire contract: state root, HTTP/WS API, events, statuses, env vars |

## 4. Invariants

1. **A finish is staged, then accepted.** `final_answer(v)` always holds the answer (`STAGED`).
   The next reply with no tool call accepts it after the goal check (when one exists). For a
   silent stage that reply's content wins over `v`, so a short final value cannot erase the
   report written next. An accepted finish ends the turn: the remaining calls in the same
   message do not run, but each still gets a paired (skipped) tool result. With a goal check no
   answer is accepted until it passes. A loop eval grades `result.final`; a live eval grades
   its workspace oracle; a prose reply with no tool call is the summary.
2. **`exec` is the one process tool; only mutation is gated.** `exec(argv)` runs a program with
   `cwd`, `env`, `stdin` and `commands=[[...]]`; `exec(shell=<string>)` is the explicit shell
   escape hatch. `bash -lc` inside argv is refused (`shell_via_exec`) and points at `shell`.
   `run()` exists only in python-only modes. The typed call is bound to the verb's real
   signature before dispatch and its declared scalar types (integer, number, boolean,
   array-of-string) are coerced from the registry schema, so a wrong/missing keyword or a bad
   scalar is `bad_args`/`bad_arguments` with the signature and the offending keyword
   (`help(tool)` prints it), never a traceback. A verb that raises is caught per typed call and
   answered `ok:false`/`internal_error` (`kind: tool_crashed`). A typed verb returning
   `ok: false` is `Cell.rejected` and counts as an error, so a bad argument shape is visible in
   run metrics.
   Programs/verbs that change tracked files (`tee`, `cp`, `mv`, `rm`, `sed -i`,
   `git apply/checkout/...`, `>` redirection; a quoted `>` is an argument) are refused as
   `kind: "wrong_call"` with a `use` hint, because they bypass the journal. A program that
   merely *repeats* a typed verb (`grep`, `cat`, `ls`, `find`, `sed -i`) is never refused: it
   runs and the result carries one `note` naming the typed call (`_VERB_DUPE`), silent under a
   flag the verb cannot express (`grep -o`, `find -exec`), one note per program per cell. `git
   add/commit/push/fetch/init/clone` are allowed, as is mutation of a scratch path (`/tmp`,
   `/var/tmp`, `/dev/shm`, `$TMPDIR`) **by a program**: the scratch exemption belongs to the
   process gate, because that output is not journalled. The typed editors (`write`, `edit`,
   `splice`, `delete`, `mkdir`, `apply`) are workspace-only and answer `outside_root` with a
   `use` that says so; a scratch file inside the workspace (`bench/.tmp/`) is fine. `read` is the
   one typed verb that accepts an absolute scratch path (`/tmp`, `/var/tmp`, `/dev/shm`,
   `$TMPDIR`), so output a program wrote there can be read back; the typed writers stay
   workspace-only. Direct
   spawning (`subprocess.Popen`, `os.system`, `os.exec*`, `os.posix_spawn`, `os.spawn`) runs
   through the same classifier (`_audit_spawn`).
   A program can still write files: the journal is a typed-helper contract, not a sandbox. Keep
   `_mutating_command`, `_HINT`, `_VERB_DUPE` and `RUN_NOTE` honest.
3. **`write()` replaces** and refuses ANY shorter body without `force=True` (a body that is
   a prefix of the old file is only the clearest case); a body whose only line breaks are a
   literal backslash-n comes back with a note. **`edit(edits=[{path, old, new}, ...])`** is
   the typed batch form (the python `edit(path, old, new)` helper remains); the list is
   atomic — all old strings must match, or nothing changes, and a miss returns the enclosing
   `outline()` definition plus `closest` lines. **`delete(path)`** and **`mkdir(path)`** cover
   the shell mutations that would bypass the journal. **`splice(path, start, end, text)`**
   replaces 1-based inclusive lines and `end` is required: `end=start-1` inserts, `text=''`
   deletes. **`apply(<unified diff>)`** writes several files in one cell. All go through
   `_commit`, which is what makes the journal honest; `commit()` is a python helper.
4. **A repeated read is a pointer, not a re-delivery.** `read` tracks delivered ranges and the
   file digest; a contained range again returns `already_read` with the `_OBS[n]` handle.
   `read(..., repeat=True)` re-delivers. `_commit` invalidates the entry; an external write is
   caught by the digest. This is context hygiene, not a read cap. A typed result is normalized
   into `Cell.envelope` and rendered verbatim; only python/exec output is digested. Structured
   `[data]` is never dropped by a size gate: every field is rendered, a large value is clipped
   to about ten lines, and one `[data] clipped: <keys>` note names what was cut, so
   `closest`/`context`, `dirs_matched`, `argv`/`results` and error `signature`/`valid` reach the
   model. One paging
   contract: `offset` is the start of what this response delivered, `next` is the resume
   cursor, `has_more` means the file/result continues, `truncated` means a built-in default
   limit cut output the caller did not ask to have cut, and `complete` is exactly
   `not truncated` — an explicit window (`end`, `max_files`, `max_entries`, `max_results`,
   `max_chars`) is complete with `has_more` true. `read`'s window arguments are `start`/`end`,
   the same names the schema exposes, and a non-integer or `start > end` is `bad_arguments` at
   the boundary. A missing path is `ok:false`/`not_found`, never `ok:true`/`miss`.
5. **Map and search skip generated trees.** `map`/`search` use `_walk`, which applies root
   `.gitignore` patterns plus `_SKIP_DIRS`; `bench/ws`, `.tmp`, `.cache` and `node_modules` do
   not pollute a repo map. `search` returns exact line text (no 200-char clip), merges
   overlapping context so each line appears once, and keeps hit text in stdout while the result
   carries locators. Changing `.gitignore` resets the pattern cache.
6. **The oracle never trusts the visible test.** `bench/evals/oracle.py` runs the workspace's
   own code and is the only verdict; a green visible suite is fixture evidence, not a pass. The
   archived edge grader additionally hashed `test_core.py`/`test_calc2.py` so repairing a test
   scored zero. The hard oracle lives outside the workspace
   (`bench/archive/legacy/hard_hidden/`) and cannot be read or edited by the agent.
7. **The check plan is the goal, and one `Verifier` owns it.** `run_plan`/`run_specs` run the
   ordered plan and return the first failure; a spec's `scope` prefixes filter the *cheap*
   health run to the paths a cell changed (the goal still runs every spec). The `Verifier` owns
   the goal, the cheap subset, the throttle, the cache and rendering: the cheap subset re-runs
   after a mutating cell (at most every 20s) and appears as the `[check]` line of the state
   card. `final_answer` is rejected (`GOAL_UNMET`) until the whole plan passes. Production
   passes `goal_stops=False`, so a failing check never ends the run by itself; a green `checks`
   closes the verification tail (`GREEN`): further `exec` and `checks` are refused while code
   cells and mutations stay open, and a mutation invalidates the pass.
8. **Eval ground truth is data, not prose.** `bench/evals/fixtures.py` builds the input;
   `bench/evals/oracle.py` owns the hidden answer. A fixture never contains its own oracle,
   and an oracle never reads a visible test. The archived legacy suite and its fixtures live
   under `bench/archive/legacy/`; `bench/archive/results/` keeps the recorded traces and
   workspaces. Do not revive the archived suites as the primary eval.
9. **`bench/evals/results/`, `bench/archive/results/traces/` and
   `bench/archive/results/ws/` are append-only evidence.** Add runs; never rewrite a recorded
   row or journal. `bench/live.py` still creates `bench/ws/runs/<tag>`; treat that as generated
   output and do not expect it in a fresh clone.
10. **Generated, not source:** `bench/.tmp/`, `bench/.cache/`, `bench/evals/results/`,
    `bench/archive/results/`, `tui/node_modules/`, and the TUI state root
    (`~/.local/state/code-tui/` by default).
11. **`bench/eval.py` has two import blocks** — script mode (`python3 bench/eval.py`) and package
    mode (`python3 -m bench.eval`). Keep both working.
12. **Every module explains itself, and something checks.** A source module carries all four
    header sections; `bench/agent/headers.py` parses them and `python3 bench/guide.py --check`
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
14. **Fire-and-forget is the contract.** A production run is unattended: no human answers a
    question, reads the code, or repairs the same session. The loop rejects a finish that asks
    a human (`NEEDS_INPUT`), the `Verifier` is the acceptance authority, and the evals grade
    the workspace's observable behavior, never the agent's prose. `config.AUTONOMY` owns the
    gate; `worker.AUTONOMOUS` owns the wording.

## 5. Verification gates

`docs/run.md` §1 owns the ladder: the command order, each expected exit code, what it proves and
which knob changes a run. Run it before a commit. A failure is a contract change, not a flaky
test: fix the code or fix the document in the same change.

The offline pass is the regression gate. `--live` needs network and a key; without a key, stop at
the offline gates and say so in the report.

## 6. Change recipes

| Change | Touch | Verify |
|---|---|---|
| A typed action | `verbs.py` (entry and provider `summary`), the owning `kernel/prelude_*.py` fragment or `kernel/run_prelude.py` (function), `bench/smoke_checks/verbs.py` (a `Kernel.call` case) | smoke |
| A process/git gate | `kernel/run_prelude.py` (`_mutating_command`, `_HINT`, `RUN_NOTE`) | `smoke_checks/gates.py` |
| Observation shape/paging | `envelope.py`, `state_loop._observe`, `repl.observation` | smoke envelopes + a run |
| Finish protocol or stop reason | `tool_loop.ToolLoop.run` (a reply dialect overrides `_reply`, see `repl.py`) | `smoke_checks/turns.py` |
| A check command / plan | `check_run.py` (one command), `check_plan.py` (`CheckSpec.parse`, `Verifier`), `tui/src/detect-checks.ts` | smoke plans + `bun test` |
| A loop failure case | `bench/evals/loops.py` (scripted) or `live.py` (model), then `runner.REGISTRY` | `bench/eval.py --tasks <id>` |
| Eval input or answer | `fixtures.py` (input), `oracle.py` (answer), never both | the case's row |
| A delegated capability | `subagent.py` / `skeptic.py` / `custom.py`, then `config.build` and the worker env flags | `bench/eval.py`, a live run |
| Model/temperature/cache | `llm.Client`, TUI env | a live run |
| TUI board/log/keys/clipboard | `tui/src/client.tsx` over `client-*.ts(x)`, `board-view.tsx`/`board-task.tsx`, `log-view.tsx`/`log-rows.tsx`, `theme.ts`, `list-model.ts`, `clipboard.ts` | `bun test` |
| TUI queue/journal/API | `tui/src/server.ts` over `server-*.ts`, `tui/src/types.ts` | `bun test`, `bench/live.py` |

Adding a module means adding its four-section header before its first line of code, a one-line
contract docstring on every public function and class, then `python3 bench/guide.py --check` and
`python3 -m ruff check .`. Name it so a `search` for the concept finds one family of files
(`check_run.py` / `check_plan.py`), never a name one character away from an existing module.
Adding a kernel helper means also adding its `verbs.py` entry and `summary` (the provider
description); `TOOL_RULES` / `STATE_RULES` stay policy, not a catalog. If it replaces a mutating
shell verb, add it to `_RUN_SHELL_MUTATORS` / `_RUN_GIT_MUTATORS` and `_HINT` too.

## 7. Working rules

- State the contract, the entry point and the failure mode. Delete tutorials.
- Every public function, method, class and exported TS symbol carries a one-line docstring
  (Python) or JSDoc line (TS): that line is the contract an agent reads through `inspect`,
  `help(tool)`, `outline` and editor hover. It states what the thing does, not how. A private
  helper gets one when its name is not obvious. Python is gated by `smoke_checks/style.py`;
  TS conventions are not gated, so match the file you are editing.
- Prefer one call that does the job over a pipeline of calls; prefer a typed tool over
  `exec(shell=...)`; prefer deleting a duplicate catalog over syncing two copies.
- One fact, one owner. `AGENTS.md` owns invariants, recipes and failure modes, `README.md` owns
  identity, the eval registry and status, `docs/how-it-works.md` owns the design argument,
  `docs/how-to.md` owns the task recipes, `docs/run.md` owns invocation, `docs/architecture.md` owns
  the runtime shape, `docs/README.md` owns the document map, `tui/README.md` owns the wire contract,
  `HARD.md` owns the evidence, a module header owns that module. Point at the owner instead of
  restating it: `bench/smoke_checks/docs.py` fails on a paragraph that appears in two documents.
- A doc claim must be executable or checkable. If you cannot verify it, cut it.
- Commit style: `area(scope): lowercase summary`, e.g.
  `bench(agent/kernel): add grep, write, edit`.
- Commit and push only after the gates in `docs/run.md` §1 pass. Report what changed, what you
  verified, and what is still unverified.

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
