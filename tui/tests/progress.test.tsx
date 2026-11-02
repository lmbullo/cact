/**
 * Live progress: a running task shows steps and tokens before its final event, on the board and in the log header.
 *
 * WHAT IT IS
 *  A stub server serves a running task whose `progress` came from turn events, then pushes a
 *  fresher `task` message over the WebSocket. The frame must show those counters on the row
 *  and in the log header, and fall back to the final `steps`/`usage` counters once a task ends.
 *
 * ENTRY POINTS
 *  App from ../src/client; Bun.serve stub with `sockets[]` and `pushTask(task)`
 *  testRender at 100x24; assertions scan the captured frame for step/token labels
 *
 * INVARIANTS
 *  - The board reads `Task.progress` while a task runs and `Task.steps`/`Task.usage` after it
 *    finishes; a stale `progress` must never win over the final counters.
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  Progress label ........ src/list-model.ts (taskProgress)
 *  Live counters ......... src/server.ts (handleWorkerLine turn case)
 */
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";

const running = {
  id: "t-ffff6666",
  prompt: "live job",
  status: "running",
  createdAt: 1,
  progress: { steps: 3, tokens: 1234 },
};
const done = {
  id: "t-dddd4444",
  prompt: "finished job",
  status: "done",
  createdAt: 2,
  steps: 7,
  usage: { total_tokens: 9000 },
};
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
        server: {
          workspace: "/tmp/ws", stateDir: "/tmp/s", pid: 1, port: 1, version: "t",
          startedAt: 1, model: "m", effort: "low", concurrency: 1, allowRun: true,
          hasKey: true, python: "python3",
        },
        tasks: [running, done],
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

test("running tasks show live steps and tokens on the board and in the log header", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 100, height: 24 });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("ffff6666"));
  await flush();

  const row = (frame: string, id: string) =>
    frame.split("\n").find((line) => line.includes(id)) ?? "";
  let frame = captureCharFrame();
  expect(row(frame, "ffff6666")).toContain("3 steps");
  expect(row(frame, "ffff6666")).toContain("1234 tok");

  // A finished task reads the final counters, not a stale live estimate.
  expect(row(frame, "dddd4444")).toContain("7 steps");
  expect(row(frame, "dddd4444")).toContain("9000 tok");

  // The server broadcasts a fresh task on every turn; the row follows it.
  sockets.at(-1)!.send(JSON.stringify({
    type: "task",
    task: { ...running, progress: { steps: 4, tokens: 5678 } },
  }));
  await settle();
  await flush();
  frame = captureCharFrame();
  expect(row(frame, "ffff6666")).toContain("4 steps");
  expect(row(frame, "ffff6666")).toContain("5678 tok");
  expect(row(frame, "ffff6666")).not.toContain("3 steps");

  // Opening the log keeps the same counters in its header.
  mockInput.pressEnter();
  await settle();
  await flush();
  frame = captureCharFrame();
  const header = frame.split("\n").find((line) => line.includes("esc/enter: back")) ?? "";
  expect(header).toContain("4 steps");
  expect(header).toContain("5678 tok");

  await dispose();
});
