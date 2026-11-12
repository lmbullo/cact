# cact

cact is a coding agent that works on a repository while you are away. A model runs in a loop with
a persistent Python cell and a small typed action space; `code`, a terminal UI, runs that loop on
the directory you start it in and keeps the work going after you close the window.

The engine is Rust (`rust/`, binary `cact`): one build, no runtime dependencies beyond `python3`
for the cell and `git` for the tree. The TUI spawns `cact worker`; there is no Python worker.

It does not chat, role-play or ask for permission. It reads through bounded observations, changes
files through a journal, verifies with the project's own commands and stops with a report. The
module map and the build are `rust/README.md`; the TUI wire contract is `tui/README.md`.

## Run it

Requires Python 3.10+, [bun](https://bun.sh), a Rust toolchain (1.78+) and a DeepSeek API key.

```bash
make -C rust build                     # the engine: target/release/cact (~30s, 4 jobs, no debuginfo)
cd tui && bun install
export DEEPSEEK_API_KEY=...            # or write it to ~/.config/code-tui/env
/path/to/cact/tui/bin/code             # run this inside the project you want the agent to work on
```

Type the task, press `Ctrl+Enter`, and close the terminal whenever you like. The agent edits the
working tree in place; there is no copy and no sandbox. `/path/to/cact/tui/bin/code-stop` stops
that directory's server. There is no root `package.json`, no `pyproject.toml` and no CI:
`bun install` in `tui/` and `make -C rust build` are the install steps.

## The shape

`rust/` is the engine: the loop, the typed verbs, the state cards, the check plan and the
`@@TUI@@` worker the TUI spawns. `tui/` is `code`, the server-backed terminal UI that runs the
engine on a working tree: a per-workspace server and one worker per task own the board and log
views.

The action space is the lean `fast-code` set generated in `rust/crates/cact/src/spec.rs`:
`guide`, `skill`, `map`, `read`, `checks`, `status`, `search`, `page`, `outline`, `edit`, `splice`,
`write`, `delete`, `mkdir`, `apply`, `exec`, `git`, plus the persistent `python` cell. `exec` is
the only process tool and `git` the only VCS tool; both are gated where they could bypass the edit
journal. The cell covers computation and anything the typed tools cannot express.

The turn machine, the finish protocol, the card renderer, the journal and the kernel child are
documented by their `//!` headers under `rust/crates/cact/src/`.

## Gates

The offline gates need no key and no network:

```bash
make -C rust check                 # clippy + cargo test + the 17-case selfcheck
cd tui && bun run typecheck && bun test
```

A live task needs `DEEPSEEK_API_KEY` and network: `make -C rust build` then
`rust/scripts/live.sh`, or run one task with `rust/target/release/cact run`.

## Status and limits

- The agent trusts the check plan for the definition of done. A project with no runnable checks
  gets a weak verifier, so give it commands (`CODE_TUI_CHECK`, `CODE_TUI_CHECKS`) or a test runner
  it can detect.
- The only Python in the engine is the persistent cell child
  (`rust/crates/cact/src/kernel_driver.py`). It is not the agent loop.
- MIT: see `LICENSE`.
