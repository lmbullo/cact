/**
 * server-state.ts: the server's one mutable state — tasks, workers, socket, info and staleness.
 *
 * WHAT IT IS
 *  The values every other server module shares: the task list and its indexes, this process's
 *  ServerInfo, the socket, the shutdown latch, and the read-time views over them.
 *
 * ENTRY POINTS
 *  tasks, taskById, running, info, setInfo, setServer, stopServer, beginShutdown, nextSeq,
 *  sourceStatus, refreshChecks, broadcast, snapshot
 *
 * INVARIANTS
 *  - One process, one workspace: every field is that process's, never shared or persisted.
 *  - sourceStatus re-hashes at most once per SOURCE_RECHECK_MS and is never persisted.
 *  - refreshChecks rewrites server.json best-effort: an unwritable file must not stop the plan.
 *  - broadcast no-ops before the server exists and while it shuts down.
 *
 * WHERE TO CHANGE
 *  A board read .... snapshot   A client message .... broadcast   Staleness .... sourceStatus
 */
import { writeFileSync } from "node:fs";
import { detectChecks } from "./detect-checks";
import { bootSource, compareSource } from "./source-hash";
import {
  ROOT,
  SOURCE_RECHECK_MS,
  VERSION,
  allowRun,
  concurrency,
  effort,
  initialChecks,
  initialKey,
  logsDir,
  model,
  profile,
  python,
  serverFile,
  stateDir,
  workspace,
} from "./server-config";
import type { CheckSpec, ServerInfo, Snapshot, Task } from "./types";

/** The code this process is running, taken once at boot; `hashes` is what `changed` compares to. */
export const boot = bootSource(ROOT);

/** This process's ServerInfo; `port` is filled in once Bun.serve binds. */
export let info: ServerInfo = {
  workspace,
  stateDir,
  pid: process.pid,
  port: 0,
  version: VERSION,
  startedAt: Date.now(),
  model,
  effort,
  profile,
  concurrency,
  allowRun,
  hasKey: initialKey,
  python,
  check: initialChecks[0]?.cmd ?? "",
  checks: initialChecks,
  logsDir,
  source: boot.source,
};

/** Replace the published ServerInfo (a port bind or a fresh check plan). */
export function setInfo(next: ServerInfo): void {
  info = next;
}

/** The published Bun server, or null before it binds and after it stops. */
export let server: any = null;

/** Publish the Bun server `broadcast` writes to. */
export function setServer(next: any): void {
  server = next;
}

/** Stop the published server; a server that never bound, or is already down, is fine. */
export function stopServer(): void {
  try {
    server?.stop(true);
  } catch {
    // ignore
  }
}

/** True once shutdown has started; nothing new may start after it. */
export let shuttingDown = false;

/** Take the shutdown latch; the second caller gets false and must do nothing. */
export function beginShutdown(): boolean {
  if (shuttingDown) return false;
  shuttingDown = true;
  return true;
}

/** Every task this server knows, oldest first once loadTasks has sorted it. */
export const tasks: Task[] = [];
/** The same tasks by id, for the HTTP routes and the cancel path. */
export const taskById = new Map<string, Task>();
/** The live workers by task id: their process and the last stderr line for the failure text. */
export const running = new Map<string, { proc: ReturnType<typeof Bun.spawn>; lastStderr: string }>();

let seqCounter = 0;

/** A monotonic event sequence: milliseconds, then a 0-999 counter so a burst keeps its order. */
export function nextSeq(): number {
  return Date.now() * 1000 + (seqCounter++ % 1000);
}

/**
 * Is the running code still the code on disk? Re-hashes the boot file list, throttled to one
 * comparison every SOURCE_RECHECK_MS, and reports the paths that changed (a missing file counts).
 * Read-time only: it is never persisted, and a restart is the only fix.
 */
let sourceCache: { at: number; stale: boolean; changed: string[] } = {
  at: boot.source.hashedAt,
  stale: false,
  changed: [],
};

/** The cached staleness read: re-hashes at most once per SOURCE_RECHECK_MS. */
export function sourceStatus(): { stale: boolean; changed: string[] } {
  const now = Date.now();
  if (now - sourceCache.at >= SOURCE_RECHECK_MS) {
    const { stale, changed } = compareSource(ROOT, boot);
    sourceCache = { at: now, stale, changed };
  }
  return { stale: sourceCache.stale, changed: sourceCache.changed };
}

/** Re-detect the workspace's checks, record them and rewrite server.json; returns the plan. */
export function refreshChecks(): CheckSpec[] {
  const next = detectChecks(workspace);
  info = { ...info, checks: next, check: next[0]?.cmd ?? "" };
  try {
    writeFileSync(serverFile, JSON.stringify(info, null, 2));
  } catch {
    /* the plan still reaches the worker even if the state file is unwritable */
  }
  return next;
}

/** Publish one JSON message on the "tasks" channel every client socket subscribes to. */
export function broadcast(message: unknown): void {
  if (!server) return;
  try {
    server.publish("tasks", JSON.stringify(message));
  } catch {
    // server is shutting down
  }
}

/** The whole board in one message: the server read plus a copy of every task. */
export function snapshot(): Snapshot {
  return {
    type: "snapshot",
    server: {
      ...info,
      ...sourceStatus(),
      hasKey: Boolean(process.env.DEEPSEEK_API_KEY),
    },
    tasks: tasks.map((task) => ({ ...task })),
  };
}
