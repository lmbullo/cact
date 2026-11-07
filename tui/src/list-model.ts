/**
 * Pure list model: ordering, rows, selection stepping, scroll window. No React, no I/O —
 * this is what tui/tests/list.test.tsx exercises directly.
 *
 * WHAT IT IS
 *  The board's layout logic as pure functions: task grouping, section order, selection
 *  stepping and the visible scroll window.
 *
 * ENTRY POINTS
 *  STATUS_ORDER                         running -> queued -> done -> partial -> failed -> cancelled
 *  orderTasks(tasks)                    stable sort by rank, then createdAt (oldest first)
 *  taskHandle(id)                       short display handle for a task id
 *  buildRows(ordered) -> ListRow[]      header + task rows (what the board renders)
 *  stepSelection(ordered, current, d)   move the highlight, clamped
 *  selectedRowIndex(rows, selected)     row index for the highlighted task (-1 when absent)
 *  scrollWindow(rowCount, budget, focus) visible slice {start, size} keeping focus in view
 *  taskProgress(task)                   live `steps / tokens` label ("" until one is known)
 *  taskDuration(task, now)              live/final wall time label ("" until it starts)
 *
 * INVARIANTS
 *  - Deterministic and side-effect free: same input, same output, no clock. `taskDuration`
 *    takes `now` as an argument for the same reason.
 *  - Ties are broken by `createdAt`, so a re-render never reshuffles the list.
 *  - `scrollWindow` never returns a negative start and never exceeds rowCount.
 *
 * WHERE TO CHANGE
 *  Section order or row shape ... STATUS_ORDER, rank, buildRows
 *  Keyboard movement ............ stepSelection
 *  Visible window maths ......... scrollWindow
 *  A row's duration label ....... taskDuration
 */
import type { Task, TaskStatus } from "./types";
import { elapsed } from "./theme";

/** Section order: live work first, then finished work; oldest first inside a section. */
export const STATUS_ORDER: TaskStatus[] = [
  "running",
  "queued",
  "done",
  "partial",
  "failed",
  "cancelled",
];

function rank(task: Task): number {
  const index = STATUS_ORDER.indexOf(task.status);
  return index < 0 ? STATUS_ORDER.length : index;
}

/** `N steps · M tok` for a task: live `progress` first, final counters as a fallback. */
export function taskProgress(task: Task): string {
  const steps = task.progress?.steps ?? task.steps;
  const tokens = task.progress?.tokens ?? task.usage?.total_tokens;
  return [
    steps !== undefined ? `${steps} steps` : "",
    tokens ? `${tokens} tok` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * Wall time for a row tail: finished tasks use their recorded span, a running task uses
 * `now - startedAt`, and anything without a start reads empty so the column stays silent.
 */
export function taskDuration(task: Task, now: number): string {
  const start = task.startedAt;
  if (!start) return task.wallMs !== undefined ? elapsed(task.wallMs) : "";
  const end = task.finishedAt ?? (task.status === "running" ? now : undefined);
  if (end === undefined || end < start) {
    return task.wallMs !== undefined ? elapsed(task.wallMs) : "";
  }
  return elapsed(end - start);
}

/** Board order: status band first (running, queued, done, failed), then oldest task first. */
export function orderTasks(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    const diff = rank(a) - rank(b);
    return diff !== 0 ? diff : a.createdAt - b.createdAt;
  });
}

/** Rows are labelled with the stable task id, never with a position that moves. */
export function taskHandle(id: string): string {
  return id;
}

/** A board row: a status band header, or one task. */
export type ListRow =
  | { kind: "section"; status: TaskStatus; count: number }
  | { kind: "task"; task: Task };

/** Flat render list: one header per status band, then that band's tasks. */
export function buildRows(ordered: Task[]): ListRow[] {
  const rows: ListRow[] = [];
  for (const status of STATUS_ORDER) {
    const group = ordered.filter((task) => task.status === status);
    if (group.length === 0) continue;
    rows.push({ kind: "section", status, count: group.length });
    for (const task of group) rows.push({ kind: "task", task });
  }
  return rows;
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

/** First visible row so the selected task stays on screen. */
export function scrollWindow(rowCount: number, budget: number, focus: number): { start: number; size: number } {
  const size = Math.max(1, Math.min(rowCount, budget));
  let start = Math.max(0, rowCount - size);
  if (focus >= 0 && (focus < start || focus >= start + size)) {
    start = Math.max(0, Math.min(focus - Math.floor(size / 2), rowCount - size));
  }
  return { start, size };
}