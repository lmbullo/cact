/**
 * client-view.ts: the screen's own state — which task/screen is open, the toast, the prompt.
 *
 * WHAT IT IS
 *  The `useBoardViewState` hook: the local UI state that is not the server's and not the board's
 *  maths — the selection and view switch, the toast line, and the prompt's wrapped-row measure.
 *
 * ENTRY POINTS
 *  useBoardViewState(options) -> { toast, notify, promptRow, promptRows, setPromptRow,
 *                                  setPromptRows, measurePrompt, selectTask, openLog, closeLog }
 *  useSpinner(active) -> one braille frame while a run is live, else ""
 *
 * INVARIANTS
 *  - `selectTask` always clears the event buffer and its `seq` watermark: a new task may reuse
 *    the old seq numbers, and a stale one would swallow the first live events.
 *  - A toast replaces the previous one and clears itself after 3500 ms; nothing queues them.
 *  - Opening a log focuses the one prompt box on the next tick, after the pane swaps; so does
 *    closing it. The prompt box itself never unmounts.
 *  - The view switch is local: it never cancels, queues or re-orders anything on the server.
 *
 * WHERE TO CHANGE
 *  Selection and view ... selectTask, openLog, closeLog
 *  Toast lifetime ........ notify, toastTimer (3500 ms)
 *  Prompt and spinner .... measurePrompt, promptRow, promptRows, SPINNER_FRAMES, useSpinner
 */
import type { TextareaRenderable } from "@opentui/core";
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { TaskEvent } from "./types";
import { USES } from "./board-help";

/** What `useBoardViewState` needs: the boxes and refs its callbacks read and write. */
export interface BoardViewStateOptions {
  /** The one prompt box; opened/closed views nudge its focus. */
  inputRef: { current: TextareaRenderable | null };
  /** Readable current view, for a callback that runs outside the render. */
  viewRef: { current: "list" | "log" };
  /** Readable current selection, so open/close stay consistent with what is drawn. */
  selectedRef: { current: string | null };
  /** Highest event `seq` held, cleared whenever the selection changes. */
  eventsSeqRef: { current: number };
  /** Selection state setter. */
  setSelected: Dispatch<SetStateAction<string | null>>;
  /** Event buffer setter, cleared whenever the selection changes. */
  setEvents: Dispatch<SetStateAction<TaskEvent[]>>;
  /** Prompt state setter, kept in step with the textarea when a use case is loaded. */
  setInput: Dispatch<SetStateAction<string>>;
  /** View state setter. */
  setView: Dispatch<SetStateAction<"list" | "log">>;
}

/** The screen's local state: selection, view, toast and the prompt's measured geometry. */
export interface BoardViewState {
  /** The toast line, cleared by its own timer. */
  toast: string;
  /** Show a message on the hairline for 3500 ms, replacing any previous one. */
  notify: (message: string) => void;
  /** Visual (wrapped) row of the prompt cursor, used for the "3/5" overflow note. */
  promptRow: number;
  /** Wrapped rows the prompt needs, so the box grows when text runs past the width. */
  promptRows: number;
  /** Cursor-row setter, for the Esc clear. */
  setPromptRow: Dispatch<SetStateAction<number>>;
  /** Row-count setter, for the Esc clear. */
  setPromptRows: Dispatch<SetStateAction<number>>;
  /** Re-measure the prompt's wrapped rows after an edit, a cursor move or a resize. */
  measurePrompt: () => void;
  /** Load starter use case `index` into the prompt; never queues it. */
  applyExample: (index: number) => void;
  /** Select a task (or none): clears the event buffer and its watermark. */
  selectTask: (id: string | null) => void;
  /** Open the live run log for a task; only ever called from an explicit user action. */
  openLog: (id: string | null) => void;
  /** Back to the task list; stop streaming events for the task we leave. */
  closeLog: () => void;
}

/** The braille spinner frames, one per interval tick. */
const SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/** One animated spinner cell while `active`, or "" when nothing is running. */
export function useSpinner(active: boolean): string {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    if (!active) {
      setFrame(0);
      return;
    }
    const timer = setInterval(() => setFrame((index) => (index + 1) % SPINNER_FRAMES.length), 100);
    return () => clearInterval(timer);
  }, [active]);
  return active ? SPINNER_FRAMES[frame]! : "";
}

/** Own the selection, the view switch, the toast and the prompt measure for one board. */
export function useBoardViewState({
  inputRef,
  viewRef,
  selectedRef,
  eventsSeqRef,
  setSelected,
  setEvents,
  setInput,
  setView,
}: BoardViewStateOptions): BoardViewState {
  /** Visual (wrapped) row of the prompt cursor, used for the "3/5" overflow note. */
  const [promptRow, setPromptRow] = useState(0);
  /** Wrapped rows the prompt needs, so the box grows when text runs past the width. */
  const [promptRows, setPromptRows] = useState(1);
  const [toast, setToast] = useState("");
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
  }, [inputRef]);

  const notify = useCallback((message: string) => {
    setToast(message);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 3500);
  }, []);

  /** Load a starter use case into the prompt; the reader still presses ctrl+enter to run it. */
  const applyExample = useCallback(
    (index: number) => {
      const use = USES[index];
      if (!use) return;
      inputRef.current?.setText(use.prompt);
      setInput(use.prompt);
      setPromptRow(0);
      setPromptRows(1);
      notify("example loaded · ctrl+enter runs it");
      measurePrompt();
    },
    [inputRef, setInput, notify, measurePrompt],
  );

  const selectTask = useCallback(
    (id: string | null) => {
      selectedRef.current = id;
      eventsSeqRef.current = 0;
      setSelected(id);
      setEvents([]);
    },
    [selectedRef, eventsSeqRef, setSelected, setEvents],
  );

  const openLog = useCallback(
    (id: string | null) => {
      if (!id) return;
      viewRef.current = "log";
      selectTask(id);
      setView("log");
      // There is a single prompt box for both views, so this is only a focus nudge.
      setTimeout(() => inputRef.current?.focus(), 0);
    },
    [selectTask, viewRef, setView, inputRef],
  );

  const closeLog = useCallback(() => {
    viewRef.current = "list";
    setView("list");
    selectTask(null);
    // Same prompt box on both views: hand focus back after the content pane swaps.
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [selectTask, viewRef, setView, inputRef]);

  return {
    toast,
    notify,
    promptRow,
    promptRows,
    setPromptRow,
    setPromptRows,
    measurePrompt,
    applyExample,
    selectTask,
    openLog,
    closeLog,
  };
}
