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
 *  TRIAGE_ORDER                         cancelled -> failed -> partial -> running -> queued -> done
 *  BOARD_ORDERS                         the arrangements the order key cycles, with their labels
 *  bandOrder(order) -> TaskStatus[]      the bands one arrangement draws, in order
 *  nextOrder(order) -> BoardOrder        the next arrangement in the keybind cycle
 *  orderLabel(order) -> string           the hairline word for an arrangement (`newest first`)
 *  orderTasks(tasks, order)             stable sort by band rank, then createdAt (oldest or newest first)
 *  taskHandle(id)                       short `#suffix` display handle for a task id
 *  buildRows(ordered, bands) -> ListRow[]  header + task rows (what the board renders)
 *  stepSelection(ordered, current, d)   move the highlight, clamped
 *  selectedRowIndex(rows, selected)     row index for the highlighted task (-1 when absent)
 *  scrollWindow(rowCount, budget, focus, anchor)
 *                                       visible slice, moved just enough to reveal focus
 *  followSelection(previous, rowCount, budget, selected, focus)
 *                                       next slice + state: one-row scroll, stable on reorder
 *  taskUsage(task)                      the task's numbers: live `progress`, else `usage`
 *  foldTurn(previous, turn, data)       one turn event folded into the live `progress`
 *  formatTokens(count)                  `950`, `4.2k`, `12k`, `1.0M`
 *  taskCounters(task)                   `3 steps`, `↑4.2k ↓310`, `$0.07`, `62% cache`, `ctx 4.2k/1.0M`
 *  taskCost(task)                       the task's price as a row part (`$0.07`), "" when unpriced
 *  taskProgress(task)                   the board row's tail: the counters and the cache rate
 *  taskDuration(task, now)              live/final wall time label ("" until it starts)
 *  isFinishedStatus(status)             true once a task can no longer emit turns
 *  nextRunningTaskId(ordered, current)  the next running task after `current`, wrapping
 *
 * INVARIANTS
 *  - Deterministic and side-effect free: same input, same output, no clock. `taskDuration`
 *    takes `now` as an argument for the same reason.
 *  - Ties are broken by `createdAt`, so a re-render never reshuffles the list.
 *  - The arrangement is display-only: `orderTasks` and `buildRows` take it as an argument and
 *    the server's task list is never rewritten, so a snapshot cannot undo a reorder.
 *  - `order === "status"` is the default board: live work first, oldest task first in a band.
 *  - `taskUsage` is the only place a counter is read: the board row, the log header and the
 *    closing block all format what it returns, so two views cannot print different steps,
 *    tokens, price, cache rate or context for the same task.
 *  - The price is not a counter: `taskCost` asks pricing.ts for it, and both views get it from
 *    `taskCounters`, so the USD table and its one format stay in that module.
 *  - Live `progress` wins only while it exists; the server clears it with the `final` event,
 *    so a stale estimate can never outrank the final `steps`/`usage`.
 *  - `scrollWindow` never returns a negative start and never exceeds rowCount.
 *  - The window never recentres: a selection that moves one row past an edge scrolls the
 *    list by exactly one row, so the marker never jumps half a screen.
 *  - `followSelection` keeps the window put while the same task's index changes (a status
 *    band reorder) by pinning that task to the screen row it already occupied.
 *
 * WHERE TO CHANGE
 *  Section order or row shape ... STATUS_ORDER, TRIAGE_ORDER, BOARD_ORDERS, bandOrder, buildRows
 *  What the order key cycles ..... BOARD_ORDERS, nextOrder, orderLabel
 *  Keyboard movement ............ stepSelection
 *  Visible window maths ......... scrollWindow, followSelection
 *  A task's numbers ............. taskUsage (read this, not Task fields, in a view)
 *  What a turn adds to progress . foldTurn
 *  How a number is abbreviated .. formatTokens
 *  A row's duration label ....... taskDuration
 *  Auto-advance targets ......... isFinishedStatus, nextRunningTaskId
 */
import type { Task, TaskStatus } from "./types";
import { cost, formatCost } from "./pricing";
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

/**
 * The task's price as a row part (`$0.07`, `<$0.01` under a cent), or "" when its model has no
 * published price — pricing.ts owns the table and the format, this only feeds it the usage the
 * row already derived. A missing cache split is priced as a miss there.
 */
export function taskCost(task: Task): string {
  const usage = taskUsage(task);
  return formatCost(
    cost(
      {
        prompt_cache_hit_tokens: usage.cacheHit,
        prompt_cache_miss_tokens: usage.cacheMiss,
        prompt_tokens: usage.tokensIn,
        completion_tokens: usage.tokensOut,
      },
      task.model,
    ),
  );
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
 * instead of losing the whole label: steps, tokens in/out, price, cache hit rate, context
 * window. A part is omitted when its number is unknown, never printed as a zero place-holder;
 * the price follows the tokens it is computed from.
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
  const price = taskCost(task);
  if (price) parts.push(price);
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