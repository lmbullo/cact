# Contributing

Thanks for looking at cact. It is a small project with a strict contract: one model in a loop, a
typed action space, a check plan that gates every finish, and a terminal UI that keeps the work
going after the window closes. The source of truth is the code and its `//!` module headers, then
`AGENTS.md`, then the README.

## Before you start

- Open an issue for anything larger than a small fix, so the shape is agreed before you write it.
- Read `AGENTS.md` before changing the engine. It states the invariants a diff must keep.
- Never include an API key, token, private repository content or personal path in an issue, a PR or
  a commit.

## Branches

`main` is the default branch and carries the Rust engine in `rust/` plus the TypeScript TUI in
`tui/`. The `rust` branch tracks the same engine and is where engine work is pushed. Base pull
requests on `main`.

## Toolchain

- Rust 1.78+ (`rustup`), a C toolchain, `git`.
- Python 3.10+ for the persistent cell and the offline scripts.
- [bun](https://bun.sh) for the TUI and its tests.

## Build and run

```bash
make -C rust build          # rust/target/release/cact
cd tui && bun install
export DEEPSEEK_API_KEY=... # or write it to ~/.config/code-tui/env; only needed for a live task
tui/bin/code                # run inside the project the agent should work on
```

A live task is the only part that needs a key and the network. Everything below runs offline.

## Gates

A pull request is ready when both offline gates pass on your branch:

```bash
make -C rust check                              # clippy -D warnings, cargo test, the offline selfcheck
cd tui && bun run typecheck && bun test
```

`make -C rust check` is the same set the TUI's `checks()` runs. If your change touches the model
loop, also run one live task: `rust/scripts/live.sh` with `DEEPSEEK_API_KEY` set. Do not weaken a
gate to make a change pass; fix the change or explain the exception in the PR.

## Change rules

- Keep the diff small and focused. Refactors and behavior changes go in separate pull requests.
- Every public function, type and module has a one-line docstring; a module starts with a `//!`
  header that states its invariants and where to change it.
- State cards and the worker protocol are contracts. If you change a card's shape, update
  `tui/README.md` and the module header in the same pull request.
- Mutation goes through the journal. Do not add a path that writes tracked files through `exec`,
  `git` or the Python cell.
- No new dependency without a reason in the PR, and no new network or credential requirement in an
  offline gate.
- Documentation is prose, not marketing: one fact, one owner, and every backticked path must
  exist.

## Pull requests

Fill in the template. A reviewer checks the gate output, the invariants in `AGENTS.md`, and that no
secret or generated artifact rode along. Commits are read; write a subject in the imperative mood
and keep the body about the why.

## Code of conduct

Participation is covered by `CODE_OF_CONDUCT.md`. Report an incident privately to the maintainer
[@lmbullo](https://github.com/lmbullo) on GitHub.

## Security

Do not report a vulnerability in a public issue. Follow `.github/SECURITY.md`.
