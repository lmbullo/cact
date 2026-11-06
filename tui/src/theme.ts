/**
 * Theme: the palette, the status vocabulary and the one-line text helpers.
 *
 * WHAT IT IS
 *  The values every view shares: the gruvbox palette (`C`), the one-word status table, the
 *  prompt-row cap, and the four pure helpers that turn a payload into one terminal line
 *  (`clip`, `oneLine`, `lineCount`, `toolArgs`). No JSX, no state, no I/O.
 *
 * ENTRY POINTS
 *  C                        RGBA palette; explicit hex so the host terminal scheme cannot change it
 *  STATUS                   TaskStatus -> {word, color}; one word per row
 *  PROMPT_MAX_ROWS          how tall the prompt grows before it scrolls internally
 *  clip(text, width)        collapse to one line and ellipsize to a width
 *  oneLine(text)            collapse whitespace, drop CRs
 *  lineCount(text)          non-empty line count, for the `(+42L)` tail
 *  toolArgs(code)           the arguments of a python cell's first call, for the run log
 *
 * INVARIANTS
 *  - Pure functions only: a view may call them in render, and they are cheap enough for that.
 *  - Palette values are hex literals, never ansi() slots (see the README's TUI contract).
 *  - `clip` always collapses whitespace first, so a multi-line payload never breaks a row.
 *
 * WHERE TO CHANGE
 *  A colour ......................... C
 *  A status word or colour .......... STATUS
 *  How a payload is shortened ........ clip / oneLine / lineCount / toolArgs
 */
import { RGBA } from "@opentui/core";
import type { TaskStatus } from "./types";

/**
 * Gruvbox dark palette. Explicit hex values rather than ansi() slots, so the app keeps
 * the same look no matter how the host terminal's own scheme is configured.
 */
const hex = (value: string): RGBA => RGBA.fromHex(value);

export const C = {
  bg: hex("#282828"), // bg0
  bg1: hex("#3c3836"), // bg1, for subtle raised surfaces
  bg2: hex("#504945"), // bg2, selection fill
  bg4: hex("#7c6f64"), // bg4, scrollbar thumb
  text: hex("#ebdbb2"), // fg1
  dim: hex("#a89984"), // fg4
  faint: hex("#928374"), // gray
  accent: hex("#83a598"), // blue
  green: hex("#b8bb26"), // green
  yellow: hex("#fabd2f"), // yellow
  red: hex("#fb4934"), // red
  tool: hex("#d3869b"), // gruvbox purple, tool calls in the run log
  orange: hex("#fe8019"), // orange
};

/** One short word per status, so a row reads `3 running fix the flaky test`. */
export const STATUS: Record<TaskStatus, { word: string; color: RGBA }> = {
  running: { word: "running", color: C.green },
  queued: { word: "queue", color: C.yellow },
  done: { word: "done", color: C.dim },
  partial: { word: "partial", color: C.yellow },
  failed: { word: "failed", color: C.red },
  cancelled: { word: "cancelled", color: C.faint },
};

/** The prompt grows with its content up to this many rows, then scrolls internally. */
export const PROMPT_MAX_ROWS = 5;

export function clip(text: string, width: number): string {
  if (width <= 1) return "";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= width ? flat : `${flat.slice(0, Math.max(0, width - 1))}…`;
}

/** Collapse any multi-line blob onto one space-separated line. */
export function oneLine(text: string): string {
  return text.replace(/\r/g, "").replace(/\s+/g, " ").trim();
}

/** Count non-empty lines, so a big error dump reads `(+42L)` instead of 42 rows. */
export function lineCount(text: string): number {
  return text.replace(/\r/g, "").split("\n").filter((line) => line.trim().length > 0).length;
}

/**
 * A cell parsed down to the one line the log shows: only the arguments of the
 * call, since the log keeps tool calls to their bare arguments.
 */
export function toolArgs(code: string): string {
  const lines = code
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) return "(empty cell)";
  const flat = oneLine(lines[0]!);
  const call = flat.match(/^([A-Za-z_][\w.]*)\s*\(/);
  if (!call) return flat;
  const start = call[0].length;
  let depth = 1;
  let end = start;
  for (; end < flat.length; end++) {
    const ch = flat[end];
    if (ch === "(") depth += 1;
    else if (ch === ")") {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  return flat.slice(start, end).trim();
}
