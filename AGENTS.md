# AGENTS.md

Orientation for coding agents. The human owner does not read this code; you do. Read this
file first, then call the typed `guide(query)` (or `python3 bench/map.py` outside a cell):
it parses the header of every source module (WHAT IT IS / ENTRY POINTS / INVARIANTS / WHERE
TO CHANGE) and prints which module owns what. `guide(query)` answers "where do I change
this?"; `guide(path)` prints one module whole. The typed `map` tool lists files; it is not
the header index.
`README.md` is the full prose reference; this file is the short version.

## What this repo is

An LLM coding agent in two halves, one language each:

| Path | Language | What it is |
|---|---|---|
| `bench/` | Python 3.10+, stdlib only | The agent itself (`bench/agent/`) plus the benchmark suites, fixtures and recorded traces |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`: a server-backed TUI that runs the same agent on the current working tree |

The agent is a model in a loop with a **persistent Python kernel**. The default action space is a small set of typed tools (guide, map, read, search, recall, outline, edit, splice, write, apply, exec, git, in that registry order) generated from one registry (`bench/agent/verbs.py`); `python` is for computation. `exec` is the one process tool: argv, commands, or an explicit `shell` string; the typed `git` tool covers the common git reads and writes. `run()` is available only in python-only modes (repl/bare). A cell can read, search, map, write, edit and run programs; every mutation is journalled so `changes()`, `diff()` and `undo()` work. `bench/` scores that loop against graded tasks; `tui/` puts a task board in front of it.

No root `package.json`, no `pyproject.toml`, no CI. The only install step is `bun install`
inside `tui/`.

## Agent-first contract

The human owner does not read this code and does not sit in the loop. A task is
fire-and-forget: a prompt goes in, the agent works unattended, and the check plan is the
acceptance signal. That makes these properties part of the architecture, not style:

- **Self-describing surface.** Every source module has the four-section header; `map.py`
  and `guide()` index it; every typed tool carries a JSON schema and a one-line summary from
  `verbs.py`; `help()` returns the live signatures. An agent must never need to read an
  implementation to learn how to call it.
- **Self-verifying work.** `final_answer` is rejected until the `Verifier` plan passes. A
  failed check is a repair instruction, not a question for a human. There are no interactive
  prompts in production (`worker.py` is unattended).
- **Buildable by other agents.** One fact lives in one place. Adding a verb is: registry entry
  in `verbs.py`, function in `kernel.PRELUDE`, smoke call, and a mention in the rules text (the
  schemas and `help()` are generated; the prose rules are hand-written). Do not add a second
  hand-written catalog in a prompt or a README. A new module gets a header before it gets code.
- **Machine-readable failure.** Unknown tools, bad arguments (`kind: "bad_args"`,
  `error.code: "bad_arguments"`, plus the offending signature hint), refused mutations
  (`wrong_call`) and check failures return structured dicts (`kind`, `error`, `use`,
  `_OBS[n]`), not prose that only a human can parse.
- **Design the tool for the job, not a wrapper around a shell.** `exec` takes an argv and a
  `cwd`, not a command string: no quoting, no `cd` prefix, no shell grammar to classify, and
  no reason to build another layer around it. `search` takes `include`/`context` instead of
  reproducing `grep -rn --include ... | head`. When the common path is a typed tool, the
  shell stops being the catch-all. Add the capability to the typed surface first; use
  `exec(shell=...)` (or `run()` in repl/bare) for the cases that genuinely need shell syntax.
- **The bottleneck is reasoning and turns, not typing.** Do not fix an efficiency problem by
  lowering `max_steps`, truncating context, or making the model dumber. Reduce wasted turns
  (self-describing tools, typed results, batching, idempotent reads) and wasted verification
  (the check plan and its `[check]` line). The human loop is already out; the remaining
  bottlenecks are model reasoning, turn count, context size, and tool ergonomics.

The agent is built to be modified by agents: prefer a clean contract over a patch, and
prefer deleting a duplicated catalog over keeping two copies in sync.

## Read these first

| File | Why |
|---|---|
| `bench/agent/kernel.py` | `DRIVER` + `PRELUDE` + `RUN_PRELUDE` implement the action space, and `Kernel` is the subprocess that enforces it; `Kernel.run(code)` and `Kernel.call(tool, args)` share one Cell pipeline |
| `bench/agent/verbs.py` | The typed verb registry: name, JSON schema, summary, mutates, implementing function. `tools.py` builds the provider specs from it; the child gets it as `KERNEL_VERBS` for dispatch and `help()` |
| `bench/agent/tools.py` | `TYPE_TOOLS` generated from `verbs.VERBS`; `python` stays the escape hatch in `tool_loop.py` |
| `bench/agent/headers.py` | The header index: `index()`, `guide()`, `problems()`. Shipped as source to every kernel child, so `python3 bench/map.py` and the `guide()` verb run the same code |
| `bench/agent/state_loop.py` | The loop production actually uses: tool call → kernel cell → state card, output digests, context compaction |
| `bench/harness.py` | The tool-path evals L1-L7 (HTML analysis via map/read/outline, regex search, exec batch, atomic edit batch, explicit shell, batched read, local checksum); each grades the call record as well as the answer |
| `bench/agent/tool_loop.py` | The turn machine those cards plug into: `final_answer`, the goal check plan, the `[check]` health line, stop reasons |
| `bench/run.py` | The benchmark CLI: suites, modes, budgets, traces, the results table |
| `bench/HARD.md` | Why the envelope exists: hard-suite results against `--mode bare` |
| `tui/python/worker.py` | The seam between the TUI and `bench`: builds the loop, streams `@@TUI@@` JSON lines to `server.ts` |

## Rules that must not break

1. **A finish is staged, then accepted.** `final_answer(v)` in a cell that also printed
   holds the answer (`STAGED`); the next reply with no tool call accepts `v` after the goal
   check (when there is one). An accepted finish ends the turn: the remaining calls in the
   same message do not run, but each still gets a paired (skipped) tool result. With a goal
   check, no answer is accepted until the check passes. `run.py` grades `result.final`; a
   prose reply with no tool call is the summary.
2. **`exec` is the one process tool; only mutation is gated.** `exec(argv)` runs a program
   with `cwd`, `env`, `stdin` and `commands=[[...]]`; `exec(shell=<string>)` is the explicit
   shell escape hatch. `bash -lc` inside argv is refused (`shell_via_exec`) and points at
   `shell`. `run()` exists only in python-only modes (repl/bare), not in typed modes. The
   typed call is bound to the verb function's real signature before dispatch, so a wrong or
   missing keyword is a `bad_args`/`bad_arguments` answer (`help(tool)` prints the signature),
   not a traceback. A typed verb returning `ok: false` is a structured rejection
   (`Cell.rejected`) and counts as an error, so a bad argument shape is visible in the run
   metrics rather than hidden behind `ok: true`. Programs/verbs that change tracked files (`tee`, `cp`, `mv`, `rm`,
   `sed -i`, `git apply/checkout/...`, `>` redirection; a quoted `>` is an argument) are
   refused by the same classifier as `kind: "wrong_call"` with a `use` hint, because they
   bypass the journal.
   `git add/commit/push/fetch/init/clone` are allowed, as is mutating a scratch path
   (`/tmp`, `/var/tmp`, `/dev/shm`, `$TMPDIR`), which the journal never covered. Direct
   process spawning (`subprocess.Popen`, `os.system`, `os.exec*`, `os.posix_spawn`,
   `os.spawn`) runs through the same classifier (`_audit_spawn`), so it cannot route around
   the gate; allowed spawns are listed in `Cell.runs`. A program can still write files; the
   journal is a typed-helper contract, not a sandbox. Keep `_mutating_command`, `_HINT` and
   RUN_NOTE honest.
3. **`write()` replaces** (it refuses to silently drop a tail without `force=True`);
   **`edit()`** takes one `path/old/new` or `edits=[{path, old, new}, ...]`; the list form is
   atomic (all old strings must match, or nothing changes); **`splice(path, start, end, text)`**
   replaces 1-based inclusive lines through the same journal (read() numbers lines, so a
   surgical change emits only the changed lines); **`apply(<unified diff>)`** writes several
   files in one cell. All of them go through `_commit`, which is what makes the journal
   honest. `commit()` remains a python helper.
3a. **A repeated read is a pointer, not a re-delivery.** `read` tracks delivered ranges and
   the file digest; asking for a contained range again returns `already_read` with the
   `_OBS[n]` handle. `read(..., repeat=True)` re-delivers it, so the model can always get the
   text back. `_commit` invalidates the file's entry; an external write is caught by the
   digest. This is context hygiene, not a read cap. `has_more` means the file continues after
   `shown`; `truncated` means the default page limits cut the requested output—an explicit
   `start`/`end` before EOF is complete and only has `has_more`.
3b. **Map and search skip generated trees.** `map`/`search` use `_walk`, which applies the
   root `.gitignore` patterns plus `_SKIP_DIRS`; `bench/ws`, `.tmp`, `.cache` and
   `node_modules` do not pollute a repo map. `search` returns exact line text (no 200-char
   clip), merges overlapping context so each line appears once, and keeps hit text in stdout
   while the result carries locators. Changing `.gitignore` resets the pattern cache.
4. **Graders hash the test files** (`edge.py::_suite`). Repairing `test_core.py` or
   `test_calc2.py` scores zero; the source must be repaired. The hard suite's oracle lives
   outside the workspace (`bench/hard_hidden/`), so the agent cannot read or edit it.
5. **The check plan is the goal, and one `Verifier` owns it** (`bench/agent/checks.py`).
   `run_plan` / `run_specs` run the ordered plan and return the first failure; a spec's
   `scope` path prefixes filter the cheap health run to the files a cell changed (the goal
   still runs every spec). The `Verifier` owns the goal, the cheap subset, the throttle, the
   cache and the rendering: the `cheap` subset re-runs after a mutating cell (at most every
   20s) and is printed as the `[check]` line of the state card. `final_answer` is rejected
   (`GOAL_UNMET`) until the whole plan passes. Production passes `goal_stops=False`, so a
   failing check never ends the run by itself — it only rejects the finish.
6. **Ground truth is computed at import time** in `tasks.py` / `edge.py` / `harness.py` /
   `hard.py` from the fixtures. Editing a fixture silently redefines the expected answers;
   `H3_bigcsv`'s CSV is generated and gitignored, and the H1/H2/H4 oracle lives in
   `hard_hidden/`.
7. **`bench/traces/` is append-only evidence.** Add runs; never rewrite a recorded journal.
   `bench/ws/` (frozen workspaces + `runs/` journals) is gitignored measurement output:
   rebuild `tui-base` with `python3 -m bench.ws freeze tui bench/ws/tui-base`, and let
   `bench/live.py` create `runs/<tag>`; do not expect either in a fresh clone.
8. **Generated, not source:** `bench/.tmp/` (per-run workspaces), `bench/.cache/` (cached LLM
   replies), `bench/ws/` (frozen workspaces, rebuilt on demand), `tui/node_modules/`, and the
   TUI state root `~/.local/state/code-tui/`. `bench/edge_fixtures/` and
   `bench/harness_fixtures/` are committed but are rebuilt by `edge.build()` /
   `harness.build()` when missing.
9. **`bench/run.py` has two import blocks** — script mode (`python3 bench/run.py`) and package
   mode (`python3 -m bench.run`). Keep both working.
10. **Every module explains itself, and something checks.** A source module carries all four
    header sections; `bench/agent/headers.py` parses them into data and
    `python3 bench/map.py --check` (also a `bench/smoke.py` check) exits 1 when one is
    missing. `kernel.py` ships `headers.py`'s *source* to the child in `KERNEL_HEADERS` and
    execs it into a private `_HEADERS` namespace, exporting only `guide()`: keep that file
    stdlib-only, free of `__file__`, and read-only, or the verb breaks where `bench` is not
    importable. Do not inline the index into `PRELUDE` again — a shared namespace let its
    `_walk` shadow the prelude's.
11. **`exec`/`run()` children are journalled and reaped.** Each program starts in its own session;
    the child writes its group leader to `KERNEL_ORPHAN_JOURNAL`, and `Kernel.stop()` /
    `_restart()` kill those groups. Removing the journal or the reap leaks processes when a
    cell times out or the kernel dies. `recall(pattern)` is the session-side search over
    `_OBS`; `search` (python `grep()`) is the tree-side search. `_OBS` is persisted to
    `KERNEL_OBS_LOG` and reloaded on restart, so `recall()` must survive a crash; variables do not.

## Verify a change

```bash
python3 bench/map.py --check               # every module header has its four sections
python3 bench/map.py                       # the map: module -> what it is
python3 bench/smoke.py                     # offline contract check: imports, kernel prelude,
                                           # run() allow + refusal and the spawn gate,
                                           # apply/commit, recall across a restart,
                                           # envelopes, reply parsing, check parsing,
                                           # the header index and the guide() verb
python3 -m compileall -q bench tui/python  # syntax
cd tui && bun run typecheck && bun test    # TS: 13 tests, 6 files
```

`bench/smoke.py` is the fast signal: no key, no network, nothing written outside a temp dir.
A failure there means the contract an agent relies on changed (kernel prelude, `Cell` fields,
tool envelopes) — not that a model misbehaved.

The React `act(...)` warnings in `bun test` are expected noise. Python has no unit test
framework: `bench/smoke.py` plus a real benchmark task is the whole story, e.g.

```bash
DEEPSEEK_API_KEY=... python3 bench/run.py --suite edge --tasks E1_tests --mode state --traces
DEEPSEEK_API_KEY=... python3 bench/run.py --suite harness --mode state  # tool-path evals L1-L7
```

which needs network and a key; without one, fall back to the smoke checks above plus reading
the trace format.

## Conventions

- Commit style: `area(scope): lowercase summary`, e.g. `bench(agent/kernel): add grep, write, edit`.
- Prose is for agents: state the contract, the entry point and the failure mode; skip
  tutorials.
- When you add a module, give it the four-section header and run `python3 bench/map.py
  --check`. A header is the module's only introduction: state the contract, the entry point
  and the failure mode, and skip the tutorial.
- When you add a helper to the kernel, add the matching rule text to `tool_loop.TOOL_RULES` /
  `repl.REPL_RULES` / `state_loop.STATE_RULES`, or the model will not know it exists. If it
  replaces a mutating shell verb, add it to `_RUN_SHELL_MUTATORS` / `_RUN_GIT_MUTATORS` and
  `_HINT` in `RUN_PRELUDE` too.
- When you add a check command, give it a name and mark it `cheap` only if it takes seconds;
  the cheap ones run after a mutating cell in their `scope` (at most every 20s).
- When you add an edge task, also add its fixture to `edge.build()`.