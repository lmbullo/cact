/**
 * server-worker.ts: the worker's stdout protocol — one line reader and the one interpreter.
 *
 * WHAT IT IS
 *  A worker speaks `@@TUI@@` JSON lines on stdout. `readWorkerStream` splits the stream into
 *  lines; `handleWorkerLine` is the only code that interprets one: `turn` folds live progress,
 *  `final` installs the run's numbers, everything else is journalled by worker-events.ts.
 *
 * ENTRY POINTS
 *  readWorkerStream(stream, onLine) -> Promise   split a byte stream into non-empty lines
 *  handleWorkerLine(task, raw)                   journal + apply one worker line
 *
 * INVARIANTS
 *  - The raw line is journalled first, uninterpreted: the machine log must hold the wire, not
 *    only what the server managed to parse out of it.
 *  - `final` replaces the live estimate: `progress` is dropped and its context/window move onto
 *    the task, so the board never prefers stale numbers.
 *  - `task.final` is always text: a structured `final` is serialized with `textOf`, never stored
 *    as an object (the typed `final_answer` carries one).
 *  - An unknown kind is one journal record and no task state.
 *
 * WHERE TO CHANGE
 *  Line splitting .... readWorkerStream    One wire kind .... the switch in handleWorkerLine
 */
import type { Task, TaskEvent } from "./types";
import { foldTurn } from "./list-model";
import { capString, capValue, journalWorkerEvent, parseWorkerLine, textOf } from "./worker-events";
import { appendEvent, appendLog, saveTask } from "./server-journal";
import { broadcast } from "./server-state";

/** Split a byte stream into non-empty lines, calling `onLine` for each; a read error is logged. */
export async function readWorkerStream(stream: ReadableStream<Uint8Array>, onLine: (line: string) => void): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (line.trim()) onLine(line);
        newline = buffer.indexOf("\n");
      }
    }
    if (buffer.trim()) onLine(buffer);
  } catch (error) {
    console.error("worker stream read failed", error);
  }
}

/** Journal one worker line and apply it to the task; the only interpreter of the protocol. */
export function handleWorkerLine(task: Task, raw: string): void {
  const line = raw.trimEnd();
  // The raw stdout line first, with no interpretation: the machine log must hold the wire, not
  // only what this server managed to parse out of it.
  appendLog(task, { kind: "stdout", text: capString(line, 12000) as string });
  const message = parseWorkerLine(line);
  if (!message) {
    appendEvent(task, { kind: "log", text: capString(line, 3000) as string });
    return;
  }

  switch (message.kind) {
    case "turn": {
      const data = capValue(message.data) as Record<string, unknown>;
      // Live progress: the final event carries the authoritative counters, but a running
      // task would otherwise show nothing until it ends. The fold itself (usage, cache split,
      // context size, window) lives in list-model.foldTurn, so the numbers a view formats and
      // the numbers a turn accumulates cannot drift apart.
      task.progress = foldTurn(task.progress, Number(message.turn ?? data.turn ?? 0), data);
      saveTask(task);
      broadcast({ type: "task", task });
      appendEvent(task, {
        kind: "turn",
        turn: message.turn,
        data,
      });
      break;
    }
    case "error":
      task.error = capString(textOf(message.text), 8000) as string;
      appendLog(task, { kind: "error", text: task.error });
      appendEvent(task, { kind: "error", text: task.error });
      break;
    case "final":
      task.stop = message.stop;
      task.steps = message.steps;
      task.calls = message.calls;
      task.llmCalls = message.llmCalls;
      task.errors = message.errors;
      task.usage = message.usage;
      task.wallMs = message.wallMs;
      // The final counters replace the live estimate, so the board never prefers stale numbers.
      // The context window is not a sum, so it is kept on the task instead of derived from
      // `usage` (whose prompt tokens are a total over turns), and `progress` is dropped.
      task.context = task.progress?.context;
      task.window = task.progress?.window;
      task.progress = undefined;
      task.final = capString(textOf(message.final), 64000) as string;
      // The run's numbers in one record, so an agent gets usage/errors without folding the turns.
      appendLog(task, {
        kind: "usage",
        status: message.status,
        stop: message.stop,
        steps: message.steps,
        calls: message.calls,
        llmCalls: message.llmCalls,
        errors: message.errors,
        usage: message.usage,
        wallMs: message.wallMs,
      });
      appendEvent(task, {
        kind: "final",
        text: task.final,
        data: {
          status: message.status,
          stop: message.stop,
          steps: message.steps,
          calls: message.calls,
          llmCalls: message.llmCalls,
          errors: message.errors,
          usage: message.usage,
          wallMs: message.wallMs,
        },
      });
      break;
    default:
      // cell.start / cell.end / check / log / a kind this server does not know: one journal
      // record and no task state, so the mapping lives in worker-events.ts with its own test.
      journalWorkerEvent(message, line, (partial: Omit<TaskEvent, "seq" | "ts">) =>
        appendEvent(task, partial),
      );
  }
}
