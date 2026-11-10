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
 *
 * WHERE TO CHANGE
 *  The environment read .... httpUrl/workspace in main
 *  The mount ................ createRoot/render/closed in main
 */
import { createCliRenderer } from "@opentui/core";
import { createRoot } from "@opentui/react";
import { App } from "./client";

/** Entry point used by `bin/code` (via the launcher) — not run when the module is imported. */
export async function main(): Promise<void> {
  const httpUrl = process.env.CODE_TUI_URL;
  const workspace = process.env.CODE_TUI_WORKSPACE ?? process.cwd();
  if (!httpUrl) {
    console.error("code-tui: CODE_TUI_URL is missing; launch through tui/bin/code");
    process.exit(1);
  }

  const renderer = await createCliRenderer({ exitOnCtrlC: false });
  const root = createRoot(renderer);
  root.render(<App httpUrl={httpUrl} workspace={workspace} />);
  await renderer.closed;
  renderer.destroy();
}
