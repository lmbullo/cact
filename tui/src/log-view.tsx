/**
 * Log view: one task's journal as terse `key=value` rows, one row per event.
 *
 * WHAT IT IS
 *  `PromptView` opens the log with the task's prompt; `EventView` renders one TaskEvent as one
 *  `key=value` row, each value clipped to the columns its own key leaves; `LogView` stacks them
 *  in a sticky-bottom scrollbox. Kinds covered: cell.start (the typed call or the python cell's
 *  arguments), turn (only a rejected/staged-finish notice), final (the closing summary block),
 *  error, stderr and log. The lifecycle kinds (started/finished/queued/pid), cell.end and check
 *  render nothing: they are the task's state, which the log header (board-view.LogHeader) shows
 *  live from `Task`, and `lastCheck` is what hands it the last `check` event. When the task has a
 *  start time, every row carries a dim `+12s` gutter so the run reads as a timeline.
 *
 * ENTRY POINTS
 *  PromptView({task, width, origin})  the log's opening block: `kind=prompt chars=N` + the prompt
 *  EventView({event, width, origin, status})  one event -> one row, or null when it is silent
 *  LogView({task, events, width})     the scrollbox; nothing when no task is open
 *  lastCheck(events)                  the task's newest verdict, for the log header
 *
 * INVARIANTS
 *  - One source of truth for the run's state: the log header (board-view.LogHeader) renders
 *    `Task.status` live. The body never repeats it — no lifecycle row, no status word on the
 *    closing block — so the screen cannot show `queue` and `queued` at once, or keep a stale
 *    `queued` above a run that has moved on.
 *  - The header also owns the live counters (steps, tokens in/out, price, cache rate, context
 *    window, duration) and, once the run has settled, how it ended: the stop reason, the error
 *    count and the last failed check. The closing row therefore carries only the cell count —
 *    one fact, one place.
 *  - A row is machine-first: no glyphs, no decorative prose, no sentences — only `field=value`
 *    tokens and one gutter. The fields are the journal's own names (`kind`, `tool`, `args`,
 *    `code`, `notice`, `text`, `cells`, `lines`), so a row can be pasted into a grep.
 *  - Truncation is per field, never per row: each value is clipped to the room its key leaves, so
 *    a 300-char argument can neither push its own key nor a later field off the line, and every
 *    row stays inside the pane. `clip` (theme.ts) adds the trailing `…`.
 *  - The prompt and the final summary are the only wrapped bodies, shown whole and never
 *    clipped: they are the two things the user asked to read. Only past SUMMARY_MAX_LINES does a
 *    block report `(+N more lines)`.
 *  - A turn event is invisible unless it carries a `notice`: that is the only explanation of
 *    a rejected or staged finish the user gets, so it must never be dropped.
 *  - An unknown kind prints its `kind=` field only, never the payload it carries.
 *  - No row here contains `─`: the log's only rule is the one hairline the prompt box owns.
 *  - A finished task with nothing to show gets no `kind=waiting` row: that row is only true while
 *    the task is queued or running.
 *
 * WHERE TO CHANGE
 *  A new event kind .................... EventView's kind switch + types.ts
 *  Which kinds stay silent ............. isSilent
 *  The header's last verification ...... lastCheck
 *  The opening prompt block ........... PromptView
 *  The fields of a row ................ `fields(...)` call in EventView / PromptView
 *  How a field is clipped ............. FIELD_CAP / DEFAULT_CAP / KvRow
 *  The shared block column ............ INDENT (prompt and summary bodies)
 *  The timeline gutter ................ `since` below
 *  The scroll behaviour ................ LogView's <scrollbox> props
 */
import type { RGBA } from "@opentui/core";
import { C, STATUS, clip, elapsed, lineCount, oneLine, toolArgs, wrapText } from "./theme";
import type { CheckState, Task, TaskEvent, TaskStatus } from "./types";

/**
 * The one column a wrapped body is set in. The prompt at the top and the final summary at the
 * bottom both use it, so the two ends of a run line up under each other.
 */
const INDENT = "    ";

/** The largest a field's value may grow on its own; the row's share usually clips it sooner. */
const FIELD_CAP: Record<string, number> = {
  args: 80,
  code: 80,
  notice: 80,
  text: 120,
  prompt: 120,
  model: 28,
  effort: 10,
};
/** The cap for a field this file does not name; enough for a kind, a number or a name. */
const DEFAULT_CAP = 40;

/** One `key=value` token on a row. */
type Field = { key: string; value: string };

/** `+12s` since the task started, or nothing when the event carries no usable timestamp. */
function since(ts: number | undefined, origin: number | undefined): string {
  if (!ts || !origin || ts < origin) return "";
  return `${elapsed(ts - origin).padStart(6)} `;
}

/** The status colour for the closing row's values; plain grey for an unknown stop. */
function finalColor(status: string): RGBA {
  return STATUS[status as TaskStatus]?.color ?? C.dim;
}

/** The one reason a turn row exists: the loop rejected or staged a finish. */
function noticeOf(event: TaskEvent): string {
  const notice = (event.data ?? {}).notice;
  return typeof notice === "string" ? notice.trim() : "";
}

/** The fields that carry a value; an absent or empty field is not printed at all. */
function fields(...entries: Array<[string, string | number | undefined | null]>): Field[] {
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
function isSilent(event: TaskEvent): boolean {
  if (event.kind === "queued" || event.kind === "started" || event.kind === "pid" || event.kind === "finished" || event.kind === "cell.end" || event.kind === "check") {
    return true;
  }
  if (event.kind === "turn") return noticeOf(event) === "";
  return false;
}

/**
 * The task's newest verification verdict: the `check` event with the highest seq, or null when
 * its journal has none. The header reads this and only this, so a later pass clears an earlier
 * failure on screen and nothing is ever re-run to find out.
 */
export function lastCheck(events: TaskEvent[]): CheckState | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.kind === "check" && event.check) return event.check;
  }
  return null;
}

/**
 * One row of `key=value` fields. Each value gets what it needs, capped by FIELD_CAP; whatever is
 * left after the earlier fields belongs to the last one. A value longer than its share is clipped
 * inside its own field, so the row never grows past `fit` and no key is pushed off it.
 */
function KvRow({ gutter, fit, fields: row, color }: { gutter: string; fit: number; fields: Field[]; color: RGBA }) {
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
function bodyRows(lines: string[], dropped: number, color: RGBA) {
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

/**
 * One event, one row — except the final event, which is its `kind=final cells=N` row plus the
 * agent's summary wrapped over as many rows as it needs. The full payload still reaches the model.
 */
export function EventView({
  event,
  width,
  origin,
  status,
}: {
  event: TaskEvent;
  width: number;
  origin?: number;
  /** The task's own status once the run has settled; it colours the closing row. */
  status?: TaskStatus;
}) {
  const gutter = since(event.ts, origin);
  const fit = Math.max(8, width - 2 - gutter.length);
  const kind = event.kind;

  if (isSilent(event)) return null;

  if (kind === "cell.start") {
    // A typed call shows its tool and its JSON arguments; a code cell shows its first call's
    // arguments only. Either way: no cell index, no counters, no colour.
    const typed = event.tool && event.tool !== "code" && event.tool !== "python";
    const body = typed
      ? oneLine(JSON.stringify(event.args ?? {}))
      : toolArgs(event.code ?? "");
    const row = fields(["kind", "cell"], ["tool", event.tool ?? "code"], [typed ? "args" : "code", body]);
    return <KvRow gutter={gutter} fit={fit} fields={row} color={C.dim} />;
  }

  // A turn is normally silent (tool output is never rendered), but the loop records a
  // `notice` when it rejects or stages a finish: that reason must stay visible.
  if (kind === "turn") {
    const notice = oneLine(noticeOf(event));
    const lines = lineCount(notice);
    const row = fields(["kind", "turn"], ["lines", lines > 1 ? lines : undefined], ["notice", notice]);
    return <KvRow gutter={gutter} fit={fit} fields={row} color={C.yellow} />;
  }

  if (kind === "final") {
    const data = (event.data ?? {}) as Record<string, unknown>;
    // The values take their colour from the task's status once the run has settled, and only
    // from the event when it has not: the header already spells the state out.
    const color = finalColor(status ?? String(data.status ?? ""));
    // Only the cell count: the header above already prints the stop reason, the error count and
    // the last check's verdict, and a number or a word never appears twice on this screen.
    const row = fields(["kind", "final"], ["cells", data.calls !== undefined ? String(data.calls) : undefined]);

    // The summary is the editor's note and closes the bracket the prompt opened: same row shape,
    // one blank row, the same INDENT column, wrapped to the pane and never clipped.
    const { lines, dropped } = wrapText(event.text ?? "", Math.max(8, fit - INDENT.length));

    return (
      <box style={{ flexDirection: "column", flexShrink: 0, paddingTop: 1, paddingBottom: 1 }}>
        <KvRow gutter={gutter} fit={fit} fields={row} color={color} />
        {lines.length > 0 ? bodyRows(lines, dropped, C.dim) : null}
      </box>
    );
  }

  if (kind === "error" || kind === "stderr" || kind === "log") {
    const text = event.text ?? "";
    const lines = lineCount(text);
    const row = fields(["kind", kind], ["lines", lines > 1 ? lines : undefined], ["text", oneLine(text)]);
    return <KvRow gutter={gutter} fit={fit} fields={row} color={kind === "error" ? C.red : C.faint} />;
  }
  // An unknown kind still prints its kind field only, never the payload it carries.
  return <KvRow gutter={gutter} fit={fit} fields={fields(["kind", kind])} color={C.quiet} />;
}

/**
 * One task's log inside a scrollbox: the prompt it started from, the event stream, and the final
 * summary. A null task renders nothing; before the first renderable event the prompt still shows,
 * because it is the one thing known about the run. The rows are the silent-filtered events, so
 * `kind=waiting` is decided by what is actually on screen, not by the journal's length.
 */
export function LogView({ task, events, width }: { task: Task | null; events: TaskEvent[]; width: number }) {
  if (!task) return null;
  const rows = events.filter((event) => !isSilent(event));
  // The header owns the state; once the task has settled, the closing row borrows its colour.
  const live = task.status === "queued" || task.status === "running";
  const settled = live ? undefined : task.status;
  return (
    <scrollbox
      style={{ flexGrow: 1, flexBasis: 0, minHeight: 0 }}
      stickyScroll
      stickyStart="bottom"
      focused={false}
    >
      <box style={{ flexDirection: "column", width: "100%", paddingLeft: 1 }}>
        <PromptView task={task} width={width} origin={task.startedAt} />
        {rows.length === 0 ? (
          live ? (
            <KvRow
              gutter={INDENT}
              fit={Math.max(8, width - 3 - INDENT.length)}
              fields={fields(["kind", "waiting"], ["status", task.status])}
              color={C.quiet}
            />
          ) : null
        ) : (
          rows.map((event) => (
            <EventView
              key={`${task.id}:${event.seq}`}
              event={event}
              width={width}
              origin={task.startedAt}
              status={settled}
            />
          ))
        )}
      </box>
    </scrollbox>
  );
}
