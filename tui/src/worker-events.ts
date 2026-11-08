/**
 * worker-events.ts: one `@@TUI@@` line from python/worker.py becomes one journal record.
 *
 * WHAT IT IS
 *  The stateless half of server.ts's worker-message handling. `parseWorkerLine` turns a stdout
 *  line into a WorkerMessage; `journalWorkerEvent` turns every kind that keeps no state on the
 *  Task — `cell.start`, `cell.end`, `check`, `log`, and an unknown kind — into exactly one event,
 *  handed to the caller's `append` (server.ts passes one that adds `seq`/`ts` and writes the
 *  journal). `cell.end` is the compact per-call record `{cell, tool, ok, verb_kind, code, page,
 *  chars, duration_ms, notes}` that bench/task_journal.py prefers over the payload. `capString`
 *  and `capValue` are the caps every journaled payload passes through.
 *
 * ENTRY POINTS
 *  EVENT_PREFIX                      the marker python/worker.py prefixes its JSON lines with
 *  parseWorkerLine(line)             line -> WorkerMessage, or null when it is not one of ours
 *  journalWorkerEvent(msg, line, append)  appends exactly one JournalEvent for the pure kinds
 *  capString(value, limit)           a string capped at `limit` chars, unchanged otherwise
 *  capValue(value)                   args/data capped by string length, item count and depth
 *  JournalEvent                      Omit<TaskEvent, "seq" | "ts">
 *
 * INVARIANTS
 *  - Exactly one event per message: no kind may reach both its own branch and the `log` fallback,
 *    because bench/task_journal.py counts calls and rejections by reading these lines back.
 *  - Importable without side effects, which is why this file reads only types: no task, no file,
 *    no socket. `tests/worker-events.test.ts` journals a real wire line through it.
 *  - The task-mutating kinds stay in server.ts `handleWorkerLine`: `turn` folds progress, `error`
 *    sets `Task.error`, `final` installs the authoritative counters.
 *  - `cell.end` carries no payload body: `text` and `args` are absent by construction; only the
 *    notes are capped, because those are free text the agent wrote.
 *  - An unknown kind journals the raw line as `log`, so a worker newer than this server still
 *    leaves a readable journal instead of dropping the line.
 *
 * WHERE TO CHANGE
 *  A worker message kind ......... python/worker.py (source of truth) + types.ts WorkerMessage
 *  What a kind journals .......... journalWorkerEvent
 *  A kind that mutates the task .. server.ts handleWorkerLine
 *  The caps ...................... capString / capValue
 */
import type { TaskEvent, WorkerMessage } from "./types";

/** python/worker.py prefixes every machine-readable line with this; anything else is a log line. */
export const EVENT_PREFIX = "@@TUI@@";

/** One journal line without the fields appendEvent adds: what a message contributes to the journal. */
export type JournalEvent = Omit<TaskEvent, "seq" | "ts">;

/** A string capped at `limit` chars (the cut is marked), or the value untouched. */
export function capString(value: unknown, limit: number): unknown {
  return typeof value === "string" && value.length > limit ? `${value.slice(0, limit)}\n… truncated` : value;
}

/** The same caps applied recursively to a journaled payload: strings, 40 items, depth 12. */
export function capValue(value: any, depth = 0): any {
  if (depth > 12) return "[depth]";
  if (typeof value === "string") return capString(value, 16000);
  if (typeof value === "number" || typeof value === "boolean" || value === null) return value;
  if (Array.isArray(value)) return value.slice(0, 40).map((item) => capValue(item, depth + 1));
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) out[key] = capValue(item, depth + 1);
    return out;
  }
  return String(value);
}

/** The message on a worker stdout line, or null when the line is not a `@@TUI@@` JSON payload. */
export function parseWorkerLine(line: string): WorkerMessage | null {
  if (!line.startsWith(EVENT_PREFIX)) return null;
  try {
    return JSON.parse(line.slice(EVENT_PREFIX.length)) as WorkerMessage;
  } catch {
    return null;
  }
}

/**
 * Journal one message that keeps no state on the task, calling `append` exactly once.
 * `line` is the raw stdout line the message came from: the fallback kind journals it verbatim,
 * so a worker this server does not understand still shows up in the log.
 */
export function journalWorkerEvent(
  message: WorkerMessage,
  line: string,
  append: (event: JournalEvent) => void,
): void {
  switch (message.kind) {
    case "cell.start":
      append({
        kind: "cell.start",
        cell: message.cell,
        tool: message.tool,
        args: capValue(message.args ?? {}) as Record<string, unknown>,
        code: message.code ? (capString(message.code, 12000) as string) : undefined,
      });
      return;
    case "cell.end":
      // Journal history only: one compact record per call for task_journal. No payload body.
      append({
        kind: "cell.end",
        cell: message.cell,
        tool: message.tool,
        ok: message.ok,
        verb_kind: message.verb_kind,
        code: message.code,
        page: message.page ?? null,
        chars: message.chars,
        duration_ms: message.duration_ms,
        notes: (message.notes ?? []).map((note) => capString(note, 500) as string),
      });
      return;
    case "check":
      // The verdict rides its own event so the log header can name a failed check from the
      // journal alone: the turn that caused it says only pass/fail. Capped like any payload.
      append({
        kind: "check",
        check: {
          ok: Boolean(message.ok),
          name: String(capString(message.name ?? "", 200)),
          line: String(capString(message.line ?? "", 600)),
        },
      });
      return;
    case "log":
      append({ kind: "log", text: capString(message.text, 4000) as string });
      return;
    default:
      append({ kind: "log", text: line.slice(0, 3000) });
  }
}
