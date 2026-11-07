/**
 * Theme and row telemetry: the pure text helpers the new board depends on.
 *
 * WHAT IT IS
 *  Direct tests for `elapsed` (the duration grammar of the row tail and the log gutter),
 *  `shortPath` (the `~` abbreviation in the top bar) and `taskDuration` (live for a running
 *  task, recorded for a finished one, silent before a task starts). No React and no server:
 *  these are the functions that decide what the columns say.
 *
 * ENTRY POINTS
 *  elapsed / shortPath from ../src/theme; taskDuration from ../src/list-model
 *
 * INVARIANTS
 *  - `taskDuration` is never handed a clock: the caller passes `now`, so these cases are exact.
 *  - `shortPath` only rewrites a real `$HOME` prefix; an unrelated absolute path is unchanged.
 *
 * WHERE TO CHANGE
 *  The duration grammar .... src/theme.ts (elapsed)
 *  The live/final choice ... src/list-model.ts (taskDuration)
 */
import { expect, test } from "bun:test";
import { elapsed, shortPath } from "../src/theme";
import { taskDuration } from "../src/list-model";

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
