/**
 * `bin/code-stop`: SIGTERM this workspace's server and clear its server.json.
 *
 * WHAT IT IS
 *  The stop half of the launcher: resolve the workspace's state dir, read server.json, kill
 *  the pid, remove the file.
 *
 * ENTRY POINTS
 *  (no exports) — side-effect entry point, imported by tui/bin/code-stop.
 *
 * INVARIANTS
 *  - Resolves the same state dir as launcher.ts (CODE_TUI_STATE_ROOT, else
 *    $XDG_STATE_HOME/code-tui, then workspaces/<sha256(cwd)[:16]>/). Change it in all three
 *    files or the pieces stop finding each other.
 *  - A missing file or dead pid exits 0: stopping twice is fine.
 *
 * WHERE TO CHANGE
 *  The state-dir formula ... here, launcher.ts and server.ts together
 *  The kill signal .......... stop.ts (SIGTERM, then the server shuts down)
 */
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
