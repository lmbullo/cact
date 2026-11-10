# tui
generated_by=bench/agent/pages.py
source_headers=bench/agent/headers.py
source_symbols=bench/index.py
subsystem=tui
page=docs/agent/tui.md
modules=46
module=tui/python/worker.py symbols=main
entry=main()  argv: --workspace --prompt-file --model --effort --max-steps --max-wall --cell-timeout | --max-output --allow-run --check --checks; emits cell.start|cell.end|turn|check|log|error|final. | re-exported: emit/EventTrace/StreamingKernel/cell_end_payload; state_dir/history_brief/record_history.
module=tui/python/worker_events.py symbols=emit,check_name,first_failure,check_payload,cell_end_payload,final_payloads,StreamingKernel,StreamingKernel.run,StreamingKernel.call,EventTrace,EventTrace.write,EventTrace.close
entry=emit(payload)  one `@@TUI@@{json}` line on stdout, flushed | cell_end_payload(cell, number, tool) -> dict  compact cell.end (counts and codes only) | check_payload(check, verifier) -> dict  `check` event: ok/name/first failing line | final_payloads(result) -> (status, [dict])  closing `error`/`final` events + status | StreamingKernel(kernel); EventTrace(emit_event, verifier)  the two streaming adapters
module=tui/python/worker_history.py symbols=state_dir,history_brief,record_history
entry=state_dir(workspace) -> Path               the per-workspace state directory | history_brief(workspace, limit=4, issues=6) -> str   the prompt prefix ('' when no history) | record_history(workspace, prompt, result, files) -> None   append one row
module=tui/src/board-task.tsx symbols=TaskRow
entry=TaskRow({task, selected, width, now, onSelect})   one task, one line, click or key selects
module=tui/src/board-view.tsx symbols=TopBar,Rule,SectionHeader,LogHeader,EmptyState
entry=TopBar({workspace, model, effort, running, total, connected, stale, width}) | Rule({width, note})  hairline above the prompt box   SectionHeader({status, count})  `● running  1` | TaskRow({task, selected, width, now, onSelect})  one task, one line, click or key selects | LogHeader({task, width, now, check})  the run log's status line: state, then how it ended | EmptyState({width})  the first screen, before any task exists
module=tui/src/client-actions.ts symbols=CopyGestureOptions,TaskActionOptions,TaskActions,useTaskActions,useCopyGesture,EventBackfillOptions,useEventBackfill
entry=useTaskActions(options) -> { queue(text), cancelSelected() } | useEventBackfill(options)   re-read `?limit=800` when the open task or the link changes | useCopyGesture(options)     a left drag copies the selection and clears the highlight
module=tui/src/client-board.ts symbols=BoardModelOptions,BoardModel,useBoardModel
entry=useBoardModel(options) -> { ordered, listRows, logCheck, selectedTask, runningCount, now, | cycleSelection }
module=tui/src/client-input.ts symbols=BoardKeysOptions,useBoardKeys
entry=useBoardKeys(options)     the whole table; it only calls the actions the board passes in
module=tui/src/client-layout.ts symbols=LayoutInput,BoardLayout,boardLayout
entry=boardLayout(input) -> { ruleWidth, promptHeight, overflow, showingLog, logTask, rowBudget, | focusRow, scroll, visible, note }
module=tui/src/client-main.tsx symbols=main
entry=main()  createCliRenderer + createRoot(<App/>); resolves when the renderer closes
module=tui/src/client-panes.tsx symbols=PromptBoxProps,PromptBox,HeaderBar,RuleAbove,ContentPane
entry=PromptBox(props)    the one prompt box, rendered outside the view switch | HeaderBar(props)    the log header wrapper; zero rows on the board, one on the log | RuleAbove(props)    the one hairline: status note + prompt overflow marker | ContentPane(props)  board rows, the empty state, or the live log of the open task
module=tui/src/client-probe.ts symbols=probeServer,publishedUrl,sameServerRead
entry=probeServer(url, workspace)                 one /health read -> ServerInfo | null | publishedUrl(stateDir, workspace, current)  the port server.json publishes, when it is new | sameServerRead(current, fresh)              do two reads agree on the drawn fields?
module=tui/src/client-socket.ts symbols=ServerLinkOptions,ServerLink,useServerLink
entry=useServerLink(options) -> { tasks, server, connected, baseUrl, socketEpoch, bumpSocketEpoch }
module=tui/src/client-view.ts symbols=BoardViewStateOptions,BoardViewState,useBoardViewState
entry=useBoardViewState(options) -> { toast, notify, promptRow, promptRows, setPromptRow, | setPromptRows, measurePrompt, selectTask, openLog, closeLog }
module=tui/src/client.tsx symbols=App
entry=App({httpUrl, workspace})  top bar, log header, list or log, prompt box, toast | main()                     createCliRenderer + createRoot(<App/>); re-exported, not run here
module=tui/src/clipboard.ts symbols=copyText
entry=copyText(renderer, text)     true when at least one destination accepted the text
module=tui/src/detect-checks.ts symbols=CheckEnv,detectChecks
entry=detectChecks(root, env = process.env) -> CheckSpec[]
module=tui/src/launcher.ts symbols=-
entry=(no exports) — imported by tui/bin/code;  health(port) / existingServer() / waitForServer() | / launchDetachedServer(log) / ensureServer()
module=tui/src/list-model.ts symbols=-
entry=Re-exports list-order.ts, list-usage.ts, list-scroll.ts: STATUS_ORDER, TRIAGE_ORDER, | BOARD_ORDERS, bandOrder, nextOrder, orderLabel, orderTasks, taskHandle, buildRows, | stepSelection, selectedRowIndex, scrollWindow, followSelection, taskUsage, foldTurn, | formatTokens, taskCounters, taskCost, taskProgress, taskDuration, isFinishedStatus, | nextRunningTaskId, and their types.
module=tui/src/list-order.ts symbols=STATUS_ORDER,TRIAGE_ORDER,BoardOrder,BoardOrderPreset,BOARD_ORDERS,bandOrder,nextOrder,orderLabel,orderTasks,taskHandle,ListRow,buildRows,isFinishedStatus,nextRunningTaskId,stepSelection,selectedRowIndex
entry=STATUS_ORDER, TRIAGE_ORDER; BOARD_ORDERS, BoardOrder, BoardOrderPreset (the order key cycles) | bandOrder(order), nextOrder(order), orderLabel(order) | orderTasks(tasks, order)  stable sort by band rank, then createdAt | taskHandle(id); ListRow, buildRows(ordered, bands)  handle + header/task rows | isFinishedStatus(status), nextRunningTaskId(ordered, current), stepSelection(ordered, current, d), | selectedRowIndex(rows, selected)
module=tui/src/list-scroll.ts symbols=ScrollState,scrollWindow,followSelection
entry=ScrollState                                     { start, focus, selected }, kept between renders | scrollWindow(rowCount, budget, focus, anchor)   the visible slice, with `focus` revealed | followSelection(previous, rowCount, budget, selected, focus) | the next slice + the state to carry forward
module=tui/src/list-usage.ts symbols=TaskUsage,foldTurn,taskUsage,taskCost,formatTokens,taskCounters,taskProgress,taskDuration
entry=TaskUsage                        the numbers one task reports, already reconciled | foldTurn(previous, turn, data)   one turn event folded into the live `progress` | taskUsage(task), taskCost(task)  live `progress` / final `usage`, and the row price (`$0.07`) | formatTokens(count)              `950`, `4.2k`, `12k`, `1.0M` | taskCounters(task), taskProgress(task), taskDuration(task, now)
module=tui/src/log-rows.tsx symbols=INDENT,FIELD_CAP,DEFAULT_CAP,Field,since,finalColor,noticeOf,fields,isSilent,KvRow,bodyRows,PromptView
entry=INDENT, FIELD_CAP, DEFAULT_CAP, Field   the shared column and the per-key caps | fields, since, finalColor, noticeOf, isSilent   tokens, gutter, colour, notices, silence | KvRow({gutter, fit, fields, color})     one row; each value clipped inside its own field | bodyRows(lines, dropped, color)         a wrapped block plus its `(+N more lines)` | PromptView({task, width, origin})       the opening block: `kind=prompt chars=N` + prompt
module=tui/src/log-view.tsx symbols=lastCheck,EventView,LogView
entry=PromptView({task, width, origin})  opening block `kind=prompt chars=N` + prompt   lastCheck(events)  newest verdict | EventView({event, width, origin, status})  one row, or null when the kind is silent | LogView({task, events, width})  the scrollbox; nothing when no task is open
module=tui/src/pricing.ts symbols=Price,PRICES,DEFAULT_MODEL,Usage,priceFor,cost,formatCost
entry=PRICES                 model -> {hit, miss, out} in USD per 1M tokens | DEFAULT_MODEL          the model server.ts runs when CODE_TUI_MODEL is unset | priceFor(model)        the table row, or undefined for a model with no published price | cost(usage, model?)    USD for one usage record, or undefined when there is nothing to price | formatCost(usd)        `$0.07`, `<$0.01` under a cent, "" when there is no price
module=tui/src/source-hash.ts symbols=SOURCE_FILES,fileHash,fileHashes,combineHash,bootSource,compareSource
entry=SOURCE_FILES                  server.ts, worker-events.ts, detect-checks.ts | bootSource(root, files, now)  boot: {source: {hash, files, hashedAt}, hashes} | compareSource(root, boot)     read time: {stale, changed}; a missing file counts as changed | fileHash(root, file)          one file's sha256, or "missing" | combineHash(files, hashes)    the one digest that stands for the whole set
module=tui/src/stop.ts symbols=-
entry=(no exports) — side-effect entry point, imported by tui/bin/code-stop.
module=tui/src/theme.ts symbols=C,STATUS,PROMPT_MAX_ROWS,SUMMARY_MAX_LINES,clip,oneLine,lineCount,wrapText,shortPath,elapsed,toolArgs
entry=C = RGBA palette (hex literals, no background role); STATUS = TaskStatus -> {word, mark, color}; PROMPT_MAX_ROWS. | clip(text, width) collapse to one line + ellipsize;  oneLine(text) collapse whitespace, drop CRs;  lineCount(text) non-empty lines. | wrapText(text, width) word-wrap, keeps every line (prompt, summary);  toolArgs(code) first call's arguments. | shortPath(path) `$HOME` -> `~`;  elapsed(ms) compact duration `0.8s`, `12s`, `2m04s`, `1h02m`;  SUMMARY_MAX_LINES.
module=tui/src/types.ts symbols=TaskStatus,Task,CheckState,TaskEvent,SourceFingerprint,ServerInfo,CheckSpec,Snapshot,WsMessage,WorkerMessage
entry=Task, TaskStatus, TaskEvent, CheckState, ServerInfo, SourceFingerprint, CheckSpec, Snapshot, WsMessage, WorkerMessage
module=tui/src/worker-events.ts symbols=EVENT_PREFIX,JournalEvent,capString,capValue,parseWorkerLine,journalWorkerEvent
entry=EVENT_PREFIX = the marker python/worker.py prefixes its JSON lines with | parseWorkerLine(line) -> WorkerMessage | null;  journalWorkerEvent(msg, line, append) appends one JournalEvent for the pure kinds | capString(value, limit) = a string capped at limit chars, unchanged otherwise | capValue(value) = args/data capped by string length, item count and depth;  JournalEvent = Omit<TaskEvent, "seq" | "ts">
module=tui/tests/auto-view.test.tsx symbols=-
entry=App from ../src/client with `autoAdvanceMs` shortened so a test never sleeps for five seconds | Bun.serve stub with a mutable `tasks[]` (snapshot on open, no server-side timer) | list-model.isFinishedStatus / nextRunningTaskId for the pure decisions
module=tui/tests/detect-checks.test.ts symbols=-
entry=detectChecks from ../src/detect-checks; env is the second argument, never process.env | workspace() ... a fresh mkdtemp root; afterAll() removes every root it made | the plan is `[{name, cmd, cheap, scope}]`
module=tui/tests/list.test.tsx symbols=-
entry=App from ../src/client; Bun.serve stub bound to 127.0.0.1:0 (port never fixed) | testRender(<App httpUrl=... workspace="/tmp/ws" />, {width: 100, height: 24}) | snapshot(tasks) / `tasks` ... the fixtures the stub serves
module=tui/tests/log.test.tsx symbols=-
entry=App from ../src/client; Bun.serve stub with `sockets[]` and `push(seq, event)` | captureCharFrame() for text, captureSpans() when a span (not a row) is the subject
module=tui/tests/order.test.tsx symbols=-
entry=orderTasks(tasks, order) / bandOrder(order) / nextOrder(order) / orderLabel(order) | App from ../src/client; Bun.serve stub bound to 127.0.0.1:0 (port never fixed) | mockInput.pressKey("o", { ctrl: true }) ...... one Ctrl-O press
module=tui/tests/pricing.test.tsx symbols=-
entry=cost / formatCost / priceFor from ../src/pricing | TaskRow and LogHeader from ../src/board-view; testRender at 60x4 and 100x4 | `cached`/`missed`/`bare` ... the usage fixtures; `cachedTask`/`cheapTask` the row fixtures
module=tui/tests/progress.test.tsx symbols=-
entry=App from ../src/client; Bun.serve stub with `sockets[]` and `pushTask(task)` | testRender at 100x24; assertions scan the captured frame for step/token labels
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
