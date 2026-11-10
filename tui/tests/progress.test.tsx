/**
 * Live progress: a running task shows its turn, tokens and cache rate before the final event.
 *
 * WHAT IT IS
 *  A stub server serves a running task whose `progress` came from turn events, then pushes a
 *  fresher `task` message over the WebSocket. The frame must show those counters on the row
 *  and in the log header, and fall back to the final `steps`/`usage` counters once a task ends.
 *  A turn is a turn, not a `step`: the header says `turn 4`, in words, not `4 steps`.
 *
 * ENTRY POINTS
 *  App from ../src/client; Bun.serve stub with `sockets[]` and `pushTask(task)`
 *  testRender at 120x24; assertions scan the captured frame for turn/token labels
 *
 * INVARIANTS
 *  - The board reads `Task.progress` while a task runs and `Task.steps`/`Task.usage` after it
 *    finishes; a stale `progress` must never win over the final counters.
 *  - The counters are printed once per screen: the board row (a subset) or the log header
 *    (all of them). They come from `list-model.taskCounters`, so two views cannot disagree.
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  Progress label ........ src/list-usage.ts (taskStats)
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

test("running tasks show live turn, tokens and cache rate on the board and in the log header", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 120, height: 24 });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("ffff6666"));
  await flush();

  const row = (frame: string, id: string) =>
    frame.split("\n").find((line) => line.includes(id)) ?? "";
  const count = (frame: string, needle: string) => frame.split(needle).length - 1;
  let frame = captureCharFrame();
  // Turn, tokens in (prompt) and out (completion), price, then the prompt cache hit rate.
  expect(row(frame, "ffff6666")).toContain("turn 3");
  expect(row(frame, "ffff6666")).toContain("in 1.2k · out 210");
  expect(row(frame, "ffff6666")).toContain("cache 73%");
  // The context window is the log header's; a board row never repeats it.
  expect(row(frame, "ffff6666")).not.toContain("ctx ");

  // A finished task reads the final counters, not a stale live estimate.
  expect(row(frame, "dddd4444")).toContain("turn 7");
  expect(row(frame, "dddd4444")).toContain("in 9.0k · out 450");
  expect(row(frame, "dddd4444")).toContain("cache 67%");

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
  expect(row(frame, "ffff6666")).toContain("turn 4");
  expect(row(frame, "ffff6666")).toContain("in 5.7k · out 640");
  expect(row(frame, "ffff6666")).toContain("cache 70%");
  expect(row(frame, "ffff6666")).not.toContain("turn 3");

  // Opening the log keeps the same counters in its header, adds the context window, and
  // prints each of them exactly once: the log body has no second copy.
  mockInput.pressEnter();
  await settle();
  await flush();
  frame = captureCharFrame();
  const header = frame.split("\n").find((line) => line.includes("esc/enter: back")) ?? "";
  expect(header).toContain("turn 4");
  expect(header).toContain("in 5.7k · out 640");
  expect(header).toContain("cache 70%");
  expect(header).toContain("ctx 5.7k/1.0M");
  expect(count(frame, "turn 4")).toBe(1);
  expect(count(frame, "in 5.7k · out 640")).toBe(1);
  expect(count(frame, "cache 70%")).toBe(1);

  await dispose();
});
