/**
 * client.tsx: the board — task list + live run log + one prompt box; read-only over the server.
 *
 * WHAT IT IS
 *  The @opentui/react App mounted by main(); local UI state only, the server snapshot/events
 *  are truth. Views "list" (board) and "log"; every piece is a module next to this one.
 *
 * ENTRY POINTS
 *  App({httpUrl, workspace})  top bar, log header, list or log, prompt box, toast
 *  main()                     createCliRenderer + createRoot(<App/>); re-exported, not run here
 *
 * INVARIANTS
 *  submit always queues a new task; events capped at 1000, de-duped by `seq`; missing open task -> list.
 *  order and the auto view changer are local only (never cancel/queue); server.stale is the server's re-hash.
 *  baseUrl is the only address used (socket/events/queue/cancel); nothing here starts or stops a server.
 *
 * WHERE TO CHANGE
 *  probes/socket ... client-probe.ts, client-socket.ts   keys ... client-input.ts
 *  local state ... client-view.ts   writes ... client-actions.ts   board maths ... client-board.ts
 *  frame ... client-layout.ts   rows ... client-panes.tsx + board-view.tsx / log-view.tsx
 *  requests and copy ... client-actions.ts   mount ... client-main.tsx
 */
import type { CliRenderer, TextareaRenderable } from "@opentui/core";
import { useRenderer, useTerminalDimensions } from "@opentui/react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { TaskEvent } from "./types";
import { type BoardOrder, type ScrollState } from "./list-model";
import { TopBar } from "./board-view";
import { copyText } from "./clipboard";
import { boardLayout } from "./client-layout";
import { useBoardModel } from "./client-board";
import { useServerLink } from "./client-socket";
import { useBoardViewState, useSpinner } from "./client-view";
import { useBoardKeys } from "./client-input";
import { useCopyGesture, useEventBackfill, useTaskActions } from "./client-actions";
import { ContentPane, HeaderBar, PromptBox, RuleAbove, RuleBelow } from "./client-panes";
import { main } from "./client-main";

export { main };

/** The board: task list + live run log + one prompt box, mounted against one server's HTTP URL. */
export function App({
  httpUrl,
  workspace,
  stateDir = process.env.CODE_TUI_STATE ?? null,
  copy = copyText,
  autoAdvanceMs = 5000,
}: {
  httpUrl: string;
  workspace: string;
  /**
   * The launcher's state dir (CODE_TUI_STATE). Its `server.json` is how this screen finds a
   * restarted server; without it the reconnect loop just retries the same URL.
   */
  stateDir?: string | null;
  /** Clipboard writer; tests inject a spy so they never touch the host clipboard. */
  copy?: (renderer: CliRenderer, text: string) => Promise<boolean>;
  /** Idle time before a finished log moves to the next running task (ms); tests shorten it. */
  autoAdvanceMs?: number;
}) {
  const renderer = useRenderer();
  const { width, height } = useTerminalDimensions();
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [input, setInput] = useState("");
  const [view, setView] = useState<"list" | "log">("list");
  /** How the board arranges its bands; local UI state, cycled by Ctrl-O. Never sent to the server. */
  const [order, setOrder] = useState<BoardOrder>("status");
  /**
   * Counts interactions, not time: every key, edit, click, scroll or drag bumps it, which
   * restarts the idle clock in client-board.ts. Its value is never displayed.
   */
  const [activity, setActivity] = useState(0);

  const inputRef = useRef<TextareaRenderable | null>(null);
  const viewRef = useRef<"list" | "log">("list");
  const selectedRef = useRef<string | null>(null);
  const eventsSeqRef = useRef(0);
  /**
   * The board window survives renders: keeping the previous start is what stops a live
   * task update from re-pinning (or recentring) the list under the reader.
   */
  const scrollRef = useRef<ScrollState>({ start: 0, focus: -1, selected: null });

  /** Record one interaction: the auto view changer's idle clock restarts on every bump. */
  const bumpActivity = useCallback(() => setActivity((value) => value + 1), []);
  const ui = useBoardViewState({
    inputRef,
    viewRef,
    selectedRef,
    eventsSeqRef,
    setSelected,
    setEvents,
    setView,
  });
  useCopyGesture({ bumpActivity, renderer, copy, notify: ui.notify });

  const link = useServerLink({
    httpUrl,
    workspace,
    stateDir,
    notify: ui.notify,
    selectTask: ui.selectTask,
    setView,
    viewRef,
    selectedRef,
    eventsSeqRef,
    setEvents,
  });
  useEventBackfill({
    baseUrl: link.baseUrl,
    selected,
    connected: link.connected,
    view,
    eventsSeqRef,
    setEvents,
  });
  const board = useBoardModel({
    tasks: link.tasks,
    order,
    events,
    selected,
    view,
    input: input,
    activity,
    autoAdvanceMs,
    openLog: ui.openLog,
    closeLog: ui.closeLog,
    selectTask: ui.selectTask,
    selectedRef,
  });
  // A run is live somewhere: the prompt box's top line spins while work is in flight.
  const spinner = useSpinner(board.runningCount > 0);
  const { queue, cancelSelected } = useTaskActions({
    baseUrl: link.baseUrl,
    notify: ui.notify,
    openLog: ui.openLog,
    tasks: link.tasks,
    inputRef,
    setInput,
    selectedRef,
  });
  useBoardKeys({
    bumpActivity,
    renderer,
    cancelSelected,
    bumpSocketEpoch: link.bumpSocketEpoch,
    notify: ui.notify,
    order,
    setOrder,
    viewRef,
    inputRef,
    input,
    setInput,
    promptRows: ui.promptRows,
    setPromptRow: ui.setPromptRow,
    setPromptRows: ui.setPromptRows,
    queue,
    cycleSelection: board.cycleSelection,
    openLog: ui.openLog,
    closeLog: ui.closeLog,
    tasks: link.tasks,
    selectedRef,
    selectTask: ui.selectTask,
  });

  // Re-measure the prompt after every edit and resize: the textarea re-wraps against the
  // width it was laid out at, so the row count is only reliable one tick after layout.
  useEffect(() => {
    ui.measurePrompt();
    const frame = setTimeout(ui.measurePrompt, 0);
    return () => clearTimeout(frame);
  }, [input, width, ui.promptRows, ui.measurePrompt]);

  // Layout is always: [log header, log only] / content / hairline / prompt. Only the
  // content pane swaps between the board and a run log; the prompt box never does. There
  // is no background fill: the terminal's own colours show through the whole screen.
  const layout = boardLayout({
    width,
    height,
    promptRow: ui.promptRow,
    promptRows: ui.promptRows,
    view,
    selected,
    selectedTask: board.selectedTask,
    listRows: board.listRows,
    connected: link.connected,
    toast: ui.toast,
    server: link.server,
    order,
    previous: scrollRef.current,
  });
  scrollRef.current = layout.scroll.state;

  return (
    <box
      style={{ width: "100%", height: "100%", flexDirection: "column" }}
      onMouseDown={bumpActivity}
      onMouseScroll={bumpActivity}
    >
      <TopBar
        workspace={workspace}
        model={link.server?.model}
        effort={link.server?.effort}
        running={board.runningCount}
        total={board.ordered.length}
        connected={link.connected}
        stale={link.server?.stale ?? false}
        width={width}
      />
      <box style={{ height: 1, flexShrink: 0 }} />
      <HeaderBar
        task={layout.logTask}
        width={width}
        now={board.now}
        check={board.logCheck}
        showingLog={layout.showingLog}
      />
      <ContentPane
        task={layout.logTask}
        events={events}
        width={width}
        listRows={board.listRows}
        visible={layout.visible}
        selected={selected}
        now={board.now}
        onSelect={ui.selectTask}
      />
      <RuleAbove width={layout.ruleWidth} note={`${layout.note}${layout.overflow}`} spinner={spinner} />
      <PromptBox
        width={width}
        height={layout.promptHeight}
        inputRef={inputRef}
        input={input}
        workspace={workspace}
        bumpActivity={bumpActivity}
        setInput={setInput}
        measurePrompt={ui.measurePrompt}
        queue={queue}
      />
      <RuleBelow width={layout.ruleWidth} />
    </box>
  );
}

if (import.meta.main) void main();
