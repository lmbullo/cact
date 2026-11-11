/**
 * server-config.ts: the server's paths, arguments and environment, read once at boot.
 *
 * WHAT IT IS
 *  Everything told from outside — argv, ~/.config/code-tui/env, the state dir, the limits —
 *  resolved into constants. It creates the state dirs and chdirs into the workspace at import,
 *  because both must happen before a worker can spawn.
 *
 * ENTRY POINTS
 *  arg(name, fallback), loadEnvFile(); VERSION, ROOT, WORKER, WORKER_CMD, workspace, stateDir,
 *  tasksDir, eventsDir, promptsDir, logsDir, serverFile, model, effort, profile, concurrency,
 *  maxSteps, maxOutput, maxWall, cellTimeout, allowRun, python, initialChecks, initialKey,
 *  serverLog, SOURCE_RECHECK_MS
 *
 * INVARIANTS
 *  - loadEnvFile() runs before any env read; every constant is computed once, so a later
 *    process.env edit cannot move a path mid-run. Every state dir exists after import.
 *  - Limits are clamped here, not at use: concurrency 1-MAX_CONCURRENCY, maxSteps >= 1
 *    (0 = unlimited), maxOutput >= 1024.
 *
 * WHERE TO CHANGE
 *  Paths and their args .... arg, workspace, stateDir, *Dir, serverFile
 *  Limits and defaults ..... model, effort, profile, concurrency, max*, allowRun
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { detectChecks } from "./detect-checks";
import type { CheckSpec } from "./types";

/** The version printed on `/` and recorded in server.json. */
export const VERSION = "0.1.0";
/** The tui/ directory: `import.meta.dir` is tui/src at runtime. */
export const ROOT = resolve(import.meta.dir, "..");
/** The one worker entry point every task runs when no Rust binary is built: python/worker.py. */
export const WORKER = join(ROOT, "python", "worker.py");

/** One `--flag value` from argv, or `fallback` when the flag or its value is missing. */
export function arg(name: string, fallback: string): string {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1]! : fallback;
}

/** Read ~/.config/code-tui/env (then .env) into process.env, last one winning. */
export function loadEnvFile(): void {
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

/** The absolute workspace this server serves; also the workers' cwd. */
export const workspace = resolve(arg("--workspace", process.cwd()));
const workspaceHash = createHash("sha256").update(workspace).digest("hex").slice(0, 16);
const stateRoot = process.env.CODE_TUI_STATE_ROOT
  ? resolve(process.env.CODE_TUI_STATE_ROOT)
  : join(process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state"), "code-tui");
/** This workspace's state dir: tasks, events, prompts, logs and server.json live here. */
export const stateDir = resolve(arg("--state", join(stateRoot, "workspaces", workspaceHash)));
/** `tasks/<id>.json`, one atomic snapshot per task. */
export const tasksDir = join(stateDir, "tasks");
/** `events/<id>.jsonl`, the append-only event stream the log view reads. */
export const eventsDir = join(stateDir, "events");
/** `prompts/<id>.txt`, the prompt exactly as it was queued. */
export const promptsDir = join(stateDir, "prompts");
/** Where every task's machine-readable JSONL lives: `logs/<id>.jsonl`, the stable per-task path. */
export const logsDir = join(stateDir, "logs");
/** `server.json`, the port/pid the client and stop.ts read. */
export const serverFile = join(stateDir, "server.json");

for (const dir of [stateDir, tasksDir, eventsDir, promptsDir, logsDir]) mkdirSync(dir, { recursive: true });
process.chdir(workspace);

/** The model every task is started with (CODE_TUI_MODEL). */
export const model = process.env.CODE_TUI_MODEL ?? "deepseek-flash";
/** The reasoning effort every task is started with (CODE_TUI_EFFORT). */
export const effort = process.env.CODE_TUI_EFFORT ?? "low";
// The measured arm. `fast-code` is the lean typed set plus the python cell, inline finish and
// green-plan stop; the worker's config.py owns the same default when it is launched directly.
export const profile = process.env.CODE_TUI_PROFILE ?? "fast-code";
process.env.CODE_TUI_PROFILE = profile;
/** The highest run count a server may be configured with; 100 live runs is a supported load. */
export const MAX_CONCURRENCY = 256;
/** How many tasks may run at once: CODE_TUI_CONCURRENCY, clamped to 1-MAX_CONCURRENCY. */
export const concurrency = Math.max(
  1,
  Math.min(MAX_CONCURRENCY, Number(process.env.CODE_TUI_CONCURRENCY ?? 1) || 1),
);
/** The worker's step budget per task; 0 means unlimited. */
export const maxSteps = (() => {
  const requested = Number(process.env.CODE_TUI_MAX_STEPS ?? 300);
  return requested === 0 ? 0 : Math.max(1, requested || 300);
})();
/** The worker's output budget per task, in characters. */
export const maxOutput = Math.max(1024, Number(process.env.CODE_TUI_MAX_OUTPUT ?? 393216) || 393216);
/** The worker's wall-clock budget per task, in seconds. */
export const maxWall = Math.max(30, Number(process.env.CODE_TUI_MAX_WALL ?? 1800) || 1800);
/** How long one python cell may run, in seconds. */
export const cellTimeout = Math.max(5, Number(process.env.CODE_TUI_CELL_TIMEOUT ?? 120) || 120);
/** Whether a task may run shell/git at all (CODE_TUI_ALLOW_RUN != "0"). */
export const allowRun = process.env.CODE_TUI_ALLOW_RUN !== "0";
/** The interpreter a worker is spawned with. */
export const python = process.env.CODE_TUI_PYTHON ?? "python3";

/**
 * The worker's full argv, resolved once at boot: CODE_TUI_WORKER, else the built Rust binary
 * (`rust/target/{release,debug}/cact worker`), else `python3 python/worker.py`.
 * An explicit CODE_TUI_PYTHON wins over the Rust binary: setting the interpreter is how a caller
 * (a test, a sandbox) asks for the python worker on purpose.
 */
export const WORKER_CMD: string[] = (() => {
  const override = process.env.CODE_TUI_WORKER?.trim();
  if (override) return override.split(/\s+/);
  if (process.env.CODE_TUI_PYTHON) return [python, WORKER];
  for (const name of ["release", "debug"]) {
    const bin = join(resolve(ROOT, ".."), "rust", "target", name, "cact");
    if (existsSync(bin)) return [bin, "worker"];
  }
  return [python, WORKER];
})();

/** The workspace's checks, detected once at boot and refreshed per task start. */
export const initialChecks: CheckSpec[] = detectChecks(workspace);
/** Whether the API key was present at boot; `/health` re-reads the live value instead. */
export const initialKey = Boolean(process.env.DEEPSEEK_API_KEY);
/**
 * This process's own stdout/stderr file. launcher.ts redirects there, so the default is the path
 * it already uses; `--log` exists so a launcher that redirects elsewhere can say so in one place.
 */
export const serverLog = resolve(arg("--log", join(stateDir, "server.log")));
/** How often a read may re-hash the server's own source: two small files, but not per keystroke. */
export const SOURCE_RECHECK_MS = 2000;
