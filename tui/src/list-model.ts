/**
 * Pure list model: ordering, rows, selection stepping, scroll window. No React, no I/O.
 *
 * WHAT IT IS
 *  Layout logic split by concern: arrangement/rows/selection (list-order.ts), one task's
 *  numbers (list-usage.ts), visible window (list-scroll.ts). This is the one import path.
 *
 * ENTRY POINTS
 *  Re-exports list-order.ts, list-usage.ts, list-scroll.ts: STATUS_ORDER, TRIAGE_ORDER,
 *  BOARD_ORDERS, bandOrder, nextOrder, orderLabel, orderTasks, taskHandle, buildRows,
 *  stepSelection, selectedRowIndex, scrollWindow, followSelection, taskUsage, foldTurn,
 *  formatTokens, taskCounters, taskCost, taskProgress, taskDuration, isFinishedStatus,
 *  nextRunningTaskId, and their types.
 *
 * INVARIANTS
 *  No logic here: the three modules are the implementation; this file only re-exports.
 *  One `export *` per module, no symbol renamed.
 *
 * WHERE TO CHANGE
 *  Section order, rows, selection .... list-order.ts
 *  A task's counters and price ...... list-usage.ts
 *  Visible window maths ............. list-scroll.ts
 */
export * from "./list-order";
export * from "./list-usage";
export * from "./list-scroll";
