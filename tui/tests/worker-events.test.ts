/**
 * Worker lines: one `@@TUI@@` line, one journal record.
 *
 * WHAT IT IS
 *  `journalWorkerEvent` is the mapping server.ts `handleWorkerLine` uses for every kind that keeps
 *  no state on the task, and these cases are its contract. A real wire line (prefix + JSON) is
 *  parsed by `parseWorkerLine` and journaled through a stub `append`, so nothing is written, no
 *  port is opened and no worker is spawned: a `cell.end` line has to arrive as exactly one
 *  compact event, because bench/task_journal.py reads calls and rejections back out of it.
 *
 * ENTRY POINTS
 *  EVENT_PREFIX / parseWorkerLine / journalWorkerEvent from ../src/worker-events
 *  journal(line) ... one wire line in, the events it appended out
 *
 * INVARIANTS
 *  - Exactly one event per message: a kind that also fell through to the `log` fallback would
 *    double every call in the journal.
 *  - `cell.end` keeps the compact fields and no payload body (`text`/`args` stay undefined), and
 *    `seq`/`ts` are the server's, so the extracted record never invents them.
 *  - An unknown kind falls back to a `log` event carrying the raw line, unchanged from before the
 *    switch moved here: a worker this server predates still shows up in the log.
 *
 * WHERE TO CHANGE
 *  What a kind journals .... src/worker-events.ts (journalWorkerEvent)
 *  A kind that mutates a task ... src/server.ts (handleWorkerLine); this file cannot reach it
 *
 * The server also journals these process kinds from handleWorkerLine: started, queued, pid,
 * stderr, finished, error, final, log and turn. They are named here so the static wiring gate
 * can see every kind a journal can contain.
 */
import { expect, test } from "bun:test";
import { EVENT_PREFIX, journalWorkerEvent, parseWorkerLine, type JournalEvent } from "../src/worker-events";

/** Journal one wire line the way server.ts does: parse it, then hand the message to the mapper. */
function journal(line: string): JournalEvent[] {
  const events: JournalEvent[] = [];
  const message = parseWorkerLine(line);
  if (!message) return events;
  journalWorkerEvent(message, line, (event) => events.push(event));
  return events;
}

const wire = (message: Record<string, unknown>): string => `${EVENT_PREFIX}${JSON.stringify(message)}`;

test("a failed cell.end line journals one compact event with its page and chars", () => {
  const line = wire({
    kind: "cell.end",
    cell: 7,
    tool: "read",
    ok: false,
    verb_kind: "read",
    code: "bad_arguments",
    page: { shown: 3, total: 9, unit: "line", next: { start: 4 } },
    chars: 4211,
    duration_ms: 12,
    notes: ["path[0] must be a string"],
  });
  const events = journal(line);
  // One call, one record: task_journal counts calls from these lines.
  expect(events.length).toBe(1);
  const event = events[0]!;
  expect(event.kind).toBe("cell.end");
  expect(event.cell).toBe(7);
  expect(event.tool).toBe("read");
  expect(event.ok).toBe(false);
  expect(event.verb_kind).toBe("read");
  expect(event.code).toBe("bad_arguments");
  expect(event.page).toEqual({ shown: 3, total: 9, unit: "line", next: { start: 4 } });
  expect(event.chars).toBe(4211);
  expect(event.duration_ms).toBe(12);
  expect(event.notes).toEqual(["path[0] must be a string"]);
  // Compact: the payload body never reaches the journal, and seq/ts belong to appendEvent.
  expect(event.text).toBeUndefined();
  expect(event.args).toBeUndefined();
  expect(event.seq).toBeUndefined();
  expect(event.ts).toBeUndefined();

  // A bare call still journals: a missing page is null and the notes list is never undefined.
  const bare = journal(wire({ kind: "cell.end", cell: 8, tool: "edit", ok: true }));
  expect(bare.length).toBe(1);
  expect(bare[0]!.page).toBeNull();
  expect(bare[0]!.notes).toEqual([]);
  expect(bare[0]!.ok).toBe(true);
});

test("an unknown kind still falls back to the log event it does today", () => {
  const line = wire({ kind: "verb.audit", text: "a kind this server predates" });
  const events = journal(line);
  expect(events.length).toBe(1);
  expect(events[0]!.kind).toBe("log");
  // The raw line, prefix and all: the fallback is what makes an unknown worker readable.
  expect(events[0]!.text).toBe(line.slice(0, 3000));

  // A line that is not ours at all is not a message; server.ts journals it as a plain log line.
  expect(parseWorkerLine("plain stdout from the worker")).toBeNull();
  expect(journal("plain stdout from the worker")).toEqual([]);
});
