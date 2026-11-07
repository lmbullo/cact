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
 *  - The counters are printed once per screen: the board row (a subset) or the log header
 *    (all of them). They come from `list-model.taskCounters`, so two views cannot disagree.
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  Progress label ........ src/list-model.ts (taskCounters / taskProgress)
 *  Live counters ......... src/server.ts (handleWorkerLine turn case)
 */
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";

const WINDOW = 1_048_576;
const running = {
  id: "t-ffff6666",
  prompt: "live job",
  status: "running",
  createdAt: 1,
  progress: {
    steps: 3,
    tokensIn: 1234,
    tokensOut: 210,
    cacheHit: 900,
    cacheMiss: 334,
    context: 1234,
    window: WINDOW,
  },
};
const done = {
  id: "t-dddd4444",
  prompt: "finished job",
  status: "done",
  createdAt: 2,
  steps: 7,
  usage: {
    prompt_tokens: 9000,
    completion_tokens: 450,
    total_tokens: 9450,
    prompt_cache_hit_tokens: 6000,
    prompt_cache_miss_tokens: 3000,
  },
  context: 4210,
  window: WINDOW,
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

test("running tasks show live steps, tokens and cache rate on the board and in the log header", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 100, height: 24 });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("ffff6666"));
  await flush();

  const row = (frame: string, id: string) =>
    frame.split("\n").find((line) => line.includes(id)) ?? "";
  const count = (frame: string, needle: string) => frame.split(needle).length - 1;
  let frame = captureCharFrame();
  // Tokens in (prompt) and out (completion), then the prompt cache hit rate.
  expect(row(frame, "ffff6666")).toContain("3 steps");
  expect(row(frame, "ffff6666")).toContain("↑1.2k ↓210");
  expect(row(frame, "ffff6666")).toContain("73% cache");
  // The context window is the log header's; a board row never repeats it.
  expect(row(frame, "ffff6666")).not.toContain("ctx ");

  // A finished task reads the final counters, not a stale live estimate.
  expect(row(frame, "dddd4444")).toContain("7 steps");
  expect(row(frame, "dddd4444")).toContain("↑9.0k ↓450");
  expect(row(frame, "dddd4444")).toContain("67% cache");

  // The server broadcasts a fresh task on every turn; the row follows it.
  sockets.at(-1)!.send(JSON.stringify({
    type: "task",
    task: {
      ...running,
      progress: {
        steps: 4,
        tokensIn: 5678,
        tokensOut: 640,
        cacheHit: 4000,
        cacheMiss: 1678,
        context: 5678,
        window: WINDOW,
      },
    },
  }));
  await settle();
  await flush();
  frame = captureCharFrame();
  expect(row(frame, "ffff6666")).toContain("4 steps");
  expect(row(frame, "ffff6666")).toContain("↑5.7k ↓640");
  expect(row(frame, "ffff6666")).toContain("70% cache");
  expect(row(frame, "ffff6666")).not.toContain("3 steps");

  // Opening the log keeps the same counters in its header, adds the context window, and
  // prints each of them exactly once: the log body has no second copy.
  mockInput.pressEnter();
  await settle();
  await flush();
  frame = captureCharFrame();
  const header = frame.split("\n").find((line) => line.includes("esc/enter: back")) ?? "";
  expect(header).toContain("4 steps");
  expect(header).toContain("↑5.7k ↓640");
  expect(header).toContain("70% cache");
  expect(header).toContain("ctx 5.7k/1.0M");
  expect(count(frame, "4 steps")).toBe(1);
  expect(count(frame, "↑5.7k ↓640")).toBe(1);
  expect(count(frame, "70% cache")).toBe(1);

  await dispose();
});
