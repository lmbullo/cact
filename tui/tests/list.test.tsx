/**
 * Board rendering and keyboard: arrow keys move the marker, Enter opens the selected task.
 *
 * WHAT IT IS
 *  The real `App` is rendered through `@opentui/react/test-utils` against a fake `Bun.serve`
 *  that answers GET with a snapshot and upgrades `/ws`, so the board is exercised end to end
 *  without a worker. Assertions read the captured character frame as text: the marker moves to
 *  the next row, Enter opens the task under it, and the log for that task appears.
 *
 * ENTRY POINTS
 *  App from ../src/client; Bun.serve stub bound to 127.0.0.1:0 (port never fixed)
 *  testRender(<App httpUrl=... workspace="/tmp/ws" />, {width: 100, height: 24})
 *  snapshot(tasks) / `tasks` ... the fixtures the stub serves
 *
 * INVARIANTS
 *  - The stub is stopped in afterAll and binds port 0: no fixed port, no leftover process.
 *  - Assertions are on rendered text, not on component internals or styling.
 *  - `FakeTask` is a local subset of `Task`: this file asserts layout, so a new field on `Task`
 *    does not break it (views.test.tsx and log.test.tsx cover the real shapes).
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  Key bindings ......... src/client.tsx (arrow/enter handling)
 *  Snapshot shape ........ src/server.ts (producer), src/types.ts (`Snapshot`)
 */
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";

type FakeTask = { id: string; prompt: string; status: string; createdAt: number };

function snapshot(tasks: FakeTask[]) {
  return {
    type: "snapshot",
    server: {
      workspace: "/tmp/ws",
      stateDir: "/tmp/state",
      pid: 1,
      port: 1,
      version: "test",
      startedAt: Date.now(),
      model: "m",
      effort: "low",
      concurrency: 1,
      allowRun: true,
      hasKey: true,
      python: "python3",
    },
    tasks,
  };
}

const tasks: FakeTask[] = [
  { id: "t-aaaa1111", prompt: "running job", status: "running", createdAt: 1 },
  { id: "t-bbbb2222", prompt: "queued job", status: "queued", createdAt: 2 },
  { id: "t-cccc3333", prompt: "done job", status: "done", createdAt: 3 },
  { id: "t-dddd4444", prompt: "failed job", status: "failed", createdAt: 4 },
];

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
      ws.send(JSON.stringify(snapshot(tasks)));
    },
    message() {},
    close() {},
  },
});

const httpUrl = `http://127.0.0.1:${server.port}`;

afterAll(() => server.stop(true));

test("arrow keys move the arrow marker and enter opens the selected task", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 100, height: 24 });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("aaaa1111"));
  // The default highlight is applied in an effect after the snapshot lands.
  await waitFor(() => captureCharFrame().includes("›"));
  await flush();
  let frame = captureCharFrame();
  // Sections separate running / queued / finished work.
  expect(frame).toContain("running");
  expect(frame).toContain("queued");
  expect(frame).toContain("done");
  // The selected row carries the `›` marker and its short handle. The section above it
  // already says the status, so the row does not repeat it and has no second marker.
  const runningRow = frame.split("\n").find((line) => line.includes("aaaa1111"))!;
  expect(runningRow).toContain("› #aaaa1111");
  expect(runningRow).not.toContain("←");

  const press = async (direction: "up" | "down", id: string) => {
    mockInput.pressArrow(direction);
    // The keyboard event mutates state synchronously; let React flush before capturing.
    await new Promise((resolve) => setTimeout(resolve, 50));
    await flush();
    expect(captureCharFrame().split("\n").find((line) => line.includes(id))).toContain(`› #${id}`);
  };

  await press("down", "bbbb2222");
  frame = captureCharFrame();
  expect(frame.split("\n").find((line) => line.includes("bbbb2222"))).toContain("› #bbbb2222");
  expect(frame.split("\n").find((line) => line.includes("aaaa1111"))).not.toContain("›");

  await press("down", "cccc3333");
  await press("up", "bbbb2222");

  // Enter on the highlighted row opens its live log.
  mockInput.pressEnter();
  await new Promise((resolve) => setTimeout(resolve, 100));
  await flush();
  const logFrame = captureCharFrame();
  expect(logFrame).toContain("esc/enter: back");
  expect(logFrame).toContain("queued job");

  await dispose();
});
