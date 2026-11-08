/**
 * Stale server: the running process, the code on disk, and the one word that says they differ.
 *
 * WHAT IT IS
 *  Two halves of the same signal. `bootSource`/`compareSource` are exercised against a temporary
 *  copy of the two files a detached server imports (`src/server.ts`, `src/detect-checks.ts`), so
 *  the fingerprint can be flipped without touching the real tree; `TopBar` is rendered at 60 and
 *  80 columns to prove the `stale` word joins the masthead's right-hand bits without dropping
 *  `offline` and without turning one row into two.
 *
 * ENTRY POINTS
 *  bootSource / compareSource / SOURCE_FILES from ../src/source-hash; TopBar from ../src/board-view
 *  testRender at 60x1 and 80x1; assertions scan the captured frame
 *
 * INVARIANTS
 *  - The temp copy is the only thing written; the checked-in sources are read, never edited.
 *  - A file that appeared at boot and is gone now is `changed`, not "equal": the missing literal
 *    is compared like any other digest.
 *  - The masthead invariant is the row count: a stale process may not push the brand or the
 *    status onto a second line at either width, and `offline` is never sacrificed for `stale`.
 *  - No timers and no server: the fingerprint is a pure function of the files, the bar of props.
 *
 * WHERE TO CHANGE
 *  The files a server fingerprints .... src/source-hash.ts (SOURCE_FILES)
 *  What the masthead says ............. src/board-view.tsx (TopBar)
 *  Where `stale` comes from ........... src/server.ts (sourceStatus), src/types.ts (ServerInfo)
 */
import { afterAll, expect, test } from "bun:test";
import { appendFileSync, copyFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { testRender } from "@opentui/react/test-utils";
import { SOURCE_FILES, bootSource, compareSource } from "../src/source-hash";
import { TopBar } from "../src/board-view";

const TUI = resolve(import.meta.dir, "..");
const sandboxes: string[] = [];

/** A temp workspace holding a byte-identical copy of the server's own source. */
function sandbox(): string {
  const root = mkdtempSync(join(tmpdir(), "code-tui-source-"));
  sandboxes.push(root);
  mkdirSync(join(root, "src"), { recursive: true });
  for (const file of SOURCE_FILES) copyFileSync(join(TUI, file), join(root, file));
  return root;
}

afterAll(() => {
  for (const root of sandboxes) rmSync(root, { recursive: true, force: true });
});

test("an untouched tree is fresh, an appended file is stale, a restore is fresh again", () => {
  const root = sandbox();
  const boot = bootSource(root, SOURCE_FILES, 1000);
  expect(boot.source.files).toEqual(SOURCE_FILES);
  expect(boot.source.hashedAt).toBe(1000);
  expect(boot.source.hash).toMatch(/^[0-9a-f]{16}$/);
  // Equality at boot is the zero case: the process is running exactly what is on disk.
  expect(compareSource(root, boot)).toEqual({ stale: false, changed: [] });

  // One appended line under a live pid is the whole staleness signal, and it names the file.
  appendFileSync(join(root, "src", "server.ts"), "\n// edited after the server booted\n");
  expect(compareSource(root, boot)).toEqual({ stale: true, changed: ["src/server.ts"] });

  // Restoring the byte-identical content clears it: the flag is content, never an mtime.
  copyFileSync(join(TUI, "src", "server.ts"), join(root, "src", "server.ts"));
  expect(compareSource(root, boot)).toEqual({ stale: false, changed: [] });

  // A file that vanished counts as changed, like any other differing content.
  rmSync(join(root, "src", "detect-checks.ts"));
  expect(compareSource(root, boot)).toEqual({ stale: true, changed: ["src/detect-checks.ts"] });
});

test("a second boot hashes the edited tree, so the digest is the identity of the code", () => {
  const root = sandbox();
  const first = bootSource(root);
  appendFileSync(join(root, "src", "detect-checks.ts"), "\n// edit\n");
  const second = bootSource(root);
  expect(second.source.hash).not.toBe(first.source.hash);
  // A server restarted on the edited tree is fresh: the fingerprint follows the boot, not history.
  expect(compareSource(root, second).stale).toBe(false);
});

/** Render a TopBar at one width and return its rows; the bar is one row of a two-row frame. */
async function rowsAt(width: number, props: { stale: boolean; connected?: boolean }) {
  const setup = await testRender(
    <TopBar
      workspace="/tmp/ws"
      model="deepseek-flash"
      effort="low"
      running={1}
      total={3}
      connected={props.connected ?? false}
      stale={props.stale}
      width={width}
    />,
    { width, height: 2 },
  );
  await setup.flush();
  const frame = setup.captureCharFrame();
  await setup.dispose();
  return frame.split("\n");
}

test("the masthead shows stale beside offline and still fits one row", async () => {
  for (const width of [60, 80]) {
    const rows = await rowsAt(width, { stale: true });
    // A second non-empty row is a wrapped masthead: the top bar is always exactly one line.
    const filled = rows.filter((row) => row.trim().length > 0);
    expect(filled.length).toBe(1);
    const line = filled[0]!;
    // Both words survive, in the documented order: offline first, stale last.
    expect(line).toContain("1 running · offline · stale");
    expect(line.indexOf("offline")).toBeLessThan(line.indexOf("stale"));
    expect(line.trimEnd().length).toBeLessThanOrEqual(width);
  }

  // Without staleness the word is simply absent — no place-holder, no second row.
  const fresh = await rowsAt(80, { stale: false });
  expect(fresh.filter((row) => row.includes("stale")).length).toBe(0);
  expect(fresh.join("\n")).toContain("offline");
});
