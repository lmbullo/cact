# rust: the cact engine, ported from `bench/` (Python) to Rust

`cact` is the same agent, in Rust: one model in a loop with a typed action space, the state-card
observations, the check plan that gates every finish, and the same `@@TUI@@` worker protocol the
TUI already reads. The TypeScript TUI is untouched except for the one line that resolves which
worker to spawn (`tui/src/server-config.ts::WORKER_CMD`).

## Layout

| Path | What it is |
|---|---|
| `crates/cact/src/llm.rs` | the chat-completions client: one POST through `curl`, retries, disk reply cache |
| `crates/cact/src/spec.rs` | the system rules, the tool specs, and `Scope` (the capability set) |
| `crates/cact/src/tools/` | the typed verbs: read, search, map, outline, guide, skill, mutate, exec, git |
| `crates/cact/src/kernel.rs` | the persistent Python cell child (RPC verbs, timeout, restart) |
| `crates/cact/src/kernel_driver.py` | the child side of that protocol (the only Python we ship) |
| `crates/cact/src/card.rs` | one cell rendered as `[s]`/`[obs]`/`[page]`/`[data]`/`[read]` |
| `crates/cact/src/engine.rs` | the turn machine, the finish protocol, the stop vocabulary |
| `crates/cact/src/checks.rs` | the check plan: parse, run in order, first failing line |
| `crates/cact/src/worker.rs` | the TUI seam: argv, `@@TUI@@` events, status mapping, exit code |
| `crates/cact/src/selfcheck.rs` | the offline gate: 14 cases, no key, no network |

## Build

```bash
make dev          # debug binary, opt-level 1, no debuginfo, 4 parallel jobs
make build        # release binary at target/release/cact
make check        # fmt + clippy + test + selfcheck
make selfcheck    # ./target/debug/cact selfcheck (the offline contract check)
```

Build memory: `.cargo/config.toml` caps `jobs` at 4 and `Cargo.toml` sets `debug = 0` for dev and
dependencies, plus `lto = "thin"`, `opt-level = 2` for release. `JOBS=2 make build` when the machine
is busy, `JOBS=8` when it is idle. There is no debuginfo to write, so a clean release build is
~20s and stays under ~1.5GB peak.

## Run

```bash
# one task, no TUI protocol
target/release/cact run --workspace /path/to/repo --prompt 'Add fib.py and tests' \
    --model deepseek-flash --checks '[{"name":"test","cmd":"python3 -m pytest -q"}]'

# the worker the TUI spawns (same argv as tui/python/worker.py)
target/release/cact worker --workspace /path/to/repo --prompt-file /tmp/prompt.txt \
    --model deepseek-flash --effort low --max-steps 300 --max-wall 1800 \
    --checks '[{"name":"test","cmd":"python3 -m pytest -q"}]'
```

`DEEPSEEK_API_KEY` is required (or `--key` for `run`). `DEEPSEEK_BASE_URL`,
`CODE_TUI_ALLOWED_TOOLS`, `CODE_TUI_STATE`, `BENCH_LLM_CACHE` and `PYTHON` are honoured.

## What is ported, and what is not

Ported: the LLM client and cache, the tool registry and every verb in the production scope
(`guide`, `skill`, `map`, `read`, `session_diff`, `checks`, `status`, `search`, `page`, `outline`,
`edit`, `splice`, `write`, `delete`, `mkdir`, `apply`, `exec`, `git`, `plan`, `task`, `code`), the
persistent Python cell, the state card, the journal, the check plan and verifier, the finish
protocol, the worker protocol and history, and the offline selfcheck.

Not ported (still Python, under `bench/`): the benchmark/eval harness (`bench/evals`,
`bench/live.py`, `bench/remote.py`, `bench/smoke*`), the Python kernel prelude's extra verbs that
the production scope does not grant (`serve`, `revert_check`, `recall`, `docs`, `delegate`,
`custom`), and the `repl`/`tool` loop variants. The Python tree stays as the reference
implementation and its `bench/smoke.py` gate still runs; `bench/agent/headers.py` skips `rust/`
because it only parses Python/TS headers.

## The Python cell

A cell keeps Python variables between calls, so the interpreter is a child process
(`python3 -u -c <kernel_driver.py>`). The typed verbs stay in Rust: a verb used inside a cell sends
one `rpc` line to the parent, the parent runs the real Rust verb, and the cell receives the same
observation text a typed call would have produced. A timeout kills the child; the next cell
restarts it and the card says `[state] kernel restarted`.
