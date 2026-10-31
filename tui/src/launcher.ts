import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import type { ServerInfo } from "./types";

const ROOT = resolve(import.meta.dir, "..");
const VERSION = "0.1.0";

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

function shq(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

async function health(port: number): Promise<ServerInfo | null> {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/health`, {
      signal: AbortSignal.timeout(700),
    });
    if (!response.ok) return null;
    const data = (await response.json()) as ServerInfo & { ok?: boolean };
    return data.workspace === WORKSPACE ? data : null;
  } catch {
    return null;
  }
}

async function existingServer(): Promise<ServerInfo | null> {
  if (!existsSync(SERVER_FILE)) return null;
  try {
    const info = JSON.parse(readFileSync(SERVER_FILE, "utf8")) as ServerInfo;
    if (!info.port || !info.workspace) return null;
    if (resolve(info.workspace) !== WORKSPACE) return null;
    return await health(info.port);
  } catch {
    return null;
  }
}

async function waitForServer(timeoutMs: number): Promise<ServerInfo | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const info = await existingServer();
    if (info) return info;
    await sleep(100);
  }
  return null;
}

function launchDetachedServer(logPath: string): void {
  const command = [
    "nohup",
    shq(process.execPath),
    shq(join(ROOT, "src", "server.ts")),
    "--workspace",
    shq(WORKSPACE),
    "--state",
    shq(STATE_DIR),
    ">>",
    shq(logPath),
    "2>&1",
    "</dev/null",
    "&",
  ].join(" ");
  const result = Bun.spawnSync({
    cmd: ["bash", "-lc", command],
    cwd: WORKSPACE,
    env: process.env,
    stdout: "pipe",
    stderr: "pipe",
  });
  if (result.exitCode !== 0) {
    const err = new TextDecoder().decode(result.stderr).trim();
    throw new Error(`could not start server: ${err || `exit ${result.exitCode}`}`);
  }
}

async function ensureServer(): Promise<ServerInfo> {
  let info = await existingServer();
  if (info) return info;

  mkdirSync(STATE_DIR, { recursive: true });
  const lock = join(STATE_DIR, "launch.lock");
  let locked = false;

  for (let attempt = 0; attempt < 2 && !locked; attempt++) {
    try {
      mkdirSync(lock);
      locked = true;
    } catch {
      // Another TUI is already starting the server. Wait for it first.
      info = await waitForServer(8000);
      if (info) return info;
      try {
        const age = Date.now() - statSync(lock).mtimeMs;
        if (age > 15000) rmSync(lock, { recursive: true, force: true });
      } catch {
        // lock disappeared
      }
    }
  }

  if (!locked) {
    throw new Error(`launch lock is busy: ${lock}`);
  }

  try {
    info = await existingServer();
    if (info) return info;
    const logPath = join(STATE_DIR, "server.log");
    launchDetachedServer(logPath);
    info = await waitForServer(12000);
    if (!info) {
      let tail = "";
      try {
        tail = readFileSync(logPath, "utf8").trim().split("\n").slice(-12).join("\n");
      } catch {
        // no log
      }
      throw new Error(`server did not become healthy. log: ${logPath}\n${tail}`);
    }
    return info;
  } finally {
    if (locked) rmSync(lock, { recursive: true, force: true });
  }
}

loadEnvFile();

const workspaceArg = process.argv.find((value) => value.startsWith("--workspace="));
const WORKSPACE = resolve(workspaceArg ? workspaceArg.slice("--workspace=".length) : process.cwd());
const workspaceHash = createHash("sha256").update(WORKSPACE).digest("hex").slice(0, 16);
const stateRoot = process.env.CODE_TUI_STATE_ROOT
  ? resolve(process.env.CODE_TUI_STATE_ROOT)
  : join(process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state"), "code-tui");
const STATE_DIR = resolve(join(stateRoot, "workspaces", workspaceHash));
const SERVER_FILE = join(STATE_DIR, "server.json");

process.chdir(WORKSPACE);
mkdirSync(STATE_DIR, { recursive: true });

try {
  const info = await ensureServer();
  if (!process.env.DEEPSEEK_API_KEY) {
    console.error("code-tui: DEEPSEEK_API_KEY is not set; tasks will fail until it is configured.");
    console.error(`code-tui: add it to ${join(homedir(), ".config", "code-tui", "env")} or export it.`);
  }
  process.env.CODE_TUI_URL = `http://127.0.0.1:${info.port}`;
  process.env.CODE_TUI_WORKSPACE = WORKSPACE;
  process.env.CODE_TUI_STATE = STATE_DIR;
  process.env.CODE_TUI_VERSION = VERSION;
  const client = await import("./client.tsx");
  await client.main();
} catch (error) {
  console.error(`code-tui: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
}
