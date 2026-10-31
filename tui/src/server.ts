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
import { join, resolve } from "node:path";
import type { ServerInfo, Snapshot, Task, TaskEvent, WorkerMessage } from "./types";

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
const maxSteps = Math.max(1, Number(process.env.CODE_TUI_MAX_STEPS ?? 120) || 120);
const maxOutput = Math.max(1024, Number(process.env.CODE_TUI_MAX_OUTPUT ?? 393216) || 393216);
const maxWall = Math.max(30, Number(process.env.CODE_TUI_MAX_WALL ?? 1800) || 1800);
const cellTimeout = Math.max(5, Number(process.env.CODE_TUI_CELL_TIMEOUT ?? 120) || 120);
const allowRun = process.env.CODE_TUI_ALLOW_RUN !== "0";
const python = process.env.CODE_TUI_PYTHON ?? "python3";

function detectCheck(root: string): string {
  const explicit = process.env.CODE_TUI_CHECK;
  if (explicit !== undefined) return explicit.trim();
  try {
    const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8")) as { scripts?: Record<string, string> };
    const scripts = pkg.scripts ?? {};
    if (scripts.typecheck) return "bun run typecheck";
    if (scripts.test) return "bun test";
  } catch {
    return "";
  }
  return "";
}

const check = detectCheck(workspace);
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
  check,
};

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
  if (depth > 5) return "[depth]";
  if (typeof value === "string") return capString(value, 8000);
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
        code: capString(message.code, 12000) as string,
      });
      break;
    case "turn":
      appendEvent(task, {
        kind: "turn",
        turn: message.turn,
        data: capValue(message.data),
      });
      break;
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
      task.final = capString(message.final ?? "", 12000) as string;
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

