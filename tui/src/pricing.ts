/**
 * Pricing: the one table of what a run costs, and the one place a price is computed.
 * WHAT IT IS
 *   USD per 1M tokens for the model server.ts runs (`CODE_TUI_MODEL`), split as the provider bills:
 *   prompt cache hit, prompt cache miss, completion; no view formats a price of its own.
 * ENTRY POINTS
 *   PRICES                 model -> {hit, miss, out} in USD per 1M tokens
 *   DEFAULT_MODEL          the model server.ts runs when CODE_TUI_MODEL is unset
 *   priceFor(model)        the table row, or undefined for a model with no published price
 *   cost(usage, model?)    USD for one usage record, or undefined when there is nothing to price
 *   formatCost(usd)        `$0.07`, `<$0.01` under a cent, "" when there is no price
 * INVARIANTS
 *   one table, one date: `PRICES` is the only place a price is written down; the README points here
 *   instead of copying numbers, so a price change has exactly one edit.
 *   never invent a price: a model not in `PRICES` yields undefined and the row keeps its token
 *   count alone; no default rate, no "estimated" figure.
 *   cache-hit, cache-miss and output tokens are priced separately and per 1M, as the provider bills
 *   them; folding hit and miss would misprice a cached run.
 *   missing cache counters mean all input is priced as miss, so a provider that stops reporting the
 *   split cannot make a run look cheaper than it was.
 * WHERE TO CHANGE
 *   a price or the source date ..... PRICES (move the date with the numbers)
 *   how usage becomes USD .......... cost
 *   how a price is printed ......... formatCost
 */

/** The billed rates for one model, in USD per 1M tokens. */
export type Price = {
  /** Prompt tokens the provider served from its own cache. */
  hit: number;
  /** Prompt tokens it had to process (a cache miss). */
  miss: number;
  /** Completion (output) tokens. */
  out: number;
};

/**
 * USD per 1M tokens, from DeepSeek's published price list of 2025-02-09
 * (https://api-docs.deepseek.com/quick_start/pricing). `deepseek-flash` is the alias this
 * repo's worker sends (bench/agent/llm.py) to the DeepSeek chat endpoint, so it shares the
 * chat rates. Renew the numbers and this date together.
 */
export const PRICES: Record<string, Price> = {
  "deepseek-flash": { hit: 0.07, miss: 0.27, out: 1.1 },
  "deepseek-chat": { hit: 0.07, miss: 0.27, out: 1.1 },
  "deepseek-reasoner": { hit: 0.14, miss: 0.55, out: 2.19 },
};

/** What server.ts runs when `CODE_TUI_MODEL` is unset; the default for a model-less usage. */
export const DEFAULT_MODEL = "deepseek-flash";

/** The provider's usage record, as `WorkerMessage.turn`/`final` carry it. */
export type Usage = {
  prompt_cache_hit_tokens?: number;
  prompt_cache_miss_tokens?: number;
  /** The whole prompt: hit + miss, used when the cache split is missing. */
  prompt_tokens?: number;
  completion_tokens?: number;
};

/** A usable token count, or undefined: the wire may carry nulls, strings or nothing at all. */
function tokens(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}

/** The table row for a model, or undefined: an unknown model is unpriced, never guessed. */
export function priceFor(model: string | undefined): Price | undefined {
  return model ? PRICES[model] : undefined;
}

/**
 * USD for one usage record on `model` (the running model when the caller does not know it).
 * Returns undefined when the model has no price or there are no tokens; a missing cache split
 * prices the whole prompt as a miss.
 */
export function cost(usage: Usage | undefined, model: string = DEFAULT_MODEL): number | undefined {
  const price = priceFor(model);
  if (!price || !usage) return undefined;
  const hit = tokens(usage.prompt_cache_hit_tokens) ?? 0;
  const prompt = tokens(usage.prompt_tokens);
  const miss =
    tokens(usage.prompt_cache_miss_tokens) ?? (prompt !== undefined ? Math.max(0, prompt - hit) : 0);
  const out = tokens(usage.completion_tokens) ?? 0;
  if (hit + miss + out <= 0) return undefined;
  return (hit * price.hit + miss * price.miss + out * price.out) / 1_000_000;
}

/** A price as a row tail: `$0.07`, `<$0.01` under a cent, "" when there is nothing to print. */
export function formatCost(usd: number | undefined): string {
  if (usd === undefined || !Number.isFinite(usd) || usd < 0) return "";
  if (usd <= 0) return "$0.00";
  if (usd < 0.01) return "<$0.01";
  return `$${usd.toFixed(2)}`;
}
