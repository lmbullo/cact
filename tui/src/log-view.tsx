/** Log view: one task's journal as a human, one-glance page.
 *
 * WHAT IT IS
 * PromptView opens the log with the reader's prompt under a `▸ you` head; EventView renders a
 * notice, an error or the closing summary; LogView stacks them in a sticky-bottom scrollbox.
 * The run's state and counters live in board-view.LogHeader, never in the body.
 *
 * ENTRY POINTS
 * PromptView({task, width})  `▸ you` + the whole prompt   lastCheck(events)  newest verdict
 * EventView({event, width, origin, status})  one block, or null when silent
 * LogView({task, events, width})  the scrollbox; nothing when no task is open
 *
 * INVARIANTS
 * state has one source: board-view.LogHeader renders Task.status live; the body never repeats it.
 * the body is the two things the reader keeps: the prompt and the summary; tool calls are silent.
 * a prose block opens with a `▸` word and a body in the shared INDENT column; a diagnostic row stays `field=value`.
 * only the prompt and the summary wrap, whole and unclipped, capped at SUMMARY_MAX_LINES then `(+N more lines)`.
 * a turn row shows only with a notice; an unknown kind prints `kind=` only; a settled task adds no row.
 * no row contains `─`; a diagnostic row gets a dim `+12s` gutter; the closing block carries only its head.
 * WHERE TO CHANGE
 * kind switch ... EventView + types.ts   silent kinds ... isSilent   last check ... lastCheck
 * fields ... `fields(...)`   clip ... FIELD_CAP/DEFAULT_CAP/KvRow   columns ... INDENT, `since`
 */
import { C, lineCount, oneLine, wrapText } from "./theme";
import type { CheckState, Task, TaskEvent, TaskStatus } from "./types";
import {
  BlockHead,
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
 * One event, one block — except the final event, which is a `▸ summary` head plus the agent's
 * summary wrapped over as many rows as it needs. A notice or an error keeps its `key=value` row;
 * the full payload still reaches the model.
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
    // The header above already prints the stop reason, the error count, the last check's verdict
    // and every counter; the block below is the one thing the reader came for. So the closing
    // head is one word, `summary`, in the colour the run ended with — nothing is repeated.
    const { lines, dropped } = wrapText(event.text ?? "", Math.max(8, fit - INDENT.length));

    return (
      <box style={{ flexDirection: "column", flexShrink: 0, paddingTop: 1, paddingBottom: 1 }}>
        <BlockHead label="summary" color={color} />
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
 * One task's log inside a scrollbox: the prompt it started from and the events that printed a
 * block or a diagnostic row. A null task renders nothing; before the first renderable event the
 * prompt still shows, because it is the one thing known about the run. The rows are the
 * silent-filtered events; none of them repeats the header's state or counters.
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
        <PromptView task={task} width={width} />
        {rows.map((event) => (
          <EventView
            key={`${task.id}:${event.seq}`}
            event={event}
            width={width}
            origin={task.startedAt}
            status={settled}
          />
        ))}
      </box>
    </scrollbox>
  );
}
