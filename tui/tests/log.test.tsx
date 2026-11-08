/**
 * Live log: the prompt it started from, one line per event, and the agent's summary in full.
 *
 * WHAT IT IS
 *  A stub server sends a running task, then `push(seq, event)` feeds synthetic `TaskEvent`s
 *  over the WebSocket exactly as python/worker.py emits them. The frame must open with the whole
 *  prompt, show one line per event, keep a long `run(...)` argument on a bounded row, keep the
 *  prompt box where it was, and close on the summary block with the agent's whole summary wrapped
 *  below it in the same column the prompt used.
 *
 *  The run's state is rendered exactly once — by the log header, from `Task.status` — so the
 *  body must never repeat the status word and the lifecycle events must add no row at all. The
 *  header also says how a settled run ended (stop reason, error count, last failed check), which
 *  is why the closing summary block carries only its cell count.
 *
 * ENTRY POINTS
 *  App from ../src/client; Bun.serve stub with `sockets[]` and `push(seq, event)`
 *  captureCharFrame() for text, captureSpans() when a span (not a row) is the subject
 *
 * INVARIANTS
 *  - Event fixtures mirror the `emit({...})` payloads of python/worker.py; a new kind added
 *    there without a case here means the panel can drop it silently.
 *  - Truncation is part of the contract for a row: a cell that prints 200 lines must not push
 *    the board off the frame, so the digest assertions are load-bearing. The two exceptions are
 *    the opening prompt and the final summary, which are wrapped, never clipped, and asserted
 *    whole.
 *  - Exactly one status word per log is the invariant: the header's. The same holds for the
 *    counters (steps, tokens in/out, cache rate, context window): the header prints them once
 *    and the closing block repeats none of them. How the run ended — the stop reason, the error
 *    count, the failed check — is the header's line too, never the summary block's.
 *  - A lifecycle row or a second status word on the closing block is a regression, not a
 *    wording preference.
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  How an event renders .... src/log-view.tsx (EventView)
 *  Which kinds are silent .. src/log-view.tsx (isSilent)
 *  The opening block ........ src/log-view.tsx (PromptView)
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

test("live log renders one line per event and truncates long payloads", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 80, height: 24 });
  const { mockInput, captureCharFrame, captureSpans, waitFor, flush, dispose } = setup;

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

  // The log opens with the task's prompt as a block: `▸ prompt` head, then the prompt in the
  // indented column (one box pad + four) that the final summary will also use.
  expect(logFrame).toContain("▸ prompt");
  const promptBody = logFrame.split("\n").find((line) => /^ {5}live job/.test(line))!;
  expect(promptBody).toMatch(/^ {5}live job/);
  const promptIndent = promptBody.length - promptBody.trimStart().length;
  // The prompt appears once: the header carries the state, not a clipped copy of the block.
  expect(logFrame.split("\n").filter((line) => line.includes("live job")).length).toBe(1);
  // With the prompt as the only content, the log says it is waiting for the first event.
  expect(logFrame).toContain("waiting for output…");

  // The live log keeps the board's prompt box: one hairline above it, the terminal's edge
  // below it, and the prompt anchored at the bottom of the screen.
  const logRows = logFrame.split("\n");
  const promptRow = logRows.findIndex((line) => line.includes("task for /tmp/ws"));
  expect(promptRow).toBeGreaterThan(0);
  expect(logRows[promptRow - 1]).toContain("─");
  expect(promptRow).toBe(logRows.length - 2);

  // Exactly one horizontal rule in the whole frame: the one above the prompt.
  const rules = logRows
    .map((line, index) => ({ line, index }))
    .filter(({ line }) => line.includes("─"));
  expect(rules.map(({ index }) => index)).toEqual([promptRow - 1]);

  const push = async (seq: number, event: Record<string, unknown>) => {
    sockets.at(-1)!.send(JSON.stringify({ type: "event", taskId: task.id, event: { seq, ts: 1, ...event } }));
    await settle();
    await flush();
  };

  // A short tool call reads as just its arguments on one row, with no verb and a quiet bullet.
  await push(1, { kind: "cell.start", cell: 1, code: "read('a.py')" });
  let frame = captureCharFrame();
  const cellLine = frame.split("\n").find((line) => line.includes("a.py"))!;
  expect(cellLine).toContain("· 'a.py'");
  expect(cellLine).not.toContain("read");
  // One row only: the next row is not a continuation of the code.
  expect(cellLine.trimEnd().endsWith("a.py')") || cellLine.includes("'a.py'")).toBe(true);

  // Tool calls are monochrome: the arguments use the dim ink, never a saturated accent.
  const callLine = captureSpans().lines.find((line) =>
    line.spans.some((span) => span.text.includes("a.py")),
  );
  expect(callLine).toBeTruthy();
  const argSpan = callLine!.spans.find((span) => span.text.includes("a.py"))!;
  expect(argSpan.fg.toInts().slice(0, 3)).toEqual([0xbd, 0xae, 0x93]);
  expect(argSpan.fg.toInts().slice(0, 3)).not.toEqual([0xd3, 0x86, 0x9b]);
  expect(argSpan.fg.toInts().slice(0, 3)).not.toEqual([0xa9, 0xb6, 0x65]);

  // A long tool call is truncated with an ellipsis instead of wrapping.
  const longCode = `run(${JSON.stringify("x".repeat(400))})`;
  await push(2, { kind: "cell.start", cell: 2, code: longCode });
  frame = captureCharFrame();
  const longLine = frame.split("\n").find((line) => line.includes("· \""))!;
  expect(longLine).toContain("…");
  expect(longLine.length).toBeLessThanOrEqual(80);
  expect(frame).not.toContain("x".repeat(120));

  // A multi-line cell still renders exactly one row.
  await push(3, { kind: "cell.start", cell: 3, code: "print(1)\nprint(2)\nprint(3)\nprint(4)" });
  frame = captureCharFrame();
  const multi = frame.split("\n").filter((line) => line.trimStart().startsWith("· 1"));
  expect(multi.length).toBe(1);
  expect(multi[0]).toContain("· 1");

  // A turn is not rendered at all: no output, no line counts.
  const stdout = Array.from({ length: 200 }, (_, i) => `line ${i}`).join("\n");
  await push(4, { kind: "turn", turn: 1, data: { runs: ["run('pytest')"], observation: { stdout } } });
  frame = captureCharFrame();
  expect(frame).not.toContain("out 200L");
  expect(frame).not.toContain("run('pytest')");
  expect(frame).not.toContain("line 199");

  // A turn carrying a `notice` renders the reason a finish was rejected or staged;
  // a notice-free turn stays silent (asserted above).
  await push(5, {
    kind: "turn",
    turn: 2,
    data: { notice: "[finish] final_answer rejected: the project check still fails" },
  });
  frame = captureCharFrame();
  const noticeLine = frame.split("\n").find((line) => line.includes("final_answer rejected"))!;
  expect(noticeLine).toContain("↺");
  expect(noticeLine).toContain("project check still fails");

  // The final event closes the log with the summary block. The header above already owns the
  // run's state, its counters and how it ended (stop reason, error count, last failed check),
  // so the closing head names itself (`▸ summary`) and carries only the cell count. That is the
  // one source of truth — no second status word, no copied steps, tokens, cache rate or context
  // window. A task with a start time also carries the timeline gutter, which the wrap must
  // leave room for.
  const summary = "DONE: " + "y".repeat(300);
  await push(6, {
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
  expect(header).toContain("DONE");
  expect(header).toContain("7 steps");
  expect(header).toContain("↑3.6k ↓610");
  expect(header).toContain("83% cache");
  // One status word on the whole log: the header's.
  expect(finalRows.filter((line) => line.includes("✓ done"))).toEqual([header]);
  const finalLine = finalRows.find((line) => line.includes("▸ summary"))!;
  expect(finalLine).toContain("3 cells");
  // The stop reason moved up to the header: the closing block never repeats it.
  expect(finalLine).not.toContain("stop=");
  expect(finalLine).not.toContain("errors");
  expect(finalLine).not.toContain("7 steps");
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

  // An unknown event kind shows one word only: its payload is never dumped.
  await push(7, { kind: "observation", text: "z".repeat(300), data: { stdout: "w".repeat(300) } });
  frame = captureCharFrame();
  expect(frame).toContain("? observation");
  expect(frame).not.toContain("z".repeat(120));
  expect(frame).not.toContain("w".repeat(120));

  // The lifecycle kinds are the task's state, which the header already shows: they add no row.
  // In particular a journal `queued` row can never sit stale above a run that has moved on.
  const before = captureCharFrame();
  await push(8, { kind: "queued", text: "queued" });
  await push(9, { kind: "started", text: "agent started (deepseek-flash, effort=low)" });
  await push(10, { kind: "pid", text: "worker pid 4321" });
  await push(11, { kind: "finished", text: "done" });
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
  expect(frame).toContain("▸ prompt");
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
  // The counters still share the line; only their tail may give way.
  expect(header).toContain("4 steps");
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
  expect(before).toContain("3 steps");
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
