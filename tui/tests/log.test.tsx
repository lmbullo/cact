/**
 * Live log: the prompt it started from, the tool calls as they are made, then the summary.
 *
 * WHAT IT IS
 *  A stub server sends a running task, then `push(seq, event)` feeds synthetic `TaskEvent`s
 *  over the WebSocket exactly as python/worker.py emits them. The frame must open with the whole
 *  prompt under a `▸ you` head, draw each `cell.start` as a diagnostic row, add no row for the
 *  lifecycle or checks, keep the prompt box where it was, and close on the summary block under
 *  `▸ summary` in the same column the prompt used.
 *
 *  The run's state is rendered exactly once — by the log header, from `Task.status` — so the
 *  body must never repeat the status word and the lifecycle events must add no row at all. The
 *  header also says how a settled run ended (stop reason, error count, last failed check) and
 *  it is the only place the counters are printed, which is why the closing block carries only
 *  its head.
 *
 * ENTRY POINTS
 *  App from ../src/client; Bun.serve stub with `sockets[]` and `push(seq, event)`
 *  captureCharFrame() for text
 *
 * INVARIANTS
 *  - Event fixtures mirror the `emit({...})` payloads of python/worker.py; a new kind added
 *    there without a case here means the panel can drop it silently.
 *  - A prose block is headed by a `▸` word (`you`, `summary`) and its body wraps unclipped; a
 *    diagnostic row (a rejected finish, an error) stays machine-first `field=value`.
 *  - A `cell.start` renders one diagnostic row (`kind=cell tool=… args=…`); a `cell.end` is
 *    journal history and adds no row, so a call is never named twice.
 *  - Exactly one status word per log is the invariant: the header's. The same holds for the
 *    counters (turn, tokens in/out, price, cache rate, context window): the header prints them
 *    once and the closing block repeats none of them. How the run ended — the stop reason, the
 *    error count, the failed check — is the header's line too, never the summary block's.
 *  - A successful stop `DONE` is the status word `done`: the header never prints both.
 *  - A lifecycle row or a second status word on the closing block is a regression, not a
 *    wording preference.
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  How an event renders .... src/log-view.tsx (EventView)
 *  Which kinds are silent .. src/log-rows.tsx (isSilent)
 *  The opening block ........ src/log-rows.tsx (PromptView)
 *  The status line .......... src/board-view.tsx (LogHeader)
 *  The last verdict .......... src/log-view.tsx (lastCheck) + the `check` event
 *  A new event kind ........ python/worker.py emit(), src/types.ts, a case here
 */
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";

const baseTask = { id: "t-eeee5555", prompt: "live job", status: "running", createdAt: 1 };
/** The task the stub serves; a later test swaps in a long prompt before it connects. */
let task = baseTask;
const sockets: any[] = [];

const server = Bun.serve({
  hostname: "127.0.0.1",
  port: 0,
  fetch(req, srv) {
    const url = new URL(req.url);
    if (url.pathname === "/ws") {
      if (srv.upgrade(req, {})) return;
      return new Response("no", { status: 400 });
    }
    if (url.pathname.endsWith("/events")) return Response.json({ events: [] });
    return Response.json({ error: "not found" }, { status: 404 });
  },
  websocket: {
    open(ws: any) {
      sockets.push(ws);
      ws.send(JSON.stringify({
        type: "snapshot",
        server: { workspace: "/tmp/ws", stateDir: "/tmp/s", pid: 1, port: 1, version: "t",
          startedAt: 1, model: "m", effort: "low", concurrency: 1, allowRun: true,
          hasKey: true, python: "python3" },
        tasks: [task],
      }));
    },
    message() {},
    close() {},
  },
});
const httpUrl = `http://127.0.0.1:${server.port}`;
afterAll(() => server.stop(true));

const settle = async () => {
  await new Promise((r) => setTimeout(r, 60));
};

test("live log opens on the prompt and closes on the summary", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 80, height: 24 });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("eeee5555"));
  await flush();
  mockInput.pressEnter();
  await settle();
  await flush();
  expect(captureCharFrame()).toContain("esc/enter: back");

  // The prompt box is visible (same dimensions) on the log screen too.
  const logFrame = captureCharFrame();
  expect(logFrame).toContain("›");
  expect(logFrame).toContain("task for /tmp/ws");

  // The log opens with the task's prompt as a block: a `▸ you` head, then the prompt in the
  // indented column (one box pad + four) that the final summary will also use.
  expect(logFrame).toContain("▸ you");
  const promptBody = logFrame.split("\n").find((line) => /^ {5}live job/.test(line))!;
  expect(promptBody).toMatch(/^ {5}live job/);
  const promptIndent = promptBody.length - promptBody.trimStart().length;
  // The prompt appears once: the header carries the state, not a clipped copy of the block.
  expect(logFrame.split("\n").filter((line) => line.includes("live job")).length).toBe(1);
  // Nothing in the body repeats the header: no status word, no `kind=` token, no counters.
  expect(logFrame).not.toContain("kind=");
  expect(logFrame).not.toContain("status=running");
  expect(logFrame).not.toContain("turn ");

  // The live log keeps the board's prompt box: a hairline above and below it, so the prompt
  // sits inside a closed frame at the bottom of the screen.
  const logRows = logFrame.split("\n");
  const promptRow = logRows.findIndex((line) => line.includes("task for /tmp/ws"));
  expect(promptRow).toBeGreaterThan(0);
  expect(logRows[promptRow - 1]).toContain("─");
  expect(logRows[promptRow + 1]).toContain("─");
  expect(promptRow).toBe(logRows.length - 3);

  // Exactly two horizontal rules in the whole frame: the prompt box's top and bottom lines.
  const rules = logRows
    .map((line, index) => ({ line, index }))
    .filter(({ line }) => line.includes("─"));
  expect(rules.map(({ index }) => index)).toEqual([promptRow - 1, promptRow + 1]);

  // A live run puts the spinner at the top-left of the prompt box's frame.
  expect(logRows[promptRow - 1]!.trimStart()).toMatch(/^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] /);

  // Everything on the log's status line is anchored left: the hint follows the handle with
  // the fixed two-column gap, not pushed to the terminal's right edge.
  const headerRow = logRows.find((line) => line.includes("● running"))!;
  expect(headerRow).toMatch(/#eeee5555 {2}esc\/enter: back/);
  // No counters yet: the task has no steps or usage, so the header prints no place-holder.
  expect(headerRow).not.toContain("turn ");
  expect(headerRow).not.toContain("steps");
  expect(headerRow).not.toContain("↑");
  const push = async (seq: number, event: Record<string, unknown>) => {
    sockets.at(-1)!.send(JSON.stringify({ type: "event", taskId: task.id, event: { seq, ts: 1, ...event } }));
    await settle();
    await flush();
  };

  // The body between the two prompt-box lines; the spinner above it may tick, so compare it.
  const body = (whole: string) => {
    const rows = whole.split("\n");
    const top = rows.findIndex((line) => line.includes("─"));
    const bottom = rows.findIndex((line, index) => index > top && line.includes("─"));
    return rows.slice(top + 1, bottom).join("\n");
  };

  // Tool calls are drawn as they are made: one diagnostic row with the tool and its arguments.
  await push(1, { kind: "cell.start", cell: 1, tool: "read", args: { path: "a.py" } });
  let frame = captureCharFrame();
  expect(frame).toContain("kind=cell");
  expect(frame).toContain("tool=read");
  expect(frame).toContain('args={"path":"a.py"}');

  // A code cell has no named verb, so its first line stands in; a long one is clipped to its
  // field, never wrapped over the pane.
  await push(2, { kind: "cell.start", cell: 2, tool: "code", code: `run(${JSON.stringify("x".repeat(400))})` });
  frame = captureCharFrame();
  expect(frame).toContain("tool=code");
  expect(frame).not.toContain("x".repeat(120));

  await push(3, { kind: "cell.start", cell: 3, tool: "exec", args: { argv: ["bun", "test"] } });
  frame = captureCharFrame();
  expect(frame).toContain("tool=exec");
  expect(frame).toContain("bun");

  // The result record is journal history, not a row: `tool=read` already named the call.
  const beforeEnd = captureCharFrame();
  await push(4, { kind: "cell.end", cell: 1, tool: "read", ok: true, verb_kind: "read", chars: 12, duration_ms: 4 });
  frame = captureCharFrame();
  expect(body(frame)).toBe(body(beforeEnd));

  // A turn is not rendered at all: no output, no line counts.
  const stdout = Array.from({ length: 200 }, (_, i) => `line ${i}`).join("\n");
  await push(5, { kind: "turn", turn: 1, data: { runs: ["run('pytest')"], observation: { stdout } } });
  frame = captureCharFrame();
  expect(frame).not.toContain("out 200L");
  expect(frame).not.toContain("run('pytest')");
  expect(frame).not.toContain("line 199");

  // A turn carrying a `notice` renders the reason a finish was rejected or staged;
  // a notice-free turn stays silent (asserted above).
  await push(6, {
    kind: "turn",
    turn: 2,
    data: { notice: "[finish] final_answer rejected: the project check still fails" },
  });
  frame = captureCharFrame();
  const noticeLine = frame.split("\n").find((line) => line.includes("final_answer rejected"))!;
  expect(noticeLine).toContain("kind=turn");
  expect(noticeLine).toContain("notice=");
  expect(noticeLine).toContain("project check still fails");

  // The final event closes the log with the summary block. The header above already owns the
  // run's state, its counters and how it ended (stop reason, error count, last failed check),
  // so the closing head is one word, `▸ summary`, and carries nothing else. That is the one
  // source of truth — no second status word, no copied turn, tokens, cache rate or context
  // window.
  const summary = "DONE: " + "y".repeat(300);
  await push(7, {
    kind: "final",
    text: summary,
    data: { status: "done", stop: "DONE", steps: 7, calls: 3, errors: 0, wallMs: 1234 },
  });
  sockets.at(-1)!.send(JSON.stringify({
    type: "task",
    task: {
      ...task,
      status: "done",
      stop: "DONE",
      startedAt: 1,
      steps: 7,
      usage: {
        prompt_tokens: 3600,
        completion_tokens: 610,
        total_tokens: 4210,
        prompt_cache_hit_tokens: 3000,
        prompt_cache_miss_tokens: 600,
      },
      context: 4210,
      window: 1_048_576,
    },
  }));
  await settle();
  await flush();
  frame = captureCharFrame();
  const finalRows = frame.split("\n");
  const header = finalRows.find((line) => line.includes("esc/enter: back"))!;
  expect(header).toContain("✓ done");
  // `✓ done` already says the run finished, so the stop word DONE is not printed twice.
  expect(header).not.toContain("DONE");
  expect(header).toContain("turn 7");
  expect(header).toContain("in 3.6k · out 610");
  // The price claims the room the cache rate used to have at 80 columns: the tail still gives
  // way, and the number that moves (turn, tokens, price) is kept before the rate.
  expect(header).toContain("<$0.01");
  expect(header).not.toContain("cache 83%");
  // One status word on the whole log: the header's.
  expect(finalRows.filter((line) => line.includes("✓ done"))).toEqual([header]);
  const finalLine = finalRows.find((line) => line.includes("▸ summary"))!;
  // The closing head is one word: the summary below it is the fact the reader came for.
  expect(finalLine).not.toContain("kind=");
  expect(finalLine).not.toContain("cells");
  expect(finalLine).not.toContain("stop=");
  expect(finalLine).not.toContain("errors");
  expect(finalLine).not.toContain("turn");
  expect(finalLine).not.toContain("↑");
  expect(finalLine).not.toContain("cache");
  expect(finalLine).not.toContain("done");
  // The summary body is the wrapped block below it, and no word of it is lost.
  const summaryStart = finalRows.findIndex((line) => line.includes("DONE: y"));
  expect(summaryStart).toBeGreaterThan(finalRows.indexOf(finalLine));
  expect(finalRows[summaryStart]!).toMatch(/^ {1,7}DONE: y/);
  // The summary starts in the same column the prompt did: the log's two ends line up.
  expect(finalRows[summaryStart]!.length - finalRows[summaryStart]!.trimStart().length).toBe(promptIndent);
  const shown = frame.split("").filter((ch) => ch === "y").length;
  expect(shown).toBeGreaterThanOrEqual(300);
  expect(finalRows.every((line) => line.length <= 80)).toBe(true);

  // An unknown event kind shows its kind field only: its payload is never dumped.
  await push(8, { kind: "observation", text: "z".repeat(300), data: { stdout: "w".repeat(300) } });
  frame = captureCharFrame();
  expect(frame).toContain("kind=observation");
  expect(frame).not.toContain("z".repeat(120));
  expect(frame).not.toContain("w".repeat(120));

  // The lifecycle kinds are the task's state, which the header already shows: they add no row.
  // In particular a journal `queued` row can never sit stale above a run that has moved on.
  const before = captureCharFrame();
  await push(9, { kind: "queued", text: "queued" });
  await push(10, { kind: "started", text: "agent started (deepseek-flash, effort=low)" });
  await push(11, { kind: "pid", text: "worker pid 4321" });
  await push(12, { kind: "finished", text: "done" });
  const after = captureCharFrame();
  expect(after).toBe(before);
  expect(after).not.toContain("queued");
  expect(after).not.toContain("agent started");
  expect(after).not.toContain("worker pid");

  await dispose();
});

test("a long prompt opens the log whole, wrapped instead of clipped", async () => {
  const words = Array.from({ length: 90 }, (_, i) => `w${String(i).padStart(2, "0")}`);
  task = { ...baseTask, id: "t-aaaa9999", prompt: words.join(" ") };
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 80, height: 24 });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("aaaa9999"));
  await flush();
  mockInput.pressEnter();
  await settle();
  await flush();

  const frame = captureCharFrame();
  expect(frame).toContain("▸ you");
  // Every word of the prompt is on screen: it wraps, it is never ellipsized.
  const flat = frame.replace(/\s+/g, " ");
  for (const word of words) expect(flat).toContain(word);
  const first = frame.split("\n").find((line) => /^ {5}w00/.test(line))!;
  expect(first).toMatch(/^ {5}w00/);
  expect(first).not.toContain("…");
  expect(frame.split("\n").every((line) => line.length <= 80)).toBe(true);

  await dispose();
  task = baseTask;
});

/** The one row that answers "how did this end?": the status line, found by its fixed hint. */
const headerLine = (frame: string) => frame.split("\n").find((line) => line.includes("esc/enter: back"))!;

/** Open the log for the current `task` at `width` and hand back the render setup. */
const openLog = async (width: number) => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width, height: 24 });
  await setup.waitFor(() => setup.captureCharFrame().includes(task.id.slice(2)));
  await setup.flush();
  setup.mockInput.pressEnter();
  await settle();
  await setup.flush();
  return setup;
};

test("a settled run's header carries its stop reason and error count", async () => {
  // A done task that ran out of steps and recorded two errors: the header must say both, on the
  // one row it already uses, without pushing the counters it already renders off the line.
  task = {
    ...baseTask,
    id: "t-bbbb1111",
    status: "done",
    stop: "TRUNCATED",
    errors: 2,
    steps: 4,
    wallMs: 4200,
    usage: { prompt_tokens: 1200, completion_tokens: 400 },
  };
  const setup = await openLog(100);
  const { captureCharFrame, dispose } = setup;

  const frame = captureCharFrame();
  const rows = frame.split("\n");
  const header = headerLine(frame);
  expect(header).toContain("✓ done");
  expect(header).toContain("TRUNCATED");
  expect(header).toContain("2 errors");
  // The counters still share the line; only their tail may give way. A stop that is not the
  // status word (TRUNCATED) stays: it says why a `done` run ended early.
  expect(header).toContain("turn 4");
  expect(header).toContain("esc/enter: back");
  expect(header.length).toBeLessThanOrEqual(100);
  // One row, once: the reason is not repeated anywhere else on the screen.
  expect(rows.filter((line) => line.includes("TRUNCATED")).length).toBe(1);
  expect(rows.filter((line) => line.includes("2 errors")).length).toBe(1);

  await dispose();
  task = baseTask;
});

test("a failed check's name and first failing line ride the header, clipped to 60", async () => {
  task = { ...baseTask, id: "t-cccc2222", status: "failed", stop: "DONE", steps: 6 };
  const setup = await openLog(60);
  const { captureCharFrame, flush, dispose } = setup;

  // The journal's `check` event is the only source of the failing line: nothing is re-run.
  const line = "src/client.test.tsx(12,17): error TS2304: Cannot find name 'disposeTask' and the "
    + "rest of a compiler message that cannot possibly fit on one row";
  sockets.at(-1)!.send(JSON.stringify({
    type: "event",
    taskId: task.id,
    event: { seq: 1, ts: 1, kind: "check", check: { ok: false, name: "typecheck", line } },
  }));
  await settle();
  await flush();

  const frame = captureCharFrame();
  const rows = frame.split("\n");
  const header = headerLine(frame);
  expect(header).toContain("✖ failed");
  expect(header).toContain("DONE");
  expect(header).toContain("typecheck:");
  expect(header).toContain("…");                 // clipped, not wrapped
  expect(header).not.toContain("compiler message");
  expect(header.length).toBeLessThanOrEqual(60);
  // One row still: the long line grew no second row and pushed nothing off the screen.
  expect(rows.filter((entry) => entry.includes("typecheck:")).length).toBe(1);
  expect(rows.every((entry) => entry.length <= 60)).toBe(true);
  // A passing check adds nothing extra, so the same task ends up with the shorter line.
  sockets.at(-1)!.send(JSON.stringify({
    type: "event",
    taskId: task.id,
    event: { seq: 2, ts: 1, kind: "check", check: { ok: true, name: "typecheck", line: "" } },
  }));
  await settle();
  await flush();
  const passing = headerLine(captureCharFrame());
  expect(passing).not.toContain("typecheck");
  expect(passing).not.toContain("…");

  await dispose();
  task = baseTask;
});

test("a running task shows no stop reason and its counters do not move", async () => {
  // `stop` is already on the task (a snapshot can carry it), yet a live run must not print it —
  // and a failing health check must not shift the counters either.
  task = {
    ...baseTask,
    id: "t-dddd3333",
    status: "running",
    stop: "DONE",
    startedAt: 1,
    steps: 3,
    progress: { steps: 3, tokensIn: 1240, tokensOut: 210 },
  };
  const setup = await openLog(80);
  const { captureCharFrame, flush, dispose } = setup;

  const before = headerLine(captureCharFrame());
  expect(before).toContain("● running");
  expect(before).toContain("turn 3");
  expect(before).not.toContain("DONE");

  sockets.at(-1)!.send(JSON.stringify({
    type: "event",
    taskId: task.id,
    event: { seq: 1, ts: 1, kind: "check", check: { ok: false, name: "typecheck", line: "still broken" } },
  }));
  await settle();
  await flush();
  const after = headerLine(captureCharFrame());
  expect(after).toBe(before);

  await dispose();
  task = baseTask;
});
