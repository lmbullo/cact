/** Log view: one task's journal as terse `key=value` rows, one row per event.
 *
 * WHAT IT IS
 * PromptView opens the log with the task's prompt; EventView renders one TaskEvent as one `key=value` row, each value
 * clipped to the columns its key leaves; LogView stacks rows in a sticky-bottom scrollbox. Kinds: turn (only a
 * rejected/staged-finish notice), final (closing summary), error, stderr, log; lifecycle kinds, cell.start/end, check silent.
 *
 * ENTRY POINTS
 * PromptView({task, width, origin})  opening block `kind=prompt chars=N` + prompt   lastCheck(events)  newest verdict
 * EventView({event, width, origin, status})  one row, or null when the kind is silent
 * LogView({task, events, width})  the scrollbox; nothing when no task is open
 *
 * INVARIANTS
 * state has one source: board-view.LogHeader renders Task.status live (counters, stop reason, last failed check); the body never repeats it.
 * the body is the two things the reader keeps: the prompt it started from and the summary it ended with; tool calls are silent.
 * a row is machine-first (field=value only); truncation is per field (`theme.clip` adds `…`), never per row.
 * only the prompt and the final summary wrap, whole and unclipped, capped at SUMMARY_MAX_LINES then `(+N more lines)`.
 * a turn row shows only with a notice; an unknown kind prints `kind=` only; a finished task gets no `kind=waiting` row.
 * no row contains `─` (the prompt box owns the only hairline); a start time gives every row a dim `+12s` gutter.
 * a closing row carries only its kind: the summary below it is the fact.
 * WHERE TO CHANGE
 * kind switch ... EventView + types.ts   silent kinds ... isSilent   last verification ... lastCheck
 * fields ... `fields(...)` in EventView/PromptView   clip ... FIELD_CAP/DEFAULT_CAP/KvRow
 * column/gutter/scroll ... INDENT, `since`, LogView's <scrollbox> props
 */
import { C, lineCount, oneLine, wrapText } from "./theme";
import type { CheckState, Task, TaskEvent, TaskStatus } from "./types";
import {
  INDENT,
  PromptView,
  bodyRows,
  fields,
  finalColor,
  isSilent,
  KvRow,
  noticeOf,
  since,
} from "./log-rows";

export { PromptView };

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
 * One event, one row — except the final event, which is its `kind=final` row plus the agent's
 * summary wrapped over as many rows as it needs. The full payload still reaches the model.
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
    // Only the kind: the header above already prints the stop reason, the error count and the
    // last check's verdict, and the summary below is the fact the reader came for.
    const row = fields(["kind", "final"]);

    // The summary is the editor's note and closes the bracket the prompt opened: same row shape,
    // one blank row, the same INDENT column, the same body ink, wrapped to the pane and never
    // clipped. It is the one thing a trusted loop reports back, so it is set like the prompt.
    const { lines, dropped } = wrapText(event.text ?? "", Math.max(8, fit - INDENT.length));

    return (
      <box style={{ flexDirection: "column", flexShrink: 0, paddingTop: 1, paddingBottom: 1 }}>
        <KvRow gutter={gutter} fit={fit} fields={row} color={color} />
        {lines.length > 0 ? bodyRows(lines, dropped, C.text) : null}
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
