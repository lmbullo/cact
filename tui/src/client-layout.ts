/**
 * client-layout.ts: the frame around the panes — window size, hairline note, visible rows.
 *
 * WHAT IT IS
 *  One pure function over the render inputs: the prompt height and overflow marker, the row
 *  budget left for the content pane, the visible slice of the board and the hairline note.
 *  No React, no state; the caller carries `scroll.state` into the next render.
 *
 * ENTRY POINTS
 *  boardLayout(input) -> { ruleWidth, promptHeight, overflow, showingLog, logTask, rowBudget,
 *                          focusRow, scroll, visible, note }
 *
 * INVARIANTS
 *  - Layout is always [log header, log only] / content / prompt top line / prompt /
 *    prompt bottom line; the prompt box never moves between views, so it never remounts.
 *  - The window follows the selection through `followSelection` and never recentres.
 *  - The note is quiet chrome: connected and nothing to say = "", and the order only shows
 *    while it differs from the default board.
 *
 * WHERE TO CHANGE
 *  Heights and budgets .... rowBudget, promptHeight, overflow
 *  Hairline text .......... statusNote, staleNote, orderNote, scrollNote
 *  Visible slice .......... scroll, visible
 */
import type { ServerInfo, Task } from "./types";
import {
  followSelection,
  orderLabel,
  selectedRowIndex,
  type BoardOrder,
  type ListRow,
  type ScrollState,
} from "./list-model";
import { PROMPT_MAX_ROWS, clip } from "./theme";

/** Everything the frame needs from the render: sizes, view, selection and server state. */
export interface LayoutInput {
  /** Terminal width in columns. */
  width: number;
  /** Terminal height in rows. */
  height: number;
  /** Visual row of the prompt cursor, for the overflow marker. */
  promptRow: number;
  /** Wrapped rows the prompt needs, so the box grows with the text. */
  promptRows: number;
  /** The screen in view; a log only exists while a task is open. */
  view: "list" | "log";
  /** The selected task id, for the scroll window's anchor. */
  selected: string | null;
  /** The open task, or null when the selection resolves to none. */
  selectedTask: Task | null;
  /** Flat header + task rows the board window slices. */
  listRows: ListRow[];
  /** Whether a socket is open; false makes the hairline say `reconnecting`. */
  connected: boolean;
  /** The current toast, cleared by its own timer. */
  toast: string;
  /** The last server read, for the key and stale notes. */
  server: ServerInfo | null;
  /** The arrangement in view; only a non-default one is named on the hairline. */
  order: BoardOrder;
  /** The window carried from the previous render. */
  previous: ScrollState;
}

/** What one render draws with: the sizes, the visible slice and the hairline note. */
export interface BoardLayout {
  /** Width of the hairline; one column short of the terminal so it never wraps. */
  ruleWidth: number;
  /** Height of the prompt body (between its two lines), capped at PROMPT_MAX_ROWS. */
  promptHeight: number;
  /** The "2/5" cursor marker, or "" while the prompt fits. */
  overflow: string;
  /** True when a run log is on screen instead of the board. */
  showingLog: boolean;
  /** The task whose log is open, or null on the board. */
  logTask: Task | null;
  /** Rows left for the content pane once the chrome is paid for. */
  rowBudget: number;
  /** Screen row of the selected task, or -1. */
  focusRow: number;
  /** The visible window and the state to carry into the next render. */
  scroll: { start: number; size: number; state: ScrollState };
  /** The rows on screen this frame. */
  visible: ListRow[];
  /** The hairline note: status, stale code, arrangement, then the row range. */
  note: string;
}

/** Compute the frame around the panes: sizes, the visible slice and the hairline note. */
export function boardLayout({
  width,
  height,
  promptRow,
  promptRows,
  view,
  selected,
  selectedTask,
  listRows,
  connected,
  toast,
  server,
  order,
  previous,
}: LayoutInput): BoardLayout {
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

  // Top bar, the breath under it, the prompt's top and bottom lines, the prompt body, and
  // the log's status line when it is showing.
  const rowBudget = Math.max(1, height - 4 - promptHeight - (showingLog ? 1 : 0));
  const focusRow = selectedRowIndex(listRows, selected);
  // Carry the window from the previous render; it only moves when the reader moves the
  // selection (one row at an edge) or the followed task's index changes under it.
  const scroll = followSelection(previous, listRows.length, rowBudget, selected, focusRow);
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

  return {
    ruleWidth,
    promptHeight,
    overflow,
    showingLog,
    logTask,
    rowBudget,
    focusRow,
    scroll,
    visible,
    note,
  };
}
