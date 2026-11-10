/**
 * client-probe.ts: the read-only reads that decide which server address the screen trusts.
 *
 * WHAT IT IS
 *  Three helpers over one workspace: one `/health` read, the port the state dir publishes now,
 *  and whether two reads agree on the bits the board draws. No React, no state, no socket.
 *
 * ENTRY POINTS
 *  probeServer(url, workspace)                 one /health read -> ServerInfo | null
 *  publishedUrl(stateDir, workspace, current)  the port server.json publishes, when it is new
 *  sameServerRead(current, fresh)              do two reads agree on the drawn fields?
 *
 * INVARIANTS
 *  - Never throws: a down, slow, unparseable or foreign server is `null`, not an exception.
 *  - A read is trusted only when its `workspace` resolves to ours (a recycled port is not us).
 *  - `publishedUrl` answers only with a *different* URL; the current one is `null`.
 *
 * WHERE TO CHANGE
 *  Timeout and adoption rule .... PROBE_TIMEOUT_MS, probeServer, publishedUrl
 *  What counts as an unchanged read .... sameServerRead
 */
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { ServerInfo } from "./types";

/** How long one `/health` read may take before the server counts as down. */
const PROBE_TIMEOUT_MS = 1200;

/** One `/health` read: the server's own view of itself, or null when it is down or foreign. */
export async function probeServer(url: string, workspace: string): Promise<ServerInfo | null> {
  try {
    const response = await fetch(`${url}/health`, {
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    if (!response.ok) return null;
    const info = (await response.json()) as ServerInfo;
    if (!info || !info.port) return null;
    return resolve(info.workspace ?? "") === resolve(workspace) ? info : null;
  } catch {
    return null;
  }
}

/** The URL the state dir publishes now, when it differs from the one already in use. */
export function publishedUrl(
  stateDir: string | null,
  workspace: string,
  current: string,
): string | null {
  if (!stateDir) return null;
  try {
    const info = JSON.parse(readFileSync(join(stateDir, "server.json"), "utf8")) as ServerInfo;
    if (!info || !info.port) return null;
    if (info.workspace && resolve(info.workspace) !== resolve(workspace)) return null;
    const url = `http://127.0.0.1:${info.port}`;
    return url === current ? null : url;
  } catch {
    return null;
  }
}

/** Do two server reads agree on the bits the screen draws? A health probe refreshes only these. */
export function sameServerRead(current: ServerInfo | null, fresh: ServerInfo): boolean {
  if (!current) return false;
  return (
    current.port === fresh.port &&
    current.pid === fresh.pid &&
    current.model === fresh.model &&
    current.effort === fresh.effort &&
    current.hasKey === fresh.hasKey &&
    current.stale === fresh.stale &&
    (current.changed ?? []).join(",") === (fresh.changed ?? []).join(",")
  );
}
