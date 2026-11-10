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
- `server.ts` is the entry point over `server-*.ts` (config, state, journals, queue, worker
  protocol, routes): it owns the queue, concurrency, cancellation, task JSON and event journals.
  It serves HTTP plus a WebSocket and skips the worker when no key is set.
- `python/worker.py` builds the `bench` loop with `config.build(...)` over a `Kernel` with
  `prelude="all"`, and streams each code cell, turn and observation to the server as
  `@@TUI@@`-prefixed JSON lines. It also prepends a short history of previous tasks in the same
  workspace (files they changed, one line each) as `[workspace history]`.
- `client.tsx` mounts `client-main.tsx` over the `client-*.ts(x)` modules (probe, socket, view,
  keys, actions, board maths, layout, panes); `board-view.tsx`/`board-task.tsx` and
  `log-view.tsx`/`log-rows.tsx` split rendering from row shape. Every TUI module is <=250 lines.
- `list-model.ts` is the pure ordering/row/scroll model.
- `pricing.ts` is the one USD table and the one cost calculation (`cost`/`formatCost`) the board
  row and the log header both print.
- `worker-events.ts` turns one `@@TUI@@` worker line into one journal event (the stateless kinds;
  `turn`/`error`/`final` mutate the task and stay in `server.ts`).
- `clipboard.ts` owns the one write to the user's clipboard: host clipboard first, OSC 52
  fallback; a remote terminal uses OSC 52 only.
- The server broadcasts task updates and events over WebSocket; the TUI keeps only in-memory
  state and reconnects every second on close. There is no TUI-side database. A reconnect first
  probes the URL it knows and, when that server is gone, re-reads `server.json` in the state
  dir, so a restarted server on a new port is picked up without closing and reopening the TUI.

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
  server.log           detached server log; its first line is the JSON launch line
  launch.lock/         held while a launcher is starting the server
  tasks/<id>.json      task metadata
  events/<id>.jsonl    append-only event journal
  logs/<id>.jsonl      machine-readable per-task log (see section 9): event records, raw
                       worker stdout/stderr, the prompt, the run's usage and its errors
  prompts/<id>.txt     original prompt
  history.jsonl        last 50 tasks (prompt, summary, changed files, gates, deferred,
                       untested); newest 4 plus up to 6 open items are prepended to the
                       next state-mode task
```

The workspace is the directory where `code` ran, resolved with `path.resolve`; the server
additionally `chdir`s into it.

## 3. Board contract

There is no background colour anywhere. The app draws on whatever the terminal is set to — dark,
light, translucent — and only the ink is fixed. The screen is a masthead, the task sections, one
hairline and the prompt; nothing is boxed, nothing is filled.

One row per task: a `›` selection marker, the task's short handle (`#4f7c1`), the prompt, then
right-aligned telemetry — `steps`, tokens in and out, the run's price — and the wall time.
Tokens read `↑in ↓out` (prompt, then completion) and the cache part is the fraction of prompt
tokens the provider served from its own cache. The price is what the run cost in USD: `$0.07`
with two decimals, `<$0.01` under a cent, computed by `pricing.ts` from the same usage split
(cache-hit, cache-miss and output tokens are priced separately, and a run with no cache counters
has all its input priced as a miss). That table is the only place a price exists: a model it
does not list keeps its token count alone, with no guessed figure. A row whose task recorded
errors appends `N err` in clay after the time. Every number comes from one place,
`list-model.taskUsage`: counters come from turn events (`Task.progress`) while the task runs,
and a finished row falls back to the final `steps`/`usage` totals and its recorded span. The
context window (`ctx`) is the log header's alone — a row has no room for it and would repeat
the same fill twice. Row id is `t-<base36 timestamp>-<5 hex>`; the row shows only its last
segment (`#4f7c1`) because the timestamp half is already implied by the order. The full id
stays in `tasks/<id>.json` and `GET /api/tasks`.

The masthead is the same on both screens: a `▌ code` brand, the workspace with `$HOME`
shortened to `~`, then the model, effort and the running count on the right. It reads `offline`
in red while the socket is down, `idle` when the board is empty, and `stale` last in the same
red when the last snapshot said this server is running older code than the tree (`source-hash.ts`:
the files the detached process imported at boot, `src/server.ts`, `src/worker-events.ts` and
`src/detect-checks.ts`).
One blank row follows it, so the list starts with air rather than against a bar.

`offline` is not a dead end and never needs a relaunch. The screen retries every second; each
attempt asks the URL it knows for `/health`, and when that process is gone it re-reads
`server.json` in the state dir and adopts the port published there once it answers. So
`bin/code-stop` turns the bar to `offline` within a second, and the next `bin/code` — a fresh
server on a fresh port — brings the board back with a `server is back` toast. A restart also
rewrites the tasks: a `running` task does not survive it, so the row returns `failed` with
`server restarted while this task was running`. A slow watchdog repeats the `/health` probe every
five seconds while the screen thinks it is connected, so a half-open socket cannot freeze the
board, and the same read refreshes `stale` between snapshots. `Ctrl+R` forces one attempt now.

```
 ▌ code  ~/some/project                       deepseek-flash · low · 1 running

 ● running  1
 › #4f7c1  add a retry to fetch     3 steps · ↑421k ↓8.0k · $0.07 · 62% cache  11s

 ○ queue  1
   #1a9de  write tests for the parser

 ✓ done  1
   #0c4aa  rename the config loader                                  15s
───────────────────────────────────────────────────────────────────────────────
 › task for /home/you/some/project…
```

A server whose running code no longer matches the tree turns the same bar into
`deepseek-flash · low · 1 running · offline · stale` — one more word in the same row, never a
second one — and the hairline above the prompt gains the fix, `stale code · bin/code-stop`.
Only a restart clears it, and `code` silently reuses a healthy `server.json`, so a fresh launch
is exactly where the word has to appear.

Grouping order: `running`, `queued`, `done`, `partial`, `failed`, `cancelled`. The `queued`
state renders as the shorter word `queue`. `Ctrl-O` cycles the board arrangement in place:
`by status` (the default just described, oldest task first inside a band), `newest first` (the
same bands, newest task on top) and `problems first` (`cancelled`, `failed`, `partial` above the
live work, `done` last, newest first inside a band). An arrangement is display-only: the same
tasks sorted again, never a write to the server, so a snapshot or a task update cannot undo the
reader's choice. While it is not the default the hairline prints `order: <word>`. A band is a
dot, its word and its count — no rule and no column of dashes: the blank row between bands is
what separates them, and a row never repeats the status word its section already said. Telemetry
is right-aligned and the prompt gives up room first, so steps, tokens, price and wall time form
one readable column. Two numbers on a row never describe the same thing: `↑` is prompt tokens,
`↓` completion tokens, `% cache` the hit rate, `$` the run's USD, and none of them is a copy of
another. When the frame is too narrow even for an empty prompt, the row sheds its wall time and
then clips: it never wraps. A running row's wall time ticks once a second; an idle board does
not re-render.

| State | Section label | Dot | Colour |
|---|---|---|---|
| `running` | `● running  3` | `●` | olive |
| `queued` | `○ queue  1` | `○` | amber |
| `done` | `✓ done  12` | `✓` | olive |
| `partial` | `◐ partial  1` | `◐` | amber |
| `failed` | `✖ failed  2` | `✖` | clay |
| `cancelled` | `⊘ cancelled  1` | `⊘` | quietest grey |

Only the dot carries the state colour; the word stays grey, so a column of rows reads as one
block of type instead of a colour chart. The handle is the quietest ink on the row, the prompt is
body ink, and the telemetry is quiet — three weights, one line.

Selected row: `›` on the left and the prompt in brighter ink. There is no second marker on the
right and no background fill: the marker plus the ink contrast is the whole cue. Mouse click
selects (does not open) and never copies. Left-drag selects rendered text; releasing the button
copies the selection to the clipboard and clears the highlight in the same gesture. `Up`/`Down`
move the highlight while the prompt fits one row; once the prompt wraps or holds a typed newline
they move the text cursor. Scrolling is edge-following and one row at a time: the window stays
anchored until the marker crosses an edge, so the list never recentres under the reader. A task
whose status change moves it between bands keeps its screen row, so a live finish does not push
the board up. When the board is taller than the window, the hairline also reports the visible
range (`rows 12–31/40`). The prompt box grows one row per newline or wrapped row, up to five,
then scrolls vertically and the hairline above it shows the cursor position (`3/5`). `Ctrl+Enter`
queues; `Escape` clears the prompt, then the highlight; `Enter` on an empty prompt opens the
highlighted task. The same hairline reports `reconnecting`, the last toast, or `no api key`, in
that priority; the prompt is the last row of the screen, so the terminal's own edge closes it.
A task that hits its step or wall cap but still produces a summary is `partial`, not `failed`;
its final text stays in the log.

Before the first task the board shows `no tasks yet`, one hint line and the fire-and-forget
reminder; it never invents a fake section or an empty table.

## 4. Live run log

`Enter` on an empty prompt opens the selected task's log. Every row is machine-first: one dim
elapsed gutter, then `key=value` fields and nothing else — no glyphs, no sentences, no decorative
prose. The fields are the journal's own names, so a row can be pasted into a grep. The task's
prompt opens the log as a wrapped body under a `kind=prompt` row, and the agent's final summary
closes it as a wrapped body under a `kind=final` row.

The run's state has exactly one source: the status line at the top, drawn from `Task.status`.
The body never repeats it — no `queued`/`started`/`pid`/`finished` row, no status field on the
closing row — so the screen cannot say `queue` and `queued` at once, and nothing can go on
saying `queued` after the run has left that state. Lifecycle events stay in `events/<id>.jsonl`
as journal history; they are not rendered.

```
 ▌ code  ~/some/project                       deepseek-flash · low · 1 running

 ● running  #4f7c1        esc/enter: back · 3 steps · ↑421k ↓8.0k · 11s · $0.07 · 62% cache · ctx 421k/1.0M

  0ms kind=prompt chars=118 model=deepseek-flash effort=low

      add a retry with exponential backoff and jitter to the fetch helper, and cover the
      503 path with a test

  1.5s kind=cell tool=read args={"path":"src/client.tsx"}
  3.0s kind=cell tool=exec args={"argv":["bun","test"]}
  4.5s kind=cell tool=code code='a.py'
  6.0s kind=turn notice=[finish] final_answer rejected: the project check still fails

  7.5s kind=final cells=3

      Retry added to the fetch helper; the backoff path is covered by a test.

      changed
      - src/fetch.ts: three attempts, exponential backoff, jitter
      - tests/fetch.test.ts: a 503 then a success

      verified
      - bun test (19 tests)
───────────────────────────────────────────────────────────────────────────────────────────────────
 › task for /home/you/some/project…
```

The log is the same screen with its content pane swapped: the masthead, the breath under it, a
status line, the rows, and the one hairline over the prompt. The status line carries the dot,
the status word, the short handle, then how the run ended, then `esc/enter: back` and the live
counters; the hint is never dropped and the counters disappear from the tail as the terminal
narrows. It does not repeat the prompt — the log opens with that in full, right below.

Once the run has settled, the line also answers "how did this end?" at a glance, from the task
JSON and the journal alone: the stop reason (`Task.stop` — `DONE`, `TRUNCATED`, `LLM_FAILED`,
`GOAL_UNMET`, ...), the error count when the task recorded any (`Task.errors`), and — when the
last `check` event of the journal failed — the check's name with the first failing line of its
output. A running task has no stop reason yet, so it prints none of this and its counters do not
move.

```
  ✓ done  #4f7c1  DONE   esc/enter: back · 7 steps · ↑12.4k ↓1.2k · 11s · <$0.01 · 62% cache
  ✖ failed  #9a2b0  GOAL_UNMET  typecheck: src/client.test.tsx(12,17): error TS2304: …  esc/enter: back
```

The failing check keeps a third of the row before the counters start filling it, so the counters
give way (from the tail) rather than the verdict. At 60 columns the same line is
`✖ failed  #9a2b0  GOAL_UNMET  typecheck: src… esc/enter: back`: still one row, still clipped.

Those counters are the log's data row, and the only one: turns taken, tokens in and out (`↑`
prompt, `↓` completion, both summed over turns), the wall time, the run's price, the prompt-cache
hit rate and the context window as `ctx <latest prompt>/<window>`. They are derived once, by
`list-model.taskUsage`, from `Task.progress` while the run streams and from the final
`steps`/`usage` after it. No row in the body prints a counter, so a number cannot appear twice or
mean two things.

Row fields by kind (`src/log-view.tsx`, `EventView`):

| kind | fields |
|---|---|
| `cell.start` | `kind=cell tool=<tool> args=<json>` for a typed call, `code=<first call's arguments>` for a python cell |
| `turn` | `kind=turn notice=<the rejected or staged finish, one line>` and `lines=N` when it was several |
| `final` | `kind=final cells=N`, then the summary body |
| `error` | `kind=error text=<one line>` and `lines=N` when it was several |
| `stderr` | `kind=stderr …` |
| `log` | `kind=log …` |
| anything else | `kind=<kind>` only; the payload is never dumped |
| no rows yet | `kind=waiting status=queued|running` |

Truncation is per field, not per row: each value is clipped to the columns its own key leaves
(`FIELD_CAP`, then what is left of the pane), so a 300-char argument can neither push its key nor
a later field off the line, and `…` marks the cut. The two exceptions are the opening prompt and
the final summary: they are wrapped whole by `theme.wrapText` into the shared `INDENT` column,
never clipped, and past `SUMMARY_MAX_LINES` (400) the rest is reported as `(+N more lines)`. The
log opens and closes in that same column, so the start and the finish of a run line up.

The log is monochrome by design: the gutter and the keys use the quietest greys, the values body
ink, amber marks a rejected or staged finish, clay an error, and the closing row borrows the
run's status colour. Nothing is bold. Colour appears only where it means something.

Turn events (the observation after each call, including stdout/stderr) are not rendered, with
one exception: a turn carrying a `notice` — a rejected or staged `final_answer`, recorded by
`bench/agent/tool_loop.py` — keeps its first line. The full payload is still in the journal and
the machine log, and reaches the model. The log keeps up to 1000 events in memory, seeded from
the journal (last 800) on open.

`esc` (empty prompt) or `enter` (empty prompt) goes back; `Ctrl-K` cancels from either screen.
There is only one prompt box, mounted once below whichever view is showing, so switching views
keeps the same textarea, text, cursor, row count and focus. It has the same width, growth and
keys on both screens, and a prompt can be typed and queued while a run streams.

A finished log is not a dead end. When the open log belongs to a task that has finished (`done`,
`partial`, `failed` or `cancelled`) and the screen sees no input for five seconds, it moves
itself to the next running task's log — the next one down the board, wrapping — or back to the
board when nothing else is running. Keys, typing, clicks, scrolling and drags all count as
input and restart the five seconds; a prompt with text in it pauses the move because it is a
draft the reader is still writing. The mover only changes the view: it never cancels, queues or
edits a task. The idle window is `autoAdvanceMs` on `App` (default 5000) so tests can shorten it.

## 5. Keys

| Key | Action |
|---|---|
| `Up` / `Down` | Move the task highlight one row, scrolling at an edge (or the prompt cursor once the prompt wraps) |
| `Enter` | Insert a newline in the prompt; open the highlighted task when the prompt is empty |
| `Ctrl+Enter` | Queue the prompt |
| `Ctrl+N` / `Ctrl+P` | Move the task highlight without the arrow keys |
| `Ctrl+K` | Cancel selected queued/running task |
| `Ctrl+O` | Cycle the board arrangement: by status → newest first → problems first |
| `Ctrl+R` | Reconnect the WebSocket |
| `Ctrl+Q` / `Ctrl+C` | Close the TUI; leave the server running |
| Left-drag | Select text; release copies it and clears the highlight |
| `Escape` | Clear the prompt, then the highlight |
| `Enter` / `Escape` (log) | Leave the log when the prompt is empty; otherwise act on the prompt |
| 5 s idle (log) | On a finished task's log, move to the next running task's log or back to the board |

## 6. Environment

Read by the server (and inherited by workers), plus `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` by
the worker:

| Variable | Default | Meaning |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | Required |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API endpoint |
| `CODE_TUI_MODEL` | `deepseek-flash` | Model name |
| `CODE_TUI_EFFORT` | `low` | `reasoning_effort` (`none` disables it) |
| `CODE_TUI_EFFORT_POLICY` | `fixed` | `adaptive` sends the effort only after a surprising turn; `fixed` always sends `CODE_TUI_EFFORT` |
| `CODE_TUI_PROFILE` | `fast-code` | Measured arm: `fast-code` (lean typed set + python cell, effort `low`, inline finish, green-plan stop); also `fast` (same without `code`), `m3-plan`, `current`, `code-act` |
| `CODE_TUI_CONCURRENCY` | `1` | Max simultaneously running agents (clamped 1–8) |
| `CODE_TUI_MAX_STEPS` | `300` | Step cap for every loop |
| `CODE_TUI_MAX_WALL` | `1800` | Seconds per task |
| `CODE_TUI_CELL_TIMEOUT` | `120` | Seconds per kernel cell |
| `CODE_TUI_MAX_OUTPUT` | `393216` | Max completion tokens per reply (clamped to 393216) |
| `CODE_TUI_MAX_CALLS_PER_TURN` | `8` | Tool calls one turn may carry |
| `CODE_TUI_MAX_TURN_TOKENS` | `8192` | Completion-token ceiling for one turn; `0` uses the run's output cap |
| `CODE_TUI_MAX_REPORT_CHARS` | `4000` | Final-report ceiling; the clip names the cut and points at the structured handoff |
| `CODE_TUI_CODE_MODE` | `0` | `1` exposes the orchestration cell as `code` instead of `python` |
| `CODE_TUI_INLINE_FINISH` | profile | `1` accepts a passing silent `final_answer` in the same turn; `fast-code`/`fast`/`current`/`code-act` set it, `m3-plan` does not |
| `CODE_TUI_MODE` | `state` | Loop to build: `state`, `tool` or `repl` |
| `CODE_TUI_ALLOW_RUN` | `1` | `0` disables `exec`, `git`, `run()` and runs the kernel under bwrap |
| `CODE_TUI_ALLOW_EXEC` | `1` | `0` removes the `exec` verb from the scope (the `ALLOW_RUN` kernel gate is separate) |
| `CODE_TUI_ALLOWED_TOOLS` | unset | Comma-separated tool names that replaces the default scope; the only way to grant `serve` |
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

`launcher.ts` also sets `CODE_TUI_URL`, `CODE_TUI_WORKSPACE` and `CODE_TUI_VERSION` in the
client's environment; they are launcher outputs, not knobs.

No knob is re-read by a running server: the environment, the check plan's defaults and the
server's own `src/` are read at boot. An edited variable or an edited `src/server.ts` therefore
needs `bin/code-stop` and a new `code`; the masthead reports the source case as `stale`.

Key resolution, if you must run it: `~/.config/code-tui/env` (also `.env` in the launch
directory) or an exported `DEEPSEEK_API_KEY`. The footer shows `no api key` until the key is
available; a worker started without one fails immediately with a clear event.

## 7. Verification plans

When a task starts the server detects a plan (an ordered list of named commands):
`CODE_TUI_CHECKS` if set, else `CODE_TUI_CHECK` as a single entry, else
`python3 bench/smoke.py`, `python3 -m ruff check .` (each when the workspace has the file), and
for a `Cargo.toml` root `cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D
warnings` and `cargo test --workspace`, plus `lint`, `typecheck`, `format:check` and `test` (or
`build` when neither exists) from the root and `tui/package.json`. `lint`, `typecheck` and
`format:check` are cheap, so a mutating cell cannot leave the tree in a state the project itself
would reject. The plan is re-detected
per task, so a script an earlier task added is picked up without restarting the server. The
runner follows the lockfile (`bun.lock`/`bun.lockb` → bun, `package-lock.json` → npm). The plan
is passed to the worker as `--checks <json>`, where one `check_plan.Verifier` owns it.

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
| `GET /health` | `{ ok: true, ...ServerInfo, hasKey }`; `logsDir` is the per-task log directory |
| `GET /api/snapshot` | `{ type: "snapshot", server, tasks }` |
| `POST /api/tasks` | `{ prompt }` → `201 { task }`; empty or >20000 chars is `400`. `task.log` is that task's JSONL |
| `GET /api/tasks/:id` | `{ task }`, `log` included |
| `GET /api/tasks/:id/events?limit=600` | `{ events }` from the journal (limit clamped to 2000) |
| `POST /api/tasks/:id/cancel` | Request cancellation, `{ task }` |
| `GET /ws` | WebSocket: a `snapshot` on open, then `task`, `event`, `ping` |

Cancelling a queued task marks it `cancelled` immediately. Cancelling a running task sends
`SIGTERM` to the worker, then `SIGKILL` after 5 seconds; the task ends `cancelled`.

The detached server prints one JSON line when it starts, and nothing else at that point, so a
launcher or an agent reads it without parsing prose:

```json
{"event":"listening","pid":14745,"port":39943,"profile":"fast-code","check":"python3 bench/smoke.py","log":"<state dir>/server.log"}
```

`log` is the server's own stdout/stderr file (the launcher redirects there; `--log` overrides it);
the per-task JSONL directory is `logsDir` on `/health` and `log` on every task payload.

Every `ServerInfo` also carries the code the process booted with:

```json
"source": { "hash": "a7a8dba4d38442c9",
            "files": ["src/server.ts", "src/worker-events.ts", "src/detect-checks.ts"],
            "hashedAt": 1794104821079 },
"stale": true,
"changed": ["src/server.ts"]
```

`source` is written once at boot, beside `pid` and `startedAt`. `stale` and `changed` are measured
on every `/health`, `/api/snapshot` and WebSocket-snapshot read by re-hashing those files, at
most once every 2 seconds, so they describe the running process and not the tree in general. Only
the server's own modules — `src/server.ts`, `src/worker-events.ts`, `src/detect-checks.ts` — are
covered: `client.tsx` is re-imported by every `code` launch and `bench/**` plus `python/worker.py`
by every task's worker, so nothing else can be stale under a live pid. A missing file counts as
changed. The cure is `bin/code-stop`, then `code`: there is no reload path, and a running server
never re-imports itself.

## 9. Events and statuses

Each task's journal is append-only JSONL with `seq`, `ts`, `kind`, and kind-specific fields.
Kinds: `queued`, `started`, `pid`, `cell.start`, `cell.end`, `turn`, `check`, `log`, `stderr`,
`error`, `final`, `finished`. The lifecycle kinds, `cell.start`, `cell.end` and `check` are
journal history only: their live state is `Task.status` (and `Task.pid`), which the board and
the log status line render, so the TUI draws them nowhere. `turn` (with a `notice`), `log`,
`stderr`, `error` and `final` are what the log body shows. `cell.start` is the call about to
run; `cell.end` is its compact result for analysis — `{cell, tool, ok, verb_kind, code, page,
chars, duration_ms, notes}` with no payload body — and `bench/task_journal.py` prefers it for
`obs_chars`, `rejected` and `dupe_note_turn`. The mapping from a worker message to its line is
`src/worker-events.ts` (`journalWorkerEvent`), so one `cell.end` message is one event by
construction, and a kind it does not know falls back to a `log` of the raw line. `check` is one
verification run — `{ok, name, line}`, where `name` is the plan entry (`typecheck`, `test`, ...)
and `line` is the first failing line of its output (empty on a pass); `python/worker.py` emits it
right after the `turn` whose record carried a verdict, so the log status line names a failed check
from the newest `check` event without re-running anything. `turn` keeps its existing shape (its
`data.check` is still the bare `{ok, cmd}`), so a client that predates `cell.end` or `check`
still reads every run. Payloads are capped before storage: strings at 16000 chars, arrays at 40
items, nesting at depth 12 (`[depth]` marks the cut), so nested call arguments survive and one
huge observation cannot bloat a journal.

The server also writes each task's machine log at `logs/<id>.jsonl` — the same facts in one file
an agent can read without a join. One JSON record per line, each with its own `ts`:
`{kind:"prompt", text}` when the task is created, `{kind:"event", event}` for every journal
event, `{kind:"stdout", text}` for every raw worker stdout line and `{kind:"stderr", text}` for
every stderr line, `{kind:"usage", status, stop, steps, calls, llmCalls, errors, usage, wallMs}`
from the final message, and `{kind:"error", text}` for a worker error. It is append-only and
never rewritten. `Task.log` names the file and `ServerInfo.logsDir` its directory, so neither a
reader nor a tool needs to know the state-dir formula.

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
bun test            # 0 fail over tests/: list, log, progress, prompt, views, selection,
                    # order, auto-view, detect-checks, scroll, theme helpers, pricing, stale
                    # sources, worker events, board and log frames; React act(...) warnings are
                    # expected noise
```

The rendering tests mount `<App>` against a fake Bun server and assert on captured character
frames, so they exercise the same rows the terminal shows. A read-only cross-workspace view of
the newest tasks (no server or key needed): `python3 ../bench/task_journal.py --recent 10`.

## 12. Diagnosis

| Symptom | Check |
|---|---|
| board stale / `reconnecting` | `cat <state-root>/workspaces/<hash>/server.log`; `Ctrl+R`; `server.json` for pid/port. A stopped or restarted server is followed by itself — the board goes `offline` and comes back on the new port |
| masthead `stale` | this server runs older code than the tree: `bin/code-stop`, then `bin/code`; `/health` lists the files in `changed` |
| server dead | `bin/code-stop`, then `bin/code`; a stale `launch.lock` is cleared by the launcher |
| `no api key` | `~/.config/code-tui/env` or `DEEPSEEK_API_KEY` in the server environment |
| task `failed` at start | worker stderr in `events/<id>.jsonl` (`kind: "stderr"` / `"error"`), or the same records plus every raw stdout line in `logs/<id>.jsonl` |
| task `failed` with "server restarted" | expected: a `running` task does not survive a server restart |
| checks never run | plan empty (`CODE_TUI_CHECK=`) or a scoped entry with no matching changed path |

Health without the TUI is the `serve` tool: `serve(action="status")` returns the state dir, pid,
port, health, `stale`/`changed`, the check plan and the queue, and `serve(action="logs", tail=40)`
tails `server.log` — no `server.json` parsing and no `curl` needed.

`bin/code-stop` sends `SIGTERM` to the server for the current directory. Running and queued
tasks are marked failed on shutdown; completed history remains.
