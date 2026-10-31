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

