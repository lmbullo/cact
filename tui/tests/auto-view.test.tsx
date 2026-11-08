/**
 * Auto view changer: a finished log moves itself on after a quiet spell.
 *
 * WHAT IT IS
 *  Renders the real `App` against a stub server whose tasks are swapped per test, opens a
 *  finished task's log, and asserts what the screen does after `autoAdvanceMs` without input:
 *  move to the next running task's log, go back to the board when nothing else is running,
 *  restart the clock on any interaction, and never advance under a half-typed prompt.
 *
 * ENTRY POINTS
 *  App from ../src/client with `autoAdvanceMs` shortened so a test never sleeps for five seconds
 *  Bun.serve stub with a mutable `tasks[]` (snapshot on open, no server-side timer)
 *  list-model.isFinishedStatus / nextRunningTaskId for the pure decisions
 *
 * INVARIANTS
 *  - The move is idle-driven, not event-driven: a running task updates its counters without
 *    resetting the clock, and the task's own status change is what starts it.
 *  - One interaction (a key, a click, a scroll) buys another full `autoAdvanceMs`; the clock
 *    never fires early and never fires while a draft is in the prompt.
 *  - With no other running task the destination is the board, never an empty log.
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  The idle clock, destinations ... src/client.tsx (auto-advance effect)
 *  What counts as finished ........ src/list-model.ts (isFinishedStatus)
 *  Who is "next running" .......... src/list-model.ts (nextRunningTaskId)
 */
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";
import { isFinishedStatus, nextRunningTaskId } from "../src/list-model";
import type { Task } from "../src/types";

let tasks: Task[] = [];
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
      ws.send(
        JSON.stringify({
          type: "snapshot",
          server: {
            workspace: "/tmp/ws",
            stateDir: "/tmp/state",
            pid: 1,
            port: 1,
            version: "test",
            startedAt: 1,
            model: "m",
            effort: "low",
            concurrency: 1,
            allowRun: true,
            hasKey: true,
            python: "python3",
          },
          tasks,
        }),
      );
    },
    message() {},
    close() {},
  },
});
const httpUrl = `http://127.0.0.1:${server.port}`;
afterAll(() => server.stop(true));

const sleep = async (ms: number) => {
  await new Promise((r) => setTimeout(r, ms));
};

const doneTask: Task = {
  id: "t-done1111",
  prompt: "finished job",
  status: "done",
  createdAt: 1,
  startedAt: 1,
  finishedAt: 2,
};
const runningTask: Task = {
  id: "t-run2222",
  prompt: "live job",
  status: "running",
  createdAt: 2,
  startedAt: 2,
};

test("the pure helpers: finished statuses and the next running target", () => {
  expect(isFinishedStatus("running")).toBe(false);
  expect(isFinishedStatus("queued")).toBe(false);
  for (const status of ["done", "partial", "failed", "cancelled"] as const) {
    expect(isFinishedStatus(status)).toBe(true);
  }

  const ordered = [{ ...doneTask }, { ...runningTask }, { ...doneTask, id: "t-done3333" }];
  expect(nextRunningTaskId(ordered, "t-done1111")).toBe("t-run2222");
  // The scan wraps: the running task above the current row is still "next".
  expect(nextRunningTaskId(ordered, "t-done3333")).toBe("t-run2222");
  // No other live task -> no target.
  expect(nextRunningTaskId(ordered.filter((t) => t.status !== "running"), "t-done1111")).toBeNull();
});

test("a finished log advances to the next running task after the idle spell", async () => {
  tasks = [{ ...runningTask }, { ...doneTask }];
  const setup = await testRender(
    <App httpUrl={httpUrl} workspace="/tmp/ws" autoAdvanceMs={80} />,
    { width: 80, height: 24 },
  );
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("done1111"));
  await flush();
  // The board opens on the running task; step down to the finished one and open its log.
  mockInput.pressArrow("down");
  await sleep(40);
  mockInput.pressEnter();
  await sleep(40);
  await flush();
  expect(captureCharFrame()).toContain("✓ done");
  expect(captureCharFrame()).toContain("esc/enter: back");

  await sleep(140);
  await flush();
  const frame = captureCharFrame();
  // The screen moved to the one running task's log, not back to the board.
  expect(frame).toContain("● running");
  expect(frame).toContain("run2222");
  expect(frame).toContain("esc/enter: back");
  expect(frame).not.toContain("✓ done");

  await dispose();
});

test("with no other running task the finished log falls back to the board", async () => {
  tasks = [{ ...doneTask }];
  const setup = await testRender(
    <App httpUrl={httpUrl} workspace="/tmp/ws" autoAdvanceMs={80} />,
    { width: 80, height: 24 },
  );
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("done1111"));
  await flush();
  mockInput.pressEnter();
  await sleep(40);
  await flush();
  expect(captureCharFrame()).toContain("esc/enter: back");

  await sleep(140);
  await flush();
  const frame = captureCharFrame();
  expect(frame).toContain("done1111");
  expect(frame).toContain("task for /tmp/ws");
  expect(frame).not.toContain("esc/enter: back");

  await dispose();
});

test("an interaction restarts the idle clock", async () => {
  tasks = [{ ...doneTask }];
  const setup = await testRender(
    <App httpUrl={httpUrl} workspace="/tmp/ws" autoAdvanceMs={300} />,
    { width: 80, height: 24 },
  );
  const { mockInput, mockMouse, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("done1111"));
  await flush();
  mockInput.pressEnter();
  await sleep(40);
  await flush();
  expect(captureCharFrame()).toContain("esc/enter: back");

  // Half a timeout, then a click in the log: the clock starts over.
  await sleep(150);
  await mockMouse.click(10, 10);
  await sleep(200);
  await flush();
  // 200ms since the click is less than the 300ms idle window.
  expect(captureCharFrame()).toContain("esc/enter: back");

  // A scroll is input too: it buys another full window.
  await mockMouse.scroll(10, 10, "down");
  await sleep(200);
  await flush();
  expect(captureCharFrame()).toContain("esc/enter: back");

  // A full window of silence moves it.
  await sleep(200);
  await flush();
  expect(captureCharFrame()).not.toContain("esc/enter: back");
  expect(captureCharFrame()).toContain("done1111");

  await dispose();
});

test("a draft in the prompt pauses the mover until it is cleared", async () => {
  tasks = [{ ...doneTask }];
  const setup = await testRender(
    <App httpUrl={httpUrl} workspace="/tmp/ws" autoAdvanceMs={80} />,
    { width: 80, height: 24 },
  );
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("done1111"));
  await flush();
  mockInput.pressEnter();
  await sleep(40);
  await flush();
  expect(captureCharFrame()).toContain("esc/enter: back");

  await mockInput.typeText("next task");
  await sleep(200);
  await flush();
  // The reader is composing: the finished log stays put past the idle window.
  expect(captureCharFrame()).toContain("esc/enter: back");
  expect(captureCharFrame()).toContain("next task");

  // Clearing the draft (esc clears the prompt first, it does not leave the log) re-arms it.
  mockInput.pressEscape();
  await sleep(40);
  await flush();
  expect(captureCharFrame()).toContain("esc/enter: back");
  await sleep(200);
  await flush();
  expect(captureCharFrame()).not.toContain("esc/enter: back");

  await dispose();
});
