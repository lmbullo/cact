/**
 * Board scrolling: one-row edge movement, a persistent anchor and a stable row on reorder.
 *
 * WHAT IT IS
 *  Direct tests for the pure window maths in list-model.ts. They pin the feel of the board:
 *  the viewport never recentres, a selection that crosses an edge moves the list by one row,
 *  and a task whose index changes under a live status update keeps its screen row.
 *
 * ENTRY POINTS
 *  scrollWindow(rowCount, budget, focus, anchor)   a slice, anchored at the previous start
 *  followSelection(previous, rowCount, budget, selected, focus)  next slice + state
 *
 * INVARIANTS
 *  - `start` is always clamped to `[0, rowCount - size]`.
 *  - A focus already inside the window never changes `start`.
 *  - Repeated renders with the same selection and focus are idempotent.
 *
 * WHERE TO CHANGE
 *  Window behaviour .... src/list-model.ts
 *  What the board shows .. src/client.tsx (followSelection call)
 */
import { expect, test } from "bun:test";
import { followSelection, scrollWindow } from "../src/list-model";

const ROWS = 40;
const BUDGET = 10;

test("scrollWindow starts at the top and reveals a focus with one-row steps", () => {
  // A fresh board reads from the first row, not the bottom of the list.
  expect(scrollWindow(ROWS, BUDGET, 0)).toEqual({ start: 0, size: BUDGET });
  expect(scrollWindow(ROWS, BUDGET, -1)).toEqual({ start: 0, size: BUDGET });

  // Inside the window: the anchor stays put.
  expect(scrollWindow(ROWS, BUDGET, 5, 0).start).toBe(0);
  expect(scrollWindow(ROWS, BUDGET, 9, 0).start).toBe(0);

  // One row past the bottom edge scrolls exactly one row, never half a screen.
  expect(scrollWindow(ROWS, BUDGET, 10, 0).start).toBe(1);
  expect(scrollWindow(ROWS, BUDGET, 11, 1).start).toBe(2);

  // Above the window: the focus becomes the top row.
  expect(scrollWindow(ROWS, BUDGET, 10, 20).start).toBe(10);
  expect(scrollWindow(ROWS, BUDGET, 5, 30).start).toBe(5);
});

test("scrollWindow clamps the anchor and does not scroll a list that fits", () => {
  expect(scrollWindow(ROWS, BUDGET, 39, 999).start).toBe(ROWS - BUDGET);
  expect(scrollWindow(ROWS, BUDGET, 0, -5).start).toBe(0);
  expect(scrollWindow(4, 10, 2, 3)).toEqual({ start: 0, size: 4 });
});

test("followSelection scrolls minimally when the user moves the highlight", () => {
  const previous = { start: 10, focus: 15, selected: "a" };

  // Still inside the window: no movement at all.
  expect(followSelection(previous, ROWS, BUDGET, "b", 16).start).toBe(10);

  // Crossing the bottom edge moves the list by one row.
  const down = followSelection({ start: 10, focus: 19, selected: "a" }, ROWS, BUDGET, "b", 20);
  expect(down.start).toBe(11);

  // Crossing the top edge moves it back by one row.
  const up = followSelection({ start: 10, focus: 10, selected: "a" }, ROWS, BUDGET, "b", 9);
  expect(up.start).toBe(9);

  // A selection outside the window is revealed without recentring.
  const jump = followSelection({ start: 0, focus: 2, selected: "a" }, ROWS, BUDGET, "b", 30);
  expect(jump.start).toBe(21);
});

test("followSelection pins the same task to its screen row across a status reorder", () => {
  const previous = { start: 10, focus: 15, selected: "a" };
  // The task drops ten rows (running -> done); the window moves with it so the row the
  // reader is looking at stays at the same screen position.
  const moved = followSelection(previous, ROWS, BUDGET, "a", 25);
  expect(25 - moved.start).toBe(15 - previous.start);

  // A reorder near the top clamps at the first row instead of going negative.
  const top = followSelection({ start: 4, focus: 4, selected: "a" }, ROWS, BUDGET, "a", 0);
  expect(top.start).toBe(0);

  // A list that shrinks clamps the carried anchor to its new last page.
  const shrunk = followSelection({ start: 30, focus: 39, selected: "a" }, 12, BUDGET, "a", 11);
  expect(shrunk.start).toBe(2);
});

test("followSelection is stable when nothing follows and idempotent on re-render", () => {
  // No selection yet, or escape cleared it: the window keeps its place.
  expect(followSelection({ start: 12, focus: 15, selected: "a" }, ROWS, BUDGET, null, -1).start).toBe(12);

  const once = followSelection({ start: 0, focus: -1, selected: null }, ROWS, BUDGET, "a", 0);
  const twice = followSelection(once.state, ROWS, BUDGET, "a", 0);
  expect(twice.start).toBe(once.start);
  expect(twice.state).toEqual(once.state);
});
