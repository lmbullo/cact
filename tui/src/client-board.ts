/**
 * client-board.ts: the board's derived model — ordered tasks, rows, run state and idle clock.
 *
 * WHAT IT IS
 *  The `useBoardModel` hook: everything the board computes from `tasks`, `order`, `events` and
 *  the open selection. It derives, it never fetches; the only writes are the two view moves the
 *  auto view changer and the keep-a-row-selected rule perform through `openLog`/`closeLog`.
 *
 * ENTRY POINTS
 *  useBoardModel(options) -> { ordered, listRows, logCheck, selectedTask, runningCount, now,
 *                              cycleSelection }
 *
 * INVARIANTS
 *  - The server's task list is never rewritten: ordering and rows are display-only copies.
 *  - The wall clock ticks only while a task is running, so an idle board never re-renders.
 *  - The auto view changer moves only on a *finished* open task after `autoAdvanceMs` without
 *    an interaction, and a non-empty prompt (a draft) pauses it.
 *  - A row stays highlighted even before the first key, so Enter always has a target.
 *
 * WHERE TO CHANGE
 *  Ordering and rows .... orderTasks/buildRows (list-model.ts); this hook only calls them
 *  Auto view changer ..... the second effect     Wall clock ..... the first effect
 *  Keyboard stepping ..... cycleSelection
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CheckState, Task, TaskEvent } from "./types";
import {
  bandOrder,
  buildRows,
  isFinishedStatus,
  nextRunningTaskId,
  orderTasks,
  stepSelection,
  type BoardOrder,
  type ListRow,
} from "./list-model";
import { lastCheck } from "./log-view";

/** What `useBoardModel` needs: the server's tasks plus the reader's local view state. */
export interface BoardModelOptions {
  /** Every task the last snapshot listed; the single source of the board. */
  tasks: Task[];
  /** The arrangement the board is showing (Ctrl-O cycles it); display-only. */
  order: BoardOrder;
  /** The open log's events, folded for the header's last check. */
  events: TaskEvent[];
  /** The selected task id, or null when nothing is selected. */
  selected: string | null;
  /** The screen in view: the idle clock and the mover only act on "log". */
  view: "list" | "log";
  /** The prompt text; a non-empty prompt is a draft and pauses the auto view changer. */
  input: string;
  /** Interaction counter: every bump restarts the auto view changer's idle clock. */
  activity: number;
  /** Idle time before a finished log moves on (ms); tests shorten it. */
  autoAdvanceMs: number;
  /** Open a task's log (the mover's forward move). */
  openLog: (id: string | null) => void;
  /** Back to the list (the mover's move when no other task runs). */
  closeLog: () => void;
  /** Board selection setter: stepping and the keep-a-row-selected rule both move it. */
  selectTask: (id: string | null) => void;
  /** Readable selection, so the timer acts on the row selected when it fires. */
  selectedRef: { current: string | null };
}

/** The board's derived model, recomputed on every render from the server's tasks. */
export interface BoardModel {
  /** Tasks in board order; the selection and the rows are both spelled against this. */
  ordered: Task[];
  /** Flat header + task rows the board window slices. */
  listRows: ListRow[];
  /** How the open run's last verification ended, from its own events. */
  logCheck: CheckState | null;
  /** The open task, or null when the selection does not resolve to one. */
  selectedTask: Task | null;
  /** How many tasks are running, for the masthead and the live clock. */
  runningCount: number;
  /** Wall clock for live row durations; ticks only while something is running. */
  now: number;
  /** Move the highlight by `delta` rows, clamped by the pure stepping helper. */
  cycleSelection: (delta: number) => void;
}

/** Derive the board from the server's tasks: order, rows, run state, clock and mover. */
export function useBoardModel({
  tasks,
  order,
  events,
  selected,
  view,
  input,
  activity,
  autoAdvanceMs,
  openLog,
  closeLog,
  selectTask,
  selectedRef,
}: BoardModelOptions): BoardModel {
  const [now, setNow] = useState(() => Date.now());
  /** The current board arrangement, readable from a timer without making it an effect dep. */
  const orderedRef = useRef<Task[]>([]);

  const ordered = useMemo(() => orderTasks(tasks, order), [tasks, order]);
  orderedRef.current = ordered;
  // Hooks must run on every render, so the flat row list is built before the log view returns.
  const listRows = useMemo(() => buildRows(ordered, bandOrder(order)), [ordered, order]);
  /**
   * How the open run's last verification ended, derived from its own events on every batch: the
   * header names a failed check from the journal's newest `check` event, so the client keeps no
   * state (and a later pass simply stops the failure being drawn).
   */
  const logCheck = useMemo(() => lastCheck(events), [events]);
  const selectedTask = ordered.find((task) => task.id === selected) ?? null;
  const runningCount = ordered.filter((task) => task.status === "running").length;

  // A live clock, but only while a row can actually use it: an idle board must not re-render.
  const live = runningCount > 0;
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);

  // The auto view changer. A log on a finished task is a dead end, so after `autoAdvanceMs`
  // without an interaction it moves to the next running task's log, or back to the board when
  // no other task is running. Dependencies are the open task's identity and status (not its
  // streaming counters, so a live update cannot postpone the move) plus the activity counter
  // (every key, edit, click, scroll or drag restarts the clock). `orderedRef` is read inside
  // the timeout so the target is the board as it is when the timer fires, and a non-empty
  // prompt is a draft the reader is still writing: it pauses the mover until it is cleared.
  const hasDraft = input.trim().length > 0;
  useEffect(() => {
    if (view !== "log" || !selectedTask || !isFinishedStatus(selectedTask.status) || hasDraft) {
      return;
    }
    const timer = setTimeout(() => {
      const next = nextRunningTaskId(orderedRef.current, selectedRef.current);
      if (next) openLog(next);
      else closeLog();
    }, autoAdvanceMs);
    return () => clearTimeout(timer);
  }, [view, selectedTask?.id, selectedTask?.status, hasDraft, activity, autoAdvanceMs, openLog, closeLog]);

  const cycleSelection = useCallback(
    (delta: number) => {
      const next = stepSelection(ordered, selectedRef.current, delta);
      if (next) selectTask(next);
    },
    [ordered, selectTask],
  );

  // Keep a row highlighted even before the user presses a key, so Enter always has a target.
  useEffect(() => {
    if (view !== "list") return;
    if (ordered.length === 0) {
      if (selectedRef.current) selectTask(null);
      return;
    }
    if (ordered.some((task) => task.id === selectedRef.current)) return;
    const preferred = ordered.find((task) => task.status === "running") ?? ordered[0]!;
    selectTask(preferred.id);
  }, [ordered, selectTask, view]);

  return { ordered, listRows, logCheck, selectedTask, runningCount, now, cycleSelection };
}
