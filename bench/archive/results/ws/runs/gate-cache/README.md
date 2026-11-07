# FIRE//CODE

A server-backed, fire-and-forget coding agent TUI for the REPL agent in `../bench`.
The command is `code`; the package is `code-tui`.

The TUI is a thin client. A small Bun server owns the task queue, persists every task
under `~/.local/state/code-tui/` (or `$XDG_STATE_HOME/code-tui`), and keeps running
after the TUI exits. Reopening `code` in the same directory reconnects to the same
server and the same task board.

```
  ─ running (1)
  › t-m3k9a1x2-4f7c1 running  add a retry to the fetch helper        ←
  ─ queue (2)
    t-m3k9b7c4-1a9de queue    write tests for the parser
    t-m3k9d2e8-77b02 queue    audit error handling
  ─ done (1)
    t-m3k9f5g6-0c4aa done     rename the config loader
────────────────────────────────────────────────────────────────
  › task for /home/you/some/project…
────────────────────────────────────────────────────────────────
```

Every task is one row: its id, its state in a single lowercase word, and the prompt.
The id is `t-<base36 timestamp>-<5 hex>`, generated once and never reused, so a row
keeps its label while the board reorders. Rows are grouped under a section header per
state, in the order `running`, `queued`, `done`, `partial`, `failed`, `cancelled`, so
live work and finished work never share a block. Note that the `queued` state renders
as the shorter word `queue` in both section headers and rows:

| State | Section header | Row word | Colour |
|---|---|---|---|
| `running` | `─ running (n)` | `running` | green |
| `queued` | `─ queue (n)` | `queue` | yellow |
| `done` | `─ done (n)` | `done` | dim |
| `partial` | `─ partial (n)` | `partial` | yellow |
| `failed` | `─ failed (n)` | `failed` | red |
| `cancelled` | `─ cancelled (n)` | `cancelled` | faint |

The selected row is marked with `›` on the left and `←` on the right. A mouse click on
a row selects it (it does not open it).

`Up` / `Down` move the highlight while the prompt fits on one row; once the prompt
wraps or holds a typed newline they move the text cursor instead. The box grows with the
text — a row per newline, or per wrapped row when a long line runs past the terminal
width — up to five rows, after which it scrolls vertically and the bottom rule shows the
cursor position (`3/5`) — `Ctrl+Enter` queues the prompt, and `Escape` clears the prompt,
or the highlight once the prompt is empty. `Enter` on an empty prompt opens the
highlighted task. The screen is the task list, a rule, the prompt box, and a plain rule
below it — no key-hint tooltips are drawn under the prompt box.
The rule above the prompt reports `reconnecting`, the last toast, or `no api key` (in
that priority) when there is something to say.

A task that reaches its step or wall-clock cap but still produces a summary is shown as
`partial` rather than `failed`; its final text remains available in the log.

## The live run log

`Enter` on an empty prompt opens the selected task's live run log. It shows one row
per tool call and nothing else — no output, no verb, no counters:

```
  ● running  add a retry to the fetch helper       esc/enter: back · ctrl-k cancel · 12 events
  ▸ 'src/client.tsx'
  ▸ 'bun test'
  ▸ 'src/client.tsx'
  ● done stop=DONE · 7 steps · 3 cells · errors 0 · 18432ms · 4210 tok  = all tests pass
  ────────────────────────────────────────────────────────────────────────────────
  › task for /home/you/some/project…
  ────────────────────────────────────────────────────────────────────────────────
```

A row is the arguments of one cell, trimmed to a single line and clipped to the
terminal width with a trailing `…` when they do not fit: `read('a.py')` reads as
`▸ 'a.py'`, `run('bun test')` as `▸ 'bun test'`. The call's verb, the cell index, and
any counters are dropped, and only the first line of a multi-line cell is kept. Calls
render in gruvbox purple (`#d3869b`), so they stay distinct from the status colours
around them (green running, yellow queue/partial, red failures).

Turn events — the observation after each call, including the command's stdout and
stderr — are never rendered at all. The full payload is still written to the event
journal and still reaches the model; only the TUI drops it. The log keeps up to 1000
events in memory, seeded from the journal (last 800) when the screen opens.

`esc` (with an empty prompt) or `enter` (on an empty prompt) go back to the board;
`Ctrl-K` cancels the task from either screen.

There is only ever one prompt box. It is mounted once, below whichever view is showing,
so switching between the board and a run log swaps the content above it and nothing else:
the same textarea keeps the text, the cursor, the row count, and the focus, between the
two rules. The box therefore has the same width, the same growth to five rows, and the
same keys on both screens — you can keep typing (and queue) a prompt while a run streams.

## How it works

- `bin/code` → `src/launcher.ts`.
- `src/launcher.ts` hashes the launch directory, takes a `launch.lock`, finds or starts a
  detached `src/server.ts` for that workspace (logging to `server.log`), waits for
  `/health` to answer for this workspace, then imports and mounts `src/client.tsx`.
- `src/server.ts` owns the queue, concurrency, cancellation, task JSON, and event
  journals. It serves HTTP plus a WebSocket and skips the worker when no key is set.
- `python/worker.py` builds the `bench` agent loop with `config.build(...)` over a
  `Kernel` with `prelude="all"` and streams each code cell, turn, and observation back
  to the server as `@@TUI@@`-prefixed JSON lines.
- `src/list-model.ts` is the pure ordering / row / scroll model.
- The server broadcasts task updates and events over WebSocket; the TUI only keeps
  in-memory state and reconnects every second on close. There is no TUI-side database.

State lives here (`$XDG_STATE_HOME/code-tui` when set, else `~/.local/state/code-tui`):

```
workspaces/<sha256-of-workspace, first 16 hex>/
  server.json          pid, port, and the rest of ServerInfo
  server.log           detached server log
  launch.lock/         held while a launcher is starting the server
  tasks/<id>.json      task metadata
  events/<id>.jsonl    append-only event journal
  prompts/<id>.txt     original prompt
```

The workspace is the directory where you ran `code`, resolved with `path.resolve` (the
server additionally `chdir`s into it). Different directories are different task boards.

## Setup

```bash
cd ~/code/tui
bun install
```

Put the API key in the config file used by both the launcher and server:

```bash
mkdir -p ~/.config/code-tui
printf 'DEEPSEEK_API_KEY=sk-...\n' >> ~/.config/code-tui/env
chmod 600 ~/.config/code-tui/env
```

Or export it in the shell before launching:

```bash
export DEEPSEEK_API_KEY=sk-...
```

The footer shows `no api key` until the key is available; tasks fail immediately with a
clear event if it is still missing when a worker starts.

Add the `code` command to `~/.zshrc`:

```zsh
code() {
  local _code_tui="$HOME/code/tui/bin/code"
  if [[ ! -x "$_code_tui" ]]; then
    print -u2 "code: missing $_code_tui"
    return 1
  fi
  "$_code_tui" "$@"
}
```

Then:

```bash
cd ~/some/project
code
```

Close the TUI with `Ctrl+Q` (or `Ctrl+C`). The server and tasks keep running. Open
`code` again in the same directory to see the same board.

## Keys

| Key | Action |
|---|---|
| `Up` / `Down` | Move the task highlight (or the prompt cursor once the prompt wraps onto more than one row) |
| `Enter` | Insert a newline in the prompt; open the highlighted task when the prompt is empty |
| `Ctrl+Enter` | Queue the prompt |
| `Ctrl+N` / `Ctrl+P` | Move the task highlight without the arrow keys |
| `Ctrl+K` | Cancel selected queued/running task |
| `Ctrl+R` | Reconnect the WebSocket |
| `Ctrl+Q` / `Ctrl+C` | Close the TUI; leave the server running |
| `Escape` | Clear the prompt, then the highlight |
| `q` / `Enter` / `Escape` (log) | Leave the live run log and return to the board |

## Configuration

Environment variables are read by the server (and inherited by workers), plus
`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` by the worker:

| Variable | Default | Meaning |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | Required |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API endpoint |
| `CODE_TUI_MODEL` | `deepseek-flash` | Model name |
| `CODE_TUI_EFFORT` | `low` | `reasoning_effort` (`none` disables it) |
| `CODE_TUI_CONCURRENCY` | `1` | Max simultaneously running agents (clamped 1–8) |
| `CODE_TUI_MAX_STEPS` | `120` | ReplLoop step cap |
| `CODE_TUI_MAX_WALL` | `1800` | Seconds per task |
| `CODE_TUI_CELL_TIMEOUT` | `120` | Seconds per kernel cell |
| `CODE_TUI_MAX_OUTPUT` | `393216` | Max characters of cell output kept per call |
| `CODE_TUI_ALLOW_RUN` | `1` | `0` disables `run()` and uses the bwrap sandbox |
| `CODE_TUI_CHECK` | auto | Verification command; empty string disables it |
| `CODE_TUI_PYTHON` | `python3` | Python with `bench` dependencies |
| `CODE_TUI_STATE_ROOT` | `~/.local/state/code-tui` | Alternative state root |

### Verification goal

On startup the server picks a check command: `CODE_TUI_CHECK` if it is set, otherwise
`bun run typecheck` when the workspace's `package.json` has a `typecheck` script, else
`bun test` when it has a `test` script, else nothing. The worker passes it to the agent
as a goal; when the check exits `0` the run stops with `VERIFIED` and lands as `done`
with the label `verified: <check> passed`. Set `CODE_TUI_CHECK=` to opt out.

## HTTP API

The server binds to `127.0.0.1` on a random port (written to `server.json`):

| Route | Meaning |
|---|---|
| `GET /` | Plain-text banner: version, workspace, state, port, model, key |
| `GET /health` | `{ ok: true, ...ServerInfo, hasKey }` |
| `GET /api/snapshot` | `{ type: "snapshot", server, tasks }` |
| `POST /api/tasks` | `{ prompt }` → `201 { task }`; empty or >20000 chars is `400` |
| `GET /api/tasks/:id` | `{ task }` |
| `GET /api/tasks/:id/events?limit=600` | `{ events }` from the journal (limit clamped to 2000) |
| `POST /api/tasks/:id/cancel` | Request cancellation, `{ task }` |
| `GET /ws` | WebSocket: a `snapshot` on open, then `task`, `event`, and `ping` messages |

Cancelling a queued task marks it `cancelled` immediately. Cancelling a running task
sends `SIGTERM` to the worker, then `SIGKILL` after 5 seconds; the task ends as
`cancelled`.

## Events and statuses

Each task's journal is append-only JSONL with `seq`, `ts`, `kind`, and kind-specific
fields. Kinds: `queued`, `started`, `pid`, `cell.start`, `turn`, `log`, `stderr`,
`error`, `final`, `finished`.

The worker reports `done` for `DONE` / `VERIFIED`, `partial` for `STEP_LIMIT` /
`WALL_LIMIT` with a non-empty summary, and `failed` otherwise. The server keeps the
same mapping and also treats a worker `failed` with a step or wall limit plus a summary
as `partial`. A worker killed before it writes a final message becomes `failed` with the
last stderr line as the error. Tasks that were `running` when the server restarted are
marked `failed` with `server restarted while this task was running`.

## Safety

This is meant for trusted, fire-and-forget work. With the default
`CODE_TUI_ALLOW_RUN=1`, the agent has an unsandboxed Python kernel and shell in the
launch directory. It can edit files and run commands as your user.

For untrusted tasks use `CODE_TUI_ALLOW_RUN=0`, which uses the `bwrap` read-only
sandbox and disables the `run()` helper. Test-repair tasks will then be much harder.

`run()` is reserved for whole programs (tests, builds, git, servers). Shell file
inspection such as `grep`, `cat`, `sed`, `ls`, and `find` is rejected with a protocol
error; the agent must use `read()`, `grep()`, `map()`, `write()`, or `edit()` instead.
The kernel prelude provides all five helpers.

The server binds to `127.0.0.1` with a random port. It has no authentication; do not
expose the port.

## Development

```bash
bun run typecheck   # tsc --noEmit
bun test            # tests/ list, log, and prompt rendering
```

The rendering tests mount `<App>` against a fake Bun server and assert on the captured
character frames, so they exercise the same rows the terminal shows.
Both commands must pass before a change lands.

## Troubleshooting

Server log:

```bash
cat ~/.local/state/code-tui/workspaces/<hash>/server.log
```

The server JSON contains the pid, port, and resolved settings:

```bash
cat ~/.local/state/code-tui/workspaces/<hash>/server.json
```

Health check:

```bash
PORT=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["port"])' \
  ~/.local/state/code-tui/workspaces/<hash>/server.json)
curl "http://127.0.0.1:$PORT/health"
```

To stop a workspace server cleanly:

```bash
~/code/tui/bin/code-stop
```

This sends `SIGTERM` to the server for the current directory. Running and queued tasks
are marked failed on shutdown; completed task history remains.