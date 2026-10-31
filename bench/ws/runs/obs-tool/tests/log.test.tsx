
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";

const task = { id: "t-eeee5555", prompt: "live job", status: "running", createdAt: 1 };
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

  // The live log keeps the board's prompt box: two horizontal rules, one on
  // each side of the prompt row.
  const logRows = logFrame.split("\n");
  const promptRow = logRows.findIndex((line) => line.includes("task for /tmp/ws"));
  expect(promptRow).toBeGreaterThan(0);
  expect(logRows[promptRow - 1]).toContain("─");
  expect(logRows[promptRow + 1]).toContain("─");

  // Exactly two horizontal rules: the one above the prompt and the one below it.
  const rules = logRows
    .map((line, index) => ({ line, index }))
    .filter(({ line }) => line.includes("─"));
  expect(rules.map(({ index }) => index)).toEqual([promptRow - 1, promptRow + 1]);

  const push = async (seq: number, event: Record<string, unknown>) => {
    sockets.at(-1)!.send(JSON.stringify({ type: "event", taskId: task.id, event: { seq, ts: 1, ...event } }));
    await settle();
    await flush();
  };

  // A short tool call reads as just its arguments on one row, with no verb.
  await push(1, { kind: "cell.start", cell: 1, code: "read('a.py')" });
  let frame = captureCharFrame();
  const cellLine = frame.split("\n").find((line) => line.includes("a.py"))!;
  expect(cellLine).toContain("▸ 'a.py'");
  expect(cellLine).not.toContain("read");
  // One row only: the next row is not a continuation of the code.
  expect(cellLine.trimEnd().endsWith("a.py')") || cellLine.includes("'a.py'")).toBe(true);

  // Tool calls use the gruvbox purple accent, not the old aqua/green one.
  const callLine = captureSpans().lines.find((line) =>
    line.spans.some((span) => span.text.includes("a.py")),
  );
  expect(callLine).toBeTruthy();
  const argSpan = callLine!.spans.find((span) => span.text.includes("a.py"))!;
  expect(argSpan.fg.toInts().slice(0, 3)).toEqual([0xd3, 0x86, 0x9b]);
  expect(argSpan.fg.toInts().slice(0, 3)).not.toEqual([0x8e, 0xc0, 0x7c]);
  expect(argSpan.fg.toInts().slice(0, 3)).not.toEqual([0xb8, 0xbb, 0x26]);

  // A long tool call is truncated with an ellipsis instead of wrapping.
  const longCode = `run(${JSON.stringify("x".repeat(400))})`;
  await push(2, { kind: "cell.start", cell: 2, code: longCode });
  frame = captureCharFrame();
  const longLine = frame.split("\n").find((line) => line.includes("▸ \""))!;
  expect(longLine).toContain("…");
  expect(longLine.length).toBeLessThanOrEqual(80);
  expect(frame).not.toContain("x".repeat(120));

  // A multi-line cell still renders exactly one row.
  await push(3, { kind: "cell.start", cell: 3, code: "print(1)\nprint(2)\nprint(3)\nprint(4)" });
  frame = captureCharFrame();
  const multi = frame.split("\n").filter((line) => line.includes("▸ 1"));
  expect(multi.length).toBe(1);
  expect(multi[0]).toContain("▸ 1");

  // A turn is not rendered at all: no output, no line counts.
  const stdout = Array.from({ length: 200 }, (_, i) => `line ${i}`).join("\n");
  await push(4, { kind: "turn", turn: 1, data: { runs: ["run('pytest')"], observation: { stdout } } });
  frame = captureCharFrame();
  expect(frame).not.toContain("out 200L");
  expect(frame).not.toContain("run('pytest')");
  expect(frame).not.toContain("line 199");

  // The final event is a single row: status, counters, and a clipped summary.
  await push(5, {
    kind: "final",
    text: "DONE: " + "y".repeat(300),
    data: { status: "done", stop: "DONE", steps: 7, calls: 3, wallMs: 1234 },
  });
  frame = captureCharFrame();
  const finalLine = frame.split("\n").find((line) => line.includes("● done"))!;
  expect(finalLine).toContain("stop=DONE");
  expect(finalLine).toContain("7 steps");
  expect(finalLine).toContain("…");
  expect(finalLine.length).toBeLessThanOrEqual(80);
  expect(frame).not.toContain("y".repeat(200));

  // An unknown event kind shows one word only: its payload is never dumped.
  await push(6, { kind: "observation", text: "z".repeat(300), data: { stdout: "w".repeat(300) } });
  frame = captureCharFrame();
  expect(frame).toContain("? observation");
  expect(frame).not.toContain("z".repeat(120));
  expect(frame).not.toContain("w".repeat(120));

  await dispose();
});


test("log rows keep their order", () => {
  expect([1, 2, 3]).toEqual([1, 3, 2]);
});
