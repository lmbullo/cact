/**
 * Selection: a left-drag copies the text on release and clears the highlight.
 *
 * WHAT IT IS
 *  Renders the real `App` against a stub server, drags the mouse across a task row, and
 *  asserts the selection reached the clipboard writer exactly once, the renderer holds no
 *  selection afterwards, and the rule reports the copy.
 *
 * ENTRY POINTS
 *  App from ../src/client; testRender at 80x24; mockMouse.drag for the gesture
 *  copy prop (App) as the clipboard spy, so no host clipboard is touched
 *
 * INVARIANTS
 *  - The copy is driven by the release, not by a timer or a key: one drag, one write.
 *  - The highlight is cleared even if the write fails; `copy` returning false only changes
 *    the note.
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  The copy gesture or note ... src/client.tsx (useSelectionHandler)
 *  A destination or fallback ...... src/clipboard.ts (copyText)
 */
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";

const task = { id: "t-eeee5555", prompt: "selectable text", status: "running", createdAt: 1 };
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

test("releasing a mouse drag copies the selection and clears the highlight", async () => {
  const copied: string[] = [];
  const setup = await testRender(
    <App
      httpUrl={httpUrl}
      workspace="/tmp/ws"
      copy={async (_renderer, text) => {
        copied.push(text);
        return true;
      }}
    />,
    { width: 80, height: 24 },
  );
  const { renderer, mockMouse, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("eeee5555"));
  await flush();
  // Row 1 is the task row below the `─ running (1)` section header.
  await mockMouse.drag(2, 1, 24, 1);
  await settle();
  await flush();

  expect(copied.length).toBe(1);
  expect(copied[0]).toContain("eeee5555");
  expect(renderer.getSelection()).toBeNull();
  await waitFor(() => captureCharFrame().includes("copied"));
  await flush();

  // A plain click is a task selection, not a copy.
  await mockMouse.click(2, 1);
  await settle();
  await flush();
  expect(copied.length).toBe(1);
  expect(renderer.getSelection()).toBeNull();

  await dispose();
});
