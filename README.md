# cact

cact is a coding agent that works on a repository while you are not watching. A model runs in a
loop with a persistent Python kernel and a small typed action space; `code`, a terminal UI, runs
that loop on the directory you started it in and keeps the work going after you close the window.

The agent does not chat, role-play or ask for permission. It reads with bounded observations,
mutates through a journal, verifies with the project's own commands, and ends with a report. That
shape is the project, not an implementation detail: `docs/how-it-works.md` argues for it and
`docs/how-to.md` covers the operations. The rest of this file is what the thing is and how it is
measured.

## 1. Quickstart

Requires Python 3.10+, [bun](https://bun.sh) and a DeepSeek API key.

```bash
cd tui && bun install
export DEEPSEEK_API_KEY=...            # or write it to ~/.config/code-tui/env
/path/to/cact/tui/bin/code             # run this inside the project you want the agent to work on
```

Type the task, press `Ctrl+Enter`, close the terminal whenever you like. `/path/to/cact/tui/bin/code-stop`
stops that directory's server. The agent edits the working tree in place; there is no copy and no
sandbox around it.

There is no root `package.json`, no `pyproject.toml` and no CI. `bun install` in `tui/` is the only
install step, and `bench/` is Python standard library only.

## 2. What runs what

| Path | Language | Role |
|---|---|---|
| `bench/` | Python 3.10+, stdlib only | The agent (`bench/agent/`), the failure-mode evals (`bench/evals/`), archived legacy suites and results (`bench/archive/`) |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`: a server-backed, fire-and-forget TUI that runs the same agent on a working tree |

Both entry points build one loop:

- `tui/bin/code` — per-workspace server, one worker per task (`tui/python/worker.py`), board and
  log views. This is the product.
- `python3 bench/eval.py` — the evals. Offline loop contracts by default; `--live` runs the
  model-backed failure suite.

The action space is 21 typed verbs generated from one registry (`bench/agent/verbs.py`): `guide`,
`skill`, `map`, `read`, `session_diff`, `checks`, `status`, `search`, `recall`, `page`, `outline`,
`edit`, `splice`, `write`, `delete`, `mkdir`, `apply`, `exec`, `git`, plus the operational `serve`.
`exec` is the only process tool and `git` the only VCS tool; both are gated where they could
bypass the edit journal. A python cell (`code`, or `python` in a python-only run) covers
computation and anything the typed tools cannot express.

Runtime shape — trees, one cell end to end, the loops, observation paging, the check plan and
compaction — is `docs/architecture.md`. The commands that prove each property are `docs/run.md` §1.

## 3. Evals and grading

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

Cost is measured the same way, against a fixed task and a recorded baseline: `LOOP.md` has the
step, token and wall-time numbers, and the seven loop changes behind them.

## 4. Gates

The offline gates, runnable without a key or a network:

```bash
python3 bench/guide.py --check     # every module header is complete and within 24 lines
python3 -m ruff check .            # the code contract (ruff.toml)
python3 bench/smoke.py             # the offline contract check, one group per area
python3 bench/eval.py              # the 16 scripted loop contracts
cd tui && bun run typecheck && bun test
```

`docs/run.md` §1 owns the order, each expected exit code and what it proves. Inside a task the
same plan is one call (`checks()`), and a green run is the only thing that counts as a gate.

## 5. Documents

Every document here is written for an agent that will edit the code unattended; humans read the
same files. `docs/README.md` is the trigger map (which file owns which fact) and owns the rules
`bench/smoke_checks/docs.py` enforces: a named path must exist, and no document may copy a
paragraph from another.

| Read this | When |
|---|---|
| `AGENTS.md` | before any edit: precedence, invariants, change recipes, failure modes |
| `docs/how-it-works.md` | the design: why an agent, not a persona |
| `docs/how-to.md` | task recipes: run it, check it, extend it, debug it |
| `docs/run.md` | before running anything: commands, exit codes, knobs |
| `docs/architecture.md` | the runtime shape |
| `tui/README.md` | the TUI wire contract |
| `docs/agent/README.md` | generated module map: subsystem → page → entry points |
| `LOOP.md`, `bench/HARD.md` | the measurements, with their archived suites |

Source contracts are the machine index, not a list in Markdown:

```bash
python3 bench/guide.py                  # module -> what it is (one line each)
python3 bench/guide.py --query <topic>  # which module owns it
python3 bench/guide.py --module <path>  # one header, whole
python3 bench/index.py --write          # regenerate the symbol index (gitignored)
```

## 6. Status and limits

- The live suite, and any real task, need `DEEPSEEK_API_KEY` and network. The offline gates do not.
- The agent trusts the check plan for the definition of done; a project with no runnable checks gets
  a weak verifier, so give it commands (`CODE_TUI_CHECK`, `CODE_TUI_CHECKS`) or a test runner it
  can detect.
- `bench/remote.py` (Daytona sandboxes) is the only optional third-party import and it is lazy.
- No license file yet: treat the code as all rights reserved until one is added.
