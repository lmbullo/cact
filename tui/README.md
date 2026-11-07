# tui — wire contract for coding agents

You are the reader. `code` is a server-backed, fire-and-forget coding-agent TUI over the agent
in `../bench` (the `state` loop by default; `CODE_TUI_MODE` switches it). This file is the
reference for an agent that has to change or run it. The short contract is `../AGENTS.md`; the
repository map is `../docs/README.md`, and what runs where is `../README.md`.

Read the source headers first:

```bash
python3 ../bench/guide.py --query tui
python3 ../bench/guide.py --check     # fails when a module header loses a section
```

The only files with no header are the two shell entry points, `bin/code` and `bin/code-stop`: they
hold no logic, so `src/` and `python/` are the whole surface.

Two contract checks live outside this directory: `python3 ../bench/smoke.py` (agent/kernel
interface, no key) and `python3 ../bench/live.py` (one real end-to-end task).

## 1. Process model

- `bin/code` → `src/launcher.ts`.
- `launcher.ts` hashes the launch directory, takes a `launch.lock`, finds or starts a detached
  `src/server.ts` for that workspace (logging to `server.log`), waits for `/health` to answer
  for this workspace, then imports and mounts `src/client.tsx`.
- `server.ts` owns the queue, concurrency, cancellation, task JSON and event journals. It serves
  HTTP plus a WebSocket and skips the worker when no key is set.
- `python/worker.py` builds the `bench` loop with `config.build(...)` over a `Kernel` with
  `prelude="all"`, and streams each code cell, turn and observation to the server as
  `@@TUI@@`-prefixed JSON lines. It also prepends a short history of previous tasks in the same
  workspace (files they changed, one line each) as `[workspace history]`.
- `list-model.ts` is the pure ordering/row/scroll model.
- `clipboard.ts` owns the one write to the user's clipboard: host clipboard first, OSC 52
  fallback; a remote terminal uses OSC 52 only.
- The server broadcasts task updates and events over WebSocket; the TUI keeps only in-memory
  state and reconnects every second on close. There is no TUI-side database.

Invariants:

- The server outlives the TUI. Closing `code` must not stop running tasks.
- One server per workspace (sha256 of the resolved directory). Different directories are
  different boards.
- The task board is append-only history: a task id is generated once and never reused.
- `client.tsx` holds no persistent state; the server is the source of truth.

## 2. State root

`$CODE_TUI_STATE_ROOT` when set, else `$XDG_STATE_HOME/code-tui`, else
`~/.local/state/code-tui`:

```
workspaces/<sha256-of-workspace, first 16 hex>/
  server.json          pid, port, and the rest of ServerInfo
  server.log           detached server log
  launch.lock/         held while a launcher is starting the server
  tasks/<id>.json      task metadata
  events/<id>.jsonl    append-only event journal
  prompts/<id>.txt     original prompt
  history.jsonl        last 50 tasks (prompt, summary, changed files); newest 4 are
                       prepended to the next state-mode task
```

The workspace is the directory where `code` ran, resolved with `path.resolve`; the server
additionally `chdir`s into it.

## 3. Board contract

One row per task: id, state as a single lowercase word, live `steps · tokens` counters when it
has them, and the prompt. Counters come from turn events (`Task.progress`), so a running row
moves before the final answer; a finished row falls back to the final `steps`/`usage` counters.
Row id is `t-<base36 timestamp>-<5 hex>`.

```
  ─ running (1)
  › t-m3k9a1x2-4f7c1 running  3 steps · 4210 tok  add a retry to the fetch helper ←
  ─ queue (2)
    t-m3k9b7c4-1a9de queue    write tests for the parser
  ─ done (1)
    t-m3k9f5g6-0c4aa done     rename the config loader
────────────────────────────────────────────────────────────────
  › task for /home/you/some/project…
────────────────────────────────────────────────────────────────
```

Grouping order: `running`, `queued`, `done`, `partial`, `failed`, `cancelled`. The `queued`
state renders as the shorter word `queue` in headers and rows.

| State | Section header | Row word | Colour |
|---|---|---|---|
| `running` | `─ running (n)` | `running` | green |
| `queued` | `─ queue (n)` | `queue` | yellow |
| `done` | `─ done (n)` | `done` | dim |
| `partial` | `─ partial (n)` | `partial` | yellow |
| `failed` | `─ failed (n)` | `failed` | red |
| `cancelled` | `─ cancelled (n)` | `cancelled` | faint |

Selected row: `›` left, `←` right. Mouse click selects (does not open) and never copies.
Left-drag selects rendered text; releasing the button copies the selection to the clipboard
and clears the highlight in the same gesture. `Up`/`Down` move the highlight while the prompt
fits one row; once the prompt wraps or holds a typed newline they move the text cursor. The
prompt box grows one row per newline or wrapped row, up to five, then
scrolls vertically and the bottom rule shows the cursor position (`3/5`). `Ctrl+Enter` queues;
`Escape` clears the prompt, then the highlight; `Enter` on an empty prompt opens the highlighted
task. The rule above the prompt reports `reconnecting`, the last toast, or `no api key`, in that
priority. A task that hits its step or wall cap but still produces a summary is `partial`, not
`failed`; its final text stays in the log.

## 4. Live run log

`Enter` on an empty prompt opens the selected task's log: one row per tool call, plus one `↺`
row when the loop rejected or staged a finish.

```
  ● running  add a retry to the fetch helper   3 steps · 4210 tok · esc/enter: back · ctrl-k cancel · 12 events
  ▸ read({"path":"src/client.tsx"})
  ▸ exec({"argv":["bun","test"]})
  ▸ 'src/client.tsx'
  ↺ [finish] final_answer rejected: the project check still fails
  ● done stop=DONE · 7 steps · 3 cells · errors 0 · 18432ms · 4210 tok  = all tests pass
```

A row is one call, trimmed to one line and clipped to the terminal width with a trailing `…`.
A typed call keeps its tool name and shows the JSON arguments: `read({"path":"a.py"})`,
`exec({"argv":["bun","test"]})`. A python cell shows the arguments of its first call, so
`read('a.py')` reads as `▸ 'a.py'`; the cell index and counters are dropped and only the first
line of a multi-line cell is kept. Calls render in gruvbox purple (`#d3869b`).

Turn events (the observation after each call, including stdout/stderr) are not rendered, with
one exception: a turn carrying a `notice` (a rejected or staged `final_answer`, recorded by
`bench/agent/tool_loop.py`) prints its first line and a `(+NL)` count — that is how the reason a
finish did not take becomes visible. The full payload is still journalled and reaches the model.
The log keeps up to 1000 events in memory, seeded from the journal (last 800) on open.

`esc` (empty prompt) or `enter` (empty prompt) goes back; `Ctrl-K` cancels from either screen.
There is only one prompt box, mounted once below whichever view is showing, so switching views
keeps the same textarea, text, cursor, row count and focus. It has the same width, growth and
keys on both screens, and a prompt can be typed and queued while a run streams.

## 5. Keys

| Key | Action |
|---|---|
| `Up` / `Down` | Move the task highlight (or the prompt cursor once the prompt wraps) |
| `Enter` | Insert a newline in the prompt; open the highlighted task when the prompt is empty |
| `Ctrl+Enter` | Queue the prompt |
| `Ctrl+N` / `Ctrl+P` | Move the task highlight without the arrow keys |
| `Ctrl+K` | Cancel selected queued/running task |
| `Ctrl+R` | Reconnect the WebSocket |
| `Ctrl+Q` / `Ctrl+C` | Close the TUI; leave the server running |
| Left-drag | Select text; release copies it and clears the highlight |
| `Escape` | Clear the prompt, then the highlight |
| `Enter` / `Escape` (log) | Leave the log when the prompt is empty; otherwise act on the prompt |

## 6. Environment

Read by the server (and inherited by workers), plus `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` by
the worker:

| Variable | Default | Meaning |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | Required |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API endpoint |
| `CODE_TUI_MODEL` | `deepseek-flash` | Model name |
| `CODE_TUI_EFFORT` | `low` | `reasoning_effort` (`none` disables it) |
| `CODE_TUI_CONCURRENCY` | `1` | Max simultaneously running agents (clamped 1–8) |
| `CODE_TUI_MAX_STEPS` | `300` | Step cap for every loop |
| `CODE_TUI_MAX_WALL` | `1800` | Seconds per task |
| `CODE_TUI_CELL_TIMEOUT` | `120` | Seconds per kernel cell |
| `CODE_TUI_MAX_OUTPUT` | `393216` | Max completion tokens per reply (clamped to 393216) |
| `CODE_TUI_MODE` | `state` | Loop to build: `state`, `tool` or `repl` |
| `CODE_TUI_ALLOW_RUN` | `1` | `0` disables `exec`, `git`, `run()` and runs the kernel under bwrap |
| `CODE_TUI_AUTONOMY` | `1` | `0` disables the unattended-ask gate (`NEEDS_INPUT`) |
| `CODE_TUI_DOCS` | `1` | `0` removes the `docs(url)` documentation tool |
| `CODE_TUI_SUBAGENTS` | unset | `1` exposes `delegate` with a read-only child loop |
| `CODE_TUI_SKEPTIC` | unset | `1` adds a read-only reviewer before a finish is accepted |
| `CODE_TUI_TOOLS` | — | Path to a JSON custom-tool manifest |
| `CODE_TUI_CHECK` | auto | Single verification command; empty string disables it |
| `CODE_TUI_CHECKS` | auto | JSON check plan `[{name, cmd, cheap, scope?}]`; wins over `CODE_TUI_CHECK` |
| `CODE_TUI_LEGACY_CHECK` | unset | `1` makes the goal return a bare pass/fail, not a parsed `Check` |
| `CODE_TUI_LEGACY_TIMEOUT` | unset | `1` disables the in-child cell alarm |
| `CODE_TUI_GIT_NAME` | `code-tui agent` | Git author/committer name for agent commits |
| `CODE_TUI_GIT_EMAIL` | `agent@code-tui.local` | Git author/committer email for agent commits |
| `CODE_TUI_PYTHON` | `python3` | Python that can import `bench` |
| `CODE_TUI_STATE_ROOT` | `~/.local/state/code-tui` | Alternative state root |

Key resolution, if you must run it: `~/.config/code-tui/env` (also `.env` in the launch
directory) or an exported `DEEPSEEK_API_KEY`. The footer shows `no api key` until the key is
available; a worker started without one fails immediately with a clear event.

## 7. Verification plans

When a task starts the server detects a plan (an ordered list of named commands):
`CODE_TUI_CHECKS` if set, else `CODE_TUI_CHECK` as a single entry, else
`python3 bench/smoke.py` and `python3 -m ruff check .` (each when the workspace has the file)
plus `lint`, `typecheck`, `format:check` and `test` (or `build` when neither exists) from the
root and `tui/package.json`. `lint` and `format:check` are cheap, so a mutating cell cannot leave
the tree in a state the project itself would reject. The plan is re-detected per task, so a
script an earlier task added is picked up without restarting the server. The runner follows the
lockfile (`bun.lock`/`bun.lockb` → bun, `package-lock.json` → npm). The plan is passed to the
worker as `--checks <json>`, where one `check_plan.Verifier` owns it.

An entry is `{name, cmd, cheap, scope?}`. `scope` is a list of path prefixes for the cheap
health run: a scoped entry is skipped unless the cell changed a path under one of them. The goal
still runs the whole plan, so a docs-only edit does not re-run TUI checks on every cell but does
before a finish. Order matters: the first failure is what the agent sees, so cheap checks come
first.

- **Cheap checks are health checks.** After a mutating cell in their scope, at most once every
  20 seconds, they re-run and the result prints as the `[check]` line of the next state card.
  They never end the run.
- **The whole plan is the goal.** `final_answer` is rejected (`GOAL_UNMET`) until every entry
  passes; then the task lands `done` with `verified: <names> passed`.

`CODE_TUI_CHECK=` (empty) disables the plan. `CODE_TUI_LEGACY_CHECK=1` makes the goal answer
with a bare pass/fail instead of a parsed `Check` (the old message, no failure lines).

## 8. HTTP API

The server binds `127.0.0.1` on a random port (written to `server.json`):

| Route | Meaning |
|---|---|
| `GET /` | Plain-text banner: version, workspace, state, port, model, key |
| `GET /health` | `{ ok: true, ...ServerInfo, hasKey }` |
| `GET /api/snapshot` | `{ type: "snapshot", server, tasks }` |
| `POST /api/tasks` | `{ prompt }` → `201 { task }`; empty or >20000 chars is `400` |
| `GET /api/tasks/:id` | `{ task }` |
| `GET /api/tasks/:id/events?limit=600` | `{ events }` from the journal (limit clamped to 2000) |
| `POST /api/tasks/:id/cancel` | Request cancellation, `{ task }` |
| `GET /ws` | WebSocket: a `snapshot` on open, then `task`, `event`, `ping` |

Cancelling a queued task marks it `cancelled` immediately. Cancelling a running task sends
`SIGTERM` to the worker, then `SIGKILL` after 5 seconds; the task ends `cancelled`.

## 9. Events and statuses

Each task's journal is append-only JSONL with `seq`, `ts`, `kind`, and kind-specific fields.
Kinds: `queued`, `started`, `pid`, `cell.start`, `turn`, `log`, `stderr`, `error`, `final`,
`finished`. Payloads are capped before storage: strings at 16000 chars, arrays at 40 items,
nesting at depth 12 (`[depth]` marks the cut), so nested call arguments survive and one huge
observation cannot bloat a journal.

The worker reports `done` for `DONE`/`VERIFIED`, `partial` for `STEP_LIMIT`/`WALL_LIMIT` with a
non-empty summary, and `failed` otherwise. The server keeps the same mapping and also treats a
worker `failed` with a step or wall limit plus a summary as `partial`. A worker killed before
its final message becomes `failed` with the last stderr line as the error. Tasks that were
`running` when the server restarted are marked `failed` with `server restarted while this task
was running`.

## 10. Safety

The tool surface and the mutation gate are `AGENTS.md` §4 invariant 2; this section is only what
is TUI-specific.

With the default `CODE_TUI_ALLOW_RUN=1` the agent has an unsandboxed Python kernel and shell in
the launch directory: it edits files and runs commands as the invoking user. For untrusted work
use `CODE_TUI_ALLOW_RUN=0`: the kernel runs under `bwrap` with a read-only `/`, the workspace is
bind-mounted writable, and `exec`/`git`/`run()` are not injected (`exec()` and `git()` arrive with
the process prelude), so test-repair tasks become much harder.

The server binds `127.0.0.1` with no authentication. Do not expose the port.

## 11. Development

```bash
bun run typecheck   # tsc --noEmit
bun test            # tests/: list, log, progress, prompt, view rendering, selection, checks
                    # 16 tests, 7 files; React act(...) warnings are expected noise
```

The rendering tests mount `<App>` against a fake Bun server and assert on captured character
frames, so they exercise the same rows the terminal shows. A read-only cross-workspace view of
the newest tasks (no server or key needed): `python3 ../bench/task_journal.py --recent 10`.

## 12. Diagnosis

| Symptom | Check |
|---|---|
| board stale / `reconnecting` | `cat <state-root>/workspaces/<hash>/server.log`; `Ctrl+R`; `server.json` for pid/port |
| server dead | `bin/code-stop`, then `bin/code`; a stale `launch.lock` is cleared by the launcher |
| `no api key` | `~/.config/code-tui/env` or `DEEPSEEK_API_KEY` in the server environment |
| task `failed` at start | worker stderr in `events/<id>.jsonl` (`kind: "stderr"` / `"error"`) |
| task `failed` with "server restarted" | expected: a `running` task does not survive a server restart |
| checks never run | plan empty (`CODE_TUI_CHECK=`) or a scoped entry with no matching changed path |

Health check without the TUI:

```bash
PORT=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["port"])' \
  <state-root>/workspaces/<hash>/server.json)
curl "http://127.0.0.1:$PORT/health"
```

`bin/code-stop` sends `SIGTERM` to the server for the current directory. Running and queued
tasks are marked failed on shutdown; completed history remains.
