/**
 * Board view: the task list rows, one line each.
 *
 * WHAT IT IS
 *  The presentational half of the board: a `Rule` (a horizontal rule that shrinks to keep its
 *  status note on screen), a `SectionHeader` per status group, and a `TaskRow` (handle,
 *  status word, live progress, clipped prompt, selection arrow).
 *
 * ENTRY POINTS
 *  Rule({width, note})                    the rule drawn above/below the prompt box
 *  SectionHeader({status, count})         `─ running (3)`
 *  TaskRow({task, selected, width, onSelect})   one task, one line, click or key selects it
 *
 * INVARIANTS
 *  - Every row is exactly one line high with `flexShrink: 0`: the parent decides how many
 *    rows fit (list-model.scrollWindow), so a row must never grow or wrap.
 *  - Status text comes from theme.STATUS, so the board and the log cannot disagree.
 *  - Selection is an arrow plus colour, not a background fill (keeps the palette calm).
 *
 * WHERE TO CHANGE
 *  Row contents or wording ............. TaskRow
 *  Section grouping or ordering ........ list-model.buildRows / orderTasks (not here)
 *  Colours ............................. theme.ts
 */
import { TextAttributes } from "@opentui/core";
import { C, STATUS, clip } from "./theme";
import { taskHandle, taskProgress } from "./list-model";
import type { Task, TaskStatus } from "./types";

export function Rule({ width, note }: { width: number; note?: string }) {
  // Keep the hint visible: shrink the rule instead of letting the note run off-screen.
  const hint = note ? ` ${note}` : "";
  const line = "─".repeat(Math.max(0, width - hint.length));
  return note ? <text fg={C.faint}>{`${line}${hint}`}</text> : <text fg={C.faint}>{line}</text>;
}

export function SectionHeader({ status, count }: { status: TaskStatus; count: number }) {
  const meta = STATUS[status];
  return (
    <box style={{ height: 1, width: "100%", flexShrink: 0 }}>
      <text>
        <span fg={meta.color} attributes={TextAttributes.BOLD}>{`─ ${meta.word} `}</span>
        <span fg={C.faint}>{`(${count})`}</span>
      </text>
    </box>
  );
}

export function TaskRow(props: {
  task: Task;
  selected: boolean;
  width: number;
  onSelect: (id: string) => void;
}) {
  const { task, selected, width, onSelect } = props;
  const meta = STATUS[task.status];
  const handle = taskHandle(task.id);
  const head = `${selected ? "›" : " "} ${handle} `;
  // Selection is shown with an arrow, not a background highlight.
  const marker = selected ? " ←" : "";
  // Live counters sit between the status and the prompt, so a running row shows progress.
  const progress = taskProgress(task);
  const progressCell = progress ? `${clip(progress, 20).padEnd(20)} ` : "";
  const prompt = clip(
    task.prompt,
    Math.max(8, width - head.length - meta.word.length - progressCell.length - marker.length - 2),
  );
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
        <span fg={selected ? C.accent : C.faint}>{head}</span>
        <span fg={meta.color}>{meta.word.padEnd(9)}</span>
        {progressCell ? <span fg={C.faint}>{progressCell}</span> : null}
        <span fg={selected ? C.text : C.dim}>{` ${prompt}`}</span>
        <span fg={selected ? C.accent : C.faint}>{marker}</span>
      </text>
    </box>
  );
}
