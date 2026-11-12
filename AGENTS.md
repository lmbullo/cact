# AGENTS.md — operating manual for coding agents

This branch is the Rust engine. The Python reference implementation and its evals were removed;
they remain on `main`. The source of truth is the code and its `//!` headers, not this file.

Precedence when sources disagree: the module header / the code, then this file, then `README.md`,
`rust/README.md` and `tui/README.md`.

## 0. Fast path

```bash
make -C rust check                  # clippy + cargo test + the 19-case selfcheck (no key)
make -C rust build                  # release binary at rust/target/release/cact
cd tui && bun run typecheck && bun test
```

A live task needs `DEEPSEEK_API_KEY` and network; without a key, stop at the offline gates.

## 1. What this repo is

| Path | Language | What it is |
|---|---|---|
| `rust/` | Rust 1.78+ | The engine: `cact worker` is the process the TUI spawns |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`, the server-backed TUI over a working tree |

`tui/src/server-config.ts::WORKER_CMD` spawns the built Rust binary. There is no Python worker and
no fallback: `make -C rust build` first. The only Python in the engine is the persistent cell child
(`rust/crates/cact/src/kernel_driver.py`), which keeps variables between calls.

## 2. Load-bearing properties

- **One model loop.** `Engine::run` sends the transcript, reads one reply, dispatches every call,
  appends one state card per call, and stops on the finish protocol.
- **Typed action space.** `spec.rs` owns the provider specs and `Scope`; the `fast-code` set is
  the lean typed list plus one cell. `Scope::filter` and dispatch both enforce the grant.
- **State cards, not transcripts.** `card.rs` renders `[s]`/`[obs]`/`[page]`/`[data]`/`[read]`
  and a structured `[error]`. Process output names the `_OBS[n]` handle; typed results page.
- **The journal owns mutation.** `write`/`edit`/`splice`/`delete`/`mkdir`/`apply` go through
  `Ctx::commit`; `exec` refuses a program that would mutate tracked files.
- **The check plan gates a finish.** `checks::Plan` runs the plan in order and the engine rejects
  `final_answer` until it passes. A green `checks` answers `checks` from the cached verdict and
  refuses a gate re-run; a non-gate read-only `exec` stays open until a mutation reopens the plan.
- **Unattended.** No human answers a question. Finish with `final_answer(...)`; the check plan is
  the acceptance authority.
- **Generated, not duplicated.** The tool list, the descriptions and the JSON schemas live in
  `spec.rs`; do not add a second catalog to a prompt or a README.

## 3. Where to change what

| Change | Touch | Verify |
|---|---|---|
| A typed verb | `spec.rs` (schema + description), `tools/` (implementation), `tools::call` dispatch | `make -C rust check` |
| Process/git gate | `tools/exec.rs`, `tools/git.rs`, `contract.rs` | selfcheck |
| A card section or paging line | `card.rs`, `envelope.rs`, `engine.rs::fit_card` | selfcheck |
| Finish/stop behaviour | `engine.rs`, `worker.rs` | `cargo test`, a live run |
| Check plan | `checks.rs`, `tui/src/detect-checks.ts` | `cargo test`, `bun test` |
| TUI queue/journal/API | `tui/src/server.ts` over `server-*.ts`, `tui/src/types.ts` | `bun run typecheck && bun test` |
| TUI board/log/keys | `tui/src/client.tsx` over `client-*.ts(x)`, `board-*.tsx`, `log-*.tsx` | `bun test` |

A module opens with `WHAT IT IS` / `ENTRY POINTS` / `INVARIANTS` / `WHERE TO CHANGE`. Every
public item carries a one-line docstring that states its contract.

## 4. Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `bad_arguments` | keyword does not match the tool signature | read the tool's `[error]` signature; fix the schema or the call |
| `wrong_call` | a process would bypass the journal | use the typed mutation; scratch paths are allowed |
| `outside_root` | a typed editor got an absolute path outside the workspace | use a workspace path, or `/tmp` through `exec` |
| `GOAL_UNMET` | the check plan still fails | repair the source; do not weaken the plan |
| `green_plan` | the plan already passes | finish; a mutation reopens verification |
| kernel `restarted` | timeout or dead child | variables are gone; earlier output survives in `_OBS` |
| no worker / spawn error | Rust binary not built | `make -C rust build` |

## 5. Docs

`README.md` owns the project identity and run steps, `rust/README.md` owns the module map and the
Rust build, `tui/README.md` owns the wire contract. `docs/` and `LOOP.md` are the Python-era
record kept from `main`; they are historical, not a contract for this branch.
