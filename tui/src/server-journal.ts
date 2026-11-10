/**
 * server-journal.ts: the append-only files — task snapshots, event streams, prompts, logs.
 *
 * WHAT IT IS
 *  The write side of the state dir and the read side of a stored task: every path formula, the
 *  atomic save, the event append (fanned out to the socket and the machine log) and the boot load.
 *
 * ENTRY POINTS
 *  taskFile, eventFile, promptFile, logFile (id); saveTask(task); appendLog(task, record);
 *  appendEvent(task, partial) -> TaskEvent; readTaskEvents(taskId, limit=600); loadTasks()
 *
 * INVARIANTS
 *  - Paths are derived, never stored, so an older task file still finds its machine log.
 *  - A failed write never breaks a run: it logs to console.error and returns.
 *  - Task files are written tmp+rename; `seq` comes from server-state, `ts` is stamped here.
 *  - loadTasks reaps a task's worker left `running` by a previous process (a crashed server),
 *    then fails the task and records why.
 *
 * WHERE TO CHANGE
 *  A path formula .... the *File helpers   One record .... saveTask/appendLog/appendEvent
 *  The boot rebuild ... loadTasks
 */
import { appendFileSync, existsSync, readdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { eventsDir, logsDir, promptsDir, tasksDir } from "./server-config";
import { broadcast, nextSeq, taskById, tasks } from "./server-state";
import { reapWorker } from "./server-procs";
import { pump } from "./server-tasks";
import type { Task, TaskEvent } from "./types";

/** `tasks/<id>.json`: the task's atomic snapshot. */
export function taskFile(id: string): string {
  return join(tasksDir, `${id}.json`);
}

/** `events/<id>.jsonl`: the task's append-only event stream. */
export function eventFile(id: string): string {
  return join(eventsDir, `${id}.jsonl`);
}

/** `prompts/<id>.txt`: the prompt exactly as it was queued, for the worker to read. */
export function promptFile(id: string): string {
  return join(promptsDir, `${id}.txt`);
}

/** The machine-readable per-task log: one append-only JSONL at a stable path under the state dir. */
export function logFile(id: string): string {
  return join(logsDir, `${id}.jsonl`);
}

/** Append one record to the task's machine log; a failed write never breaks the run. */
export function appendLog(task: Task, record: Record<string, unknown>): void {
  try {
    appendFileSync(logFile(task.id), `${JSON.stringify({ ts: Date.now(), ...record })}\n`);
  } catch (error) {
    console.error("log write failed", error);
  }
}

/** Write the task's snapshot atomically: the tmp file is complete before the rename. */
export function saveTask(task: Task): void {
  const file = taskFile(task.id);
  const tmp = `${file}.tmp`;
  writeFileSync(tmp, JSON.stringify(task, null, 2));
  renameSync(tmp, file);
}

/** Append one event, mirror it into the machine log and publish it to every socket. */
export function appendEvent(task: Task, partial: Omit<TaskEvent, "seq" | "ts">): TaskEvent {
  const event: TaskEvent = { seq: nextSeq(), ts: Date.now(), ...partial };
  try {
    appendFileSync(eventFile(task.id), `${JSON.stringify(event)}\n`);
  } catch (error) {
    console.error("event write failed", error);
  }
  // The machine log carries the event too, so one file answers "what happened" without a join.
  appendLog(task, { kind: "event", event });
  broadcast({ type: "event", taskId: task.id, event });
  return event;
}

/** The task's newest `limit` events, oldest first; a missing or unreadable file is []. */
export function readTaskEvents(taskId: string, limit = 600): TaskEvent[] {
  const file = eventFile(taskId);
  if (!existsSync(file)) return [];
  try {
    const lines = readFileSync(file, "utf8").split("\n").filter(Boolean).slice(-Math.max(1, Math.min(limit, 2000)));
    return lines
      .map((line) => {
        try {
          return JSON.parse(line) as TaskEvent;
        } catch {
          return null;
        }
      })
      .filter((event): event is TaskEvent => event !== null);
  } catch {
    return [];
  }
}

/** Rebuild the queue from `tasks/*.json` at boot; a task left running is failed and recorded. */
export function loadTasks(): void {
  for (const file of readdirSync(tasksDir).filter((name) => name.endsWith(".json"))) {
    try {
      const task = JSON.parse(readFileSync(join(tasksDir, file), "utf8")) as Task;
      if (!task.id || !task.prompt) continue;
      // The path is derived, not stored history: a task file written by an older server still gets
      // its machine log location, because the formula is the state dir's.
      task.log = logFile(task.id);
      tasks.push(task);
      taskById.set(task.id, task);
    } catch (error) {
      console.error("cannot load task", file, error);
    }
  }
  tasks.sort((a, b) => a.createdAt - b.createdAt);
  for (const task of tasks) {
    if (task.status === "running") {
      // A crashed server leaves its python worker (and the kernel child under it) alive; the
      // worker's own group id is its pid, so one reaped group frees the whole task's memory.
      if (task.pid) reapWorker(task.pid);
      task.status = "failed";
      task.finishedAt = Date.now();
      task.error = "server restarted while this task was running";
      saveTask(task);
      appendEvent(task, { kind: "error", text: task.error });
      appendEvent(task, { kind: "finished", text: "failed" });
    }
  }
  pump();
}
