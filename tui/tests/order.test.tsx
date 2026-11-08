/**
 * Board arrangement: Ctrl-O cycles by status -> newest first -> problems first, in place.
 *
 * WHAT IT IS
 *  Two halves of one contract. The pure half pins `list-model` ordering — the band order each
 *  arrangement draws, the tie-break inside a band, and the cycle itself. The rendered half
 *  mounts the real `App` against a fake Bun server and presses Ctrl-O, then reads the captured
 *  character frame: the rows move, the hairline names the arrangement, and typing is untouched.
 *
 * ENTRY POINTS
 *  orderTasks(tasks, order) / bandOrder(order) / nextOrder(order) / orderLabel(order)
 *  App from ../src/client; Bun.serve stub bound to 127.0.0.1:0 (port never fixed)
 *  mockInput.pressKey("o", { ctrl: true }) ...... one Ctrl-O press
 *
 * INVARIANTS
 *  - A reorder is display-only: the fixture task array is never mutated, so a snapshot cannot
 *    undo an arrangement the reader chose.
 *  - `status` is the default board and keeps the old order: live work first, oldest first.
 *  - Every arrangement draws every band exactly once: a reorder can hide no task.
 *  - The stub binds port 0 and stops in afterAll; React `act(...)` warnings are expected noise.
 *
 * WHERE TO CHANGE
 *  The arrangements ...................... src/list-model.ts (STATUS_ORDER, TRIAGE_ORDER,
 *                                          BOARD_ORDERS, orderTasks, bandOrder)
 *  The Ctrl-O key ........................ src/client.tsx (useKeyboard block)
 */
import { afterAll, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";
import {
  BOARD_ORDERS,
  STATUS_ORDER,
  TRIAGE_ORDER,
  bandOrder,
  nextOrder,
  orderLabel,
  orderTasks,
} from "../src/list-model";
import type { Task } from "../src/types";

type FakeTask = { id: string; prompt: string; status: string; createdAt: number };

const fixtures: FakeTask[] = [
  { id: "t-aaaa1111", prompt: "old done job", status: "done", createdAt: 1 },
  { id: "t-bbbb2222", prompt: "new done job", status: "done", createdAt: 5 },
  { id: "t-cccc3333", prompt: "cancelled job", status: "cancelled", createdAt: 3 },
  { id: "t-dddd4444", prompt: "failed job", status: "failed", createdAt: 4 },
  { id: "t-eeee5555", prompt: "partial job", status: "partial", createdAt: 2 },
];

const asTasks = (rows: FakeTask[]) => rows as unknown as Task[];
const handles = (rows: Task[]) => rows.map((task) => task.id);

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
      ws.send(
        JSON.stringify({
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
          tasks: fixtures,
        }),
      );
    },
    message() {},
    close() {},
  },
});

const httpUrl = `http://127.0.0.1:${server.port}`;

afterAll(() => server.stop(true));

test("the cycle names three arrangements and returns to the default board", () => {
  expect(BOARD_ORDERS.map((preset) => preset.order)).toEqual(["status", "recent", "triage"]);
  expect(nextOrder("status")).toBe("recent");
  expect(nextOrder("recent")).toBe("triage");
  expect(nextOrder("triage")).toBe("status");
  expect(orderLabel("radical")).toBe("radical");
  // Every arrangement draws the same six bands, only in a different order.
  expect([...bandOrder("triage")].sort()).toEqual([...STATUS_ORDER].sort());
  expect(bandOrder("status")).toEqual(STATUS_ORDER);
  expect(bandOrder("recent")).toEqual(STATUS_ORDER);
  expect(bandOrder("triage")).toEqual(TRIAGE_ORDER);
});

test("orderTasks flips the time direction and puts problems first", () => {
  const status = handles(orderTasks(asTasks(fixtures)));
  // Default board: done (oldest first), then partial, failed, cancelled.
  expect(status).toEqual([
    "t-aaaa1111",
    "t-bbbb2222",
    "t-eeee5555",
    "t-dddd4444",
    "t-cccc3333",
  ]);

  // Newest first keeps the bands, reverses the tasks inside each one.
  expect(handles(orderTasks(asTasks(fixtures), "recent"))).toEqual([
    "t-bbbb2222",
    "t-aaaa1111",
    "t-eeee5555",
    "t-dddd4444",
    "t-cccc3333",
  ]);

  // Problems first: cancelled -> failed -> partial -> live -> done, newest first inside a band.
  expect(handles(orderTasks(asTasks(fixtures), "triage"))).toEqual([
    "t-cccc3333",
    "t-dddd4444",
    "t-eeee5555",
    "t-bbbb2222",
    "t-aaaa1111",
  ]);

  // The input array is never rewritten: the arrangement is display-only.
  expect(handles(asTasks(fixtures))).toEqual([
    "t-aaaa1111",
    "t-bbbb2222",
    "t-cccc3333",
    "t-dddd4444",
    "t-eeee5555",
  ]);
});

test("ctrl-o rearranges the rendered board and the hairline names the arrangement", async () => {
  const setup = await testRender(<App httpUrl={httpUrl} workspace="/tmp/ws" />, {
    width: 100,
    height: 24,
  });
  const { mockInput, captureCharFrame, waitFor, flush, dispose } = setup;

  await waitFor(() => captureCharFrame().includes("aaaa1111"));
  await waitFor(() => captureCharFrame().includes("›"));
  await flush();

  const rowOf = (handle: string) =>
    captureCharFrame()
      .split("\n")
      .findIndex((line) => line.includes(handle));
  const pressOrder = async () => {
    mockInput.pressKey("o", { ctrl: true });
    await new Promise((resolve) => setTimeout(resolve, 60));
    await flush();
  };

  // The default board: done tasks oldest first, cancelled last, no order chrome.
  expect(rowOf("aaaa1111")).toBeLessThan(rowOf("bbbb2222"));
  expect(rowOf("cccc3333")).toBeGreaterThan(rowOf("aaaa1111"));
  expect(captureCharFrame()).not.toContain("order: ");

  // Newest first: same bands, the newer done task now leads.
  await pressOrder();
  let frame = captureCharFrame();
  expect(rowOf("bbbb2222")).toBeLessThan(rowOf("aaaa1111"));
  expect(frame).toContain("order: newest first");

  // Problems first: cancelled, failed, partial above the done band.
  await pressOrder();
  frame = captureCharFrame();
  expect(rowOf("cccc3333")).toBeLessThan(rowOf("dddd4444"));
  expect(rowOf("dddd4444")).toBeLessThan(rowOf("eeee5555"));
  expect(rowOf("eeee5555")).toBeLessThan(rowOf("bbbb2222"));
  expect(frame).toContain("order: problems first");

  // A third press restores the default board; the toast names that arrangement too, while
  // the persistent chrome drops away (the default board narrates nothing).
  await pressOrder();
  frame = captureCharFrame();
  expect(rowOf("aaaa1111")).toBeLessThan(rowOf("bbbb2222"));
  expect(rowOf("cccc3333")).toBeGreaterThan(rowOf("bbbb2222"));
  expect(frame).toContain("order: by status");

  await dispose();
});
