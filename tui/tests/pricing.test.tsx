/**
 * Price: what a run cost, computed from the usage the server already records.
 *
 * WHAT IT IS
 *  Two layers of the same fact. `cost`/`formatCost` from ../src/pricing are exercised on the
 *  three usage shapes a provider can send (a cache-heavy run, a fully missed run, and a record
 *  with no cache counters at all), and `TaskRow`/`LogHeader` are rendered at 60 and 100 columns
 *  to prove the board row and the log header print the same figure without wrapping.
 *
 * ENTRY POINTS
 *  cost / formatCost / priceFor from ../src/pricing
 *  TaskRow and LogHeader from ../src/board-view; testRender at 60x4 and 100x4
 *  `cached`/`missed`/`bare` ... the usage fixtures; `cachedTask`/`cheapTask` the row fixtures
 *
 * INVARIANTS
 *  - The $0.07 fixture is a real price for the table, not a rounded one: cache hit, miss and
 *    output are each priced with their own rate, so a change to one rate moves the number.
 *  - The no-cache fixture must cost exactly what the same tokens cost as misses: that is the
 *    documented conservative choice, and this is the test that pins it.
 *  - An unpriced model renders no price at all — never "$0.00", never a guessed figure.
 *  - A row at 60 columns is one row: the prompt gives up room, then the clock, and nothing
 *    wraps into a second line.
 *  - No server and no worker: the fixtures are plain `Task` objects.
 *
 * WHERE TO CHANGE
 *  A rate or the source date ....... ../src/pricing (PRICES)
 *  How usage becomes USD ........... ../src/pricing (cost)
 *  What a row/header prints ........ ../src/list-model (taskCounters), ../src/board-view
 */
import { expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { cost, formatCost, priceFor } from "../src/pricing";
import { LogHeader, TaskRow } from "../src/board-view";
import type { Task } from "../src/types";

const MODEL = "deepseek-flash";
const NOW = 1_000_000;

/** A cached run: 700k prompt tokens served from cache, 50k missed, 10k out. */
const cached = {
  prompt_cache_hit_tokens: 700_000,
  prompt_cache_miss_tokens: 50_000,
  prompt_tokens: 750_000,
  completion_tokens: 10_000,
};

/** The same prompt with no cache at all: every input token was a miss. */
const missed = {
  prompt_cache_hit_tokens: 0,
  prompt_cache_miss_tokens: 750_000,
  prompt_tokens: 750_000,
  completion_tokens: 10_000,
};

/** A provider that reports no cache split: only the whole prompt and the completion. */
const bare = { prompt_tokens: 750_000, completion_tokens: 10_000 };

test("a cached run prices hit, miss and output with their own rates", () => {
  // 0.7M * $0.07 + 0.05M * $0.27 + 0.01M * $1.10 = $0.0735 -> $0.07 on the row.
  const usd = cost(cached, MODEL)!;
  expect(usd).toBeCloseTo(0.0735, 6);
  expect(formatCost(usd)).toBe("$0.07");
  // The split matters: pricing the same prompt as one input rate would lose the discount.
  expect(usd).toBeLessThan(cost(missed, MODEL)!);
});

test("a fully missed run pays the miss rate for every prompt token", () => {
  // 0.75M * $0.27 + 0.01M * $1.10 = $0.2135 -> $0.21.
  const usd = cost(missed, MODEL)!;
  expect(usd).toBeCloseTo(0.2135, 6);
  expect(formatCost(usd)).toBe("$0.21");
});

test("a usage object with no cache fields is priced as all misses", () => {
  // The conservative choice: no cache counters means no discount is assumed.
  expect(cost(bare, MODEL)).toBeCloseTo(cost(missed, MODEL)!, 9);
  expect(formatCost(cost(bare, MODEL))).toBe("$0.21");
  // Nothing to price is not zero dollars: no number, no place-holder.
  expect(cost(undefined, MODEL)).toBeUndefined();
  expect(cost({}, MODEL)).toBeUndefined();
  expect(formatCost(undefined)).toBe("");
});

test("an unpriced model keeps the token count alone", () => {
  expect(priceFor("some-other-model")).toBeUndefined();
  expect(cost(cached, "some-other-model")).toBeUndefined();
  expect(formatCost(cost(cached, "some-other-model"))).toBe("");
  // Under a cent still reads as money, not as zero.
  expect(formatCost(cost({ prompt_cache_hit_tokens: 1_000 }, MODEL))).toBe("<$0.01");
});

/** The row under test: 3 steps, a cache-heavy 750k prompt, priced at $0.07. */
const cachedTask: Task = {
  id: "t-m3k9a1x2-4f7c1",
  prompt: "add a retry with exponential backoff and jitter to the fetch helper, and cover the 503 path",
  status: "running",
  createdAt: 1,
  startedAt: NOW - 11_000,
  model: MODEL,
  steps: 3,
  progress: {
    steps: 3,
    tokensIn: cached.prompt_tokens,
    tokensOut: cached.completion_tokens,
    cacheHit: cached.prompt_cache_hit_tokens,
    cacheMiss: cached.prompt_cache_miss_tokens,
    context: 421_000,
    window: 1_000_000,
  },
};

/** A run whose whole bill is under a cent, to pin the `<` format on the row. */
const cheapTask: Task = {
  id: "t-m3k9a1x2-9a2b0",
  prompt: "rename one symbol",
  status: "done",
  createdAt: 2,
  startedAt: NOW - 2_000,
  finishedAt: NOW,
  model: MODEL,
  steps: 1,
  usage: { prompt_cache_hit_tokens: 9_000, prompt_cache_miss_tokens: 0, prompt_tokens: 9_000, completion_tokens: 200 },
};

/** Render one row at `width` and return its single non-empty line. */
async function rowAt(width: number, task: Task): Promise<string> {
  const setup = await testRender(
    <TaskRow task={task} selected={false} width={width} now={NOW} onSelect={() => {}} />,
    { width, height: 4 },
  );
  await setup.flush();
  const rows = setup.captureCharFrame().split("\n").filter((row) => row.trim().length > 0);
  await setup.dispose();
  // A wrapped row would be a second line: the row itself is always one row.
  expect(rows.length).toBe(1);
  return rows[0]!;
}

test("the row keeps its price and one line at 60 columns", async () => {
  const line = await rowAt(60, cachedTask);
  expect(line).toContain("#4f7c1");
  expect(line).toContain("3 steps");
  expect(line).toContain("$0.07");
  // The price follows the tokens it was computed from, on the row as in the table.
  expect(line.indexOf("$0.07")).toBeGreaterThan(line.indexOf("↑750k"));
  // The prompt gave up its room, not the numbers.
  expect(line).not.toContain("exponential");
  expect(line.trimEnd().length).toBeLessThanOrEqual(60);
});

test("the row spends its width on the prompt first at 100 columns", async () => {
  const line = await rowAt(100, cachedTask);
  expect(line).toContain("$0.07");
  expect(line).toContain("add a retry with exponential");
  expect(line.trimEnd().length).toBeLessThanOrEqual(100);
});

test("a sub-cent run reads as money on the row, at both widths", async () => {
  for (const width of [60, 100]) {
    const line = await rowAt(width, cheapTask);
    expect(line).toContain("<$0.01");
    expect(line).not.toContain("$0.00");
    expect(line.trimEnd().length).toBeLessThanOrEqual(width);
  }
});

test("the log header prints the same price the row does", async () => {
  const setup = await testRender(<LogHeader task={cachedTask} width={100} now={NOW} />, {
    width: 100,
    height: 2,
  });
  await setup.flush();
  const rows = setup.captureCharFrame().split("\n").filter((row) => row.trim().length > 0);
  await setup.dispose();
  const header = rows[0]!;
  expect(header).toContain("● running");
  expect(header).toContain("esc/enter: back");
  expect(header).toContain("$0.07");
  expect(header.trimEnd().length).toBeLessThanOrEqual(100);
});
