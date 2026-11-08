/**
 * The board: task list + live run log + one prompt box. Read-only over the server.
 *
 * WHAT IT IS
 *  An @opentui/react app mounted by main() with CODE_TUI_URL (set by launcher.ts) and
 *  CODE_TUI_WORKSPACE. Two views: "list" (task board) and "log" (one task's live turns).
 *  All state is local UI state; the truth is the server's snapshot/event stream, re-fetched
 *  on connect and on every reconnect (1s backoff, Ctrl-R forces one).
 *
 *  CONNECTION
 *  The screen outlives the server process: each reconnect attempt first asks the old URL for
 *  `/health`, and when that is dead it re-reads `<state>/server.json`, because a restarted
 *  server (`bin/code-stop`, then `bin/code`) binds a new OS-chosen port. A different healthy
 *  URL replaces `baseUrl`, which re-opens the socket there, so a stopped/restarted/returning
 *  server shows up without closing and reopening the TUI. A slow watchdog repeats the probe
 *  while the screen thinks it is connected, so a half-open socket cannot freeze the board.
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
 *  Ctrl-C / Ctrl-Q quit, Ctrl-K cancel the selected task, Ctrl-R reconnect, Ctrl-O cycle the
 *  board arrangement (by status / newest first / problems first), up/down move the highlight
 *  (or the cursor inside a multi-row prompt), Enter opens the selected task's log (empty
 *  prompt) / adds a line, Ctrl-Enter submits, Esc clears the prompt then leaves the log. A
 *  left-drag selects text; releasing copies it and clears the highlight.
 *
 *  AUTO VIEW CHANGER
 *  A log left open on a finished task is a dead end: after 5 s with no input (keys, typing,
 *  clicks, scrolling, a drag), the screen moves itself to the next running task's log. When
 *  no other task is running it goes back to the board. Any interaction restarts the 5 s, and
 *  a prompt with text in it is treated as a draft, so it never advances under the reader.
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
 *  - The board arrangement (`order`) is local UI state, never sent to the server: the same
 *    tasks re-sorted, so a reconnect or a task update cannot undo the reader's choice. The
 *    hairline prints it while it is not the default.
 *  - The auto view changer is local UI state too: it only moves between views, never cancels
 *    or queues anything, and every interaction (`bumpActivity`) restarts its idle clock. The
 *    idle clock is keyed to the open task's id/status, not to its streaming counters, so a
 *    live task update cannot keep postponing (or trigger) the move.
 *  - `server.stale` is read-only truth from the snapshot: the server re-hashes its own files
 *    and this screen only repeats it. The masthead says `stale` (one word, beside `offline`);
 *    the hairline names the fix, `bin/code-stop`, because nothing in the client can reload a
 *    detached process. A `/health` read refreshes those read-time bits between snapshots; the
 *    screen never computes them itself.
 *  - `baseUrl` is the only address the screen talks to: the socket, the event fetch, the queue
 *    and the cancel all read it, and it changes only when the state dir publishes a healthy
 *    server somewhere else. Nothing here starts, stops or restarts a server.
 *  - The reconnect loop is the only clock that reacts to the server; the watchdog is the only
 *    poll, it is one `/health` read every `WATCHDOG_MS`, and it never cancels or queues work.
 *
 * WHERE TO CHANGE
 *  A row's look, the rule or the log rows ... board-view.tsx / log-view.tsx
 *  A colour, a status word, a clip rule ...... theme.ts
 *  Live steps/tokens/cache/context ......... list-model.ts, TaskRow, log header
 *  Keys / focus rules ....................... the useKeyboard block below
 *  Selection copy ........................... clipboard.ts, the useSelectionHandler call below
 *  Ordering, rows, scrolling ................ list-model.ts
 *  The arrangements Ctrl-O cycles ........... list-model.BOARD_ORDERS
 *  The idle clock / targets ................. the auto-advance effect below,
 *                                            list-model.nextRunningTaskId
 *  Data contract ............................ types.ts
 *  What `stale` means ...................... source-hash.ts, ServerInfo.source in server.ts
 *  Reconnecting / following a restart ...... the connection effect and the watchdog below,
 *                                            publishedUrl above `App`
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
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ServerInfo, Snapshot, Task, TaskEvent, WsMessage } from "./types";
import {
  bandOrder,
  buildRows,
  followSelection,
  isFinishedStatus,
  nextOrder,
  nextRunningTaskId,
  orderLabel,
  orderTasks,
  selectedRowIndex,
  stepSelection,
  type BoardOrder,
  type ScrollState,
} from "./list-model";
import { C, PROMPT_MAX_ROWS, clip } from "./theme";
import { EmptyState, LogHeader, Rule, SectionHeader, TaskRow, TopBar } from "./board-view";
import { LogView, lastCheck } from "./log-view";
import { copyText } from "./clipboard";

/** How long one `/health` read may take before the server counts as down. */
const PROBE_TIMEOUT_MS = 1200;
/** Delay between reconnect attempts; every miss also re-reads `server.json`. */
const RETRY_MS = 1000;
/** How often a screen that thinks it is connected re-probes, so a half-open socket cannot stick. */
const WATCHDOG_MS = 5000;

/**
 * One `/health` read: the server's own view of itself, or null when it is down, too slow to
 * answer, or a different workspace's server on a recycled port.
 */
async function probeServer(url: string, workspace: string): Promise<ServerInfo | null> {
  try {
    const response = await fetch(`${url}/health`, {
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    if (!response.ok) return null;
    const info = (await response.json()) as ServerInfo;
    if (!info || !info.port) return null;
    return resolve(info.workspace ?? "") === resolve(workspace) ? info : null;
  } catch {
    return null;
  }
}

/**
 * The URL the state dir publishes now, when it is not the one already in use. A restarted server
 * binds a fresh OS-chosen port and rewrites `server.json`; reading it is the only way a running
 * screen can find the new process without being relaunched. A stopped server leaves no file
 * (`stop.ts` removes it), and an unreadable or foreign one is simply not an answer.
 */
function publishedUrl(stateDir: string | null, workspace: string, current: string): string | null {
  if (!stateDir) return null;
  try {
    const info = JSON.parse(readFileSync(join(stateDir, "server.json"), "utf8")) as ServerInfo;
    if (!info || !info.port) return null;
    if (info.workspace && resolve(info.workspace) !== resolve(workspace)) return null;
    const url = `http://127.0.0.1:${info.port}`;
    return url === current ? null : url;
  } catch {
    return null;
  }
}

/** Do two server reads agree on the bits the screen draws? A health probe refreshes only these. */
function sameServerRead(current: ServerInfo | null, fresh: ServerInfo): boolean {
  if (!current) return false;
  return (
    current.port === fresh.port &&
    current.pid === fresh.pid &&
    current.model === fresh.model &&
    current.effort === fresh.effort &&
    current.hasKey === fresh.hasKey &&
    current.stale === fresh.stale &&
    (current.changed ?? []).join(",") === (fresh.changed ?? []).join(",")
  );
}

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
  /**
   * Where the server answers now: the launcher's URL until a restart publishes another port.
   * The socket, the event fetch, the queue and the cancel all read it.
   */
  const [baseUrl, setBaseUrl] = useState(httpUrl);
  /** Readable from a timer without making it an effect dep; mirrors `connected`. */
  const connectedRef = useRef(false);
  /** False until the first socket opens, so only a *return* is announced as `server is back`. */
  const everConnectedRef = useRef(false);
  /** Wall clock for live row durations; only ticks while something is running. */
  const [now, setNow] = useState(() => Date.now());
  /** "list" is the default screen; "log" is the per-task live run view opened on demand. */
  const [view, setView] = useState<"list" | "log">("list");
  /** How the board arranges its bands; local UI state, cycled by Ctrl-O. Never sent to the server. */
  const [order, setOrder] = useState<BoardOrder>("status");
  /**
   * Counts interactions, not time: every key, edit, click, scroll or drag bumps it, which
   * restarts the idle clock below. Its value is never displayed.
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
  /** The current board arrangement, readable from a timer without making it an effect dep. */
  const orderedRef = useRef<Task[]>([]);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Record one interaction: the auto view changer's idle clock restarts on every bump. */
  const bumpActivity = useCallback(() => setActivity((value) => value + 1), []);

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
    bumpActivity();
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

  /**
   * The connection loop. A socket close is not the only way to lose a server, so every attempt
   * is a probe first: the same URL while it answers, otherwise the port `server.json` publishes
   * now. A different healthy URL moves `baseUrl`, which tears this effect down and opens the
   * next one there; the snapshot on open then refreshes the board, the masthead and `stale`.
   */
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

    /** One pending attempt at a time: a new schedule replaces the old timer. */
    const schedule = (run: () => void) => {
      if (stopped) return;
      if (retry) clearTimeout(retry);
      retry = setTimeout(run, RETRY_MS);
    };

    const open = () => {
      socket = new WebSocket(`${baseUrl.replace(/^http/, "ws")}/ws`);
      socket.onopen = () => {
        const back = everConnectedRef.current && !connectedRef.current;
        everConnectedRef.current = true;
        connectedRef.current = true;
        setConnected(true);
        if (back) notify("server is back");
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        connectedRef.current = false;
        setConnected(false);
        schedule(() => void reconnect());
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

    /**
     * One reconnect attempt. The current URL wins when it answers (the common case: the socket
     * dropped but the process never did); otherwise the published port is probed before it is
     * adopted, so a dead or foreign entry in `server.json` cannot move the screen anywhere.
     */
    const reconnect = async () => {
      if (stopped) return;
      if (await probeServer(baseUrl, workspace)) {
        if (stopped) return;
        open();
        return;
      }
      const published = publishedUrl(stateDir, workspace, baseUrl);
      if (published && (await probeServer(published, workspace))) {
        if (stopped) return;
        // The effect re-runs on the new address and opens the socket there.
        setBaseUrl(published);
        return;
      }
      schedule(() => void reconnect());
    };

    open();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [baseUrl, stateDir, workspace, notify, selectTask, socketEpoch]);

  /**
   * The watchdog. A socket can die without a close event (a killed server, a suspended laptop)
   * and `stale`/`changed` are re-hashed per read, so one light probe keeps the masthead honest
   * between snapshots. A failed probe drops the socket and restarts the loop, which is what
   * re-reads `server.json`; a healthy one only refreshes the read-time bits.
   */
  useEffect(() => {
    const timer = setInterval(() => {
      void (async () => {
        const fresh = await probeServer(baseUrl, workspace);
        if (!fresh) {
          connectedRef.current = false;
          setConnected(false);
          setSocketEpoch((value) => value + 1);
          return;
        }
        setServer((current) =>
          sameServerRead(current, fresh) ? current : { ...(current ?? fresh), ...fresh },
        );
      })();
    }, WATCHDOG_MS);
    return () => clearInterval(timer);
  }, [baseUrl, workspace]);

  useEffect(() => {
    if (!selected || view !== "log") {
      setEvents([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(
          `${baseUrl}/api/tasks/${encodeURIComponent(selected)}/events?limit=800`,
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
  }, [baseUrl, selected, connected, view]);

  const ordered = useMemo(() => orderTasks(tasks, order), [tasks, order]);
  orderedRef.current = ordered;
  // Hooks must run on every render, so the flat row list is built before the log view returns.
  const listRows = useMemo(() => buildRows(ordered, bandOrder(order)), [ordered, order]);
  /**
   * How the open run's last verification ended, derived from its own events on every batch: the
   * header names a failed check from the journal's newest `check` event, so the client keeps no
   * state (and a later pass simply stops the failure being drawn).
   */
  const logCheck = useMemo(() => lastCheck(events), [events]);
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

  // The auto view changer. A log on a finished task is a dead end, so after `autoAdvanceMs`
  // without an interaction it moves to the next running task's log, or back to the board when
  // no other task is running. Dependencies are the open task's identity and status (not its
  // streaming counters, so a live update cannot postpone the move) plus the activity counter
  // (every key, edit, click, scroll or drag restarts the clock). `orderedRef` is read inside
  // the timeout so the target is the board as it is when the timer fires, and a non-empty
  // prompt is a draft the reader is still writing: it pauses the mover until it is cleared.
  const hasDraft = input.trim().length > 0;
  useEffect(() => {
    if (view !== "log" || !selectedTask || !isFinishedStatus(selectedTask.status) || hasDraft) {
      return;
    }
    const timer = setTimeout(() => {
      const next = nextRunningTaskId(orderedRef.current, selectedRef.current);
      if (next) openLog(next);
      else closeLog();
    }, autoAdvanceMs);
    return () => clearTimeout(timer);
  }, [view, selectedTask?.id, selectedTask?.status, hasDraft, activity, autoAdvanceMs, openLog, closeLog]);

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
        const response = await fetch(`${baseUrl}/api/tasks`, {
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
        // No "queued" toast: the log header already shows the task's state, live, and the
        // server may have started it before this response even arrived.
      } catch (error) {
        if (inputRef.current) inputRef.current.setText(previous);
        setInput(previous);
        notify(`queue failed: ${error instanceof Error ? error.message : String(error)}`);
      } finally {
        inputRef.current?.focus();
      }
    },
    [baseUrl, notify, openLog],
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
        `${baseUrl}/api/tasks/${encodeURIComponent(task.id)}/cancel`,
        { method: "POST" },
      );
      if (!response.ok) notify(`cancel failed (${response.status})`);
      else notify("cancellation requested");
    } catch (error) {
      notify(`cancel failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }, [baseUrl, notify, tasks]);

  useKeyboard((key: KeyEvent) => {
    // Every key is input: an arrow in a finished log restarts the auto view changer's clock.
    bumpActivity();
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
    if (key.ctrl && key.name === "o") {
      // Rearrange the board in place: bands and the tasks inside them, never the server's
      // data. One press is one arrangement, announced on the hairline like any other toast.
      key.preventDefault();
      key.stopPropagation();
      const next = nextOrder(order);
      setOrder(next);
      notify(`order: ${orderLabel(next)}`);
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

  // The box grows with the text, whether it wraps on its own or the user presses Enter,
  // up to PROMPT_MAX_ROWS; past that the textarea scrolls internally.
  const promptHeight = Math.max(1, Math.min(PROMPT_MAX_ROWS, promptRows));
  const overflow =
    promptRows > PROMPT_MAX_ROWS
      ? ` ${Math.min(promptRow + 1, PROMPT_MAX_ROWS)}/${PROMPT_MAX_ROWS}`
      : "";

  const showingLog = view === "log" && selectedTask !== null;
  const logTask = showingLog ? selectedTask : null;

  // Top bar, the breath under it, the hairline, the prompt, and the log's status line
  // when it is showing.
  const rowBudget = Math.max(1, height - 3 - promptHeight - (showingLog ? 1 : 0));
  const focusRow = selectedRowIndex(listRows, selected);
  // Carry the window from the previous render; it only moves when the reader moves the
  // selection (one row at an edge) or the followed task's index changes under it.
  const scroll = followSelection(
    scrollRef.current,
    listRows.length,
    rowBudget,
    selected,
    focusRow,
  );
  scrollRef.current = scroll.state;
  const winStart = scroll.start;
  const winSize = scroll.size;
  const visible = listRows.slice(winStart, winStart + winSize);

  // The hairline reports why the socket/last action matters, then where the board is in a
  // list that overflows. The range keeps the reader oriented without a second chrome row.
  const statusNote = !connected
    ? "reconnecting"
    : toast
      ? clip(toast, Math.max(8, width - 24))
      : server && !server.hasKey
        ? "no api key"
        : "";
  // The server is running code older than the tree: only a restart clears it, so the hairline
  // names the command once. The masthead carries the word; this is the fix.
  const staleNote = server?.stale ? "stale code · bin/code-stop" : "";
  const scrollNote =
    !showingLog && listRows.length > winSize
      ? `rows ${winStart + 1}–${winStart + winSize}/${listRows.length}`
      : "";
  // The arrangement is quiet chrome: it appears only while it differs from the default board,
  // so the hairline does not narrate the normal case.
  const orderNote = order === "status" ? "" : `order: ${orderLabel(order)}`;
  const note = [statusNote, staleNote, orderNote, scrollNote]
    .filter((part, index, all) => Boolean(part) && all.indexOf(part) === index)
    .join(" · ");

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
          bumpActivity();
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
      {logTask ? <LogHeader task={logTask} width={width} now={now} check={logCheck} /> : null}
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
    <box
      style={{ width: "100%", height: "100%", flexDirection: "column" }}
      onMouseDown={bumpActivity}
      onMouseScroll={bumpActivity}
    >
      <TopBar
        workspace={workspace}
        model={server?.model}
        effort={server?.effort}
        running={runningCount}
        total={ordered.length}
        connected={connected}
        stale={server?.stale ?? false}
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
