/**
 * list-scroll.ts: the board viewport — the visible slice and how it follows the selection.
 *
 * WHAT IT IS
 *  The window maths of the list model: which rows are on screen, and where the window moves
 *  when the highlight steps past an edge or a task changes index under a live status update.
 *
 * ENTRY POINTS
 *  ScrollState                                     { start, focus, selected }, kept between renders
 *  scrollWindow(rowCount, budget, focus, anchor)   the visible slice, with `focus` revealed
 *  followSelection(previous, rowCount, budget, selected, focus)
 *                                                  the next slice + the state to carry forward
 *
 * INVARIANTS
 *  - `scrollWindow` never returns a negative start and never exceeds rowCount.
 *  - The window never recentres: a selection that moves one row past an edge scrolls the list
 *    by exactly one row, so the marker never jumps half a screen.
 *  - `followSelection` keeps the window put while the same task's index changes (a status band
 *    reorder) by pinning that task to the screen row it already occupied.
 *  - `list-model.ts` re-exports every name here; import from there, not from this module.
 *
 * WHERE TO CHANGE
 *  Visible window maths ..... scrollWindow, followSelection
 */
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