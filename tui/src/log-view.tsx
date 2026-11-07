/**
 * Log view: one task's log, bookended by the prompt it started from and the summary it ended with.
 *
 * WHAT IT IS
 *  `PromptView` opens the log with the task's whole prompt; `EventView` renders one TaskEvent as
 *  a single terminal line; `LogView` stacks them in a sticky-bottom scrollbox. Kinds covered:
 *  cell.start (the typed call or the python cell's arguments), turn (only a rejected/staged-finish
 *  notice), final (status + counters, then the agent's whole summary as wrapped prose), error,
 *  stderr, log, and the lifecycle kinds (started/finished/queued/pid). When the task has a start
 *  time, every row carries a dim `+12s` gutter so the run reads as a timeline.
 *
 * ENTRY POINTS
 *  PromptView({task, width, origin})  the log's opening block: `▸ prompt` + the whole prompt
 *  EventView({event, width, origin})  one event -> one row, or null when it is silent
 *  LogView({task, events, width})     the scrollbox; nothing when no task is open
 *
 * INVARIANTS
 *  - The log is bookended: the prompt block at the top and the final block at the bottom share
 *    one shape — a marker + one word on the head row, then a blank row and the prose wrapped in
 *    the same INDENT column by theme.wrapText. Start and finish must line up, so a change to
 *    either block is a change to both (see `blockHead` / `bodyRows`).
 *  - The log is monochrome by default: the grey ramp carries the timeline, the tool calls and
 *    the prose. Colour is spent only where it means something — the accent on the prompt marker,
 *    a rejected finish, an error, the status dot of the final line — and nothing is bold.
 *  - The prompt and the final summary are shown whole, word-wrapped to the pane, never clipped:
 *    they are the two things the user asked to read. Only past SUMMARY_MAX_LINES does a block
 *    report `(+N lines)`.
 *  - A turn event is invisible unless it carries a `notice`: that is the only explanation of
 *    a rejected or staged finish the user gets, so it must never be dropped.
 *  - An unknown kind still prints one word (`? kind`), never the payload it carries.
 *  - No row here contains `─`: the log's only rule is the one hairline the prompt box owns.
 *
 * WHERE TO CHANGE
 *  A new event kind .................... EventView's kind switch + types.ts
 *  The opening prompt block ........... PromptView
 *  The counters on the final line ..... EventView's `final` case
 *  How a block wraps .................. theme.wrapText (SUMMARY_MAX_LINES)
 *  The shared block column ............ INDENT (used by both blocks)
 *  The timeline gutter ................ `since` below
 *  The scroll behaviour ................ LogView's <scrollbox> props
 */
import type { RGBA } from "@opentui/core";
import { C, STATUS, clip, elapsed, lineCount, oneLine, toolArgs, wrapText } from "./theme";
import type { Task, TaskEvent, TaskStatus } from "./types";

/**
 * The one column a log block's body is set in. The prompt at the top and the final summary at
 * the bottom both use it, so the two ends of a run line up under each other.
 */
const INDENT = "    ";

/** `+12s` since the task started, or nothing when the event carries no usable timestamp. */
function since(ts: number | undefined, origin: number | undefined): string {
  if (!ts || !origin || ts < origin) return "";
  return `${elapsed(ts - origin).padStart(6)} `;
}

/** The status look for a final event's word, falling back to a plain dot for unknown stops. */
function finalLook(status: string): { mark: string; color: RGBA } {
  return STATUS[status as TaskStatus] ?? { mark: "·", color: C.dim };
}

/**
 * The head row every block shares: the elapsed gutter, a coloured marker, one word and an
 * optional quiet meta tail. Keeping the marker to one column and the word to one token is what
 * makes the prompt and the final line read as the two ends of the same bracket.
 */
function blockHead(
  gutter: string,
  marker: string,
  color: RGBA,
  word: string,
  meta: string,
  fit: number,
) {
  const room = Math.max(4, fit - word.length - 3);
  return (
    <text>
      <span fg={C.quiet}>{gutter}</span>
      <span fg={color}>{`${marker} `}</span>
      <span fg={C.dim}>{word}</span>
      {meta ? <span fg={C.faint}>{`  ${clip(meta, room)}`}</span> : null}
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
 * The log's opening block: the whole prompt the run started from, wrapped to the pane. It is the
 * bright end of the bracket — the reader's own words — and sits in the same column and at the
 * same indent as the agent's final summary, so the log has a visible start and finish.
 */
export function PromptView({ task, width, origin }: { task: Task; width: number; origin?: number }) {
  // The prompt is the first tick of the timeline; without a start time there is no gutter.
  const gutter = origin ? `${elapsed(0).padStart(6)} ` : "";
  const fit = Math.max(8, width - 2 - gutter.length);
  const meta = [task.model, task.effort].filter(Boolean).join(" · ");
  const text = task.prompt?.trim() ? task.prompt : "(no prompt)";
  const { lines, dropped } = wrapText(text, Math.max(8, fit - INDENT.length));
  return (
    <box style={{ flexDirection: "column", flexShrink: 0, paddingTop: 1, paddingBottom: 1 }}>
      {blockHead(gutter, "▸", C.accent, "prompt", meta, fit)}
      {bodyRows(lines, dropped, C.text)}
    </box>
  );
}

/**
 * One event, one row — except the final event, which is the header row plus the agent's
 * summary wrapped over as many rows as it needs. The full payload still reaches the model.
 */
export function EventView({ event, width, origin }: { event: TaskEvent; width: number; origin?: number }) {
  const gutter = since(event.ts, origin);
  const fit = Math.max(8, width - 2 - gutter.length);
  const kind = event.kind;

  if (kind === "cell.start") {
    // A typed call shows its tool and its JSON arguments; a python cell shows its first call's
    // arguments only. Either way: no cell index, no counters, no colour.
    const typed = event.tool && event.tool !== "python";
    const verb = typed ? event.tool! : "";
    const args = typed
      ? `(${oneLine(JSON.stringify(event.args ?? {}))})`
      : toolArgs(event.code ?? "");
    const room = Math.max(2, fit - 2 - verb.length - 1);
    return (
      <text>
        <span fg={C.quiet}>{gutter}</span>
        <span fg={C.quiet}>{"· "}</span>
        {verb ? <span fg={C.faint}>{verb}</span> : null}
        <span fg={C.dim}>{clip(args, room)}</span>
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
      <text fg={C.yellow}>
        {gutter + clip(`↺ ${oneLine(notice)}${more}`, fit)}
      </text>
    );
  }

  if (kind === "final") {
    const data = (event.data ?? {}) as Record<string, unknown>;
    const status = String(data.status ?? "");
    const look = finalLook(status);
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

    // The summary is the editor's note and closes the bracket the prompt opened: same head
    // shape, one blank row, the same INDENT column, wrapped to the pane and never clipped.
    const word = status || "final";
    const { lines, dropped } = wrapText(event.text ?? "", Math.max(8, fit - INDENT.length));

    return (
      <box style={{ flexDirection: "column", flexShrink: 0, paddingTop: 1, paddingBottom: 1 }}>
        {blockHead(gutter, look.mark, look.color, word, meta, fit)}
        {lines.length > 0 ? bodyRows(lines, dropped, C.dim) : null}
      </box>
    );
  }

  if (kind === "error") {
    const text = event.text ?? "error";
    const more = lineCount(text) > 1 ? ` (+${lineCount(text) - 1}L)` : "";
    return (
      <text fg={C.red}>
        {gutter + clip(`✖ ${oneLine(text)}${more}`, fit)}
      </text>
    );
  }
  if (kind === "stderr") {
    const text = event.text ?? "";
    const first = oneLine(text);
    const more = lineCount(text) > 1 ? ` (+${lineCount(text) - 1}L)` : "";
    return <text fg={C.faint}>{gutter + clip(`✖ ${first}${more}`, fit)}</text>;
  }
  if (kind === "log") {
    return <text fg={C.faint}>{gutter + clip(oneLine(event.text ?? ""), fit)}</text>;
  }
  if (kind === "started" || kind === "finished" || kind === "queued" || kind === "pid") {
    return <text fg={C.quiet}>{gutter + clip(oneLine(event.text ?? kind), fit)}</text>;
  }
  // An unknown kind still gets one word, never the payload it carries.
  return <text fg={C.quiet}>{gutter + clip(`? ${kind}`, fit)}</text>;
}

/**
 * One task's log inside a scrollbox: the prompt it started from, the event stream, and the final
 * summary. A null task renders nothing; before the first event the prompt still shows, because it
 * is the one thing known about the run.
 */
export function LogView({ task, events, width }: { task: Task | null; events: TaskEvent[]; width: number }) {
  if (!task) return null;
  return (
    <scrollbox
      style={{ flexGrow: 1, flexBasis: 0, minHeight: 0 }}
      stickyScroll
      stickyStart="bottom"
      focused={false}
    >
      <box style={{ flexDirection: "column", width: "100%", paddingLeft: 1 }}>
        <PromptView task={task} width={width} origin={task.startedAt} />
        {events.length === 0 ? (
          <text fg={C.quiet}>{`${INDENT}waiting for output…`}</text>
        ) : (
          events.map((event) => (
            <EventView key={`${task.id}:${event.seq}`} event={event} width={width} origin={task.startedAt} />
          ))
        )}
      </box>
    </scrollbox>
  );
}
