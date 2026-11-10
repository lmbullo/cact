/**
 * board-task.tsx: one task on the board — the marker, the handle, the prompt and the numbers.
 *
 * WHAT IT IS
 *  `TaskRow`, the single line a task occupies: `›` marker, short `#handle`, the prompt, then the
 *  right-aligned telemetry (steps, tokens, cache, wall) and the error count. Re-exported from
 *  board-view.tsx, which is the import path the tests and the App use.
 *
 * ENTRY POINTS
 *  TaskRow({task, selected, width, now, onSelect})   one task, one line, click or key selects
 *
 * INVARIANTS
 *  - Exactly one row high (height 1, flexShrink 0); the board decides how many fit.
 *  - The row never repeats the status word: its section header already said it.
 *  - The prompt gives up room first, then the tail sheds the wall time, and only then is it
 *    clipped — a 60-column frame never wraps a row.
 *  - The error count rides only when the frame can hold it: gluing a clipped `3 err` to the
 *    counters would read as one number.
 *
 * WHERE TO CHANGE
 *  What a row says .... the spans at the end of TaskRow
 *  What it sheds first .... telemetry/limit in TaskRow   The numbers .... list-model taskCounters
 */
import { C, clip } from "./theme";
import { taskDuration, taskHandle, taskProgress } from "./list-model";
import type { Task } from "./types";

/**
 * One task row: the `›` marker, the short handle, the prompt, then right-aligned telemetry.
 * The prompt absorbs the slack so steps/tokens/cache and wall time line up down the board,
 * and the row never repeats the status word — its section already said it.
 */
export function TaskRow(props: {
  task: Task;
  selected: boolean;
  width: number;
  now: number;
  onSelect: (id: string) => void;
}) {
  const { task, selected, width, now, onSelect } = props;
  const handle = `#${taskHandle(task.id)}`;
  const marker = selected ? "›" : " ";
  const progress = taskProgress(task);
  const age = taskDuration(task, now);
  const errors = task.errors && task.errors > 0 ? `${task.errors} err` : "";
  // The box pads one column each side, so the row shares `width - 2`:
  // `marker handle  prompt …    telemetry  errors`.
  const content = Math.max(0, width - 2);
  const lead = 1 + 1 + handle.length + 2; // marker, space, handle, two-space gap
  const limit = Math.max(0, content - lead); // the most the tail may take, prompt or not
  const errorPart = errors ? `  ${errors}` : "";
  const telemetry = (withAge: boolean) =>
    [progress, withAge ? age : ""].filter(Boolean).join("  ");
  // The prompt gives up room first; on a frame too narrow even for that, the tail sheds the
  // wall time before anything is clipped — one line at 60 columns, never a wrapped row.
  // The error count rides only when the frame can hold it: at that width the counters are all
  // that is left, and gluing a clipped `3 err` onto them would read as one number.
  const errText = errorPart.length > limit ? "" : errorPart;
  let shown = telemetry(Boolean(age));
  if (shown.length + errText.length > limit && age) shown = telemetry(false);
  if (shown.length + errText.length > limit) {
    shown = clip(shown, Math.max(0, limit - errText.length));
  }
  const right = `${shown}${errText}`;
  const room = content - lead - right.length - 1; // one column of air before the numbers
  const prompt = room >= 6 ? clip(task.prompt, room) : "";
  const fill = Math.max(0, content - lead - prompt.length - right.length);
  return (
    <box
      style={{ height: 1, width: "100%", flexShrink: 0, paddingLeft: 1, paddingRight: 1 }}
      onMouseDown={() => onSelect(task.id)}
    >
      <text>
        <span fg={selected ? C.accent : C.quiet}>{marker}</span>
        <span fg={selected ? C.faint : C.quiet}>{` ${handle}  `}</span>
        <span fg={selected ? C.text : C.dim}>{prompt}</span>
        <span>{" ".repeat(fill)}</span>
        <span fg={C.quiet}>{shown}</span>
        {errText ? <span fg={C.red}>{errText}</span> : null}
      </text>
    </box>
  );
}
