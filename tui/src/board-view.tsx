/**
 * Board view: the masthead, the task sections and one line per task.
 *
 * WHAT IT IS
 *  The presentational half of the board: a `TopBar` (brand, workspace, model and live
 *  counts), a `Rule` (the one hairline in the app, carrying the status note above the
 *  prompt), a `SectionHeader` (status dot, word and count, no rule of its own), a `TaskRow`
 *  (`#handle`, prompt, right-aligned steps/tokens/cache and wall time) and `EmptyState`.
 *
 * ENTRY POINTS
 *  TopBar({workspace, model, effort, running, total, connected, stale, width})
 *  Rule({width, note})                    the hairline above the prompt box
 *  SectionHeader({status, count})         `● running  1`
 *  TaskRow({task, selected, width, now, onSelect})  one task, one line, click or key selects
 *  LogHeader({task, width, now, check})  the run log's status line: state, then how it ended
 *  EmptyState({width})                    the first screen, before any task exists
 *
 * INVARIANTS
 *  - Every row is exactly one line high with `flexShrink: 0`: the parent decides how many
 *    rows fit (list-model.followSelection), so a row must never grow or wrap.
 *  - Status text and dots come from theme.STATUS, so the board and the log cannot disagree.
 *  - The section label already carries the state, so a row never repeats the status word:
 *    a column of prompts reads as prose, not as a table with a redundant column.
 *  - Selection is a `›` marker plus ink contrast, not a background fill and not a second
 *    marker on the right (keeps the palette calm and the row quiet).
 *  - The handle is the short `#suffix` of the task id; the full id stays in the state root
 *    and the HTTP API, where diagnosis needs it.
 *  - Telemetry (steps, tokens in/out, cache rate, wall time) is right-aligned and the prompt
 *    gives up room, so the eye can scan one column of numbers without the prompt jittering
 *    the layout. Every number comes from list-model.taskCounters/taskUsage, which is also
 *    what the log header formats: a row and a log cannot disagree about a task.
 *  - The log header is the single source of the run's state: dot, word and handle come from
 *    `Task.status`, once. The log body never repeats them (log-view.isSilent) and the header
 *    does not repeat the prompt either — that block is the first thing in the log, in full.
 *  - The header also owns how a settled run ended — `Task.stop`, `Task.errors` and the last
 *    failed `check` — so the closing summary block carries only the cell count. A live run
 *    prints none of it: there is no stop reason yet and the counters must not move.
 *
 * WHERE TO CHANGE
 *  The top bar's contents or priority .... TopBar
 *  Row contents or wording ............... TaskRow, SectionHeader, LogHeader
 *  Section grouping or ordering .......... list-model.buildRows / orderTasks (not here)
 *  Colours ............................... theme.ts
 */
import { TextAttributes } from "@opentui/core";
import type { RGBA } from "@opentui/core";
import { C, STATUS, clip, shortPath } from "./theme";
import { taskCounters, taskDuration, taskHandle, taskProgress } from "./list-model";
import type { CheckState, Task, TaskStatus } from "./types";

/**
 * The one-row masthead: who we are, where we are, and what the server is doing. The right
 * side is sacrificed first when the terminal narrows; the brand and workspace survive.
 *
 * The right-hand bits are `model · effort · N running · offline|idle`, then `stale` last, in
 * the clay of `offline`, when the last snapshot said the server is running older code. It is a
 * word in that same one row — the line is clipped, never wrapped.
 */
export function TopBar(props: {
  workspace: string;
  model?: string;
  effort?: string;
  running: number;
  total: number;
  connected: boolean;
  /** The last snapshot reported a server whose own source changed on disk since boot. */
  stale?: boolean;
  width: number;
}) {
  const { workspace, model, effort, running, total, connected, stale = false, width } = props;
  const brand = "▌ code";
  const inner = Math.max(0, width - 2);

  const bits = [model, effort].filter((bit): bit is string => Boolean(bit));
  if (running > 0) bits.push(`${running} running`);
  if (!connected) bits.push("offline");
  else if (total === 0) bits.push("idle");
  // `stale` has its own hue, so it is appended separately; `bits` stays the faint cluster.
  const staleWord = stale ? "stale" : "";
  const right = bits.join(" · ");
  const rightAll = staleWord ? (right ? `${right} · ${staleWord}` : staleWord) : right;
  const rightText = clip(rightAll, inner);
  // Only paint it clay when the clip kept it; a cut `stale` is not a signal, it is an ellipsis.
  const staleShown = Boolean(staleWord) && rightText.endsWith(staleWord);
  const faintText = staleShown ? rightText.slice(0, rightText.length - staleWord.length) : rightText;

  const room = inner - rightText.length - (rightText ? 2 : 0);
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
        <span fg={connected ? C.faint : C.red}>{faintText}</span>
        {staleShown ? <span fg={C.red}>{staleWord}</span> : null}
      </text>
    </box>
  );
}

/** The one hairline; the optional `note` is sacrificed to keep the line on one row. */
export function Rule({ width, note }: { width: number; note?: string }) {
  // Keep the hint visible: shrink the rule instead of letting the note run off-screen,
  // and clip the note itself when the status and scroll counters together outgrow it.
  const hint = note ? ` ${clip(note, Math.max(0, width - 1))}` : "";
  const line = "─".repeat(Math.max(0, width - hint.length));
  return note ? (
    <text fg={C.rule}>
      {line}
      <span fg={C.faint}>{hint}</span>
    </text>
  ) : (
    <text fg={C.rule}>{line}</text>
  );
}

/**
 * A status band: a coloured dot, the state word and its count. No hairline — the blank row
 * between bands and the dot's colour are enough to separate them, and the board keeps a
 * single rule for the prompt.
 */
export function SectionHeader({ status, count }: { status: TaskStatus; count: number }) {
  const meta = STATUS[status];
  return (
    <box style={{ height: 1, width: "100%", flexShrink: 0, paddingLeft: 1, paddingRight: 1 }}>
      <text>
        <span fg={meta.color}>{meta.mark}</span>
        <span fg={C.dim}>{` ${meta.word}`}</span>
        <span fg={C.quiet}>{`  ${count}`}</span>
      </text>
    </box>
  );
}

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
  const telemetry = [progress, age].filter(Boolean).join("  ");
  const right = `${telemetry}${errors ? `  ${errors}` : ""}`;
  // The box pads one column each side, so the row shares `width - 2`:
  // `marker handle  prompt …    telemetry  errors`.
  const content = Math.max(0, width - 2);
  const lead = 1 + 1 + handle.length + 2; // marker, space, handle, two-space gap
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
        <span fg={C.quiet}>{telemetry}</span>
        {errors ? <span fg={C.red}>{`  ${errors}`}</span> : null}
      </text>
    </box>
  );
}

/**
 * The run log's status line, and the log's one source of truth for the run's state: the dot,
 * the status word and the handle come from `Task.status` and appear here and nowhere else. Once
 * the run has settled, the line also says how it ended: `Task.stop` (DONE, TRUNCATED,
 * GOAL_UNMET, ...), `Task.errors` when it recorded any, and — when the last `check` event of the
 * journal failed — the check's name and the first failing line of its output, clipped. A live
 * task has no stop reason yet, so its line keeps exactly the shape it had before this existed.
 *
 * The live counters (steps, tokens in/out, duration, cache hit rate, context window) sit on the
 * right and are the only place those numbers are printed — the log body never repeats them and
 * the board row formats the same `taskCounters` parts. The prompt is not repeated (the log
 * opens with it in full), and `esc/enter: back` is never dropped — a log you cannot leave is a
 * trap. The extras are dropped from the tail as the terminal narrows, so the order is by
 * priority: the reason is fixed, the failing check keeps a third of the line, then the hint,
 * then the counters from the tail.
 */
export function LogHeader(props: {
  task: Task;
  width: number;
  now: number;
  /** The task's last `check` event, if its journal has one; see log-view.lastCheck. */
  check?: CheckState | null;
}) {
  const { task, width, now, check } = props;
  const meta = STATUS[task.status];
  const label = `${meta.mark} ${meta.word}`;
  const handlePart = `  #${taskHandle(task.id)}`;
  const essential = "esc/enter: back";
  const counters = taskCounters(task);
  // Steps and tokens, then the clock, then cache and context: what a narrow frame drops is
  // always the tail of that list, never the counters that move.
  const extras = [
    ...counters.slice(0, 2),
    taskDuration(task, now),
    ...counters.slice(2),
  ].filter(Boolean);
  // The box pads one column each side, so the line shares `width - 2`.
  const content = Math.max(0, width - 2);

  // How the run ended, in priority order. A queued/running task has no stop reason yet, so the
  // cluster stays empty and the counters keep the exact place this line gave them before.
  const live = task.status === "queued" || task.status === "running";
  const stopPart = live ? "" : (task.stop ?? "").trim();
  const errorPart = !live && task.errors && task.errors > 0 ? `${task.errors} errors` : "";
  const reasons: { text: string; color: RGBA }[] = [];
  if (stopPart) reasons.push({ text: stopPart, color: C.faint });
  if (errorPart) reasons.push({ text: errorPart, color: C.red });
  const reasonRoom = reasons.reduce((sum, part, index) => sum + (index ? 3 : 2) + part.text.length, 0);
  const left = label + handlePart;

  // A failing check is the point of the line, so it keeps its share before the counters grow;
  // a passing one (or none) adds nothing, and the counters fill the line as they always have.
  const failure = !live && check && !check.ok ? `${check.name}: ${check.line ?? ""}`.trim() : "";
  const reserve = failure ? Math.max(12, Math.floor(content / 3)) : 0;

  // Grow the right side from the essential hint, so what gets dropped is always the tail.
  const rightParts = [essential];
  const joined = (parts: string[]) => parts.join(" · ");
  for (const extra of extras) {
    const candidate = [...rightParts, extra];
    if (left.length + reasonRoom + 2 + joined(candidate).length + reserve + 1 > content) break;
    rightParts.push(extra);
  }
  const right = joined(rightParts);

  // What the counters left over is the check's, clipped to it; the gap soaks up the rest.
  const room = content - left.length - reasonRoom - right.length - 3;
  const checkText = failure && room > 1 ? clip(failure, room) : "";
  const checkPart = checkText ? `  ${checkText}` : "";
  const gap = Math.max(1, content - left.length - reasonRoom - checkPart.length - right.length);
  return (
    <box style={{ height: 1, flexShrink: 0, flexDirection: "row", paddingLeft: 1, paddingRight: 1 }}>
      <text>
        <span fg={meta.color}>{meta.mark}</span>
        <span fg={C.dim}>{` ${meta.word}`}</span>
        <span fg={C.quiet}>{handlePart}</span>
        {reasons.map((part, index) => (
          <span key={`${index}:${part.text}`} fg={part.color}>{`${index ? " · " : "  "}${part.text}`}</span>
        ))}
        {checkPart ? <span fg={C.red}>{checkPart}</span> : null}
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
    <box style={{ flexDirection: "column", flexShrink: 0, paddingLeft: 2 }}>
      <text fg={C.text}>{"no tasks yet"}</text>
      <text fg={C.faint}>{hint}</text>
      <text fg={C.quiet}>{clip("every run is fire-and-forget: closing the tui leaves it running", Math.max(10, width - 4))}</text>
    </box>
  );
}
