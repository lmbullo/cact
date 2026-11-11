# cact

cact is a coding agent that works on a repository while you are away. A model runs in a loop with
a persistent Python kernel and a small typed action space; `code`, a terminal UI, runs that loop
on the directory you start it in and keeps the work going after you close the window.

It does not chat, role-play or ask for permission. It reads through bounded observations, changes
files through a journal, verifies with the project's own commands and stops with a report. The
design argument is `docs/how-it-works.md`; the operations are `docs/how-to.md`.

## Run it

Requires Python 3.10+, [bun](https://bun.sh) and a DeepSeek API key.

```bash
cd tui && bun install
export DEEPSEEK_API_KEY=...            # or write it to ~/.config/code-tui/env
/path/to/cact/tui/bin/code             # run this inside the project you want the agent to work on
```

Type the task, press `Ctrl+Enter`, and close the terminal whenever you like. The agent edits the
working tree in place; there is no copy and no sandbox. `/path/to/cact/tui/bin/code-stop` stops
that directory's server. There is no root `package.json`, no `pyproject.toml` and no CI:
`bun install` in `tui/` is the only install step, and `bench/` is Python standard library only.

## The shape

The repository has two halves. `bench/` is the agent (`bench/agent/`), the failure-mode evals
(`bench/evals/`) and the archived legacy suites (`bench/archive/`), all Python 3.10+ with no
third-party imports except an optional, lazy sandbox client. `tui/` is `code`, the server-backed
terminal UI that runs the same loop on a working tree. A per-workspace server and one worker per
task (`tui/python/worker.py`) own the board and log views; `python3 bench/eval.py` runs the
offline loop contracts, or the model-backed failure suite with `--live`.

The action space is 21 typed verbs generated from one registry (`bench/agent/verbs.py`): `guide`,
`skill`, `map`, `read`, `session_diff`, `checks`, `status`, `search`, `recall`, `page`, `outline`,
`edit`, `splice`, `write`, `delete`, `mkdir`, `apply`, `exec`, `git`, plus the operational `serve`.
`exec` is the only process tool and `git` the only VCS tool; both are gated where they could
bypass the edit journal. A python cell (`code`, or `python` in a python-only run) covers
computation and anything the typed tools cannot express.

The runtime shape — trees, one cell end to end, the loops, observation paging, the check plan and
compaction — is `docs/architecture.md`. The commands that prove each property are in
`docs/run.md` §1.

## Evals and grading

One registry (`bench/evals/runner.py::REGISTRY`) runs two disjoint lists. A case id is stable and
names the mechanism it guards; the `failure` tag is the diagnostic axis of the report.

| Suite | Cases | Grades |
|---|---|---|
| loop (default) | `L01`-`L16` | the loop contract under a scripted model: staged finish, goal rejection, bad arguments, repeated reads, the journal, compaction, custom tools, skills, delegation, the skeptic, the token cap, docs |
| live (`--live`) | `A01`-`A10`, 14 cases | workspace behavior against a hidden oracle: the contract behind a green visible test, five latent bugs, a fact across compaction, an on-demand skill, a skeptical finish, delegation, a repair, the real worker path, docs grounding, the project's own lint |

A loop case passes only when the stop, the observation and the oracle agree; a live case passes
only when its oracle passes. The model's prose is never the verdict. Fixtures are built per case
in a temp workspace (`bench/evals/fixtures.py`), and the answers live outside it
(`bench/evals/oracle.py`). The failure tags are `finish`, `tooling`, `mutation`, `context`,
`verification`, `cost` and `autonomy`. Cost is measured against a fixed task and a recorded
baseline; `LOOP.md` has the step, token and wall-time numbers and the seven loop changes behind
them.

## Gates

The offline gates need no key and no network:

```bash
python3 bench/guide.py --check     # every module header is complete and within 24 lines
python3 -m ruff check .            # the code contract (ruff.toml)
python3 bench/smoke.py             # the offline contract check, one group per area
python3 bench/eval.py              # the 16 scripted loop contracts
cd tui && bun run typecheck && bun test
```

`docs/run.md` §1 owns the order, each expected exit code and what it proves. Inside a task the
same plan is one call (`checks()`), and only a green run counts as a gate.

## Documents

The documents are written for an agent that will edit the code unattended; humans read the same
files. `docs/README.md` is the trigger map, naming the file that owns each fact, and owns the
rules `bench/smoke_checks/docs.py` enforces: a named path must exist and no document may copy a
paragraph from another. `AGENTS.md` is the operating contract to read before any edit;
`docs/agent/README.md` is the generated module map; `LOOP.md` and `bench/HARD.md` hold the
measurements.

Source contracts are the machine index, not a list in Markdown:

```bash
python3 bench/guide.py                  # module -> what it is (one line each)
python3 bench/guide.py --query <topic>  # which module owns it
python3 bench/guide.py --module <path>  # one header, whole
python3 bench/index.py --write          # regenerate the symbol index (gitignored)
```

## Status and limits

- The live suite, and any real task, need `DEEPSEEK_API_KEY` and network. The offline gates do not.
- The agent trusts the check plan for the definition of done. A project with no runnable checks
  gets a weak verifier, so give it commands (`CODE_TUI_CHECK`, `CODE_TUI_CHECKS`) or a test runner
  it can detect.
- `bench/remote.py` (Daytona sandboxes) is the only optional third-party import and it is lazy.
- MIT: see `LICENSE`.
