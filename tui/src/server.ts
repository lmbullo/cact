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
