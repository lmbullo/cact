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
 *  taskHandle(id)                       short `#suffix` display handle for a task id
 *  buildRows(ordered) -> ListRow[]      header + task rows (what the board renders)
 *  stepSelection(ordered, current, d)   move the highlight, clamped
 *  selectedRowIndex(rows, selected)     row index for the highlighted task (-1 when absent)
 *  scrollWindow(rowCount, budget, focus, anchor)
 *                                       visible slice, moved just enough to reveal focus
 *  followSelection(previous, rowCount, budget, selected, focus)
 *                                       next slice + state: one-row scroll, stable on reorder
 *  taskUsage(task)                      the task's numbers: live `progress`, else `usage`
 *  foldTurn(previous, turn, data)       one turn event folded into the live `progress`
 *  formatTokens(count)                  `950`, `4.2k`, `12k`, `1.0M`
 *  taskCounters(task)                   `3 steps`, `↑4.2k ↓310`, `62% cache`, `ctx 4.2k/1.0M`
 *  taskProgress(task)                   the board row's tail: the counters and the cache rate
 *  taskDuration(task, now)              live/final wall time label ("" until it starts)
 *
 * INVARIANTS
 *  - Deterministic and side-effect free: same input, same output, no clock. `taskDuration`
 *    takes `now` as an argument for the same reason.
 *  - Ties are broken by `createdAt`, so a re-render never reshuffles the list.
 *  - `taskUsage` is the only place a counter is read: the board row, the log header and the
 *    closing block all format what it returns, so two views cannot print different steps,
 *    tokens, cache rate or context for the same task.
 *  - Live `progress` wins only while it exists; the server clears it with the `final` event,
 *    so a stale estimate can never outrank the final `steps`/`usage`.
 *  - `scrollWindow` never returns a negative start and never exceeds rowCount.
 *  - The window never recentres: a selection that moves one row past an edge scrolls the
 *    list by exactly one row, so the marker never jumps half a screen.
 *  - `followSelection` keeps the window put while the same task's index changes (a status
 *    band reorder) by pinning that task to the screen row it already occupied.
 *
 * WHERE TO CHANGE
 *  Section order or row shape ... STATUS_ORDER, rank, buildRows
 *  Keyboard movement ............ stepSelection
 *  Visible window maths ......... scrollWindow, followSelection
 *  A task's numbers ............. taskUsage (read this, not Task fields, in a view)
 *  What a turn adds to progress . foldTurn
 *  How a number is abbreviated .. formatTokens
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

/** The numbers one task reports, already reconciled: never render a raw `Task` counter. */
export type TaskUsage = {
  steps?: number;
  /** Prompt tokens the provider billed, summed over turns. */
  tokensIn?: number;
  /** Completion tokens the provider billed, summed over turns. */
  tokensOut?: number;
  cacheHit?: number;
  cacheMiss?: number;
  /** The latest turn's prompt size: what is in the context window right now. */
  context?: number;
  /** The model's context window, as the worker reports it. */
  window?: number;
};

/** A usable count, or undefined: the wire may carry nulls, strings or nothing at all. */
function count(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** A count as the wire may carry it (a number, a numeric string, or nothing). */
function amount(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

/**
 * One `turn` event folded into the live counters: the provider's usage gives the in/out tokens
 * and the cache split, and the latest prompt size is what the context window holds right now
 * (a size, not a sum). server.ts calls this for every turn, so a running task moves while the
 * final `steps`/`usage` are still unknown.
 */
export function foldTurn(
  previous: Task["progress"],
  turn: number,
  data: Record<string, unknown>,
): NonNullable<Task["progress"]> {
  const cache = (data.cache ?? {}) as Record<string, unknown>;
  const usage = (data.usage ?? {}) as Record<string, unknown>;
  const hit = amount(cache.hit) || amount(usage.prompt_cache_hit_tokens);
  const miss = amount(cache.miss) || amount(usage.prompt_cache_miss_tokens);
  const prompt = amount(usage.prompt_tokens) || hit + miss;
  const live = previous ?? {};
  return {
    steps: turn > 0 ? turn : live.steps,
    tokensIn: (live.tokensIn ?? 0) + prompt,
    tokensOut: (live.tokensOut ?? 0) + amount(usage.completion_tokens),
    cacheHit: (live.cacheHit ?? 0) + hit,
    cacheMiss: (live.cacheMiss ?? 0) + miss,
    context: prompt || live.context,
    window: amount(data.window) || live.window,
  };
}

/**
 * The one derivation of a task's numbers. Live `progress` (from turn events) wins while it
 * exists; the server clears it with the `final` event, so the final `steps`/`usage` and the
 * kept `context`/`window` take over without a second counter ever disagreeing.
 */
export function taskUsage(task: Task): TaskUsage {
  const live = task.progress ?? {};
  const usage = task.usage ?? {};
  return {
    steps: count(live.steps) ?? count(task.steps),
    tokensIn: count(live.tokensIn) ?? count(usage.prompt_tokens),
    tokensOut: count(live.tokensOut) ?? count(usage.completion_tokens),
    cacheHit: count(live.cacheHit) ?? count(usage.prompt_cache_hit_tokens),
    cacheMiss: count(live.cacheMiss) ?? count(usage.prompt_cache_miss_tokens),
    context: count(live.context) ?? count(task.context),
    window: count(live.window) ?? count(task.window),
  };
}

/** A count in the width a terminal row can spare: `950`, `4.2k`, `12k`, `1.0M`. */
export function formatTokens(value: number): string {
  if (value < 1000) return String(Math.round(value));
  const thousands = value / 1000;
  if (thousands < 1000) return `${thousands < 10 ? thousands.toFixed(1) : Math.round(thousands)}k`;
  return `${(thousands / 1000).toFixed(1)}M`;
}

/**
 * The counters one task reports, as separate parts so a narrow frame can drop the tail
 * instead of losing the whole label: steps, tokens in/out, cache hit rate, context window.
 * A part is omitted when its number is unknown, never printed as a zero place-holder.
 */
export function taskCounters(task: Task): string[] {
  const usage = taskUsage(task);
  const parts: string[] = [];
  if (usage.steps !== undefined) parts.push(`${usage.steps} steps`);
  const tokens = [
    usage.tokensIn !== undefined ? `↑${formatTokens(usage.tokensIn)}` : "",
    usage.tokensOut !== undefined ? `↓${formatTokens(usage.tokensOut)}` : "",
  ]
    .filter(Boolean)
    .join(" ");
  if (tokens) parts.push(tokens);
  const cached = (usage.cacheHit ?? 0) + (usage.cacheMiss ?? 0);
  if (cached > 0) parts.push(`${Math.round(((usage.cacheHit ?? 0) / cached) * 100)}% cache`);
  if (usage.context !== undefined) {
    parts.push(
      usage.window
        ? `ctx ${formatTokens(usage.context)}/${formatTokens(usage.window)}`
        : `ctx ${formatTokens(usage.context)}`,
    );
  }
  return parts;
}

/** The board row's tail: the counters and the cache rate, without the log-only context window. */
export function taskProgress(task: Task): string {
  return taskCounters(task)
    .filter((part) => !part.startsWith("ctx "))
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
 * between bands. The blank is what makes the board read as sections instead of a table.
 */
export function buildRows(ordered: Task[]): ListRow[] {
  const rows: ListRow[] = [];
  for (const status of STATUS_ORDER) {
    const group = ordered.filter((task) => task.status === status);
    if (group.length === 0) continue;
    if (rows.length > 0) rows.push({ kind: "space" });
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

/** Where the board's viewport is and which task it is following, kept between renders. */
export type ScrollState = { start: number; focus: number; selected: string | null };

const clamp = (value: number, low: number, high: number): number =>
  Math.max(low, Math.min(value, high));

/**
 * Move `start` only as far as needed to put `focus` inside the window: one row when the
 * selection crosses an edge, never a recentre. Already-visible focus leaves `start` alone.
 */
function reveal(start: number, size: number, maxStart: number, focus: number): number {
  if (focus < start) return clamp(focus, 0, maxStart);
  if (focus >= start + size) return clamp(focus - size + 1, 0, maxStart);
  return start;
}

/** Visible slice starting at `anchor` (the previous start), with `focus` revealed. */
export function scrollWindow(
  rowCount: number,
  budget: number,
  focus: number,
  anchor = 0,
): { start: number; size: number } {
  const size = Math.max(1, Math.min(rowCount, budget));
  const maxStart = Math.max(0, rowCount - size);
  if (size >= rowCount || focus < 0) {
    return { start: 0, size };
  }
  const start = reveal(clamp(anchor, 0, maxStart), size, maxStart, focus);
  return { start, size };
}

/**
 * The next board window. The viewport stays where it is unless the user moves the selection;
 * a moved selection scrolls by the minimum (one row at an edge), and a task whose index
 * changes under a live status update keeps its place on screen instead of pushing the list.
 * Returns the next state so the caller can carry it into the next render.
 */
export function followSelection(
  previous: ScrollState,
  rowCount: number,
  budget: number,
  selected: string | null,
  focus: number,
): { start: number; size: number; state: ScrollState } {
  const size = Math.max(1, Math.min(rowCount, budget));
  const maxStart = Math.max(0, rowCount - size);
  let start = clamp(previous.start, 0, maxStart);

  if (size < rowCount && focus >= 0) {
    if (selected !== previous.selected) {
      // The user (or the first snapshot) moved the highlight: reveal it minimally.
      start = reveal(start, size, maxStart, focus);
    } else if (focus !== previous.focus && previous.focus >= 0) {
      // Same task, new index after a status change: pin it to its old screen row.
      const screenRow = clamp(previous.focus - previous.start, 0, size - 1);
      start = clamp(focus - screenRow, 0, maxStart);
    }
  }

  start = clamp(start, 0, maxStart);
  return { start, size, state: { start, focus, selected } };
}