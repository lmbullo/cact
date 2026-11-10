/** Theme: the palette, the status vocabulary and the text helpers.
 *
 * WHAT IT IS
 * The values every view shares: the editorial palette (C), the one-word status table, the prompt-row cap, and the pure
 * helpers that turn a payload into terminal text. No JSX, no state, no I/O.
 *
 * ENTRY POINTS
 * C = RGBA palette (hex literals, no background role); STATUS = TaskStatus -> {word, mark, color}; PROMPT_MAX_ROWS.
 * clip(text, width) collapse to one line + ellipsize;  oneLine(text) collapse whitespace, drop CRs;  lineCount(text) non-empty lines.
 * wrapText(text, width) word-wrap, keeps every line (prompt, summary);  toolArgs(code) first call's arguments.
 * shortPath(path) `$HOME` -> `~`;  elapsed(ms) compact duration `0.8s`, `12s`, `2m04s`, `1h02m`;  SUMMARY_MAX_LINES.
 *
 * INVARIANTS
 * pure functions only: a view may call them in render and they are cheap enough for that.
 * palette values are hex literals, never ansi() slots; no background colour anywhere (the terminal paints the ground).
 * colour is the exception: accent for brand/cursor/selection, three status hues for a dot or one word, the rest is the grey ramp.
 * clip collapses whitespace first, so a multi-line payload never breaks a row; wrapText never loses a line, past SUMMARY_MAX_LINES it reports overflow.
 * elapsed never returns a negative or fractional second past 10s; the bar is not a stopwatch.
 *
 * WHERE TO CHANGE
 * a colour ... C   a status word/dot/colour ... STATUS   payload shortening ... clip/oneLine/lineCount/toolArgs
 * summary wrap ... wrapText/SUMMARY_MAX_LINES   duration grammar ... elapsed
 */
import { RGBA } from "@opentui/core";
import type { TaskStatus } from "./types";

/**
 * Editorial palette: one warm paper grey for prose, a four-step grey ramp for everything
 * structural, and three low-saturation hues used only on a dot or a word. Explicit hex values
 * rather than ansi() slots, so the ink reads the same whatever the host terminal's scheme is.
 * There is deliberately no background role: the terminal's colour is the ground.
 */
const hex = (value: string): RGBA => RGBA.fromHex(value);

/** The palette, one named role per colour; never a raw hex at a call site. */
export const C = {
  rule: hex("#3c3f41"), // the single hairline above the prompt
  text: hex("#d5c4a1"), // body ink, warm paper
  dim: hex("#bdae93"), // secondary ink: prompt text on a row, log arguments
  faint: hex("#928374"), // tertiary ink: metadata, hints, counts
  quiet: hex("#665c54"), // the quietest ink: gutters, timestamps, dividers
  accent: hex("#7daea3"), // dusty teal: the one accent, used sparingly
  green: hex("#a9b665"), // muted olive, a completed run
  yellow: hex("#d8a657"), // muted amber, waiting or partly done
  red: hex("#e07a5f"), // muted clay, a failure
};

/** One short word and one dot per status, so a row reads `● running 3 steps prompt`. */
export const STATUS: Record<TaskStatus, { word: string; mark: string; color: RGBA }> = {
  running: { word: "running", mark: "●", color: C.green },
  queued: { word: "queue", mark: "○", color: C.yellow },
  done: { word: "done", mark: "✓", color: C.green },
  partial: { word: "partial", mark: "◐", color: C.yellow },
  failed: { word: "failed", mark: "✖", color: C.red },
  cancelled: { word: "cancelled", mark: "⊘", color: C.quiet },
};

/** The prompt grows with its content up to this many rows, then scrolls internally. */
export const PROMPT_MAX_ROWS = 5;

/**
 * How many rows a log block may occupy before the rest is reported as `(+N lines)`. Both blocks
 * the reader came to read — the prompt the run started from and the summary it ended with — are
 * wrapped, never clipped, and share this cap. High enough that a real block is shown in full;
 * low enough that a runaway one cannot turn the run log into a wall.
 */
export const SUMMARY_MAX_LINES = 400;

/** `text` on one line, at most `width` chars, elided with `…`; width<=1 renders nothing. */
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
 * Wrap text to `width` columns, keeping every line: a log block (the prompt at the top of a run
 * and the agent's final summary at the bottom) is shown whole, so it must wrap instead of being
 * clipped. Paragraph breaks survive as blank rows; a line longer than `width` breaks on its last
 * space, or hard when it has none.
 * Returns the wrapped rows plus how many were cut past `maxLines` (never silently).
 */
export function wrapText(
  text: string,
  width: number,
  maxLines: number = SUMMARY_MAX_LINES,
): { lines: string[]; dropped: number } {
  const limit = Math.max(4, Math.floor(width));
  const lines: string[] = [];
  let dropped = 0;
  const keep = (line: string) => {
    if (lines.length < maxLines) lines.push(line);
    else dropped += 1;
  };

  for (const raw of text.replace(/\r/g, "").split("\n")) {
    const line = raw.replace(/\t/g, "  ").trimEnd();
    if (!line.trim()) {
      keep("");
      continue;
    }
    let rest = line;
    while (rest.length > limit) {
      const window = rest.slice(0, limit + 1);
      const space = window.lastIndexOf(" ");
      const cut = space > limit / 2 ? space : limit;
      keep(rest.slice(0, cut).trimEnd());
      rest = rest.slice(cut).replace(/^\s+/, "");
    }
    keep(rest);
  }

  while (lines.length > 0 && lines[0] === "") lines.shift();
  while (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  return { lines, dropped };
}

/** Abbreviate the home directory so the top bar can name a workspace on one line. */
export function shortPath(path: string): string {
  const home = process.env.HOME;
  if (!home) return path;
  if (path === home) return "~";
  return path.startsWith(`${home}/`) ? `~${path.slice(home.length)}` : path;
}

/** Compact duration for a row tail: `0.8s`, `12s`, `2m04s`, `1h02m`. */
export function elapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m${String(seconds % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}m`;
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
