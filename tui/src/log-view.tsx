/**
 * Log view: one task's live event stream, one row per event.
 *
 * WHAT IT IS
 *  `EventView` renders a single TaskEvent as one terminal line and `LogView` stacks them in a
 *  sticky-bottom scrollbox. Kinds covered: cell.start (the typed call or the python cell's
 *  arguments), turn (only a rejected/staged-finish notice), final (status + counters), error,
 *  stderr, log, and the lifecycle kinds (started/finished/queued/pid).
 *
 * ENTRY POINTS
 *  EventView({event, width})          one event -> one row, or null when it is silent
 *  LogView({task, events, width})     the scrollbox; nothing when no task is open
 *
 * INVARIANTS
 *  - A turn event is invisible unless it carries a `notice`: that is the only explanation of
 *    a rejected or staged finish the user gets, so it must never be dropped.
 *  - An unknown kind still prints one word (`? kind`), never the payload it carries.
 *  - Long text is clipped, not wrapped: a run must not dump a wall of text into the board.
 *
 * WHERE TO CHANGE
 *  A new event kind .................... EventView's kind switch + types.ts
 *  The counters on the final line ..... EventView's `final` case
 *  The scroll behaviour ................ LogView's <scrollbox> props
 */
import { TextAttributes } from "@opentui/core";
import { C, clip, lineCount, oneLine, toolArgs } from "./theme";
import type { Task, TaskEvent } from "./types";

/**
 * One event, one row. The full payload still reaches the model; this view only
 * truncates what fits on a terminal line so a run never dumps a wall of text.
 */
export function EventView({ event, width }: { event: TaskEvent; width: number }) {
  const fit = Math.max(8, width - 2);
  const kind = event.kind;

  if (kind === "cell.start") {
    // Just the marker and the arguments: no verb, no cell index, no counters. A typed call
    // shows its tool and JSON args; a python cell shows its first call, as before.
    const room = Math.max(2, fit - 2);
    const label = event.tool && event.tool !== "python"
      ? `${event.tool}(${oneLine(JSON.stringify(event.args ?? {}))})`
      : toolArgs(event.code ?? "");
    return (
      <text>
        <span fg={C.faint}>{"▸ "}</span>
        <span fg={C.tool}>{clip(label, room)}</span>
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
        {clip(`↺ ${oneLine(notice)}${more}`, width)}
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
    const tail = clip(meta, Math.max(6, fit - status.length - 3));
    const summaryLength = 2 + status.length + 1 + tail.length;
    const headText = text ? clip(`  = ${text}`, Math.max(0, fit - summaryLength)) : "";
    return (
      <text>
        <span fg={color} attributes={TextAttributes.BOLD}>{`● ${status} `}</span>
        <span fg={C.faint}>{tail}</span>
        <span fg={color}>{headText}</span>
      </text>
    );
  }

  if (kind === "error") {
    const text = event.text ?? "error";
    const more = lineCount(text) > 1 ? ` (+${lineCount(text) - 1}L)` : "";
    return (
      <text fg={C.red} attributes={TextAttributes.BOLD}>
        {clip(`✖ ${oneLine(text)}${more}`, width)}
      </text>
    );
  }
  if (kind === "stderr") {
    const text = event.text ?? "";
    const first = oneLine(text);
    const more = lineCount(text) > 1 ? ` (+${lineCount(text) - 1}L)` : "";
    return <text fg={C.red}>{clip(`! ${first}${more}`, width)}</text>;
  }
  if (kind === "log") {
    return <text fg={C.dim}>{clip(`· ${oneLine(event.text ?? "")}`, width)}</text>;
  }
  if (kind === "started" || kind === "finished" || kind === "queued" || kind === "pid") {
    return <text fg={C.faint}>{clip(`○ ${oneLine(event.text ?? kind)}`, width)}</text>;
  }
  // An unknown kind still gets one word, never the payload it carries.
  return <text fg={C.faint}>{clip(`? ${kind}`, width)}</text>;
}

/** One task's live event stream, one row per event, inside a scrollbox; null task renders nothing. */
export function LogView({ task, events, width }: { task: Task | null; events: TaskEvent[]; width: number }) {
  if (!task) return null;
  if (events.length === 0) {
    return (
      <box style={{ flexGrow: 1, flexBasis: 0, minHeight: 0, flexDirection: "column" }}>
        <text fg={C.faint}>waiting for output…</text>
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
      <box style={{ flexDirection: "column", width: "100%" }}>
        {events.map((event) => (
          <EventView key={`${task.id}:${event.seq}`} event={event} width={width} />
        ))}
      </box>
    </scrollbox>
  );
}
