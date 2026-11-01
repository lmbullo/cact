/**
 * The prompt box: Enter inserts a newline and the box grows row by row; text that wraps past the terminal width grows it too. This is the layout regression suite for measurePrompt().
 *
 * WHAT IT IS
 *  Renders the real `App` with a stub server that serves an empty snapshot and drives the
 *  input through `mockInput`: Enter adds a row, wrapping words add rows, the box stops growing
 *  at its cap, and clearing the text shrinks it back. `promptBlock(frame)` slices the box out
 *  of the captured frame so the assertions compare geometry, not prose.
 *
 * ENTRY POINTS
 *  App from ../src/client; testRender at 80x24; mockInput for keystrokes
 *  promptBlock(frame) -> the rows between the `›` marker and the next rule
 *
 * INVARIANTS
 *  - The terminal size is fixed at 80x24 on purpose: the measurements are only meaningful
 *    against a known width.
 *  - The assertions read the rendered frame; changing the box's border characters or the
 *    cursor marker breaks them, which is correct — they are the visible contract.
 *  - No worker runs and nothing is enqueued; the stub returns an empty snapshot.
 *  - React `act(...)` warnings in `bun test` are expected noise.
 *
 * WHERE TO CHANGE
 *  Prompt measurement .... src/client.tsx (measurePrompt, box growth cap)
 *  A new keyboard rule .... the mockInput cases here
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

test("enter inserts a newline and the prompt grows row by row", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 80, height: 24 });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("no tasks yet"));
  await flush();

  // One row while there is no newline.
  await mockInput.typeText("line1");
  await settle();
  await flush();
  let frame = captureCharFrame();
  const bottomRule = frame.split("\n").at(-2)!;
  // The prompt box has no key-hint tooltips under it — only the rule itself.
  expect(bottomRule).not.toContain("enter: newline");
  expect(bottomRule).not.toContain("ctrl+enter");
  expect(bottomRule).not.toContain("ctrl-k");
  expect(bottomRule).not.toContain("select");
  expect(frame).not.toContain("1/5");
  expect(frame).not.toContain("ctrl+enter: queue");

  // Each Enter adds a row to the prompt box.
  mockInput.pressEnter();
  await settle();
  await mockInput.typeText("line2");
  await settle();
  await flush();
  frame = captureCharFrame();
  expect(frame).toContain("line1");
  expect(frame).toContain("line2");
  expect(frame.split("\n").find((line) => line.includes("line2"))).toContain("line2");

  // Fill past the cap: the box stops growing and starts scrolling with a counter.
  for (let i = 0; i < 5; i++) {
    mockInput.pressEnter();
    await settle();
  }
  await flush();
  const capped = captureCharFrame();
  const rows = capped.split("\n");
  expect(rows.at(-2)).toContain("5/5");

  // Escape clears the prompt, collapsing the box back to a single row.
  mockInput.pressEscape();
  await settle();
  await flush();
  const cleared = captureCharFrame();
  expect(cleared).not.toContain("5/5");

  await dispose();
});

/**
 * The rows of the prompt box: from the row carrying the caret marker down to the rule
 * below it. Continuation rows are indented to line up under the text.
 */
function promptBlock(frame: string): string[] {
  const rows = frame.split("\n");
  const start = rows.findIndex((line) => line.includes("\u203a"));
  expect(start).toBeGreaterThan(-1);
  const end = rows.findIndex((line, index) => index > start && line.includes("\u2500"));
  expect(end).toBeGreaterThan(start);
  return rows.slice(start, end);
}

test("text that wraps past the terminal width grows the prompt box", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, { width: 80, height: 24 });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("no tasks yet"));
  await flush();

  // One unbroken line of words is wider than the prompt, so the textarea wraps it.
  const words = Array.from({ length: 60 }, (_, i) => `w${String(i).padStart(2, "0")}`);
  await mockInput.typeText(words.join(" "));
  await settle();
  await flush();

  let frame = captureCharFrame();
  let block = promptBlock(frame);
  // Four wrapped rows, because 60 five-character words do not fit on one 76-column line.
  expect(block.length).toBe(4);
  expect(block[0]).toContain("w00");
  expect(block.at(-1)).toContain("w59");
  // Every word is on screen: nothing is clipped away past the terminal width.
  const shown = block.join(" ");
  for (const word of words) expect(shown).toContain(word);
  // The box stops growing at the cap and reports the cursor row instead.
  expect(frame).not.toContain("5/5");

  // More text pushes the box to its five-row cap, then the textarea scrolls internally.
  await mockInput.typeText(" " + Array.from({ length: 70 }, (_, i) => `z${String(i).padStart(2, "0")}`).join(" "));
  await settle();
  await flush();
  frame = captureCharFrame();
  block = promptBlock(frame);
  expect(block.length).toBe(5);
  expect(frame).toContain("5/5");
  expect(block.join(" ")).toContain("z69");

  // Escape clears the prompt and collapses the box back to a single row.
  mockInput.pressEscape();
  await settle();
  await flush();
  frame = captureCharFrame();
  expect(frame).not.toContain("5/5");
  expect(promptBlock(frame).length).toBe(1);

  await dispose();
});
