/**
 * client-input.ts: the one keyboard table — what each key does on the board and on a log.
 *
 * WHAT IT IS
 *  `useBoardKeys`: Ctrl-C/Q quit, Ctrl-K cancel, Ctrl-R reconnect, Ctrl-O cycle the arrangement,
 *  up/down and Ctrl-N/P move, Enter opens or adds a line, Ctrl-Enter queues, Esc clears.
 *
 * ENTRY POINTS
 *  useBoardKeys(options)     the whole table; it only calls the actions the board passes in
 *
 * INVARIANTS
 *  - Every key bumps the activity counter first, so an arrow in a finished log restarts the
 *    auto view changer's idle clock.
 *  - A prompt of more than one row owns the arrows (they move its cursor, not the highlight),
 *    and ordinary keys fall through to the textarea rather than being swallowed.
 *  - Ctrl+Enter queues; plain Enter adds a line, and on an empty prompt on the log goes back.
 *
 * WHERE TO CHANGE
 *  The keyboard table .... useBoardKeys    The copy gesture ..... client-actions.ts
 */
import type { CliRenderer, KeyEvent, TextareaRenderable } from "@opentui/core";
import { useKeyboard } from "@opentui/react";
import type { Dispatch, SetStateAction } from "react";
import { nextOrder, orderLabel, type BoardOrder } from "./list-model";
import type { Task } from "./types";

/** What the keyboard table needs: the board's actions, its state and the prompt box. */
export interface BoardKeysOptions {
  /** Records one interaction before anything else looks at the key. */
  bumpActivity: () => void;
  /** The renderer Ctrl-C/Ctrl-Q destroys. */
  renderer: CliRenderer;
  /** Cancel the selected task (Ctrl-K). */
  cancelSelected: () => Promise<void>;
  /** Force one reconnect attempt (Ctrl-R). */
  bumpSocketEpoch: () => void;
  /** Toast writer, used for the reconnect and arrangement notices. */
  notify: (message: string) => void;
  /** The arrangement in view; Ctrl-O cycles it. */
  order: BoardOrder;
  /** Arrangement setter for Ctrl-O. */
  setOrder: Dispatch<SetStateAction<BoardOrder>>;
  /** Readable current view, for a handler that runs outside the render. */
  viewRef: { current: "list" | "log" };
  /** The prompt box: its text is what Enter and Ctrl-Enter act on. */
  inputRef: { current: TextareaRenderable | null };
  /** Prompt text, mirrored from the textarea. */
  input: string;
  /** Prompt state setter, for the Esc clear. */
  setInput: Dispatch<SetStateAction<string>>;
  /** Wrapped rows the prompt needs; more than one row owns the arrows. */
  promptRows: number;
  /** Cursor row reset on an Esc clear. */
  setPromptRow: Dispatch<SetStateAction<number>>;
  /** Row count reset on an Esc clear. */
  setPromptRows: Dispatch<SetStateAction<number>>;
  /** Queue the prompt as a new task (Ctrl-Enter, and Ctrl-Enter on the log). */
  queue: (text: string) => Promise<void>;
  /** Move the highlight by delta rows (arrows past the prompt, Ctrl-N/Ctrl-P). */
  cycleSelection: (delta: number) => void;
  /** Open the selected task's log (Enter on an empty prompt). */
  openLog: (id: string | null) => void;
  /** Back to the list (Enter or Esc on an empty prompt on the log). */
  closeLog: () => void;
  /** The board's current tasks, so Enter only opens a task that exists. */
  tasks: Task[];
  /** Readable selection: Enter, Esc and the mover all act on it. */
  selectedRef: { current: string | null };
  /** Selection setter, for the Esc clear. */
  selectTask: (id: string | null) => void;
}

/** The one keyboard table for both views; every key bumps the activity counter first. */
export function useBoardKeys({
  bumpActivity,
  renderer,
  cancelSelected,
  bumpSocketEpoch,
  notify,
  order,
  setOrder,
  viewRef,
  inputRef,
  input,
  setInput,
  promptRows,
  setPromptRow,
  setPromptRows,
  queue,
  cycleSelection,
  openLog,
  closeLog,
  tasks,
  selectedRef,
  selectTask,
}: BoardKeysOptions): void {
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
      bumpSocketEpoch();
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
}

