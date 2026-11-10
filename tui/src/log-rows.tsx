/**
 * log-rows.tsx: the log's row primitives — one `key=value` line and the opening prompt block.
 *
 * WHAT IT IS
 *  What both halves of the log share: the field list and its per-field caps, `KvRow`, `bodyRows`,
 *  the silent-kind rule and the block that opens every log. Re-exported from log-view.tsx.
 *
 * ENTRY POINTS
 *  INDENT, FIELD_CAP, DEFAULT_CAP, Field   the shared column and the per-key caps
 *  fields, since, finalColor, noticeOf, isSilent   tokens, gutter, colour, notices, silence
 *  KvRow({gutter, fit, fields, color})     one row; each value clipped inside its own field
 *  bodyRows(lines, dropped, color)         a wrapped block plus its `(+N more lines)`
 *  PromptView({task, width, origin})       the opening block: `kind=prompt chars=N` + prompt
 *
 * INVARIANTS
 *  - Truncation is per field (theme.clip adds `…`), never per row: a long value cannot push its
 *    key or a later field off the line.
 *  - Only the prompt and the final summary wrap, whole and unclipped, capped then counted.
 *  - A lifecycle kind, `cell.end` and `check` are silent: the header already says the state.
 *  - No row contains `─`: the prompt box owns the only hairline.
 *
 * WHERE TO CHANGE
 *  What a row may hold .... FIELD_CAP, DEFAULT_CAP, fields   One row's clipping .... KvRow
 *  What is silent ......... isSilent, noticeOf   The opening block .... PromptView
 */
import type { RGBA } from "@opentui/core";
import { C, STATUS, clip, elapsed, wrapText } from "./theme";
import type { Task, TaskEvent, TaskStatus } from "./types";

/**
 * The one column a wrapped body is set in. The prompt at the top and the final summary at the
 * bottom both use it, so the two ends of a run line up under each other.
 */
export const INDENT = "    ";

/** The largest a field's value may grow on its own; the row's share usually clips it sooner. */
export const FIELD_CAP: Record<string, number> = {
  args: 80,
  code: 80,
  notice: 80,
  text: 120,
  prompt: 120,
  model: 28,
  effort: 10,
};
/** The cap for a field this file does not name; enough for a kind, a number or a name. */
export const DEFAULT_CAP = 40;

/** One `key=value` token on a row. */
export type Field = { key: string; value: string };

/** `+12s` since the task started, or nothing when the event carries no usable timestamp. */
export function since(ts: number | undefined, origin: number | undefined): string {
  if (!ts || !origin || ts < origin) return "";
  return `${elapsed(ts - origin).padStart(6)} `;
}

/** The status colour for the closing row's values; plain grey for an unknown stop. */
export function finalColor(status: string): RGBA {
  return STATUS[status as TaskStatus]?.color ?? C.dim;
}

/** The one reason a turn row exists: the loop rejected or staged a finish. */
export function noticeOf(event: TaskEvent): string {
  const notice = (event.data ?? {}).notice;
  return typeof notice === "string" ? notice.trim() : "";
}

/** The fields that carry a value; an absent or empty field is not printed at all. */
export function fields(...entries: Array<[string, string | number | undefined | null]>): Field[] {
  return entries
    .filter(([, value]) => value !== undefined && value !== null && String(value) !== "")
    .map(([key, value]) => ({ key, value: String(value) }));
}

/**
 * True for an event that renders no row. The lifecycle kinds (queued, started, pid, finished)
 * are the task's state, which the log header shows live from `Task`; drawing the journal's copy
 * would print the same fact twice — and print `queued` forever above a run that left it behind.
 * A `check` event is the same rule: it is how the run's verification ended, which the header
 * says in one row (lastCheck), not something the run's timeline prints.
 * A turn is silent unless it carries a notice (see noticeOf).
 */
export function isSilent(event: TaskEvent): boolean {
  if (event.kind === "queued" || event.kind === "started" || event.kind === "pid" || event.kind === "finished" || event.kind === "cell.end" || event.kind === "check") {
    return true;
  }
  if (event.kind === "turn") return noticeOf(event) === "";
  return false;
}

/**
 * One row of `key=value` fields. Each value gets what it needs, capped by FIELD_CAP; whatever is
 * left after the earlier fields belongs to the last one. A value longer than its share is clipped
 * inside its own field, so the row never grows past `fit` and no key is pushed off it.
 */
export function KvRow({ gutter, fit, fields: row, color }: { gutter: string; fit: number; fields: Field[]; color: RGBA }) {
  const cost = row.reduce((sum, field) => sum + field.key.length + 1, 0) + Math.max(0, row.length - 1);
  let room = Math.max(0, fit - cost);
  const caps = row.map((field) => {
    const need = Math.min(FIELD_CAP[field.key] ?? DEFAULT_CAP, field.value.length);
    const cap = Math.min(need, room);
    room -= cap;
    return cap;
  });
  return (
    <text>
      <span fg={C.quiet}>{gutter}</span>
      {row.flatMap((field, index) => {
        const cap = caps[index]!;
        // A value that fits its share is printed whole: clip() reserves one column for the `…`.
        const value = field.value.length <= cap ? field.value : clip(field.value, cap);
        return [
          index > 0 ? <span key={`s${index}`} fg={C.faint}>{" "}</span> : null,
          <span key={`k${index}`} fg={C.quiet}>{`${field.key}=`}</span>,
          <span key={`v${index}`} fg={color}>{value}</span>,
        ];
      })}
    </text>
  );
}

/**
 * The body of a block: one blank row after the head, then the wrapped prose in the shared
 * INDENT column, then the overflow count when the block was capped. Returned as a plain array
 * of rows rather than a component, so a block is exactly one column with no extra box.
 */
export function bodyRows(lines: string[], dropped: number, color: RGBA) {
  return [
    <text key="gap">{" "}</text>,
    ...lines.map((line, index) => (
      <text key={`b${index}`} fg={color}>{line ? `${INDENT}${line}` : " "}</text>
    )),
    dropped > 0 ? (
      <text key="more" fg={C.faint}>{`${INDENT}(+${dropped} more lines)`}</text>
    ) : null,
  ];
}

/**
 * The log's opening block: the whole prompt the run started from, wrapped to the pane, under a
 * `kind=prompt chars=N model=… effort=…` row. It is the bright end of the bracket — the reader's
 * own words — and sits in the same column as the agent's final summary.
 */
export function PromptView({ task, width, origin }: { task: Task; width: number; origin?: number }) {
  // The prompt is the first tick of the timeline; without a start time there is no gutter.
  const gutter = origin ? `${elapsed(0).padStart(6)} ` : "";
  const fit = Math.max(8, width - 2 - gutter.length);
  const text = task.prompt ?? "";
  const row = fields(
    ["kind", "prompt"],
    ["chars", text.length],
    ["model", task.model],
    ["effort", task.effort],
  );
  const { lines, dropped } = text.trim()
    ? wrapText(text, Math.max(8, fit - INDENT.length))
    : { lines: [], dropped: 0 };
  return (
    <box style={{ flexDirection: "column", flexShrink: 0, paddingTop: 1, paddingBottom: 1 }}>
      <KvRow gutter={gutter} fit={fit} fields={row} color={C.text} />
      {lines.length > 0 ? bodyRows(lines, dropped, C.text) : null}
    </box>
  );
}
