# tui-tests
generated_by=bench/agent/pages.py
source_headers=bench/agent/headers.py
source_symbols=bench/index.py
subsystem=tui-tests
page=docs/agent/tui-tests.md
modules=17
module=tui/tests/auto-view.test.tsx symbols=-
entry=App from ../src/client with `autoAdvanceMs` shortened so a test never sleeps for five seconds | Bun.serve stub with a mutable `tasks[]` (snapshot on open, no server-side timer) | list-model.isFinishedStatus / nextRunningTaskId for the pure decisions
module=tui/tests/detect-checks.test.ts symbols=-
entry=detectChecks from ../src/detect-checks; env is the second argument, never process.env | workspace() ... a fresh mkdtemp root; afterAll() removes every root it made | the plan is `[{name, cmd, cheap, scope}]`
module=tui/tests/help.test.tsx symbols=-
entry=App from ../src/client; testRender at 80x24; mockInput for keystrokes
module=tui/tests/list.test.tsx symbols=-
entry=App from ../src/client; Bun.serve stub bound to 127.0.0.1:0 (port never fixed) | testRender(<App httpUrl=... workspace="/tmp/ws" />, {width: 100, height: 24}) | snapshot(tasks) / `tasks` ... the fixtures the stub serves
module=tui/tests/log.test.tsx symbols=-
entry=App from ../src/client; Bun.serve stub with `sockets[]` and `push(seq, event)` | captureCharFrame() for text
module=tui/tests/order.test.tsx symbols=-
entry=orderTasks(tasks, order) / bandOrder(order) / nextOrder(order) / orderLabel(order) | App from ../src/client; Bun.serve stub bound to 127.0.0.1:0 (port never fixed) | mockInput.pressKey("o", { ctrl: true }) ...... one Ctrl-O press
module=tui/tests/pricing.test.tsx symbols=-
entry=cost / formatCost / priceFor from ../src/pricing | TaskRow and LogHeader from ../src/board-view; testRender at 60x4 and 100x4 | `cached`/`missed`/`bare` ... the usage fixtures; `cachedTask`/`cheapTask` the row fixtures
module=tui/tests/progress.test.tsx symbols=-
entry=App from ../src/client; Bun.serve stub with `sockets[]` and `pushTask(task)` | testRender at 120x24; assertions scan the captured frame for turn/token labels
module=tui/tests/prompt.test.tsx symbols=-
entry=App from ../src/client; testRender at 80x24; mockInput for keystrokes | promptBlock(frame) -> the rows between the `›` marker and the next rule
module=tui/tests/reconnect.test.tsx symbols=-
entry=App from ../src/client with `httpUrl`, `workspace` and `stateDir`; two Bun.serve stubs | testRender at 100x24; assertions scan the captured frame
module=tui/tests/scroll.test.ts symbols=-
entry=scrollWindow(rowCount, budget, focus, anchor)   a slice, anchored at the previous start | followSelection(previous, rowCount, budget, selected, focus)  next slice + state
module=tui/tests/selection.test.tsx symbols=-
entry=App from ../src/client; testRender at 80x24; mockMouse.drag for the gesture | copy prop (App) as the clipboard spy, so no host clipboard is touched
module=tui/tests/server.test.ts symbols=-
entry=launch()      spawn src/server.ts; resolve when server.json names a port | post()/get()  the HTTP API the client uses | until()       poll GET /api/tasks/:id for a status
module=tui/tests/stale.test.tsx symbols=-
entry=bootSource / compareSource / SOURCE_FILES from ../src/source-hash; TopBar from ../src/board-view | testRender at 60x1 and 80x1; assertions scan the captured frame
module=tui/tests/theme.test.ts symbols=-
entry=elapsed / shortPath / wrapText from ../src/theme; taskDuration, taskUsage, taskCounters, | foldTurn, formatTokens from ../src/list-model
module=tui/tests/views.test.tsx symbols=-
entry=App from ../src/client; Bun.serve stub with `snapshot(tasks)` and `sockets[]` | testRender at 80x24; assertions scan the captured frame for prompt and rule rows
module=tui/tests/worker-events.test.ts symbols=-
entry=EVENT_PREFIX / parseWorkerLine / journalWorkerEvent from ../src/worker-events | journal(line) ... one wire line in, the events it appended out
