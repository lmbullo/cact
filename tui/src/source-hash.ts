/**
 * source-hash.ts: is the running server the code the tree holds?
 *
 * WHAT IT IS
 *  The fingerprint of the files the detached server imports once at boot
 *  (`src/server.ts`, `src/worker-events.ts`, `src/detect-checks.ts`) and the comparison a later
 *  read makes against it. `client.tsx` and the rest are re-imported by every `code` launch, and
 *  `bench/**` plus `python/worker.py` by every task's worker, so only these can go stale under a
 *  live pid.
 *
 * ENTRY POINTS
 *  SOURCE_FILES                  the files a running server cannot re-read: server.ts,
 *                                worker-events.ts, detect-checks.ts
 *  bootSource(root, files, now)  boot: {source: {hash, files, hashedAt}, hashes}
 *  compareSource(root, boot)     read time: {stale, changed}; a missing file counts as changed
 *  fileHash(root, file)          one file's sha256, or "missing"
 *  combineHash(files, hashes)    the one digest that stands for the whole set
 *
 * INVARIANTS
 *  - Three files, ~15KB: the read-time comparison is cheap enough to run on a `/health`, but the
 *    caller still throttles it (server.ts: at most once every 2s).
 *  - Paths are relative to `root` (the `tui/` directory) and are exactly what `changed` reports,
 *    so the board can name the file without a second lookup.
 *  - An unreadable or absent file hashes to the same literal ("missing") both times, so a file
 *    that was there at boot and is gone now is `changed`, and one that was never there is not.
 *  - `hashes` is the per-file record `changed` needs; `hash` is only the combined digest that
 *    `/health` publishes. Do not try to derive one from the other.
 *  - Pure reads: nothing is written, nothing is cached here (the throttle lives in server.ts).
 *
 * WHERE TO CHANGE
 *  What the server's own source is ..... SOURCE_FILES
 *  How a file is digested .............. fileHash / combineHash
 *  What counts as stale ................ compareSource
 *  When the comparison runs ............. server.ts (sourceStatus, SOURCE_RECHECK_MS)
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { SourceFingerprint } from "./types";

/** The files the detached server imported at boot and will never re-import by itself. */
export const SOURCE_FILES: string[] = ["src/server.ts", "src/worker-events.ts", "src/detect-checks.ts"];

/** The literal both sides use for a file that could not be read, so equality stays meaningful. */
const MISSING = "missing";

/** One file's content digest (relative to `root`), or `"missing"` when it cannot be read. */
export function fileHash(root: string, file: string): string {
  try {
    return createHash("sha256").update(readFileSync(join(root, file))).digest("hex");
  } catch {
    return MISSING;
  }
}

/** The per-file digests of `files`, in the given order. */
export function fileHashes(root: string, files: readonly string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const file of files) out[file] = fileHash(root, file);
  return out;
}

/** One digest that stands for the whole ordered set: the `source.hash` of ServerInfo. */
export function combineHash(files: readonly string[], hashes: Record<string, string>): string {
  const hash = createHash("sha256");
  for (const file of files) hash.update(`${file}:${hashes[file] ?? MISSING}\n`);
  return hash.digest("hex").slice(0, 16);
}

/** Boot: the stamp published on `/health` plus the per-file hashes a later read compares to. */
export function bootSource(
  root: string,
  files: readonly string[] = SOURCE_FILES,
  now: number = Date.now(),
): { source: SourceFingerprint; hashes: Record<string, string> } {
  const hashes = fileHashes(root, files);
  return {
    source: { hash: combineHash(files, hashes), files: [...files], hashedAt: now },
    hashes,
  };
}

/** Read time: which of the boot files no longer match, so `stale` is exactly `changed.length > 0`. */
export function compareSource(
  root: string,
  boot: { source: SourceFingerprint; hashes: Record<string, string> },
): { stale: boolean; changed: string[] } {
  const changed = boot.source.files.filter((file) => fileHash(root, file) !== boot.hashes[file]);
  return { stale: changed.length > 0, changed };
}
