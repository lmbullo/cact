# docs/run.md — every command, its expected result, and the fact it proves

This file owns invocation: the gate order, each command's expected exit code and what it proves,
and the knobs that change a run. Task recipes are `how-to.md`; the runtime shape those gates
protect is `architecture.md`; the TUI's wire contract and env table are `../tui/README.md`; the
source change behind a failure is `../AGENTS.md`.

## 1. Gate order

Run in this order. A failure is a contract change, not a flaky test: fix the code or the document
in the same change. Inside an agent run the same plan is `checks()`; its rows are the verdict.

| Command | Covers | Expected |
|---|---|---|
| `make -C rust fmt` | `cargo fmt --all --check` over the engine | exit 0 |
| `make -C rust check` | `cargo clippy --all-targets -- -D warnings`, `cargo test`, the 19-case selfcheck | exit 0, `19/19 cases passed` |
| `cd tui && bun run typecheck` | TS types through `tsc --noEmit` | exit 0 |
| `cd tui && bun test` | the TUI tests: board, log, prompt, views, selection, server, detection | 0 fail; React `act(...)` warnings are expected noise |

`make -C rust check` is `lint test selfcheck` in one call, and it needs no key and no network: the
selfcheck creates one scratch tree per case under the temp directory. `make -C rust selfcheck`
runs only that part, and `rust/target/debug/cact selfcheck` is the same binary without `make`.

The TUI gate is `bun test` with 0 fail, not a fixed count.

## 2. Build

```bash
make -C rust dev                    # debug binary, opt-level 1, no debuginfo
make -C rust build                  # release binary at rust/target/release/cact
make -C rust install                # the release binary into ~/.local/bin/cact
JOBS=2 make -C rust build           # when the machine is busy; JOBS=8 when it is idle
```

`rust/.cargo/config.toml` caps `jobs` at 4 and `rust/Cargo.toml` sets `debug = 0`, plus
`lto = "thin"` and `opt-level = 2` for release. There is no debuginfo to write, so a clean release
build is short and stays under ~1.5GB peak. The build table is `../rust/README.md`.

## 3. One task, no TUI

```bash
# one headless task: prints the state cards and the final report
rust/target/release/cact run --workspace /path/to/repo --prompt 'Add fib.py and a test' \
    --model deepseek-flash --checks '[{"name":"test","cmd":"python3 -m pytest -q"}]'

# the process the TUI spawns: same engine, `@@TUI@@` lines on stdout
rust/target/release/cact worker --workspace /path/to/repo --prompt-file /tmp/prompt.txt \
    --model deepseek-flash --effort low --max-steps 300 --max-wall 1800 \
    --checks '[{"name":"test","cmd":"python3 -m pytest -q"}]'

rust/target/release/cact selfcheck  # the offline contract gate
rust/target/release/cact version    # the crate version
```

`cact run` needs `--key` or `DEEPSEEK_API_KEY`. Its defaults are `--model deepseek-flash`,
`--effort low` (`--effort none` omits the field), `--max-steps 300` (`0` means unlimited),
`--max-wall 1800`, `--cell-timeout 120`, `--max-output 32000` and `--allow-run 1`. The TUI passes
the same flags with its own `CODE_TUI_*` defaults (`--max-output` is `393216` there). `--allow-run
0` removes `exec` and `git` from the scope. An unknown subcommand, a missing key or an empty
prompt exits 2 before any kernel is spawned. The worker's exit code is the verdict: 0 only for a
`done` run.

## 4. Live

```bash
export DEEPSEEK_API_KEY=...
make -C rust live                   # = rust/scripts/live.sh
```

`rust/scripts/live.sh` initialises a throwaway git repository, writes a prompt that asks for
`fib.py` and pytest tests, and runs `cact run --max-steps 20 --max-wall 600` with
`python3 -m pytest -q` as the plan. It prints `live: ok (exit <code>, fib.py present, pytest
green)` only when the file exists and the suite passes. This is the one gate that costs money;
without a key, stop at the offline gates and say so.

## 5. TUI

```bash
cd tui && bun install               # once
cd tui && bun run typecheck
cd tui && bun test
./bin/code                          # inside the target project; starts or reuses its server
./bin/code-stop                     # SIGTERM the server for this directory
```

There is no root `package.json`, no `pyproject.toml` and no CI: `bun install` in `tui/` and
`make -C rust build` are the install steps. The server resolves its worker once at boot
(`tui/src/server-config.ts::WORKER_CMD`): `CODE_TUI_WORKER` if set, else
`rust/target/release/cact worker` or `rust/target/debug/cact worker`. There is no Python
fallback, so build the engine before starting the TUI.

## 6. Knobs

| Variable | Default | What it changes |
|---|---|---|
| `DEEPSEEK_API_KEY` | unset | the model key; `--key` is the `run` alternative, and a missing key exits 2 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | the chat-completions endpoint |
| `CODE_TUI_MODEL` / `CODE_TUI_EFFORT` | `deepseek-flash` / `low` | what the server passes the worker |
| `CODE_TUI_ALLOWED_TOOLS` | unset | a comma-separated tool list that narrows the scope |
| `CODE_TUI_ALLOW_RUN` | `1` | `0` drops `exec` and `git` from the scope |
| `CODE_TUI_STATE` | unset | the worker's state directory, else `$XDG_STATE_HOME/code-tui/<hash>` |
| `CODE_TUI_CARD_BUDGET` | `5000` | chars of state card one turn may deliver |
| `CODE_TUI_MAX_TOTAL_TOKENS` | `30000000` | the run's total token ceiling |
| `PYTHON` | `python3` | the interpreter the kernel child runs |
| `BENCH_LLM_CACHE` | `<state>/.cache/llm` | the on-disk reply cache |
| `CODE_TUI_CHECK` / `CODE_TUI_CHECKS` | unset | the plan override: one command, or the JSON list |
| `CODE_TUI_WORKER` | unset | the worker argv override: a test and sandbox hook |

## 7. Gotchas that change a run

- `make -C rust check` does not run `fmt`; `make -C rust fmt` is its own gate.
- A live run's plan is the verdict. The model's prose is never the pass condition.
- The TUI server detects the check plan when a task starts, so a script an earlier task added to
  `package.json` is picked up without a restart.
- Commit style: `area(scope): lowercase summary`, e.g. `rust(engine): pair every tool call` or
  `tui(tests): add log and prompt tests`.
