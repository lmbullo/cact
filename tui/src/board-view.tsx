/**
 * Board view: the persistent top bar, the task sections and one line per task.
 *
 * WHAT IT IS
 *  The presentational half of the board: a `TopBar` (brand, workspace, model and live
 *  counts), a `Rule` (a horizontal rule that shrinks to keep its status note on screen), a
 *  `SectionHeader` (status dot, word, hairline, right-aligned count), a `TaskRow` (handle,
 *  status word, prompt, right-aligned steps/tokens and wall time) and `EmptyState`.
 *
 * ENTRY POINTS
 *  TopBar({workspace, model, effort, running, queued, total, connected, width})
 *  Rule({width, note})                    the rule drawn above/below the prompt box
 *  SectionHeader({status, count, width})  `● running ───────────────── 3`
 *  TaskRow({task, selected, width, now, onSelect})  one task, one line, click or key selects
 *  LogHeader({task, events, width, now})  the run log's status line, navigation hint kept
 *  EmptyState({width})                    the first screen, before any task exists
 *
 * INVARIANTS
 *  - Every row is exactly one line high with `flexShrink: 0`: the parent decides how many
 *    rows fit (list-model.scrollWindow), so a row must never grow or wrap.
 *  - Status text and dots come from theme.STATUS, so the board and the log cannot disagree.
 *  - Selection is an arrow plus colour, not a background fill (keeps the palette calm).
 *  - Telemetry (steps, tokens, wall time) is right-aligned and the prompt gives up room, so
 *    the eye can scan one column of numbers without the prompt jittering the layout.
 *
 * WHERE TO CHANGE
 *  The top bar's contents or priority .... TopBar
 *  Row contents or wording ............... TaskRow, SectionHeader, LogHeader
 *  Section grouping or ordering .......... list-model.buildRows / orderTasks (not here)
 *  Colours ............................... theme.ts
 */
import { TextAttributes } from "@opentui/core";
import { C, STATUS, clip, shortPath } from "./theme";
import { taskDuration, taskHandle, taskProgress } from "./list-model";
import type { Task, TaskStatus } from "./types";

/**
 * The one-row masthead: who we are, where we are, and what the server is doing. The right
 * side is sacrificed first when the terminal narrows; the brand and workspace survive.
 */
export function TopBar(props: {
  workspace: string;
  model?: string;
  effort?: string;
  running: number;
  queued: number;
  total: number;
  connected: boolean;
  width: number;
}) {
  const { workspace, model, effort, running, queued, total, connected, width } = props;
  const brand = "▌ code";
  const inner = Math.max(0, width - 2);

  const bits = [model, effort].filter((bit): bit is string => Boolean(bit));
  if (running > 0) bits.push(`${running} running`);
  if (queued > 0) bits.push(`${queued} queue`);
  if (!connected) bits.push("offline");
  else if (total === 0) bits.push("idle");
  const right = bits.join(" · ");
  const rightText = clip(right, inner);

  const room = inner - rightText.length - (rightText ? 1 : 0);
  const pathRoom = Math.max(0, room - brand.length - 2);
  const pathText = pathRoom >= 4 ? clip(shortPath(workspace), pathRoom) : "";
  const leftLength = brand.length + (pathText ? pathText.length + 2 : 0);
  const gap = Math.max(0, inner - leftLength - rightText.length);

  return (
    <box
      style={{ height: 1, flexShrink: 0, flexDirection: "row", paddingLeft: 1, paddingRight: 1 }}
    >
      <text>
        <span fg={C.accent}>{"▌ "}</span>
        <span fg={C.text} attributes={TextAttributes.BOLD}>{"code"}</span>
        {pathText ? <span fg={C.faint}>{`  ${pathText}`}</span> : null}
        <span>{" ".repeat(gap)}</span>
        <span fg={connected ? C.faint : C.red}>{rightText}</span>
      </text>
    </box>
  );
}

/** One full-width hairline; the optional `note` is sacrificed to keep the line on one row. */
export function Rule({ width, note }: { width: number; note?: string }) {
  // Keep the hint visible: shrink the rule instead of letting the note run off-screen.
  const hint = note ? ` ${note}` : "";
  const line = "─".repeat(Math.max(0, width - hint.length));
  return note ? <text fg={C.quiet}>{`${line}`}<span fg={C.faint}>{hint}</span></text> : <text fg={C.quiet}>{line}</text>;
}

/**
 * The status band of the board: a coloured dot and word on the left, a hairline that takes
 * the remaining width, and the count pinned to the right edge like a table column.
 */
export function SectionHeader({ status, count, width }: { status: TaskStatus; count: number; width: number }) {
  const meta = STATUS[status];
  const label = `${meta.mark} ${meta.word}`;
  const tail = String(count);
  const dashes = Math.max(0, width - 4 - label.length - tail.length);
  return (
    <box style={{ height: 1, width: "100%", flexShrink: 0, paddingLeft: 1, paddingRight: 1 }}>
      <text>
        <span fg={meta.color}>{meta.mark}</span>
        <span fg={C.dim}>{` ${meta.word}`}</span>
        <span fg={C.rule}>{` ${"─".repeat(dashes)} `}</span>
        <span fg={C.quiet}>{tail}</span>
      </text>
    </box>
  );
}

/**
 * One task row: marker, handle, status, prompt, then right-aligned telemetry. The prompt
 * absorbs the slack so steps/tokens and wall time line up down the board.
 */
export function TaskRow(props: {
  task: Task;
  selected: boolean;
  width: number;
  now: number;
  onSelect: (id: string) => void;
}) {
  const { task, selected, width, now, onSelect } = props;
  const meta = STATUS[task.status];
  const handle = taskHandle(task.id);
  const arrow = selected ? "›" : " ";
  const statusCell = meta.word.padEnd(8);
  // Selection is shown with an arrow, not a background highlight.
  const marker = selected ? " ←" : "";
  const progress = taskProgress(task);
  const age = taskDuration(task, now);
  const telemetry = [progress, age].filter(Boolean).join("  ");
  const tail = `${telemetry}${marker}`;
  // ` arrow  handle status` — the fixed left column, padding included.
  const chrome = 3 + handle.length + 1;
  const prompt = clip(
    task.prompt,
    Math.max(8, width - 2 - chrome - statusCell.length - tail.length),
  );
  const fill = Math.max(0, width - 2 - chrome - statusCell.length - prompt.length - tail.length);
  return (
    <box
      style={{
        height: 1,
        width: "100%",
        flexShrink: 0,
      }}
      onMouseDown={() => onSelect(task.id)}
    >
      <text>
        <span fg={selected ? C.accent : C.quiet}>{` ${arrow} `}</span>
        <span fg={selected ? C.dim : C.quiet}>{`${handle} `}</span>
        <span fg={C.faint}>{statusCell}</span>
        <span fg={selected ? C.text : C.dim}>{prompt}</span>
        <span>{fill > 0 ? " ".repeat(fill) : ""}</span>
        <span fg={C.quiet}>{telemetry}</span>
        <span fg={C.accent}>{marker}</span>
      </text>
    </box>
  );
}

/**
 * The run log's status line. The prompt gives up room first, then the optional counters;
 * `esc/enter: back` is never dropped, because a log you cannot leave is a trap.
 */
export function LogHeader({
  task,
  events,
  width,
  now,
}: {
  task: Task;
  events: number;
  width: number;
  now: number;
}) {
  const meta = STATUS[task.status];
  const label = `${meta.mark} ${meta.word}`;
  const essential = "esc/enter: back";
  const extras = [
    taskProgress(task),
    taskDuration(task, now),
    "ctrl-k",
    `${events} events`,
  ].filter(Boolean);
  // Grow the right side from the essential hint, so what gets dropped is always the tail.
  const rightParts = [essential];
  const budget = width - 2 - label.length - 12;
  for (const extra of extras) {
    if ([...rightParts, extra].join(" · ").length > budget) break;
    rightParts.push(extra);
  }
  const right = rightParts.join(" · ");
  const room = Math.max(0, width - 5 - label.length - right.length);
  const prompt = room >= 4 ? clip(task.prompt, room) : "";
  const gap = Math.max(0, width - 2 - label.length - (prompt ? prompt.length + 2 : 0) - right.length);
  return (
    <box style={{ height: 1, flexShrink: 0, flexDirection: "row", paddingLeft: 1, paddingRight: 1 }}>
      <text>
        <span fg={meta.color}>{meta.mark}</span>
        <span fg={C.dim}>{` ${meta.word}`}</span>
        {prompt ? <span fg={C.faint}>{`  ${prompt}`}</span> : null}
        <span>{" ".repeat(gap)}</span>
        <span fg={C.quiet}>{right}</span>
      </text>
    </box>
  );
}

/** The first screen: what this thing is for and the two keys that start a task. */
export function EmptyState({ width }: { width: number }) {
  const hint = clip("describe a task below — ctrl+enter queues it · enter opens a run", Math.max(10, width - 4));
  return (
    <box style={{ flexDirection: "column", flexShrink: 0, paddingLeft: 2, paddingTop: 1 }}>
      <text fg={C.text} attributes={TextAttributes.BOLD}>{"no tasks yet"}</text>
      <text fg={C.faint}>{hint}</text>
      <text fg={C.quiet}>{clip("every run is fire-and-forget: closing the tui leaves it running", Math.max(10, width - 4))}</text>
    </box>
  );
}
