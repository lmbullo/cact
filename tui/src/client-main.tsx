/**
 * client-main.tsx: the entry point `bin/code` runs — renderer, root, one App.
 *
 * WHAT IT IS
 *  `main()` reads the two environment variables the launcher sets, builds the CLI renderer and
 *  mounts `App`. It is re-exported from client.tsx, which is what `import.meta.main` runs.
 *
 * ENTRY POINTS
 *  main()  createCliRenderer + createRoot(<App/>); resolves when the renderer closes
 *
 * INVARIANTS
 *  - Not run on import: every module that imports it (client.tsx, tests) only gets the function.
 *  - CODE_TUI_URL is required; without it the process exits 1 before a renderer is created.
 *  - The renderer is destroyed exactly once, after `renderer.closed`.
 *  - An uncaught error is appended to `<state>/client.log`, the renderer is destroyed, then the
 *    process exits; a crash never leaves the terminal in raw mode or dies without a record.
 *  - An unhandled rejection is only logged: it is usually one failed request, and taking the whole
 *    board down for it is the crash this guard exists to prevent.
 *
 * WHERE TO CHANGE
 *  The environment read .... httpUrl/workspace in main
 *  The mount ................ createRoot/render/closed in main
 */
import { appendFileSync } from "node:fs";
import { join } from "node:path";
import { createCliRenderer, type CliRenderer } from "@opentui/core";
import { createRoot } from "@opentui/react";
import { App } from "./client";

/** Append one crash record to the state dir; the one file a post-mortem can read. */
function recordCrash(stateDir: string | undefined, kind: string, error: unknown): void {
  if (!stateDir) return;
  const detail = error instanceof Error ? error.stack ?? error.message : String(error);
  try {
    appendFileSync(join(stateDir, "client.log"), `${JSON.stringify({ ts: Date.now(), kind, detail })}\n`);
  } catch {
    // The crash path must never throw its own error.
  }
}

/** Entry point used by `bin/code` (via the launcher) — not run when the module is imported. */
export async function main(): Promise<void> {
  const httpUrl = process.env.CODE_TUI_URL;
  const workspace = process.env.CODE_TUI_WORKSPACE ?? process.cwd();
  const stateDir = process.env.CODE_TUI_STATE;
  if (!httpUrl) {
    console.error("code-tui: CODE_TUI_URL is missing; launch through tui/bin/code");
    process.exit(1);
  }

  let renderer: CliRenderer | null = null;
  const crash = (kind: string, error: unknown) => {
    recordCrash(stateDir, kind, error);
    try {
      renderer?.destroy();
    } catch {
      // Already gone; nothing left to restore.
    }
    process.exit(1);
  };
  process.once("uncaughtException", (error) => crash("uncaughtException", error));
  process.on("unhandledRejection", (error) => recordCrash(stateDir, "unhandledRejection", error));

  try {
    renderer = await createCliRenderer({ exitOnCtrlC: false });
    const root = createRoot(renderer);
    root.render(<App httpUrl={httpUrl} workspace={workspace} />);
    await renderer.closed;
  } catch (error) {
    crash("main", error);
  } finally {
    renderer?.destroy();
  }
}
