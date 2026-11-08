/**
 * Theme and row telemetry: the pure text helpers the board depends on.
 *
 * WHAT IT IS
 *  Direct tests for `elapsed` (the duration grammar of the row tail and the log gutter),
 *  `shortPath` (the `~` abbreviation in the top bar), `wrapText` (the run log's final summary
 *  is shown whole, so it wraps instead of clipping), `taskDuration` (live for a running task,
 *  recorded for a finished one, silent before a task starts) and `taskCounters`/`taskUsage`
 *  (the one place a task's steps, in/out tokens, cache rate and context window are derived).
 *  No React and no server: these are the functions that decide what the columns say.
 *
 * ENTRY POINTS
 *  elapsed / shortPath / wrapText from ../src/theme; taskDuration, taskUsage, taskCounters,
 *  foldTurn, formatTokens from ../src/list-model
 *
 * INVARIANTS
 *  - `taskDuration` is never handed a clock: the caller passes `now`, so these cases are exact.
 *  - `taskUsage` prefers live `progress` and falls back to the final `steps`/`usage`; a number
 *    that is unknown stays `undefined` so no view prints a zero place-holder.
 *  - `taskCounters` prints tokens in then out, the cache hit rate only when the split is
 *    known, and the context window only against a window.
 *  - `foldTurn` sums the tokens and the cache split over turns but keeps the latest prompt as
 *    the context; it is what server.ts calls, so the live numbers come from tested code.
 *  - `shortPath` only rewrites a real `$HOME` prefix; an unrelated absolute path is unchanged.
 *  - `wrapText` keeps every word: no line exceeds the width, and past `maxLines` it reports
 *    the overflow instead of dropping it.
 *
 * WHERE TO CHANGE
 *  The duration grammar .... src/theme.ts (elapsed)
 *  The summary wrapping ..... src/theme.ts (wrapText)
 *  The live/final choice ... src/list-model.ts (taskUsage, taskDuration)
 *  A counter's wording ...... src/list-model.ts (taskCounters)
 *  A turn's live counters ... src/list-model.ts (foldTurn)
 */
import { expect, test } from "bun:test";
import { elapsed, shortPath, wrapText } from "../src/theme";
import { foldTurn, formatTokens, taskCounters, taskDuration, taskUsage } from "../src/list-model";

test("elapsed keeps durations compact and never negative", () => {
  expect(elapsed(0)).toBe("0ms");
  expect(elapsed(800)).toBe("800ms");
  expect(elapsed(1500)).toBe("1.5s");
  expect(elapsed(12_000)).toBe("12s");
  expect(elapsed(65_000)).toBe("1m05s");
  expect(elapsed(3_720_000)).toBe("1h02m");
  expect(elapsed(-5)).toBe("");
});

test("shortPath abbreviates only the home prefix", () => {
  const home = process.env.HOME;
  if (!home) return;
  expect(shortPath(home)).toBe("~");
  expect(shortPath(`${home}/some/project`)).toBe("~/some/project");
  expect(shortPath("/var/tmp/x")).toBe("/var/tmp/x");
});

test("taskDuration is live while running and recorded once finished", () => {
  const base = { id: "t-1", prompt: "p", createdAt: 1 } as const;
  expect(taskDuration({ ...base, status: "queued" }, 50_000)).toBe("");
  expect(taskDuration({ ...base, status: "running", startedAt: 1_000 }, 13_000)).toBe("12s");
  expect(taskDuration({ ...base, status: "done", startedAt: 1_000, finishedAt: 5_000 }, 0)).toBe("4.0s");
  expect(taskDuration({ ...base, status: "failed", wallMs: 2_500 }, 0)).toBe("2.5s");
});

test("taskCounters derives in/out tokens, cache rate and context from one place", () => {
  const base = { id: "t-1", prompt: "p", status: "running", createdAt: 1 } as const;
  const live = {
    ...base,
    progress: {
      steps: 3,
      tokensIn: 4210,
      tokensOut: 310,
      cacheHit: 3400,
      cacheMiss: 810,
      context: 4210,
      window: 1_048_576,
    },
  };
  expect(taskCounters(live)).toEqual(["3 steps", "↑4.2k ↓310", "<$0.01", "81% cache", "ctx 4.2k/1.0M"]);

  // A finished task falls back to the final counters, which are the same numbers: totals over
  // turns for tokens and the cache split, the kept last-turn context for the window.
  const done = {
    ...base,
    status: "done",
    steps: 7,
    usage: {
      prompt_tokens: 9000,
      completion_tokens: 450,
      prompt_cache_hit_tokens: 6000,
      prompt_cache_miss_tokens: 3000,
    },
    context: 4210,
    window: 1_048_576,
  } as const;
  expect(taskUsage(done)).toEqual({
    steps: 7,
    tokensIn: 9000,
    tokensOut: 450,
    cacheHit: 6000,
    cacheMiss: 3000,
    context: 4210,
    window: 1_048_576,
  });
  // The price follows the tokens it is computed from, before the cache rate and the context.
  expect(taskCounters(done)).toEqual(["7 steps", "↑9.0k ↓450", "<$0.01", "67% cache", "ctx 4.2k/1.0M"]);

  // Nothing known prints nothing: no `0 steps`, no `0% cache`.
  expect(taskCounters(base)).toEqual([]);
  // A cache split where nothing was cached is still a rate.
  expect(taskCounters({ ...base, progress: { cacheHit: 0, cacheMiss: 100 } })).toEqual(["<$0.01", "0% cache"]);
  // A context with no window reports the fill alone.
  expect(taskCounters({ ...base, progress: { context: 900 } })).toEqual(["ctx 900"]);
});

test("foldTurn accumulates a turn's usage into the live counters", () => {
  const first = foldTurn(undefined, 1, {
    usage: { prompt_tokens: 1000, completion_tokens: 200 },
    cache: { hit: 800, miss: 200 },
    window: 1_048_576,
  });
  expect(first).toEqual({
    steps: 1,
    tokensIn: 1000,
    tokensOut: 200,
    cacheHit: 800,
    cacheMiss: 200,
    context: 1000,
    window: 1_048_576,
  });

  // A second turn adds the tokens and the split, but replaces the context: the window holds the
  // latest prompt, not the sum of every prompt a run ever sent.
  const second = foldTurn(first, 2, {
    usage: { prompt_tokens: 1500, completion_tokens: 300 },
    cache: { hit: 1400, miss: 100 },
  });
  expect(second.tokensIn).toBe(2500);
  expect(second.tokensOut).toBe(500);
  expect(second.cacheHit).toBe(2200);
  expect(second.cacheMiss).toBe(300);
  expect(second.context).toBe(1500);
  expect(second.steps).toBe(2);
  // A turn without a window keeps the one we already know.
  expect(second.window).toBe(1_048_576);

  // A cache-only payload (no usage block) still yields a prompt size and a split.
  const bare = foldTurn(undefined, 1, { cache: { hit: 640, miss: 160 } });
  expect(bare.tokensIn).toBe(800);
  expect(bare.tokensOut).toBe(0);
  expect(bare.context).toBe(800);
});

test("formatTokens keeps a count inside a row's width", () => {
  expect(formatTokens(0)).toBe("0");
  expect(formatTokens(950)).toBe("950");
  expect(formatTokens(1234)).toBe("1.2k");
  expect(formatTokens(12_345)).toBe("12k");
  expect(formatTokens(1_048_576)).toBe("1.0M");
});

test("wrapText keeps every word of a summary and never exceeds the width", () => {
  const words = "the agent changed three files and verified them with bun test";
  const { lines, dropped } = wrapText(words, 20);
  expect(dropped).toBe(0);
  expect(lines.every((line) => line.length <= 20)).toBe(true);
  expect(lines.join(" ")).toBe(words);

  // Paragraph breaks survive as blank rows; leading/trailing blanks are dropped.
  const paragraphs = wrapText("\nfirst\n\nsecond\n", 40);
  expect(paragraphs.lines).toEqual(["first", "", "second"]);

  // An unbroken run has no space to break on, so it hard-wraps.
  const hard = wrapText("x".repeat(50), 10);
  expect(hard.lines).toEqual(["x".repeat(10), "x".repeat(10), "x".repeat(10), "x".repeat(10), "x".repeat(10)]);

  // Past maxLines the overflow is counted, never swallowed.
  const long = wrapText(Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n"), 40, 10);
  expect(long.lines.length).toBe(10);
  expect(long.dropped).toBe(20);
});
