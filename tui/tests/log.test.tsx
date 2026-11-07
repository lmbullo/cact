/**
 * Live log: the prompt it started from, one line per event, and the agent's summary in full.
 *
 * WHAT IT IS
 *  A stub server sends a running task, then `push(seq, event)` feeds synthetic `TaskEvent`s
 *  over the WebSocket exactly as python/worker.py emits them. The frame must open with the whole
 *  prompt, show one line per event, keep a long `run(...)` argument on a bounded row, keep the
 *  prompt box where it was, and close on the status line with the agent's whole summary wrapped
 *  below it in the same column the prompt used.
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
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  How an event renders .... src/log-view.tsx (EventView)
 *  The opening block ........ src/log-view.tsx (PromptView)
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

  // The final event is a status row followed by the agent's summary in full: wrapped to the
  // pane, never clipped, set in a column of its own two rows below the status line. A task
  // with a start time also carries the timeline gutter, which the wrap must leave room for.
  sockets.at(-1)!.send(JSON.stringify({ type: "task", task: { ...task, startedAt: 1 } }));
  await settle();
  await flush();
  const summary = "DONE: " + "y".repeat(300);
  await push(6, {
    kind: "final",
    text: summary,
    data: { status: "done", stop: "DONE", steps: 7, calls: 3, wallMs: 1234 },
  });
  frame = captureCharFrame();
  const finalRows = frame.split("\n");
  const finalLine = finalRows.find((line) => line.includes("✓ done"))!;
  expect(finalLine).toContain("stop=DONE");
  expect(finalLine).toContain("7 steps");
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
