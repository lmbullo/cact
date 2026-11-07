/**
 * The board: task list + live run log + one prompt box. Read-only over the server.
 *
 * WHAT IT IS
 *  An @opentui/react app mounted by main() with CODE_TUI_URL (set by launcher.ts) and
 *  CODE_TUI_WORKSPACE. Two views: "list" (task board) and "log" (one task's live turns).
 *  All state is local UI state; the truth is the server's snapshot/event stream, re-fetched
 *  on connect and on every reconnect (1s backoff, Ctrl-R forces one).
 *
 *  This file holds `App` (state + keys + layout) and `main`; the pieces it renders live next
 *  to it: theme.ts (palette, status words, one-line helpers), board-view.tsx (Rule,
 *  SectionHeader, TaskRow), log-view.tsx (EventView, LogView).
 *
 * ENTRY POINTS
 *  main()                       createCliRenderer + createRoot(<App/>); not run on import
 *  App({httpUrl, workspace})    the whole screen: top bar, log header, list or log, prompt box, toast
 *
 * KEYS
 *  Ctrl-C / Ctrl-Q quit, Ctrl-K cancel the selected task, Ctrl-R reconnect,
 *  up/down move the highlight (or the cursor inside a multi-row prompt), Enter opens the
 *  selected task's log (empty prompt) / adds a line, Ctrl-Enter submits, Esc clears the
 *  prompt then leaves the log. A left-drag selects text; releasing copies it and clears the
 *  highlight.
 *
 * INVARIANTS
 *  - Pure rendering helpers live in list-model.ts (ordering, rows, scroll) and theme.ts
 *    (palette, clipping); the screen itself is covered by the character-frame tests in
 *    tui/tests/, which import `App` from this module. Keep `App` exported and layout maths
 *    out of here when it can be a pure function.
 *  - The prompt box is shared by both views: submitting always queues a new task with the
 *    current text; it never "continues" an existing one.
 *  - Events are capped (last 1000) and de-duplicated by `seq`; a snapshot that no longer
 *    contains the open task falls back to the list.
 *  - Live progress is `taskProgress(task)`: `Task.progress` from the server's turn handling,
 *    falling back to the final `steps`/`usage` once a task ends. Wall time is
 *    `taskDuration(task, now)`, and `now` only ticks while a task is running.
 *
 * WHERE TO CHANGE
 *  A row's look, the rule or the log rows ... board-view.tsx / log-view.tsx
 *  A colour, a status word, a clip rule ...... theme.ts
 *  Live steps/tokens/duration ............... list-model.ts, TaskRow, log header
 *  Keys / focus rules ....................... the useKeyboard block below
 *  Selection copy ........................... clipboard.ts, the useSelectionHandler call below
 *  Ordering, rows, scrolling ................ list-model.ts
 *  Data contract ............................ types.ts
 */
import {
  createCliRenderer,
  type CliRenderer,
  type TextareaRenderable,
  type KeyEvent,
} from "@opentui/core";
import {
  createRoot,
  useKeyboard,
  useRenderer,
  useSelectionHandler,
  useTerminalDimensions,
} from "@opentui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ServerInfo, Snapshot, Task, TaskEvent, WsMessage } from "./types";
import {
  buildRows,
  orderTasks,
  scrollWindow,
  selectedRowIndex,
  stepSelection,
} from "./list-model";
import { C, PROMPT_MAX_ROWS, clip } from "./theme";
import { EmptyState, LogHeader, Rule, SectionHeader, TaskRow, TopBar } from "./board-view";
import { LogView } from "./log-view";
import { copyText } from "./clipboard";

/** The board: task list + live run log + one prompt box, mounted against one server's HTTP URL. */
export function App({
  httpUrl,
  workspace,
  copy = copyText,
}: {
  httpUrl: string;
  workspace: string;
  /** Clipboard writer; tests inject a spy so they never touch the host clipboard. */
  copy?: (renderer: CliRenderer, text: string) => Promise<boolean>;
}) {
  const renderer = useRenderer();
  const { width, height } = useTerminalDimensions();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [server, setServer] = useState<ServerInfo | null>(null);
  const [connected, setConnected] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [input, setInput] = useState("");
  /** Visual (wrapped) row of the prompt cursor, used for the "3/5" overflow note. */
  const [promptRow, setPromptRow] = useState(0);
  /** Wrapped rows the prompt needs, so the box grows when text runs past the width. */
  const [promptRows, setPromptRows] = useState(1);
  const [toast, setToast] = useState("");
  const [socketEpoch, setSocketEpoch] = useState(0);
  /** Wall clock for live row durations; only ticks while something is running. */
  const [now, setNow] = useState(() => Date.now());
  /** "list" is the default screen; "log" is the per-task live run view opened on demand. */
  const [view, setView] = useState<"list" | "log">("list");

  const inputRef = useRef<TextareaRenderable | null>(null);
  const viewRef = useRef<"list" | "log">("list");
  const selectedRef = useRef<string | null>(null);
  const eventsSeqRef = useRef(0);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /**
   * The textarea wraps long text onto extra rows, but the box height around it is ours to
   * set. Measure the wrapped row count after layout so the prompt grows with its text
   * instead of hiding everything past the terminal width.
   */
  const measurePrompt = useCallback(() => {
    const textarea = inputRef.current;
    if (!textarea) return;
    const rows = Math.max(1, textarea.editorView.getTotalVirtualLineCount());
    setPromptRows((current) => (current === rows ? current : rows));
    const row = Math.max(0, textarea.visualCursor.visualRow);
    setPromptRow((current) => (current === row ? current : row));
  }, []);

  const notify = useCallback((message: string) => {
    setToast(message);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 3500);
  }, []);

  // A left-drag is the whole copy gesture: releasing the mouse copies the selection and
  // clears the highlight immediately, whether or not the clipboard write then succeeds.
  // A plain click is only a task selection (or a double-click word), so it never copies
  // unless the gesture actually moved or the terminal expanded it to a word/line.
  useSelectionHandler((selection) => {
    const text = selection.getSelectedText();
    const { anchor, focus } = selection;
    const dragged = anchor.x !== focus.x || anchor.y !== focus.y;
    const expanded = selection.behavior !== "cell";
    renderer.clearSelection();
    if ((!dragged && !expanded) || !text.trim()) return;
    void copy(renderer, text).then((ok) =>
      notify(ok ? `copied ${text.length} chars` : "copy failed"),
    );
  });

  const selectTask = useCallback((id: string | null) => {
    selectedRef.current = id;
    eventsSeqRef.current = 0;
    setSelected(id);
    setEvents([]);
  }, []);

  /** Open the live run log for a task. Only ever called from an explicit user action. */
  const openLog = useCallback(
    (id: string | null) => {
      if (!id) return;
      viewRef.current = "log";
      selectTask(id);
      setView("log");
      // There is a single prompt box for both views, so this is only a focus nudge.
      setTimeout(() => inputRef.current?.focus(), 0);
    },
    [selectTask],
  );

  /** Back to the task list; stop streaming events for the task we leave. */
  const closeLog = useCallback(() => {
    viewRef.current = "list";
    setView("list");
    selectTask(null);
    // Same prompt box on both views: hand focus back after the content pane swaps.
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [selectTask]);

  useEffect(() => {
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let socket: WebSocket | null = null;

    const pushEvent = (taskId: string, event: TaskEvent) => {
      if (viewRef.current !== "log") return;
      if (taskId !== selectedRef.current) return;
      if (event.seq <= eventsSeqRef.current) return;
      eventsSeqRef.current = event.seq;
      setEvents((current) => {
        const next = [...current, event];
        return next.length > 1000 ? next.slice(-1000) : next;
      });
    };

    const connect = () => {
      socket = new WebSocket(`${httpUrl.replace(/^http/, "ws")}/ws`);
      socket.onopen = () => setConnected(true);
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        setConnected(false);
        if (!stopped) retry = setTimeout(connect, 1000);
      };
      socket.onmessage = (message) => {
        let data: WsMessage;
        try {
          data = JSON.parse(String(message.data)) as WsMessage;
        } catch {
          return;
        }
        if (data.type === "snapshot") {
          const snapshot = data as Snapshot;
          setServer(snapshot.server);
          setTasks(snapshot.tasks);
          const current = selectedRef.current;
          if (current && !snapshot.tasks.some((task) => task.id === current)) {
            // The task we were watching disappeared; fall back to the list.
            viewRef.current = "list";
            selectTask(null);
            setView("list");
          }
          return;
        }
        if (data.type === "task") {
          setTasks((current) => {
            const index = current.findIndex((task) => task.id === data.task.id);
            if (index < 0) return [...current, data.task];
            const next = [...current];
            next[index] = data.task;
            return next;
          });
          return;
        }
        if (data.type === "event") {
          pushEvent(data.taskId, data.event);
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [httpUrl, selectTask, socketEpoch]);

  useEffect(() => {
    if (!selected || view !== "log") {
      setEvents([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(
          `${httpUrl}/api/tasks/${encodeURIComponent(selected)}/events?limit=800`,
        );
        if (!response.ok) return;
        const data = (await response.json()) as { events?: TaskEvent[] };
        if (cancelled) return;
        const next = data.events ?? [];
        eventsSeqRef.current = next[next.length - 1]?.seq ?? 0;
        setEvents(next);
      } catch {
        // WebSocket reconnect will refresh the snapshot.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [httpUrl, selected, connected, view]);

  const ordered = useMemo(() => orderTasks(tasks), [tasks]);
  // Hooks must run on every render, so the flat row list is built before the log view returns.
  const listRows = useMemo(() => buildRows(ordered), [ordered]);
  const selectedTask = ordered.find((task) => task.id === selected) ?? null;
  const runningCount = ordered.filter((task) => task.status === "running").length;

  // A live clock, but only while a row can actually use it: an idle board must not re-render.
  const live = runningCount > 0;
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);

  const cycleSelection = useCallback(
    (delta: number) => {
      const next = stepSelection(ordered, selectedRef.current, delta);
      if (next) selectTask(next);
    },
    [ordered, selectTask],
  );

  // Keep a row highlighted even before the user presses a key, so Enter always has a target.
  useEffect(() => {
    if (view !== "list") return;
    if (ordered.length === 0) {
      if (selectedRef.current) selectTask(null);
      return;
    }
    if (ordered.some((task) => task.id === selectedRef.current)) return;
    const preferred = ordered.find((task) => task.status === "running") ?? ordered[0]!;
    selectTask(preferred.id);
  }, [ordered, selectTask, view]);
  const queue = useCallback(
    async (text: string) => {
      const prompt = text.trim();
      if (!prompt) {
        notify("task is empty");
        return;
      }
      const previous = inputRef.current?.plainText ?? prompt;
      if (inputRef.current) inputRef.current.clear();
      setInput("");
      try {
        const response = await fetch(`${httpUrl}/api/tasks`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ prompt }),
        });
        const data = (await response.json()) as { task?: Task; error?: string };
        if (!response.ok || !data.task) {
          if (inputRef.current) inputRef.current.setText(previous);
          setInput(previous);
          notify(data.error ?? `queue failed (${response.status})`);
          return;
        }
        openLog(data.task.id);
        notify("queued");
      } catch (error) {
        if (inputRef.current) inputRef.current.setText(previous);
        setInput(previous);
        notify(`queue failed: ${error instanceof Error ? error.message : String(error)}`);
      } finally {
        inputRef.current?.focus();
      }
    },
    [httpUrl, notify, openLog],
  );

  const cancelSelected = useCallback(async () => {
    const task = tasks.find((item) => item.id === selectedRef.current);
    if (!task) return;
    if (task.status !== "queued" && task.status !== "running") {
      notify("only queued or running tasks can be cancelled");
      return;
    }
    try {
      const response = await fetch(
        `${httpUrl}/api/tasks/${encodeURIComponent(task.id)}/cancel`,
        { method: "POST" },
      );
      if (!response.ok) notify(`cancel failed (${response.status})`);
      else notify("cancellation requested");
    } catch (error) {
      notify(`cancel failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }, [httpUrl, notify, tasks]);

  useKeyboard((key: KeyEvent) => {
    if (key.ctrl && (key.name === "c" || key.name === "q")) {
      key.preventDefault();
      key.stopPropagation();
      renderer.destroy();
      return;
    }
    if (key.ctrl && key.name === "k") {
      key.preventDefault();
      key.stopPropagation();
      void cancelSelected();
      return;
    }
    if (key.ctrl && key.name === "r") {
      key.preventDefault();
      key.stopPropagation();
      setSocketEpoch((value) => value + 1);
      notify("reconnecting");
      return;
    }
    if (viewRef.current === "log") {
      // The prompt is editable on the log screen too, so ordinary keys fall through to
      // the textarea. Only the few navigation keys are intercepted.
      const logPrompt = inputRef.current?.plainText ?? input;
      const logEnter =
        key.name === "return" || key.name === "linefeed" || key.name === "kpenter";
      if (key.ctrl && logEnter) {
        key.preventDefault();
        key.stopPropagation();
        void queue(logPrompt);
        return;
      }
      if (logEnter) {
        // Enter adds a line to the prompt; on an empty prompt it goes back to the list.
        if (logPrompt.length === 0) {
          key.preventDefault();
          key.stopPropagation();
          closeLog();
        }
        return;
      }
      if (key.name === "escape") {
        key.preventDefault();
        key.stopPropagation();
        if (logPrompt.length > 0) {
          inputRef.current?.setText("");
          setPromptRow(0);
          setPromptRows(1);
          setInput("");
          return;
        }
        closeLog();
      }
      return;
    }
    const promptText = inputRef.current?.plainText ?? input;
    if (key.name === "up" || key.name === "down") {
      // A prompt that occupies more than one row — typed newlines or wrapped text — owns
      // the arrows: they move the cursor between its rows instead of the highlight.
      if (promptRows > 1 || promptText.includes("\n")) return;
      // Otherwise arrow keys move the highlight in the task list.
      key.preventDefault();
      key.stopPropagation();
      cycleSelection(key.name === "down" ? 1 : -1);
      return;
    }
    if (key.ctrl && key.name === "n") {
      key.preventDefault();
      key.stopPropagation();
      cycleSelection(1);
      return;
    }
    if (key.ctrl && key.name === "p") {
      key.preventDefault();
      key.stopPropagation();
      cycleSelection(-1);
      return;
    }
    const isEnter =
      key.name === "return" || key.name === "linefeed" || key.name === "kpenter";
    if (key.ctrl && isEnter) {
      // Ctrl+Enter queues the whole prompt; plain Enter adds a line to it.
      key.preventDefault();
      key.stopPropagation();
      void queue(promptText);
      return;
    }
    if (isEnter) {
      const typed = promptText.trim();
      // With a row selected and an empty prompt, Enter opens that task.
      if (!typed && selectedRef.current && tasks.some((task) => task.id === selectedRef.current)) {
        key.preventDefault();
        key.stopPropagation();
        openLog(selectedRef.current);
      }
      // Otherwise the textarea turns Enter into a new line (up to the row cap).
      return;
    }
    if (key.name === "escape") {
      key.preventDefault();
      key.stopPropagation();
      if (input.length > 0) {
        inputRef.current?.setText("");
        setPromptRow(0);
        setPromptRows(1);
        setInput("");
        return;
      }
      if (selectedRef.current) selectTask(null);
      return;
    }
  });

  // Re-measure the prompt after every edit and resize: the textarea re-wraps against the
  // width it was laid out at, so the row count is only reliable one tick after layout.
  useEffect(() => {
    measurePrompt();
    const frame = setTimeout(measurePrompt, 0);
    return () => clearTimeout(frame);
  }, [input, width, promptRows, measurePrompt]);

  // Layout is always: [log header, log only] / content / hairline / prompt. Only the
  // content pane swaps between the board and a run log; the prompt box never does. There
  // is no background fill: the terminal's own colours show through the whole screen.
  const ruleWidth = Math.max(0, width - 1);

  const note = !connected
    ? " reconnecting"
    : toast
      ? ` ${clip(toast, Math.max(8, width - 24))}`
      : server && !server.hasKey
        ? " no api key"
        : "";


  // The box grows with the text, whether it wraps on its own or the user presses Enter,
  // up to PROMPT_MAX_ROWS; past that the textarea scrolls internally.
  const promptHeight = Math.max(1, Math.min(PROMPT_MAX_ROWS, promptRows));
  const overflow =
    promptRows > PROMPT_MAX_ROWS
      ? ` ${Math.min(promptRow + 1, PROMPT_MAX_ROWS)}/${PROMPT_MAX_ROWS}`
      : "";

  // Top bar, the breath under it, the hairline, the prompt, and the log's status line
  // when it is showing.
  const rowBudget = Math.max(
    1,
    height - 3 - promptHeight - (view === "log" && selectedTask ? 1 : 0),
  );
  const focusRow = selectedRowIndex(listRows, selected);
  const { start: winStart, size: winSize } = scrollWindow(listRows.length, rowBudget, focusRow);
  const visible = listRows.slice(winStart, winStart + winSize);

  const showingLog = view === "log" && selectedTask !== null;
  const logTask = showingLog ? selectedTask : null;

  // The one prompt box, rendered outside the view switch. Because it never unmounts, the
  // text, the cursor row, the row count, and the focus survive a move between the board
  // and a run log — the views only swap the content pane above it.
  const promptBox = (
    <box
      style={{
        height: promptHeight,
        flexShrink: 0,
        flexDirection: "row",
        paddingLeft: 1,
        paddingRight: 1,
        overflow: "hidden",
      }}
    >
      <text fg={C.accent}>{"› "}</text>
      <textarea
        ref={inputRef}
        placeholder={`task for ${clip(workspace, Math.max(10, width - 20))}…`}
        focused={true}
        initialValue={input}
        onContentChange={() => {
          setInput(inputRef.current?.plainText ?? "");
          measurePrompt();
        }}
        onCursorChange={() => measurePrompt()}
        onKeyDown={(key) => {
          // Ctrl+Enter queues; Enter inserts a line in the textarea.
          if ((key.ctrl || key.meta) && key.name === "return") {
            key.preventDefault();
            key.stopPropagation();
            void queue(inputRef.current?.plainText ?? "");
          }
        }}
        textColor={C.text}
        focusedTextColor={C.text}
        placeholderColor={C.faint}
        cursorColor={C.accent}
        wrapMode="word"
        style={{ flexGrow: 1 }}
      />
    </box>
  );

  // The log header only exists on the run log, but its wrapper is always rendered (zero
  // rows on the board, one on the log) so every element below it keeps its position in
  // the tree and the view switch never remounts the prompt box.
  const headerBar = (
    <box
      style={{
        height: showingLog ? 1 : 0,
        flexShrink: 0,
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {logTask ? <LogHeader task={logTask} events={events.length} width={width} now={now} /> : null}
    </box>
  );

  // The one hairline, above the prompt on both views: it carries the status note and, when
  // the prompt has scrolled internally, the cursor row.
  const ruleAbove = (
    <box style={{ height: 1, flexShrink: 0 }}>
      <Rule width={ruleWidth} note={`${note}${overflow}`} />
    </box>
  );

  // One content pane, two views: the task board, or the live log of the selected task.
  const contentPane = (
    <box style={{ flexGrow: 1, flexBasis: 0, minHeight: 0, flexDirection: "column" }}>
      {logTask ? (
        <LogView task={logTask} events={events} width={width} />
      ) : listRows.length === 0 ? (
        <EmptyState width={width} />
      ) : (
        visible.map((row, index) =>
          row.kind === "space" ? (
            <box key={`space:${index}`} style={{ height: 1, flexShrink: 0 }}>
              <text>{" "}</text>
            </box>
          ) : row.kind === "section" ? (
            <SectionHeader key={`section:${row.status}`} status={row.status} count={row.count} />
          ) : (
            <TaskRow
              key={`task:${row.task.id}`}
              task={row.task}
              selected={row.task.id === selected}
              width={width}
              now={now}
              onSelect={selectTask}
            />
          ),
        )
      )}
    </box>
  );

  return (
    <box style={{ width: "100%", height: "100%", flexDirection: "column" }}>
      <TopBar
        workspace={workspace}
        model={server?.model}
        effort={server?.effort}
        running={runningCount}
        total={ordered.length}
        connected={connected}
        width={width}
      />
      <box style={{ height: 1, flexShrink: 0 }} />
      {headerBar}
      {contentPane}
      {ruleAbove}
      {promptBox}
    </box>
  );
}
/** Entry point used by `bin/code` (via the launcher) — not run when the module is imported. */
export async function main(): Promise<void> {
  const httpUrl = process.env.CODE_TUI_URL;
  const workspace = process.env.CODE_TUI_WORKSPACE ?? process.cwd();
  if (!httpUrl) {
    console.error("code-tui: CODE_TUI_URL is missing; launch through tui/bin/code");
    process.exit(1);
  }

  const renderer = await createCliRenderer({ exitOnCtrlC: false });
  const root = createRoot(renderer);
  root.render(<App httpUrl={httpUrl} workspace={workspace} />);
  await renderer.closed;
  renderer.destroy();
}

if (import.meta.main) void main();
