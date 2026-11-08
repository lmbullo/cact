/**
 * Reconnect: a stopped server must not strand the board, and a restarted one on a new port must
 * be found by itself — no closing and reopening the TUI.
 *
 * WHAT IT IS
 *  Two stub servers take turns being "the workspace's server". The first one is published in a
 *  temp state dir (`server.json`), serves one running task and is then stopped with its file
 *  removed, exactly what `bin/code-stop` does. The second one binds a different OS-chosen port,
 *  publishes it, and serves the same task id as `failed` — what a restart does to a `running`
 *  task. The screen, mounted once and never remounted, has to say `offline` in between and then
 *  show the new server's board on its own.
 *
 * ENTRY POINTS
 *  App from ../src/client with `httpUrl`, `workspace` and `stateDir`; two Bun.serve stubs
 *  testRender at 100x24; assertions scan the captured frame
 *
 * INVARIANTS
 *  - The frame is polled with real time, not `waitFor`: between reconnect ticks the renderer is
 *    idle, and `waitFor` stops as soon as nothing is scheduled.
 *  - The test never calls `main()`, never starts a detached server and writes only inside a temp
 *    state dir; the workspace string is a fake path that no file is read from.
 *  - The reconnect path is the production one: probe `/health`, miss, re-read `server.json`,
 *    probe the published port, adopt it. Only the clock is the test's.
 *
 * WHERE TO CHANGE
 *  The loop and the watchdog .... src/client.tsx (connection effect, WATCHDOG_MS, RETRY_MS)
 *  What a restart publishes ...... src/server.ts (server.json), src/types.ts (ServerInfo)
 */
import { afterAll, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { testRender } from "@opentui/react/test-utils";
import { App } from "../src/client";
import type { ServerInfo } from "../src/types";

const WORKSPACE = "/tmp/ws";
const TASK_ID = "t-aaaa1111";
/** The row prints only the id's last segment; this is what a frame can be scanned for. */
const SHORT_ID = TASK_ID.slice(2);

function info(port: number): ServerInfo {
  return {
    workspace: WORKSPACE,
    stateDir: "/tmp/state",
    pid: port,
    port,
    version: "test",
    startedAt: 1,
    model: "m",
    effort: "low",
    concurrency: 1,
    allowRun: true,
    hasKey: true,
    python: "python3",
    stale: false,
    changed: [],
  } as ServerInfo;
}

/** One stub server: `/health`, `/ws` (one snapshot on open), and an empty event journal. */
function board(status: string) {
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(req, srv) {
      const url = new URL(req.url);
      if (url.pathname === "/ws") {
        if (srv.upgrade(req, {})) return;
        return new Response("no", { status: 400 });
      }
      if (url.pathname === "/health") return Response.json({ ok: true, ...info(server.port) });
      if (url.pathname.endsWith("/events")) return Response.json({ events: [] });
      return Response.json({ error: "not found" }, { status: 404 });
    },
    websocket: {
      open(ws: any) {
        ws.send(
          JSON.stringify({
            type: "snapshot",
            server: info(server.port),
            tasks: [{ id: TASK_ID, prompt: "live job", status, createdAt: 1 }],
          }),
        );
      },
      message() {},
      close() {},
    },
  });
  return server;
}

const stateDir = mkdtempSync(join(tmpdir(), "code-tui-reconnect-"));
const publish = (port: number) =>
  writeFileSync(join(stateDir, "server.json"), JSON.stringify(info(port)));
const unpublish = () => rmSync(join(stateDir, "server.json"), { force: true });

const servers: { stop: (force?: boolean) => void }[] = [];

afterAll(() => {
  for (const server of servers) server.stop(true);
  rmSync(stateDir, { recursive: true, force: true });
});

/** Poll a captured frame with real time; `waitFor` cannot wait for a timer that is not rendering. */
async function frameWithin(
  capture: () => string,
  predicate: (frame: string) => boolean,
  timeoutMs: number,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let frame = capture();
  while (Date.now() < deadline) {
    if (predicate(frame)) return frame;
    await new Promise((r) => setTimeout(r, 100));
    frame = capture();
  }
  throw new Error(`frame never matched:\n${frame}`);
}

test(
  "a stopped server reads offline and a restart on a new port is picked up on its own",
  async () => {
    const first = board("running");
    servers.push(first);
    publish(first.port);

    const setup = await testRender(
      <App
        httpUrl={`http://127.0.0.1:${first.port}`}
        workspace={WORKSPACE}
        stateDir={stateDir}
      />,
      { width: 100, height: 24 },
    );
    const { captureCharFrame, dispose } = setup;

    // The board is alive: the task row and no `offline` word on the masthead.
    const alive = await frameWithin(
      captureCharFrame,
      (frame) => frame.includes(SHORT_ID),
      5000,
    );
    expect(alive).not.toContain("offline");
    expect(alive).toContain("running");

    // `bin/code-stop`: the process goes away and removes the published file.
    first.stop(true);
    unpublish();
    const offline = await frameWithin(
      captureCharFrame,
      (frame) => frame.includes("offline"),
      8000,
    );
    // The board keeps the last snapshot instead of blanking: the row is still there, just offline.
    expect(offline).toContain(SHORT_ID);

    // `bin/code`: a new process, a new port, a new server.json — and the task the restart failed.
    const second = board("failed");
    servers.push(second);
    publish(second.port);
    expect(second.port).not.toBe(first.port);

    const back = await frameWithin(
      captureCharFrame,
      (frame) => !frame.includes("offline") && frame.includes("failed"),
      12000,
    );
    // Reconnected on the new port without being relaunched: the masthead is live again and the
    // board carries the restarted server's verdict for the same task id.
    expect(back).toContain(SHORT_ID);
    expect(back).not.toContain("offline");

    await dispose();
  },
  // Real seconds: one stop, one 1s retry cycle and a fresh server boot, never a fake clock.
  30000,
);
