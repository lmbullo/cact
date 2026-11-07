/**
 * Log view: one task's live event stream, one row per event.
 *
 * WHAT IT IS
 *  `EventView` renders a single TaskEvent as one terminal line and `LogView` stacks them in a
 *  sticky-bottom scrollbox. Kinds covered: cell.start (the typed call or the python cell's
 *  arguments), turn (only a rejected/staged-finish notice), final (status + counters), error,
 *  stderr, log, and the lifecycle kinds (started/finished/queued/pid). When the task has a
 *  start time, every row carries a dim `+12s` gutter so the run reads as a timeline.
 *
 * ENTRY POINTS
 *  EventView({event, width, origin})  one event -> one row, or null when it is silent
 *  LogView({task, events, width})     the scrollbox; nothing when no task is open
 *
 * INVARIANTS
 *  - A turn event is invisible unless it carries a `notice`: that is the only explanation of
 *    a rejected or staged finish the user gets, so it must never be dropped.
 *  - An unknown kind still prints one word (`? kind`), never the payload it carries.
 *  - Long text is clipped, not wrapped: a run must not dump a wall of text into the board.
 *  - No row here contains `─`: the log's only rules are the two the prompt box owns.
 *
 * WHERE TO CHANGE
 *  A new event kind .................... EventView's kind switch + types.ts
 *  The counters on the final line ..... EventView's `final` case
 *  The timeline gutter ................ `since` below
 *  The scroll behaviour ................ LogView's <scrollbox> props
 */
import { TextAttributes } from "@opentui/core";
import { C, clip, elapsed, lineCount, oneLine, toolArgs } from "./theme";
import type { Task, TaskEvent } from "./types";

/** `+12s` since the task started, or nothing when the event carries no usable timestamp. */
function since(ts: number | undefined, origin: number | undefined): string {
  if (!ts || !origin || ts < origin) return "";
  return `${elapsed(ts - origin).padStart(6)} `;
}

/**
 * One event, one row. The full payload still reaches the model; this view only
 * truncates what fits on a terminal line so a run never dumps a wall of text.
 */
export function EventView({ event, width, origin }: { event: TaskEvent; width: number; origin?: number }) {
  const gutter = since(event.ts, origin);
  const fit = Math.max(8, width - 2 - gutter.length);
  const kind = event.kind;

  if (kind === "cell.start") {
    // A typed call shows its tool in dim and its JSON arguments in the tool colour; a python
    // cell shows its first call's arguments only. Either way: no cell index, no counters.
    const typed = event.tool && event.tool !== "python";
    const verb = typed ? event.tool! : "";
    const args = typed
      ? `(${oneLine(JSON.stringify(event.args ?? {}))})`
      : toolArgs(event.code ?? "");
    const room = Math.max(2, fit - 2 - verb.length);
    return (
      <text>
        <span fg={C.faint}>{gutter}</span>
        <span fg={C.faint}>{"▸ "}</span>
        {verb ? <span fg={C.faint}>{verb}</span> : null}
        <span fg={C.tool}>{clip(args, room)}</span>
      </text>
    );
  }

  // A turn is normally silent (tool output is never rendered), but the loop records a
  // `notice` when it rejects or stages a finish: that reason must stay visible.
  if (kind === "turn") {
    const notice = (event.data ?? {}).notice;
    if (typeof notice !== "string" || !notice.trim()) return null;
    const more = lineCount(notice) > 1 ? ` (+${lineCount(notice) - 1}L)` : "";
    return (
      <text fg={C.yellow} attributes={TextAttributes.BOLD}>
        {gutter + clip(`↺ ${oneLine(notice)}${more}`, fit)}
      </text>
    );
  }

  if (kind === "final") {
    const data = (event.data ?? {}) as Record<string, unknown>;
    const status = String(data.status ?? "");
    const color = status === "done" ? C.green : status === "partial" ? C.yellow : C.red;
    const meta = [
      data.stop ? `stop=${String(data.stop)}` : "",
      data.steps !== undefined ? `${String(data.steps)} steps` : "",
      data.calls !== undefined ? `${String(data.calls)} cells` : "",
      data.errors !== undefined ? `${String(data.errors)} errors` : "",
      data.wallMs !== undefined ? `${String(data.wallMs)}ms` : "",
      (data.usage as Record<string, number> | undefined)?.total_tokens
        ? `${String((data.usage as Record<string, number>).total_tokens)} tok`
        : "",
    ]
      .filter(Boolean)
      .join(" · ");
    const text = oneLine(event.text ?? "");
    const label = `● ${status} `;
    const tail = clip(meta, Math.max(6, fit - label.length - 3));
    const summaryRoom = Math.max(0, fit - label.length - tail.length - 3);
    const summary = text && summaryRoom > 2 ? clip(text, summaryRoom) : "";
    return (
      <text>
        <span fg={C.faint}>{gutter}</span>
        <span fg={color} attributes={TextAttributes.BOLD}>{label}</span>
        <span fg={C.faint}>{tail}</span>
        {summary ? <span fg={color}>{` = ${summary}`}</span> : null}
      </text>
    );
  }

  if (kind === "error") {
    const text = event.text ?? "error";
    const more = lineCount(text) > 1 ? ` (+${lineCount(text) - 1}L)` : "";
    return (
      <text fg={C.red} attributes={TextAttributes.BOLD}>
        {gutter + clip(`✖ ${oneLine(text)}${more}`, fit)}
      </text>
    );
  }
  if (kind === "stderr") {
    const text = event.text ?? "";
    const first = oneLine(text);
    const more = lineCount(text) > 1 ? ` (+${lineCount(text) - 1}L)` : "";
    return <text fg={C.red}>{gutter + clip(`! ${first}${more}`, fit)}</text>;
  }
  if (kind === "log") {
    return <text fg={C.dim}>{gutter + clip(`· ${oneLine(event.text ?? "")}`, fit)}</text>;
  }
  if (kind === "started" || kind === "finished" || kind === "queued" || kind === "pid") {
    return <text fg={C.faint}>{gutter + clip(`○ ${oneLine(event.text ?? kind)}`, fit)}</text>;
  }
  // An unknown kind still gets one word, never the payload it carries.
  return <text fg={C.faint}>{gutter + clip(`? ${kind}`, fit)}</text>;
}

/** One task's live event stream, one row per event, inside a scrollbox; null task renders nothing. */
export function LogView({ task, events, width }: { task: Task | null; events: TaskEvent[]; width: number }) {
  if (!task) return null;
  if (events.length === 0) {
    return (
      <box style={{ flexGrow: 1, flexBasis: 0, minHeight: 0, flexDirection: "column", paddingLeft: 1 }}>
        <text fg={C.faint}>{"waiting for output…"}</text>
      </box>
    );
  }
  return (
    <scrollbox
      style={{ flexGrow: 1, flexBasis: 0, minHeight: 0 }}
      stickyScroll
      stickyStart="bottom"
      focused={false}
    >
      <box style={{ flexDirection: "column", width: "100%", paddingLeft: 1 }}>
        {events.map((event) => (
          <EventView key={`${task.id}:${event.seq}`} event={event} width={width} origin={task.startedAt} />
        ))}
      </box>
    </scrollbox>
  );
}
