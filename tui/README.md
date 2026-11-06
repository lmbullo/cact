# FIRE//CODE

A server-backed, fire-and-forget coding agent TUI for the agent in `../bench` (the `state`
loop by default; `CODE_TUI_MODE` switches it). The command is `code`; the package is
`code-tui`.

The TUI is a thin client. A small Bun server owns the task queue, persists every task
under `~/.local/state/code-tui/` (or `$XDG_STATE_HOME/code-tui`), and keeps running
after the TUI exits. Reopening `code` in the same directory reconnects to the same
server and the same task board.

## For coding agents

Start at `../AGENTS.md`, then run `python3 ../bench/map.py --query tui` to hear it from the
headers themselves. Every file here opens with a header stating what it is, its entry points,
its invariants and where to change what; `../bench/map.py --check` fails when one is
incomplete. The source map:

| File | Owns |
|---|---|
| `bin/code`, `bin/code-stop` | shell entry points; no logic |
| `src/launcher.ts` | workspace hash, server discovery/reuse, detached spawn |
| `src/server.ts` | task queue, concurrency, cancellation, HTTP + WebSocket, journals |
| `src/client.tsx` | the board, the live log, the single prompt box, keys |
| `src/detect-checks.ts` | the verification plan for one workspace (`CODE_TUI_CHECKS` / `CODE_TUI_CHECK` / `package.json`) |
| `src/list-model.ts` | pure ordering/rows/selection/scroll (the tested logic) |
| `src/types.ts` | the wire contract shared by all of the above |
| `src/stop.ts` | SIGTERM the workspace's server |
| `python/worker.py` | one task = one worker = one agent loop; `@@TUI@@` streams to the server |
| `tests/*.test.ts(x)` | rendering/detection tests; the fake Bun server lives in each file |

Two contract tests live outside this directory: `python3 ../bench/smoke.py` (agent/kernel
interface, no key needed) and `python3 ../bench/live.py` (a real end-to-end task).

```
  ─ running (1)
  › t-m3k9a1x2-4f7c1 running  3 steps · 4210 tok  add a retry to the fetch helper ←
  ─ queue (2)
    t-m3k9b7c4-1a9de queue    write tests for the parser
    t-m3k9d2e8-77b02 queue    audit error handling
  ─ done (1)
    t-m3k9f5g6-0c4aa done     rename the config loader
────────────────────────────────────────────────────────────────
  › task for /home/you/some/project…
────────────────────────────────────────────────────────────────
```

Every task is one row: its id, its state in a single lowercase word, its live
`steps · tokens` counters when it has any, and the prompt. The counters come from the
turn events (`Task.progress`), so a running row moves before the final answer arrives;
a finished row falls back to the final `steps`/`usage` counters. The id is
`t-<base36 timestamp>-<5 hex>`, generated once and never reused, so a row keeps its
label while the board reorders. Rows are grouped under a section header per
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
per tool call, plus one `↺` row when the loop rejected or staged a finish — no output,
no verb.

```
  ● running  add a retry to the fetch helper   3 steps · 4210 tok · esc/enter: back · ctrl-k cancel · 12 events
  ▸ read({"path":"src/client.tsx"})
  ▸ exec({"argv":["bun","test"]})
  ▸ 'src/client.tsx'
  ↺ [finish] final_answer rejected: the project check still fails
  ● done stop=DONE · 7 steps · 3 cells · errors 0 · 18432ms · 4210 tok  = all tests pass
  ────────────────────────────────────────────────────────────────────────────────
  › task for /home/you/some/project…
  ────────────────────────────────────────────────────────────────────────────────
```

A row is one call, trimmed to a single line and clipped to the terminal width with a
trailing `…` when it does not fit. A typed call keeps its tool name and shows the JSON
arguments: `read({"path":"a.py"})`, `exec({"argv":["bun","test"]})`. A python cell
shows the arguments of its first call, so `read('a.py')` still reads as `▸ 'a.py'`. The
cell index and any counters are dropped, and only the first line of a multi-line cell is
kept. Calls render in gruvbox purple (`#d3869b`), so they stay distinct from the status
colours around them (green running, yellow queue/partial, red failures).

Turn events — the observation after each call, including the command's stdout and
stderr — are not rendered, with one exception: when a turn carries a `notice` (a
rejected or staged `final_answer`, recorded by `bench/agent/tool_loop.py`), the log
prints its first line and a `(+NL)` count. That is how the reason a finish did not
take becomes visible. The full payload is still written to the event journal and
still reaches the model. The log keeps up to 1000
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
  to the server as `@@TUI@@`-prefixed JSON lines. It also prepends a short history of the
  previous tasks in the same workspace (files they changed, one line each).
- `src/list-model.ts` is the pure ordering / row / scroll model.
- The server broadcasts task updates and events over WebSocket; the TUI only keeps
  in-memory state and reconnects every second on close. There is no TUI-side database.

State lives here (`$CODE_TUI_STATE_ROOT` when set, else `$XDG_STATE_HOME/code-tui`, else
`~/.local/state/code-tui`):

```
workspaces/<sha256-of-workspace, first 16 hex>/
  server.json          pid, port, and the rest of ServerInfo
  server.log           detached server log
  launch.lock/         held while a launcher is starting the server
  tasks/<id>.json      task metadata
  events/<id>.jsonl    append-only event journal
  prompts/<id>.txt     original prompt
  history.jsonl        the last 50 tasks are kept (prompt, summary, changed files); the
                       newest 4 are prepended to the next state-mode task as
                       [workspace history]
```

The workspace is the directory where you ran `code`, resolved with `path.resolve` (the
server additionally `chdir`s into it). Different directories are different task boards.

## Setup

```bash
cd ~/code/tui
bun install
```

Put the API key in the config file used by both the launcher and server
(`.env` in the same directory works too):

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
| `Enter` / `Escape` (log) | Leave the live run log when the prompt is empty; otherwise they act on the prompt |

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
| `CODE_TUI_MAX_STEPS` | `300` | Step cap for every loop |
| `CODE_TUI_MAX_WALL` | `1800` | Seconds per task |
| `CODE_TUI_CELL_TIMEOUT` | `120` | Seconds per kernel cell |
| `CODE_TUI_MAX_OUTPUT` | `393216` | Max completion tokens per model reply (clamped to 393216) |
| `CODE_TUI_MODE` | `state` | Loop to build: `state`, `tool` or `repl` |
| `CODE_TUI_ALLOW_RUN` | `1` | `0` disables `exec`, `git` and `run()` and runs the kernel under bwrap |
| `CODE_TUI_CHECK` | auto | Single verification command; empty string disables it |
| `CODE_TUI_CHECKS` | auto | JSON check plan `[{name, cmd, cheap, scope?}]`; wins over `CODE_TUI_CHECK` |
| `CODE_TUI_LEGACY_CHECK` | unset | `1` makes the goal return a bare pass/fail, not a parsed `Check` |
| `CODE_TUI_LEGACY_TIMEOUT` | unset | `1` disables the in-child cell alarm (old behaviour) |
| `CODE_TUI_GIT_NAME` | `code-tui agent` | Git author/committer name for agent commits |
| `CODE_TUI_GIT_EMAIL` | `agent@code-tui.local` | Git author/committer email for agent commits |
| `CODE_TUI_PYTHON` | `python3` | Python with `bench` dependencies |
| `CODE_TUI_STATE_ROOT` | `~/.local/state/code-tui` | Alternative state root |

### Verification: the check plan

When a task starts the server detects a *plan* (an ordered list of named commands):
`CODE_TUI_CHECKS` if it is set, else `CODE_TUI_CHECK` as a single entry, else
`python3 bench/smoke.py` (when the workspace has one) plus `typecheck`/`test` (or `build`
when neither exists) from the root and `tui/package.json`. The plan is re-detected per
task, so a script an earlier task added is picked up without restarting the server. The
runner follows the lockfile (`bun.lock`/`bun.lockb` -> bun, `package-lock.json` -> npm).
The plan is passed to the worker as `--checks <json>`, where one `checks.Verifier` owns it.

A plan entry has `{name, cmd, cheap, scope?}`. `scope` is a list of path prefixes used by
the cheap health run: a scoped entry is skipped unless the cell changed a path under one
of them. The goal still runs the whole plan, so a docs-only edit does not re-run the TUI
checks on every cell but still runs them before a finish. The default plan scopes `smoke`
to `bench` and the TUI checks to `tui`. Order matters: the first failure is what the agent
sees, so cheap checks come first. One `Verifier` owns the same plan for both roles:

- **Cheap checks are health checks.** After a cell that mutated the tree in their scope,
  at most once every 20 seconds, the `cheap` entries re-run and the result appears as the
  `[check]` line of the next state card. They never end the run.
- **The whole plan is the goal.** `final_answer` is rejected (`GOAL_UNMET`) until every
  entry passes; when it does, the task lands as `done` with the label
  `verified: <names> passed`.

`CODE_TUI_CHECK=` (empty) disables the plan. `CODE_TUI_LEGACY_CHECK=1` makes the goal
answer with a bare pass/fail instead of the parsed `Check`, so a rejection says only that
the check still fails (the old message, no failure lines).

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
`error`, `final`, `finished`. Event payloads are capped for the wire before they are
stored: strings at 16000 chars, arrays at 40 items, nesting at depth 12 (`[depth]` marks
the cut), so nested call arguments survive and one huge observation cannot bloat a journal.

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

For untrusted tasks use `CODE_TUI_ALLOW_RUN=0`: the kernel runs under `bwrap` with a
read-only `/`, the workspace is bind-mounted writable so the agent can still do its job,
and `exec`/`git`/`run()` are not injected at all. Test-repair tasks will then be much harder.

`exec(argv)` runs a program directly (tests, builds, servers, read-only git) with `cwd`,
`env`, `stdin` and `commands=[[...]]`; no shell parses it, so `cwd=` replaces `cd` and there
are no pipes or redirection. `run(command)` is the python shell escape hatch for the cases
that need them. Both share the mutation gate: changing tracked files through `tee`, `cp`,
`mv`, `rm`, `sed -i`, `git apply/...` or `>` is refused as `kind: "wrong_call"` with the
helper to use instead. A typed call is bound to the verb's live signature first, so a keyword
that does not exist (for example `exec` with `code=`) comes back as `kind: "bad_args"` with
`error.code: "bad_arguments"` and a `use` hint instead of a traceback. A shell interpreter
with `-c`/`-lc` inside argv is refused as `shell_via_exec`; use `exec(shell=<string>)` for
an explicit shell string. Direct process spawning
(`subprocess.Popen`, `os.system`, `os.exec*`, `os.posix_spawn`, `os.spawn`) runs through
the same classifier, so a program cannot shell out around the gate. The kernel provides
`read()` (one path or a list; `has_more`/`truncated` are distinct, and a range already shown
returns an `already_read` pointer unless `repeat=True`), `search()` (`grep()` is the python
alias; exact lines with merged context), `outline()` (headings/defs with line numbers),
`guide()`, `map()` (`include` filters by glob; ignored trees are skipped), `write()`, `edit()`, `splice()`,
`apply()`; typed `guide()` answers header queries, typed `recall()` searches earlier cell output; `exec()` and `git()` arrive with the
process prelude, so they are absent when `CODE_TUI_ALLOW_RUN=0`. Mutations are journalled
together so `changes()`/`diff()`/`undo()` cover multi-file edits. A program
launched through `exec`/`run()` can still write files: the journal is a typed-helper contract,
not a sandbox.

The server binds to `127.0.0.1` with a random port. It has no authentication; do not
expose the port.

## Development

```bash
bun run typecheck   # tsc --noEmit
bun test            # tests/: list, log, progress, prompt, view rendering and check detection (13 tests, 6 files)
```

The rendering tests mount `<App>` against a fake Bun server and assert on the captured
character frames, so they exercise the same rows the terminal shows.

A read-only cross-workspace view of the newest tasks (no server or key needed):

```bash
python3 ../bench/runs.py --recent 10
```

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