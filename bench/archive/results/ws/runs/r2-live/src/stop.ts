import { createHash } from "node:crypto";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import type { ServerInfo } from "./types";

const workspace = resolve(process.cwd());
const hash = createHash("sha256").update(workspace).digest("hex").slice(0, 16);
const stateRoot = process.env.CODE_TUI_STATE_ROOT
  ? resolve(process.env.CODE_TUI_STATE_ROOT)
  : join(process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state"), "code-tui");
const stateDir = join(stateRoot, "workspaces", hash);
const file = join(stateDir, "server.json");

if (!existsSync(file)) {
  console.log(`no server for ${workspace}`);
  process.exit(0);
}

const info = JSON.parse(readFileSync(file, "utf8")) as ServerInfo;
if (!info.pid) {
  console.log(`server.json for ${workspace} has no pid`);
  process.exit(1);
}

try {
  process.kill(info.pid, "SIGTERM");
  console.log(`stopped code-tui server pid ${info.pid} for ${workspace}`);
} catch (error) {
  console.log(`server pid ${info.pid} is not running; clearing server.json`);
  rmSync(file, { force: true });
  process.exit(0);
}
