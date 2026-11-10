/**
 * list-usage.ts: one task's numbers — live progress, counters, price and duration.
 * WHAT IT IS
 *   The derivation half of the list model: a `turn` event folded into the live `progress`, the
 *   single `taskUsage` read every view formats, the row counters and the wall-time label.
 * ENTRY POINTS
 *   TaskUsage                        the numbers one task reports, already reconciled
 *   foldTurn(previous, turn, data)   one turn event folded into the live `progress`
 *   taskUsage(task), taskCost(task)  live `progress` / final `usage`, and the row price (`$0.07`)
 *   formatTokens(count)              `950`, `4.2k`, `12k`, `1.0M`
 *   taskCounters(task), taskProgress(task), taskDuration(task, now)
 * INVARIANTS
 *   `taskUsage` is the only place a counter is read: the board row, the log header and the closing
 *   block all format what it returns, so two views cannot disagree.
 *   the price is not a counter: taskCost asks pricing.ts and both views get it from taskCounters.
 *   live `progress` wins only while it exists; the server clears it with the `final` event, so a
 *   stale estimate can never outrank the final `steps`/`usage`.
 *   `taskDuration` takes `now` as an argument: no clock, no side effect.
 *   `list-model.ts` re-exports every name here; import from there, not from this module.
 * WHERE TO CHANGE
 *   a task's numbers .......... taskUsage (read this, not Task fields, in a view)
 *   what a turn adds .......... foldTurn
 *   how a number is shown ..... formatTokens, taskCounters, taskDuration
 */
import type { Task } from "./types";
import { cost, formatCost } from "./pricing";
import { elapsed } from "./theme";

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
