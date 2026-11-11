## What and why

Describe the change in two or three sentences: the job it does, and the failure it prevents.

Closes #

## Type

- [ ] Bug fix
- [ ] New feature or tool
- [ ] Refactor with no behavior change
- [ ] Documentation or repository metadata only

## Gates

Run the gates that cover the code you touched and paste the result. The offline gates need no
API key and no network.

- [ ] `make -C rust check` (clippy with warnings as errors, `cargo test`, the offline selfcheck)
- [ ] `cd tui && bun run typecheck && bun test`
- [ ] Live check, if the change touches the model loop: `rust/scripts/live.sh` with `DEEPSEEK_API_KEY`
- [ ] Docs updated where behavior or a contract changed, or checked as not needed

## Safety

- [ ] No API keys, tokens, private repository contents, personal paths or generated caches in the diff
- [ ] No new network call or credential requirement in the offline gates
- [ ] A tool that mutates the tree still goes through the journal, and `exec`/`git` stay gated
- [ ] New public functions, types and modules carry a one-line docstring and, for a module, a `//!` header
