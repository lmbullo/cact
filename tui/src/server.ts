/**
 * The TUI server: one detached Bun process per workspace.
 *
 * WHAT IT IS
 *  Owns the task queue, the worker processes, the HTTP/WebSocket API and the append-only
 *  journals. The client holds no state of its own and rebuilds the board from these files.
 *
 * ENTRY POINTS
 *  Started by launcher.ts, stopped by stop.ts. It picks a free port, writes server.json
 *  (ServerInfo: port, pid, model, check plan, ...) into the state dir, and prints its
 *  listening line.
 *  HTTP: GET  /                      plain-text banner
 *        GET  /health                ServerInfo + hasKey
 *        GET  /api/snapshot          {server, tasks}
 *        POST /api/tasks             {prompt} -> 201 {task}
 *        GET  /api/tasks/:id         {task}
 *        GET  /api/tasks/:id/events  ?limit=N (default 600)
 *        POST /api/tasks/:id/cancel  {task}
 *        GET  /ws                    WebSocket: Snapshot on open, then WsMessage
 *
 * INVARIANTS
 *  - One `python/worker.py` process per task, never a shared one. Concurrency is
 *    CODE_TUI_CONCURRENCY (1-8); pump() starts queued tasks while slots are free.
 *  - The worker speaks `@@TUI@@`-prefixed JSON lines on stdout. handleWorkerLine() is the
 *    only parser; any other line becomes a `log` event. Message shape lives in
 *    tui/python/worker.py.
 *  - Journal format is append-only: tasks/<id>.json is rewritten atomically (tmp + rename),
 *    events/<id>.jsonl is appended. Never rewrite an event file; the client tails it.
 *  - A `turn` message updates `Task.progress` (turn number, cumulative prompt tokens from
 *    the cache split) and broadcasts the task, so the board moves while the task runs. The
 *    `final` message clears `progress` and installs the authoritative steps/usage.
 *  - Port 0 means an OS-chosen port, published in server.json. Two servers for one workspace
 *    would race on those files, so launcher.ts always reuses a healthy one.
 *  - detectChecks() runs when a task starts, not once at boot, so a script an earlier
 *    task added to package.json is picked up without a server restart. CODE_TUI_CHECKS
 *    (JSON [{name, cmd, cheap, scope}]) wins, else CODE_TUI_CHECK as one entry, else
 *    `python3 bench/smoke.py` (cheap, scope bench) plus "typecheck"/"test" (or "build"
 *    when neither exists) from the root and tui/package.json (scope tui). The package
 *    manager follows the lockfile (bun.lock/bun.lockb -> bun, package-lock.json -> npm).
 *    An empty CODE_TUI_CHECK disables it. The plan travels to the worker as `--checks
 *    <json>` and is published in ServerInfo.checks; `cheap` entries become the agent's
 *    post-mutation health check and `scope` keeps a health run to specs whose paths changed.
 *    The goal runs the whole plan without a paths filter.
 *
 * WHERE TO CHANGE
 *  Queueing, concurrency, cancellation ... startTask / pump / cancelTask
 *  Worker message handling ............... handleWorkerLine (live progress in the `turn` case)
 *  HTTP/WebSocket surface ................ the Bun.serve fetch() block
 *  What is persisted and where ........... taskFile / eventFile / promptFile / saveTask
 *  Verification plan detection .......... detect-checks.ts, and ServerInfo.checks
 *  Task shape ............................ tui/src/types.ts
 */
import { createHash, randomUUID } from "node:crypto";
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join, relative, resolve } from "node:path";
import type { CheckSpec, ServerInfo, Snapshot, Task, TaskEvent, WorkerMessage } from "./types";
import { detectChecks } from "./detect-checks";

const VERSION = "0.1.0";
const ROOT = resolve(import.meta.dir, "..");
const WORKER = join(ROOT, "python", "worker.py");
const EVENT_PREFIX = "@@TUI@@";

function arg(name: string, fallback: string): string {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1]! : fallback;
}

function loadEnvFile(): void {
  for (const name of ["env", ".env"]) {
    const file = join(homedir(), ".config", "code-tui", name);
    if (!existsSync(file)) continue;
    for (const raw of readFileSync(file, "utf8").split("\n")) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq <= 0) continue;
      const key = line.slice(0, eq).trim();
      const value = line.slice(eq + 1).trim().replace(/^['"]|['"]$/g, "");
      process.env[key] = value;
    }
  }
}

loadEnvFile();

const workspace = resolve(arg("--workspace", process.cwd()));
const workspaceHash = createHash("sha256").update(workspace).digest("hex").slice(0, 16);
const stateRoot = process.env.CODE_TUI_STATE_ROOT
  ? resolve(process.env.CODE_TUI_STATE_ROOT)
  : join(process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state"), "code-tui");
const stateDir = resolve(arg("--state", join(stateRoot, "workspaces", workspaceHash)));
const tasksDir = join(stateDir, "tasks");
const eventsDir = join(stateDir, "events");
const promptsDir = join(stateDir, "prompts");
const serverFile = join(stateDir, "server.json");

for (const dir of [stateDir, tasksDir, eventsDir, promptsDir]) mkdirSync(dir, { recursive: true });
process.chdir(workspace);

const model = process.env.CODE_TUI_MODEL ?? "deepseek-flash";
const effort = process.env.CODE_TUI_EFFORT ?? "low";
const concurrency = Math.max(1, Math.min(8, Number(process.env.CODE_TUI_CONCURRENCY ?? 1) || 1));
const maxSteps = Math.max(1, Number(process.env.CODE_TUI_MAX_STEPS ?? 300) || 300);
const maxOutput = Math.max(1024, Number(process.env.CODE_TUI_MAX_OUTPUT ?? 393216) || 393216);
const maxWall = Math.max(30, Number(process.env.CODE_TUI_MAX_WALL ?? 1800) || 1800);
const cellTimeout = Math.max(5, Number(process.env.CODE_TUI_CELL_TIMEOUT ?? 120) || 120);
const allowRun = process.env.CODE_TUI_ALLOW_RUN !== "0";
const python = process.env.CODE_TUI_PYTHON ?? "python3";

const initialChecks = detectChecks(workspace);
const initialKey = Boolean(process.env.DEEPSEEK_API_KEY);

let server: any = null;
let shuttingDown = false;
let seqCounter = 0;
let info: ServerInfo = {
  workspace,
  stateDir,
  pid: process.pid,
  port: 0,
  version: VERSION,
  startedAt: Date.now(),
  model,
  effort,
  concurrency,
  allowRun,
  hasKey: initialKey,
  python,
  check: initialChecks[0]?.cmd ?? "",
  checks: initialChecks,
};

function refreshChecks(): CheckSpec[] {
  const next = detectChecks(workspace);
  info = { ...info, checks: next, check: next[0]?.cmd ?? "" };
  try {
    writeFileSync(serverFile, JSON.stringify(info, null, 2));
  } catch {
    /* the plan still reaches the worker even if the state file is unwritable */
  }
  return next;
}

const tasks: Task[] = [];
const taskById = new Map<string, Task>();
const running = new Map<string, { proc: ReturnType<typeof Bun.spawn>; lastStderr: string }>();

function nextSeq(): number {
  return Date.now() * 1000 + (seqCounter++ % 1000);
}

function taskFile(id: string): string {
  return join(tasksDir, `${id}.json`);
}

function eventFile(id: string): string {
  return join(eventsDir, `${id}.jsonl`);
}

function promptFile(id: string): string {
  return join(promptsDir, `${id}.txt`);
}

function saveTask(task: Task): void {
  const file = taskFile(task.id);
  const tmp = `${file}.tmp`;
  writeFileSync(tmp, JSON.stringify(task, null, 2));
  renameSync(tmp, file);
}

function capString(value: unknown, limit: number): unknown {
  return typeof value === "string" && value.length > limit ? `${value.slice(0, limit)}\n… truncated` : value;
}

function capValue(value: any, depth = 0): any {
  if (depth > 12) return "[depth]";
  if (typeof value === "string") return capString(value, 16000);
  if (typeof value === "number" || typeof value === "boolean" || value === null) return value;
  if (Array.isArray(value)) return value.slice(0, 40).map((item) => capValue(item, depth + 1));
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) out[key] = capValue(item, depth + 1);
    return out;
  }
  return String(value);
}

function appendEvent(task: Task, partial: Omit<TaskEvent, "seq" | "ts">): TaskEvent {
  const event: TaskEvent = { seq: nextSeq(), ts: Date.now(), ...partial };
  try {
    appendFileSync(eventFile(task.id), `${JSON.stringify(event)}\n`);
  } catch (error) {
    console.error("event write failed", error);
  }
  broadcast({ type: "event", taskId: task.id, event });
  return event;
}

function broadcast(message: unknown): void {
  if (!server) return;
  try {
    server.publish("tasks", JSON.stringify(message));
  } catch {
    // server is shutting down
  }
}

function snapshot(): Snapshot {
  return {
    type: "snapshot",
    server: { ...info, hasKey: Boolean(process.env.DEEPSEEK_API_KEY) },
    tasks: tasks.map((task) => ({ ...task })),
  };
}

function readTaskEvents(taskId: string, limit = 600): TaskEvent[] {
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

async function readWorkerStream(stream: ReadableStream<Uint8Array>, onLine: (line: string) => void): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (line.trim()) onLine(line);
        newline = buffer.indexOf("\n");
      }
    }
    if (buffer.trim()) onLine(buffer);
  } catch (error) {
    console.error("worker stream read failed", error);
  }
}

function handleWorkerLine(task: Task, raw: string): void {
  const line = raw.trimEnd();
  if (!line.startsWith(EVENT_PREFIX)) {
    appendEvent(task, { kind: "log", text: capString(line, 3000) as string });
    return;
  }
  let message: WorkerMessage;
  try {
    message = JSON.parse(line.slice(EVENT_PREFIX.length)) as WorkerMessage;
  } catch {
    appendEvent(task, { kind: "log", text: capString(line, 3000) as string });
    return;
  }

  switch (message.kind) {
    case "cell.start":
      appendEvent(task, {
        kind: "cell.start",
        cell: message.cell,
        tool: message.tool,
        args: capValue(message.args ?? {}) as Record<string, unknown>,
        code: message.code ? (capString(message.code, 12000) as string) : undefined,
      });
      break;
    case "turn": {
      const data = capValue(message.data) as Record<string, unknown>;
      // Live progress: the final event carries the authoritative counters, but a running
      // task would otherwise show nothing until it ends. Turn events already carry the
      // turn number and the prompt cache split, so derive `steps` and `tokens` from them.
      const cache = (data.cache ?? {}) as Record<string, unknown>;
      const usage = (data.usage ?? {}) as Record<string, unknown>;
      const turn = Number(message.turn ?? data.turn ?? 0);
      const tokens =
        Number(usage.total_tokens ?? 0) ||
        Number(cache.hit ?? 0) + Number(cache.miss ?? 0);
      const progress = task.progress ?? {};
      task.progress = {
        steps: turn > 0 ? turn : progress.steps,
        tokens: (progress.tokens ?? 0) + (Number.isFinite(tokens) ? tokens : 0),
      };
      saveTask(task);
      broadcast({ type: "task", task });
      appendEvent(task, {
        kind: "turn",
        turn: message.turn,
        data,
      });
      break;
    }
    case "log":
      appendEvent(task, { kind: "log", text: capString(message.text, 4000) as string });
      break;
    case "error":
      task.error = String(capString(message.text, 8000));
      appendEvent(task, { kind: "error", text: task.error });
      break;
    case "final":
      task.stop = message.stop;
      task.steps = message.steps;
      task.calls = message.calls;
      task.llmCalls = message.llmCalls;
      task.errors = message.errors;
      task.usage = message.usage;
      task.wallMs = message.wallMs;
      // The final counters replace the live estimate, so the board never prefers stale numbers.
      task.progress = undefined;
      task.final = capString(message.final ?? "", 64000) as string;
      appendEvent(task, {
        kind: "final",
        text: task.final,
        data: {
          status: message.status,
          stop: message.stop,
          steps: message.steps,
          calls: message.calls,
          llmCalls: message.llmCalls,
          errors: message.errors,
          usage: message.usage,
          wallMs: message.wallMs,
        },
      });
      break;
    default:
      appendEvent(task, { kind: "log", text: line.slice(0, 3000) });
  }
}

function startTask(task: Task): void {
  if (task.status !== "queued" || running.has(task.id) || shuttingDown) return;
  task.status = "running";
  task.startedAt = Date.now();
  task.model = model;
  task.effort = effort;
  const checks = refreshChecks();
  saveTask(task);
  appendEvent(task, { kind: "started", text: `agent started (${model}, effort=${effort})` });
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
    if (line.startsWith(EVENT_PREFIX)) {
      try {
        const msg = JSON.parse(line.slice(EVENT_PREFIX.length)) as WorkerMessage;
        if (msg.kind === "final") finalStatus = msg.status;
      } catch {
        // handleWorkerLine will report it as a log line
      }
    }
    handleWorkerLine(task, line);
  };
  const handleStderr = (line: string) => {
    const text = line.trim();
    if (!text) return;
    runtime.lastStderr = text.slice(-2000);
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

function pump(): void {
  if (shuttingDown) return;
  const capacity = concurrency - running.size;
  if (capacity <= 0) return;
  const queued = tasks
    .filter((task) => task.status === "queued")
    .sort((a, b) => a.createdAt - b.createdAt)
    .slice(0, capacity);
  for (const task of queued) startTask(task);
}

function createTask(prompt: string): Task {
  const id = `t-${Date.now().toString(36)}-${randomUUID().slice(0, 5)}`;
  const task: Task = {
    id,
    prompt,
    status: "queued",
    createdAt: Date.now(),
    model,
    effort,
  };
  writeFileSync(promptFile(id), prompt);
  tasks.push(task);
  taskById.set(id, task);
  saveTask(task);
  appendEvent(task, { kind: "queued", text: "queued" });
  broadcast({ type: "task", task });
  pump();
  return task;
}

function cancelTask(task: Task): void {
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

function loadTasks(): void {
  for (const file of readdirSync(tasksDir).filter((name) => name.endsWith(".json"))) {
    try {
      const task = JSON.parse(readFileSync(join(tasksDir, file), "utf8")) as Task;
      if (!task.id || !task.prompt) continue;
      tasks.push(task);
      taskById.set(task.id, task);
    } catch (error) {
      console.error("cannot load task", file, error);
    }
  }
  tasks.sort((a, b) => a.createdAt - b.createdAt);
  for (const task of tasks) {
    if (task.status === "running") {
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

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

loadTasks();

server = Bun.serve({
  hostname: "127.0.0.1",
  port: 0,
  fetch(req: Request, srv: any) {
    const url = new URL(req.url);
    if (url.pathname === "/ws") {
      if (srv.upgrade(req, { data: { id: randomUUID() } })) return;
      return new Response("websocket upgrade failed", { status: 400 });
    }
    if (url.pathname === "/health") {
      return json({ ok: true, ...info, hasKey: Boolean(process.env.DEEPSEEK_API_KEY) });
    }
    if (url.pathname === "/api/snapshot") return json(snapshot());
    if (url.pathname === "/api/tasks" && req.method === "POST") {
      return (async () => {
        try {
          const body = (await req.json()) as { prompt?: unknown };
          const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
          if (!prompt) return json({ error: "prompt is empty" }, 400);
          if (prompt.length > 20000) return json({ error: "prompt is too long (20000 char max)" }, 400);
          return json({ task: createTask(prompt) }, 201);
        } catch (error) {
          return json({ error: String(error) }, 400);
        }
      })();
    }
    const match = url.pathname.match(/^\/api\/tasks\/([^/]+)(?:\/(events|cancel))?$/);
    if (match) {
      const task = taskById.get(decodeURIComponent(match[1]!));
      if (!task) return json({ error: "task not found" }, 404);
      if (!match[2] && req.method === "GET") return json({ task });
      if (match[2] === "events" && req.method === "GET") {
        const limit = Number(url.searchParams.get("limit") ?? 600) || 600;
        return json({ events: readTaskEvents(task.id, limit) });
      }
      if (match[2] === "cancel" && req.method === "POST") {
        cancelTask(task);
        return json({ task });
      }
    }
    if (url.pathname === "/") {
      return new Response(
        [
          `code-tui ${VERSION}`,
          `workspace: ${workspace}`,
          `state:     ${stateDir}`,
          `port:      ${info.port}`,
          `model:     ${model} (${effort})`,
          `key:       ${process.env.DEEPSEEK_API_KEY ? "set" : "missing"}`,
        ].join("\n"),
        { headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }
    return json({ error: "not found" }, 404);
  },
  websocket: {
    open(ws: any) {
      ws.subscribe("tasks");
      ws.send(JSON.stringify(snapshot()));
    },
    message() {},
    close() {},
  },
});

info = { ...info, port: server.port };
writeFileSync(serverFile, JSON.stringify(info, null, 2));
console.log(`code-tui server ${VERSION} listening on 127.0.0.1:${server.port}`);
console.log(`workspace ${workspace}`);
console.log(`state     ${stateDir}`);

const heartbeat = setInterval(() => broadcast({ type: "ping", ts: Date.now() }), 15000);

function shutdown(signal: string): void {
  if (shuttingDown) return;
  shuttingDown = true;
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
  try {
    server?.stop(true);
  } catch {
    // ignore
  }
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