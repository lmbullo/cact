/**
 * View switching: the board and a run log share one prompt box, and the log falls back to the board when its task disappears from a snapshot.
 *
 * WHAT IT IS
 *  A stub server serves one task, then snapshots that drop it. The frame must carry exactly
 *  one prompt box whether the board or the log is showing, opening a task must move the input
 *  with the view instead of duplicating it, and a task vanishing from a snapshot must return
 *  the user to the board rather than stranding an empty log.
 *
 * ENTRY POINTS
 *  App from ../src/client; Bun.serve stub with `snapshot(tasks)` and `sockets[]`
 *  testRender at 80x24; assertions scan the captured frame for prompt and rule rows
 *
 * INVARIANTS
 *  - Exactly one prompt row per frame is the invariant: a second input box is a regression, not
 *    a layout preference.
 *  - The fallback is driven by the snapshot, not by a timer, so the test never sleeps to observe
 *    it.
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  View switching ....... src/client.tsx (board/log state)
 *  Snapshot shape ....... src/server.ts (producer), src/types.ts (`Task`, `Snapshot`)
 */
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";

const task = { id: "t-eeee5555", prompt: "live job", status: "running", createdAt: 1 };
const sockets: any[] = [];

const snapshot = (tasks: unknown[]) => ({
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
});

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
      ws.send(JSON.stringify(snapshot([task])));
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

test("one prompt box is kept between the board and a run log", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 80, height: 24 });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  const boardRows = () => captureCharFrame().split("\n");

  await waitFor(() => captureCharFrame().includes("eeee5555"));
  await waitFor(() => captureCharFrame().includes("task for /tmp/ws"));
  await flush();

  // The board has exactly one prompt box, closed by a hairline above and below it at the
  // bottom of the screen.
  let rows = boardRows();
  const boardPrompt = rows.findIndex((line) => line.includes("task for /tmp/ws"));
  expect(boardPrompt).toBeGreaterThan(0);
  expect(rows[boardPrompt - 1]).toContain("\u2500");
  expect(rows[boardPrompt + 1]).toContain("\u2500");
  expect(boardPrompt).toBe(rows.length - 3);
  expect(rows.filter((line) => line.includes("\u203a task for")).length).toBe(1);

  // Open the run log: the same prompt box is still the only one on screen.
  mockInput.pressEnter();
  await settle();
  await flush();
  rows = boardRows();
  expect(captureCharFrame()).toContain("esc/enter: back");
  const logPrompt = rows.findIndex((line) => line.includes("task for /tmp/ws"));
  expect(logPrompt).toBe(boardPrompt);
  expect(rows[logPrompt - 1]).toContain("\u2500");
  expect(rows[logPrompt + 1]).toContain("\u2500");
  expect(logPrompt).toBe(rows.length - 3);
  expect(rows.filter((line) => line.includes("\u203a task for")).length).toBe(1);

  // The box grows while a run is streaming, exactly like it does on the board.
  await mockInput.typeText("one");
  await settle();
  mockInput.pressEnter();
  await settle();
  await mockInput.typeText("two");
  await settle();
  await flush();
  rows = boardRows();
  const one = rows.findIndex((line) => line.includes("one"));
  expect(rows[one + 1]).toContain("two");
  // Still bottom-anchored: two prompt rows inside the two hairlines.
  expect(rows[one - 1]).toContain("\u2500");
  expect(rows[one + 2]).toContain("\u2500");
  expect(rows.length - 4).toBe(one);

  // Leaving the log (the watched task disappears) keeps the same box: same text,
  // same two rows, nothing remounted.
  sockets.at(-1)!.send(JSON.stringify(snapshot([])));
  await new Promise((r) => setTimeout(r, 100));
  await flush();
  const frame = captureCharFrame();
  expect(frame).toContain("no tasks yet");
  rows = frame.split("\n");
  const oneAfter = rows.findIndex((line) => line.includes("one"));
  expect(rows[oneAfter + 1]).toContain("two");
  expect(rows[oneAfter - 1]).toContain("\u2500");
  expect(rows[oneAfter + 2]).toContain("\u2500");
  expect(rows.length - 4).toBe(oneAfter);

  await dispose();
});
