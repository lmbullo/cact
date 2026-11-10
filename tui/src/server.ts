/**
 * server.ts: the TUI server — one detached Bun process per workspace: queue, workers, HTTP/WS.
 *
 * WHAT IT IS
 *  The entry point `launcher.ts` starts and `stop.ts` stops. It binds the socket, publishes
 *  server.json and the launch line, then waits: config, state, journals, queue, workers and
 *  routes are server-*.ts. The client holds no state and rebuilds the board from these files.
 *
 * ENTRY POINTS
 *  Bun src/server.ts --workspace DIR [--state DIR] [--log FILE]
 *  server.json (ServerInfo) + one launch line {event,pid,port,profile,check,log}; port 0 =
 *  OS-chosen. Routes: server-http.ts route(). SIGINT/SIGTERM -> shutdown().
 *
 * INVARIANTS
 *  one python/worker.py per task, never shared; CODE_TUI_CONCURRENCY 1-8; pump() fills free slots.
 *  shutdown is once: running/queued tasks become failed, workers signalled, server.json removed.
 *  A heartbeat ping every 15 s keeps a socket the client silently lost from looking alive.
 *
 * WHERE TO CHANGE
 *  Config .... server-config.ts   State/snapshot .... server-state.ts   Journals .... server-journal.ts
 *  Queue .... server-tasks.ts   Protocol .... server-worker.ts   Routes .... server-http.ts
 *  Boot order here: loadTasks(), Bun.serve, the port write, pump() — keep it.
 */
import { writeFileSync } from "node:fs";
import { rmSync } from "node:fs";
import { serverFile, serverLog } from "./server-config";
import { route } from "./server-http";
import { loadTasks, saveTask } from "./server-journal";
import {
  beginShutdown,
  broadcast,
  info,
  running,
  setInfo,
  setServer,
  snapshot,
  stopServer,
  tasks,
} from "./server-state";
import { pump } from "./server-tasks";

loadTasks();

const bound = Bun.serve({
  hostname: "127.0.0.1",
  port: 0,
  fetch: route,
  websocket: {
    open(ws: any) {
      ws.subscribe("tasks");
      ws.send(JSON.stringify(snapshot()));
    },
    message() {},
    close() {},
  },
});

setServer(bound);
setInfo({ ...info, port: bound.port! });
writeFileSync(serverFile, JSON.stringify(info, null, 2));
// One machine-readable launch line: an agent reads pid/port/profile/check paths without parsing
// prose, and `server.log` (this process's own output) is named so it never has to be guessed.
console.log(
  JSON.stringify({
    event: "listening",
    pid: info.pid,
    port: info.port,
    profile: info.profile,
    check: info.check,
    log: serverLog,
  }),
);

const heartbeat = setInterval(() => broadcast({ type: "ping", ts: Date.now() }), 15000);

/** Shut down once: fail the unfinished tasks, signal the workers, drop server.json and exit. */
function shutdown(signal: string): void {
  if (!beginShutdown()) return;
  console.log(`shutting down (${signal})`);
  clearInterval(heartbeat);
  for (const task of tasks) {
    if (task.status === "running" || task.status === "queued") {
      task.status = "failed";
      task.finishedAt = Date.now();
      task.error = "server stopped";
      saveTask(task);
    }
  }
  for (const { proc } of running.values()) {
    try {
      proc.kill("SIGTERM");
    } catch {
      // ignore
    }
  }
  rmSync(serverFile, { force: true });
  stopServer();
  process.exit(0);
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("uncaughtException", (error) => {
  console.error("uncaught", error);
  shutdown("uncaughtException");
});
process.on("unhandledRejection", (error) => {
  console.error("unhandled rejection", error);
});

pump();
