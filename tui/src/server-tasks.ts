/**
 * server-tasks.ts: the queue — create, start, pump and cancel one task's worker process.
 *
 * WHAT IT IS
 *  The task lifecycle. `createTask` queues, `pump` fills the free slots (concurrency 1-8),
 *  `startTask` spawns the one python worker a task gets and watches it exit, and `cancelTask`
 *  asks a queued task to stop or signals a running one.
 *
 * ENTRY POINTS
 *  createTask(prompt) -> Task   queue a new task, journal it, broadcast it, pump
 *  pump()                       start queued tasks while a slot is free
 *  startTask(task)              spawn the worker, read its streams, settle its status on exit
 *  cancelTask(task)             queued -> cancelled, running -> SIGTERM then SIGKILL after 5 s
 *
 * INVARIANTS
 *  - One python/worker.py per task, never shared; `running` is the slot ledger.
 *  - `final` decides the status once the process exits: done, partial (a budget stop with a
 *    summary) or failed; a cancelled task is cancelled whatever the worker said.
 *  - Nothing starts while shutting down or once the task is no longer queued.
 *
 * WHERE TO CHANGE
 *  What a worker is told ... the Bun.spawn argv/env in startTask
 *  How a run settles ....... the exit handler in startTask    Cancel .... cancelTask
 */
import { randomUUID } from "node:crypto";
import { writeFileSync } from "node:fs";
import {
  WORKER,
  allowRun,
  cellTimeout,
  concurrency,
  effort,
  maxOutput,
  maxSteps,
  maxWall,
  model,
  profile,
  python,
  stateDir,
  workspace,
} from "./server-config";
import { appendEvent, appendLog, logFile, promptFile, saveTask } from "./server-journal";
import { broadcast, refreshChecks, running, shuttingDown, taskById, tasks } from "./server-state";
import { handleWorkerLine, readWorkerStream } from "./server-worker";
import { capString, parseWorkerLine } from "./worker-events";
import type { Task } from "./types";

/** Queue a new task: write its prompt, journal it, broadcast it and pump the queue. */
export function createTask(prompt: string): Task {
  const id = `t-${Date.now().toString(36)}-${randomUUID().slice(0, 5)}`;
  const task: Task = {
    id,
    prompt,
    status: "queued",
    createdAt: Date.now(),
    model,
    effort,
    log: logFile(id),
  };
  writeFileSync(promptFile(id), prompt);
  tasks.push(task);
  taskById.set(id, task);
  saveTask(task);
  appendLog(task, { kind: "prompt", text: capString(prompt, 4000) as string });
  appendEvent(task, { kind: "queued", text: "queued" });
  broadcast({ type: "task", task });
  pump();
  return task;
}

/** Start queued tasks while a concurrency slot is free, oldest first. */
export function pump(): void {
  if (shuttingDown) return;
  const capacity = concurrency - running.size;
  if (capacity <= 0) return;
  const queued = tasks
    .filter((task) => task.status === "queued")
    .sort((a, b) => a.createdAt - b.createdAt)
    .slice(0, capacity);
  for (const task of queued) startTask(task);
}

/** Spawn the task's worker, stream its stdout/stderr through handleWorkerLine and settle it. */
export function startTask(task: Task): void {
  if (task.status !== "queued" || running.has(task.id) || shuttingDown) return;
  task.status = "running";
  task.startedAt = Date.now();
  task.model = model;
  task.effort = effort;
  const checks = refreshChecks();
  saveTask(task);
  appendEvent(task, { kind: "started", text: `agent started (${model}, effort=${effort}, profile=${profile})` });
  broadcast({ type: "task", task });

  let proc: ReturnType<typeof Bun.spawn>;
  try {
    proc = Bun.spawn({
      cmd: [
        python,
        WORKER,
        "--workspace",
        workspace,
        "--prompt-file",
        promptFile(task.id),
        "--model",
        model,
        "--effort",
        effort,
        "--max-steps",
        String(maxSteps),
        "--max-wall",
        String(maxWall),
        "--max-output",
        String(maxOutput),
        "--cell-timeout",
        String(cellTimeout),
        "--allow-run",
        allowRun ? "1" : "0",
        "--checks",
        JSON.stringify(checks),
      ],
      cwd: workspace,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        CODE_TUI_WORKSPACE: workspace,
        CODE_TUI_STATE: stateDir,
      },
      stdin: "ignore",
      stdout: "pipe",
      stderr: "pipe",
    });
  } catch (error) {
    task.status = "failed";
    task.finishedAt = Date.now();
    task.error = `failed to start worker: ${String(error)}`;
    saveTask(task);
    appendEvent(task, { kind: "error", text: task.error });
    appendEvent(task, { kind: "finished", text: "failed" });
    broadcast({ type: "task", task });
    pump();
    return;
  }

  task.pid = proc.pid;
  saveTask(task);
  appendEvent(task, { kind: "pid", text: `worker pid ${proc.pid}` });
  broadcast({ type: "task", task });

  const runtime = { proc, lastStderr: "" };
  running.set(task.id, runtime);
  let finalStatus: "done" | "partial" | "failed" | undefined;
  const handle = (line: string) => {
    // A `final` verdict decides the task's status once the process exits; handleWorkerLine owns
    // the journal, and reports a line it cannot parse as a log line.
    const message = parseWorkerLine(line);
    if (message?.kind === "final") finalStatus = message.status;
    handleWorkerLine(task, line);
  };
  const handleStderr = (line: string) => {
    const text = line.trim();
    if (!text) return;
    runtime.lastStderr = text.slice(-2000);
    appendLog(task, { kind: "stderr", text: capString(text, 4000) as string });
    appendEvent(task, { kind: "stderr", text: capString(text, 4000) as string });
  };

  const outStream = typeof proc.stdout === "object" ? (proc.stdout as ReadableStream<Uint8Array>) : null;
  const errStream = typeof proc.stderr === "object" ? (proc.stderr as ReadableStream<Uint8Array>) : null;
  const outDone = outStream ? readWorkerStream(outStream, handle) : Promise.resolve();
  const errDone = errStream ? readWorkerStream(errStream, handleStderr) : Promise.resolve();

  void (async () => {
    const exitCode = await proc.exited;
    await Promise.all([outDone, errDone]);
    running.delete(task.id);
    task.exitCode = exitCode;
    task.finishedAt = Date.now();
    const isPartial = Boolean(task.final) && (
      finalStatus === "partial" ||
      (finalStatus === "failed" && (task.stop === "STEP_LIMIT" || task.stop === "WALL_LIMIT"))
    );
    if (task.cancelRequested) {
      task.status = "cancelled";
      appendEvent(task, { kind: "finished", text: "cancelled" });
    } else if (finalStatus === "done" && task.final) {
      task.status = "done";
      appendEvent(task, { kind: "finished", text: "done" });
    } else if (isPartial) {
      task.status = "partial";
      task.error = `agent reached ${task.stop ?? "the budget"} after ${task.steps ?? "?"} steps; partial summary saved`;
      appendEvent(task, { kind: "log", text: task.error });
      appendEvent(task, { kind: "finished", text: "partial" });
    } else {
      task.status = "failed";
      if (!task.error) {
        const reason = task.final
          ? `agent stopped (${task.stop ?? "unknown"}) after ${task.steps ?? "?"} steps; see final output`
          : runtime.lastStderr
            ? runtime.lastStderr.slice(0, 2000)
            : `agent exited with code ${exitCode} without a final answer`;
        task.error = reason;
        appendEvent(task, { kind: "error", text: task.error });
      }
      appendEvent(task, { kind: "finished", text: "failed" });
    }
    saveTask(task);
    broadcast({ type: "task", task });
    pump();
  })();
}

/** Cancel a task: a queued one is cancelled outright, a running one is signalled and escalated. */
export function cancelTask(task: Task): void {
  if (task.status === "queued") {
    task.status = "cancelled";
    task.finishedAt = Date.now();
    task.cancelRequested = true;
    saveTask(task);
    appendEvent(task, { kind: "finished", text: "cancelled" });
    broadcast({ type: "task", task });
    pump();
    return;
  }
  if (task.status !== "running") return;
  task.cancelRequested = true;
  saveTask(task);
  appendEvent(task, { kind: "log", text: "cancellation requested" });
  const runtime = running.get(task.id);
  if (!runtime) return;
  try {
    runtime.proc.kill("SIGTERM");
  } catch {
    // already gone
  }
  setTimeout(() => {
    if (running.has(task.id)) {
      try {
        runtime.proc.kill("SIGKILL");
      } catch {
        // already gone
      }
    }
  }, 5000);
  broadcast({ type: "task", task });
}
