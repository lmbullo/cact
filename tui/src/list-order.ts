/**
 * list-order.ts: the board's arrangement — bands, ordering, rows and selection.
 * WHAT IT IS
 *   The layout half of the list model as pure functions: status band orders, the arrangement cycle,
 *   the flat row list the board renders and selection stepping over it. No React, no I/O.
 * ENTRY POINTS
 *   STATUS_ORDER, TRIAGE_ORDER; BOARD_ORDERS, BoardOrder, BoardOrderPreset (the order key cycles)
 *   bandOrder(order), nextOrder(order), orderLabel(order)
 *   orderTasks(tasks, order)  stable sort by band rank, then createdAt
 *   taskHandle(id); ListRow, buildRows(ordered, bands)  handle + header/task rows
 *   isFinishedStatus(status), nextRunningTaskId(ordered, current), stepSelection(ordered, current, d),
 *     selectedRowIndex(rows, selected)
 * INVARIANTS
 *   deterministic and side-effect free: same input, same output, no clock.
 *   ties are broken by `createdAt`, so a re-render never reshuffles the list.
 *   the arrangement is display-only: orderTasks and buildRows take it as an argument and the
 *   server's task list is never rewritten, so a snapshot cannot undo a reorder.
 *   `order === "status"` is the default board: live work first, oldest task first in a band.
 *   `list-model.ts` re-exports every name here; import from there, not from this module.
 * WHERE TO CHANGE
 *   section order or row shape .... STATUS_ORDER, TRIAGE_ORDER, BOARD_ORDERS, bandOrder, buildRows
 *   what the order key cycles ..... BOARD_ORDERS, nextOrder, orderLabel
 *   keyboard movement ............. stepSelection;  auto-advance ... isFinishedStatus, nextRunningTaskId
 */
import type { Task, TaskStatus } from "./types";

/** Section order: live work first, then finished work; oldest first inside a section. */
export const STATUS_ORDER: TaskStatus[] = [
  "running",
  "queued",
  "done",
  "partial",
  "failed",
  "cancelled",
];

/**
 * The band order for problems first: everything that did not finish clean (cancelled, failed,
 * partial) above the live work, and `done` last. It is the same set as STATUS_ORDER, reversed
 * in priority, so no status can be dropped from the board.
 */
export const TRIAGE_ORDER: TaskStatus[] = [
  "cancelled",
  "failed",
  "partial",
  "running",
  "queued",
  "done",
];

/** How the board arranges its status bands and the tasks inside them. */
export type BoardOrder = "status" | "recent" | "triage";

/** One arrangement in the order cycle, with the word the hairline prints for it. */
export type BoardOrderPreset = { order: BoardOrder; label: string };

/** The arrangements the order key cycles through, in press order; the first is the default. */
export const BOARD_ORDERS: BoardOrderPreset[] = [
  { order: "status", label: "by status" },
  { order: "recent", label: "newest first" },
  { order: "triage", label: "problems first" },
];

/** The status bands one arrangement draws, in the order the board renders them. */
export function bandOrder(order: BoardOrder): TaskStatus[] {
  return order === "triage" ? TRIAGE_ORDER : STATUS_ORDER;
}

/** The next arrangement in the cycle: by status -> newest first -> problems first -> by status. */
export function nextOrder(order: BoardOrder): BoardOrder {
  const index = BOARD_ORDERS.findIndex((preset) => preset.order === order);
  return BOARD_ORDERS[(index + 1) % BOARD_ORDERS.length]!.order;
}

/** The hairline word for an arrangement (`by status`, `newest first`, `problems first`). */
export function orderLabel(order: BoardOrder): string {
  return BOARD_ORDERS.find((preset) => preset.order === order)?.label ?? order;
}

/** A task's band position inside `bands`; an unknown status sorts after every known one. */
function bandRank(task: Task, bands: TaskStatus[]): number {
  const index = bands.indexOf(task.status);
  return index < 0 ? bands.length : index;
}

/**
 * Board order: the arrangement's band order first, then `createdAt` — oldest first for the
 * default board, newest first for `recent` and `triage` (the newest problem reads at the top).
 */
export function orderTasks(tasks: Task[], order: BoardOrder = "status"): Task[] {
  const bands = bandOrder(order);
  const newestFirst = order !== "status";
  return [...tasks].sort((a, b) => {
    const diff = bandRank(a, bands) - bandRank(b, bands);
    if (diff !== 0) return diff;
    return newestFirst ? b.createdAt - a.createdAt : a.createdAt - b.createdAt;
  });
}

/**
 * The short handle for a task id: the last dash-separated segment (`t-m3k9a1x2-4f7c1` ->
 * `4f7c1`). The timestamp half is already implied by the board's order, so the row shows the
 * only part that identifies the task; the full id stays in the state root and the API.
 */
export function taskHandle(id: string): string {
  const cut = id.lastIndexOf("-");
  return cut >= 0 && cut < id.length - 1 ? id.slice(cut + 1) : id;
}

/** A board row: a blank spacer row, a status band header, or one task. */
export type ListRow =
  | { kind: "space" }
  | { kind: "section"; status: TaskStatus; count: number }
  | { kind: "task"; task: Task };

/**
 * Flat render list: one header per status band, then that band's tasks, with one blank row
 * between bands. The blank is what makes the board read as sections instead of a table. The
 * bands come from the arrangement (`bandOrder`), so a reorder moves whole sections, not rows.
 */
export function buildRows(ordered: Task[], bands: TaskStatus[] = STATUS_ORDER): ListRow[] {
  const rows: ListRow[] = [];
  for (const status of bands) {
    const group = ordered.filter((task) => task.status === status);
    if (group.length === 0) continue;
    if (rows.length > 0) rows.push({ kind: "space" });
    rows.push({ kind: "section", status, count: group.length });
    for (const task of group) rows.push({ kind: "task", task });
  }
  return rows;
}

/**
 * A task that has left the live half of the board: it will emit no more turns, so a log left
 * open on it is a finished log. `queued` is not finished (it is about to start).
 */
export function isFinishedStatus(status: TaskStatus): boolean {
  return status !== "running" && status !== "queued";
}

/**
 * The next running task after `current` in `ordered`, wrapping past the end; null when the
 * board has no other live task. `ordered` is the board's arrangement, so "next" means the
 * next row the reader would have reached by stepping down the list.
 */
export function nextRunningTaskId(ordered: Task[], current: string | null): string | null {
  const start = current ? ordered.findIndex((task) => task.id === current) : -1;
  const count = ordered.length;
  for (let step = 1; step <= count; step += 1) {
    const task = ordered[(start + step) % count]!;
    if (task.status === "running" && task.id !== current) return task.id;
  }
  return null;
}

/** Next task id when moving `delta` rows through the ordered list. */
export function stepSelection(ordered: Task[], current: string | null, delta: number): string | null {
  if (ordered.length === 0) return null;
  const index = ordered.findIndex((task) => task.id === current);
  const next = index < 0 ? 0 : (index + delta + ordered.length) % ordered.length;
  return ordered[next]!.id;
}

/** Index of the selected task inside the flat row list, or -1. */
export function selectedRowIndex(rows: ListRow[], selected: string | null): number {
  if (!selected) return -1;
  return rows.findIndex((row) => row.kind === "task" && row.task.id === selected);
}
