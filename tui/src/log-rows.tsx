/**
 * log-rows.tsx: the log's row primitives — one `key=value` line and the two prose heads.
 *
 * WHAT IT IS
 *  What both halves of the log share: the field list and its per-field caps, `KvRow`, `bodyRows`,
 *  `BlockHead`, the silent-kind rule and the block that opens every log. Re-exported by log-view.
 *
 * ENTRY POINTS
 *  INDENT, FIELD_CAP, DEFAULT_CAP, Field   the shared column and the per-key caps
 *  fields, since, finalColor, noticeOf, isSilent   tokens, gutter, colour, notices, silence
 *  KvRow({gutter, fit, fields, color})     one row; each value clipped inside its own field
 *  bodyRows(lines, dropped, color)         a wrapped block plus its `(+N more lines)`
 *  BlockHead({label, color})               a block's `▸ you` / `▸ summary` head
 *  PromptView({task, width})               `▸ you` + the whole prompt
 *
 * INVARIANTS
 *  - Truncation is per field (`theme.clip` adds `…`), never per row; only the prompt and the
 *    final summary wrap, whole, unclipped, capped then counted.
 *  - Lifecycle kinds, `cell.start/end` and `check` are silent: the header says the state.
 *  - A prose block opens with a `▸` word (`you`, `summary`), never a `kind=` token.
 *  - No row contains `─`: the prompt box owns the only hairline.
 * WHERE TO CHANGE
 *  A row's fields .... FIELD_CAP, DEFAULT_CAP, fields   clipping .... KvRow   silence .... isSilent
 *  A block's head .... BlockHead, PromptView   The opening block .... PromptView
 */
import type { RGBA } from "@opentui/core";
import { TextAttributes } from "@opentui/core";
import { C, STATUS, clip, elapsed, wrapText } from "./theme";
import type { Task, TaskEvent, TaskStatus } from "./types";

/**
 * The one column a wrapped body is set in. The prompt at the top and the final summary at the
 * bottom both use it, so the two ends of a run line up under each other.
 */
export const INDENT = "    ";

/** The largest a field's value may grow on its own; the row's share usually clips it sooner. */
export const FIELD_CAP: Record<string, number> = {
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
 * `cell.start` is the loop's transcript — which verb ran with which arguments. The loop is
 * trusted and tool calls are not what the reader keeps: the body stays the prompt, a notice
 * when the loop refused a finish, an error, and the final summary.
 * A turn is silent unless it carries a notice (see noticeOf).
 */
export function isSilent(event: TaskEvent): boolean {
  if (event.kind === "queued" || event.kind === "started" || event.kind === "pid" || event.kind === "finished" || event.kind === "cell.start" || event.kind === "cell.end" || event.kind === "check") {
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
 * The head of a wrapped block: the `▸` marker plus the block's one name (`you`, `summary`).
 * A block is prose the reader came to read, so its head is a word, not a `kind=` token; the
 * marker carries the status colour and the label stays body ink.
 */
export function BlockHead({ label, color }: { label: string; color: RGBA }) {
  return (
    <text>
      <span fg={color}>{"\u25b8 "}</span>
      <span fg={C.text} attributes={TextAttributes.BOLD}>{label}</span>
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
 * `you` head. It is the bright end of the bracket — the reader's own words — and sits in the
 * same column as the agent's final summary. Model, effort and prompt length are not repeated
 * here: the masthead carries the model, and a count of the reader's own words helps nobody.
 */
export function PromptView({ task, width }: { task: Task; width: number }) {
  const fit = Math.max(8, width - 2);
  const text = task.prompt ?? "";
  const { lines, dropped } = text.trim()
    ? wrapText(text, Math.max(8, fit - INDENT.length))
    : { lines: [], dropped: 0 };
  return (
    <box style={{ flexDirection: "column", flexShrink: 0, paddingTop: 1, paddingBottom: 1 }}>
      <BlockHead label="you" color={C.accent} />
      {lines.length > 0 ? bodyRows(lines, dropped, C.text) : null}
    </box>
  );
}
