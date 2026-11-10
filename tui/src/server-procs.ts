/**
 * server-procs.ts: one worker's process tree — spawn it detached and signal the whole group.
 *
 * WHAT IT IS
 *  The process half of the queue: `workerSpawn` (the Bun.spawn options every worker shares) and
 *  `signalTree`/`reapWorker`, which reach the python worker plus the kernel child and run()
 *  children it starts. server-tasks.ts spawns and cancels; server.ts signals every worker on
 *  shutdown; server-journal.ts reaps one left behind by a crashed server.
 *
 * ENTRY POINTS
 *  workerSpawn(cmd, cwd, env) -> options   one detached worker with piped stdout/stderr
 *  signalTree(pid, signal) -> boolean      signal the worker's process group, else just the pid
 *  reapWorker(pid) -> boolean              SIGKILL a stale worker from a crashed server
 *
 * INVARIANTS
 *  - A worker is spawned detached, so its pid is its process-group id and one signal reaches the
 *    whole tree. That is why a cancel or a shutdown never leaves a kernel child behind.
 *  - A pid that does not lead a group is signalled alone: `kill(-pid)` is never sent for an
 *    unknown pid, because that could reach the server's own group.
 *  - Reaping only kills a pid whose /proc cmdline names this server's worker entry point;
 *    without /proc it does nothing rather than risk an unrelated process that reused the pid.
 *
 * WHERE TO CHANGE
 *  Spawn flags .... workerSpawn    One signal .... signalTree    Crash cleanup .... reapWorker
 */
import { readFileSync } from "node:fs";
import { WORKER } from "./server-config";

/** The spawn options every worker shares: detached group leader, piped output, no stdin. */
export function workerSpawn(cmd: string[], cwd: string, env: Record<string, string | undefined>) {
  return {
    cmd,
    cwd,
    env,
    stdin: "ignore" as const,
    stdout: "pipe" as const,
    stderr: "pipe" as const,
    detached: true,
  };
}

/** Fields of /proc/<pid>/stat after the parenthesised comm, or null when it cannot be read. */
function statFields(pid: number): string[] | null {
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
    return stat.slice(stat.lastIndexOf(")") + 2).split(" ");
  } catch {
    return null;
  }
}

/** True only when `pid` leads its own process group, the shape a detached worker has. */
export function leadsGroup(pid: number): boolean {
  const fields = statFields(pid);
  return fields !== null && Number(fields[2]) === pid;
}

/** Signal the worker's process group when it leads one, else the worker alone; false if gone. */
export function signalTree(pid: number, signal: "SIGTERM" | "SIGKILL"): boolean {
  if (!Number.isInteger(pid) || pid <= 1) return false;
  if (leadsGroup(pid)) {
    try {
      process.kill(-pid, signal);
      return true;
    } catch {
      // the group is already gone; fall through to the pid
    }
  }
  try {
    process.kill(pid, signal);
    return true;
  } catch {
    return false;
  }
}

/** Kill a worker left running by a crashed server, but only when the pid still names our worker. */
export function reapWorker(pid: number): boolean {
  if (!Number.isInteger(pid) || pid <= 1) return false;
  try {
    const cmdline = readFileSync(`/proc/${pid}/cmdline`, "utf8");
    if (!cmdline.includes(WORKER)) return false;
  } catch {
    return false;
  }
  return signalTree(pid, "SIGKILL");
}
