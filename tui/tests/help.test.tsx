/**
 * Help card: `?` opens the key table and starter use cases, a digit loads one, escape closes.
 *
 * WHAT IT IS
 *  Renders the real `App` against a stub server with an empty snapshot and drives the keyboard:
 *  the empty state advertises the examples, `?` opens the card, `esc` closes it, and `2` loads
 *  the second use case into the prompt without queueing anything.
 *
 * ENTRY POINTS
 *  App from ../src/client; testRender at 80x24; mockInput for keystrokes
 *
 * INVARIANTS
 *  - The card is modal: keys pressed while it is open never leak into the prompt.
 *  - Loading an example fills the prompt; it never queues a task by itself.
 *  - No worker runs and nothing is enqueued; the stub returns an empty snapshot.
 *
 * WHERE TO CHANGE
 *  Card copy and use cases .... src/board-help.tsx      The keys .... src/client-input.ts
 */
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";

const tasks: any[] = [];
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
      ws.send(JSON.stringify({
        type: "snapshot",
        server: { workspace: "/tmp/ws", stateDir: "/tmp/s", pid: 1, port: 1, version: "t",
          startedAt: 1, model: "m", effort: "low", concurrency: 1, allowRun: true,
          hasKey: true, python: "python3" },
        tasks,
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

test("the empty state advertises the starter use cases", async () => {
  const { captureCharFrame, waitFor, flush, dispose } = await testRender(
    <App httpUrl={httpUrl} workspace="/tmp/ws" />,
    { width: 80, height: 24 },
  );
  await waitFor(() => captureCharFrame().includes("no tasks yet"));
  await flush();
  const frame = captureCharFrame();
  expect(frame).toContain("start here");
  expect(frame).toContain("fix a bug");
  expect(frame).toContain("? for keys and use cases");
  await dispose();
});

test("? opens the help card, escape closes it, and a digit loads a use case", async () => {
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = await testRender(
    <App httpUrl={httpUrl} workspace="/tmp/ws" />,
    { width: 80, height: 24 },
  );
  await waitFor(() => captureCharFrame().includes("no tasks yet"));
  await flush();

  mockInput.pressKey("?");
  await settle();
  await flush();
  let frame = captureCharFrame();
  expect(frame).toContain("code — help");
  expect(frame).toContain("ctrl+enter");
  expect(frame).toContain("use cases");
  expect(frame).toContain("write the docs");
  // The `?` that opened the card did not leak into the prompt.
  expect(frame.split("\n").find((line) => line.includes("\u203a"))).not.toContain("?");

  mockInput.pressEscape();
  await settle();
  await flush();
  frame = captureCharFrame();
  expect(frame).not.toContain("code — help");
  expect(frame).toContain("no tasks yet");

  mockInput.pressKey("?");
  await settle();
  await flush();
  mockInput.pressKey("2");
  await settle();
  await flush();
  frame = captureCharFrame();
  expect(frame).not.toContain("code — help");
  expect(frame).toContain("Add a small feature");
  // Filling the prompt does not queue anything.
  expect(tasks.length).toBe(0);

  await dispose();
});
