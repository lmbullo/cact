/** Board view: the masthead, the task sections and one line per task.
 *
 * WHAT IT IS
 * The presentational half of the board: TopBar, Rule (the one hairline), SectionHeader, TaskRow, LogHeader, EmptyState.
 *
 * ENTRY POINTS
 * TopBar({workspace, model, effort, running, total, connected, stale, width})
 * Rule({width, note})  hairline above the prompt box   SectionHeader({status, count})  `● running  1`
 * TaskRow({task, selected, width, now, onSelect})  one task, one line, click or key selects
 * LogHeader({task, width, now, check})  the run log's status line: state, then how it ended
 * EmptyState({width})  the first screen, before any task exists
 *
 * INVARIANTS
 * every row is exactly one line high (flexShrink: 0); the parent decides how many fit (list-model.followSelection).
 * status text and dots come from theme.STATUS; the section label carries the state, so a row never repeats the status word.
 * selection = a `›` marker plus ink contrast, not a background fill and not a second marker; the handle is the `#suffix`.
 * telemetry (steps, tokens, price, cache, wall) is right-aligned from list-model.taskCounters/taskUsage/taskCost; a row and a log agree.
 * a row sheds its wall time, then clips, once the prompt is gone: a 60-column frame never wraps a row.
 * the log header is the single source of the run's state: Task.status once, plus Task.stop/Task.errors/last check when settled;
 * a live run prints no stop reason, and the closing block carries only the cell count.
 *
 * WHERE TO CHANGE
 * top bar contents/priority ... TopBar   row contents/wording ... TaskRow, SectionHeader, LogHeader   colours ... theme.ts
 * section grouping/ordering ... list-model.buildRows / orderTasks (not here)
 */
import { TextAttributes } from "@opentui/core";
import type { RGBA } from "@opentui/core";
import { C, STATUS, clip, shortPath } from "./theme";
import { taskCounters, taskDuration, taskHandle } from "./list-model";
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

/**
 * The one hairline; the optional `note` and `lead` sit at its left end and are sacrificed to
 * keep the line on one row. `lead` is the live spinner, so the prompt box shows work at the
 * top-left of its frame.
 */
export function Rule({ width, note, lead }: { width: number; note?: string; lead?: string }) {
  // Keep the hint visible: shrink the rule instead of letting the note run off-screen,
  // and clip the note itself when the status and scroll counters together outgrow it.
  const leadPart = lead ? `${lead} ` : "";
  const hint = note ? ` ${clip(note, Math.max(0, width - 1 - leadPart.length))}` : "";
  const line = "─".repeat(Math.max(0, width - leadPart.length - hint.length));
  return (
    <text fg={C.rule}>
      {leadPart ? <span fg={C.accent}>{leadPart}</span> : null}
      {line}
      {hint ? <span fg={C.faint}>{hint}</span> : null}
    </text>
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

export { TaskRow } from "./board-task";

/**
 * The run log's status line, and the log's one source of truth for the run's state: the dot,
 * the status word and the handle come from `Task.status` and appear here and nowhere else. Once
 * the run has settled, the line also says how it ended: `Task.stop` (DONE, TRUNCATED,
 * GOAL_UNMET, ...), `Task.errors` when it recorded any, and — when the last `check` event of the
 * journal failed — the check's name and the first failing line of its output, clipped. A live
 * task has no stop reason yet, so its line keeps exactly the shape it had before this existed.
 *
 * The live counters (steps, tokens in/out, the run's price, duration, cache hit rate, context
 * window) follow the left cluster and are the only place those numbers are printed — the log
 * body never repeats them and the board row formats the same `taskCounters` parts. Everything
 * on the row is anchored left: nothing is pushed to the terminal's right edge. The prompt is
 * not repeated (the log opens with it in full), and `esc/enter: back` is never dropped — a log
 * you cannot leave is a trap. The extras are dropped from the tail as the terminal narrows, so
 * the order is by priority: the reason is fixed, the failing check keeps a third of the line,
 * then the hint, then the counters from the tail.
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
  // Steps and tokens, then the clock, then price, cache and context: what a narrow frame drops
  // is always the tail of that list, never the counters that move.
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

  // What the counters left over is the check's, clipped to it.
  // Reserve the fixed gap and the check part's own two-column prefix as well.
  const room = content - left.length - reasonRoom - right.length - 4;
  const checkText = failure && room > 1 ? clip(failure, room) : "";
  const checkPart = checkText ? `  ${checkText}` : "";
  // Everything is anchored left: a fixed two-column gap, never the rest of the line.
  const gap = 2;
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
