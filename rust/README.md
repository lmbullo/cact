# rust: the cact engine

`cact` is the engine: one model in a loop with a typed action space, the state-card observations,
the check plan that gates every finish, and the `@@TUI@@` worker protocol the TypeScript TUI reads.
The TUI spawns this binary through `tui/src/server-config.ts::WORKER_CMD`; there is no Python
worker.

## Layout

| Path | What it is |
|---|---|
| `crates/cact/src/llm.rs` | the chat-completions client: one POST through `curl`, retries, disk reply cache |
| `crates/cact/src/spec.rs` | the system rules, the tool specs, and `Scope` (the capability set) |
| `crates/cact/src/shape.rs` | the pre-dispatch repair for the generic call shapes the model emits |
| `crates/cact/src/tools/` | the typed verbs: read, search, map, outline, guide, skill, mutate, exec, git |
| `crates/cact/src/kernel.rs` | the persistent Python cell child (RPC verbs, timeout, restart) |
| `crates/cact/src/kernel_driver.py` | the child side of that protocol (the only Python we ship) |
| `crates/cact/src/card.rs` | one cell rendered as `[s]`/`[obs]`/`[page]`/`[data]`/`[read]` |
| `crates/cact/src/engine.rs` | the turn machine, the finish protocol, the stop vocabulary |
| `crates/cact/src/checks.rs` | the check plan: parse, run in order, first failing line |
| `crates/cact/src/worker.rs` | the TUI seam: argv, `@@TUI@@` events, status mapping, exit code |
| `crates/cact/src/selfcheck.rs` | the offline gate: 19 cases, no key, no network |

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

# the worker the TUI spawns
target/release/cact worker --workspace /path/to/repo --prompt-file /tmp/prompt.txt \
    --model deepseek-flash --effort low --max-steps 300 --max-wall 1800 \
    --checks '[{"name":"test","cmd":"python3 -m pytest -q"}]'
```

`DEEPSEEK_API_KEY` is required (or `--key` for `run`). `DEEPSEEK_BASE_URL`,
`CODE_TUI_ALLOWED_TOOLS`, `CODE_TUI_STATE`, `BENCH_LLM_CACHE` and `PYTHON` are honoured.

## What is ported

The LLM client and cache, the `fast-code` tool set (`guide`, `skill`, `map`, `read`, `checks`,
`status`, `search`, `page`, `outline`, `edit`, `splice`, `write`, `delete`, `mkdir`, `apply`,
`exec`, `git`, `final_answer`, plus the `python` cell), the persistent Python cell, the state card, the journal,
the check plan and verifier, the green-plan stop, the finish protocol, the worker protocol and
history, and the offline selfcheck.

The Python reference implementation and its evals are gone from every branch. The only Python
here is the persistent cell child (`kernel_driver.py`); it is not the agent loop. The
`plan`/`task`/`session_diff` verbs remain callable inside a cell but are not provider tools in the
`fast-code` profile.

## The Python cell

A cell keeps Python variables between calls, so the interpreter is a child process
(`python3 -u -c <kernel_driver.py>`). The typed verbs stay in Rust: a verb used inside a cell sends
one `rpc` line to the parent, the parent runs the real Rust verb, and the cell receives the same
observation text a typed call would have produced. A timeout kills the child; the next cell
restarts it and the card says `[state] kernel restarted`.
