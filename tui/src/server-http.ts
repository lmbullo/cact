/**
 * server-http.ts: the HTTP and WebSocket surface — routes, JSON shape and the socket upgrade.
 *
 * WHAT IT IS
 *  One `route(req, srv)` function: GET / (plain text), /health (ServerInfo + live key state),
 *  /api/snapshot, /api/tasks/:id[/events|cancel], POST /api/tasks and the /ws upgrade. Every
 *  JSON answer is built by `json`, so the content type and no-store cache rule cannot drift.
 *
 * ENTRY POINTS
 *  json(data, status=200) -> Response   one JSON answer with the server's headers
 *  route(req, srv) -> Response | Promise<Response> | undefined   the route table; undefined only
 *  after an upgrade
 *
 * INVARIANTS
 *  - Read-only except POST /api/tasks (queues) and POST /api/tasks/:id/cancel (signals).
 *  - A missing task is 404 with `{error}`, never an empty 200; an unknown path is 404 too.
 *  - The prompt is trimmed and bounded (20000 chars) before a task is created.
 *  - `/health` re-reads DEEPSEEK_API_KEY and the staleness hash on every call, so the client's
 *    probe sees the live state, not the boot value.
 *
 * WHERE TO CHANGE
 *  One route ................. the if-chain in route
 *  The JSON contract ......... json, /api/tasks shapes
 *  The plain-text front page . the `/` branch
 */
import { randomUUID } from "node:crypto";
import {
  VERSION,
  effort,
  model,
  stateDir,
  workspace,
} from "./server-config";
import { readTaskEvents } from "./server-journal";
import { info, snapshot, sourceStatus, taskById } from "./server-state";
import { cancelTask, createTask } from "./server-tasks";

/** One JSON answer with the server's content type and its no-store cache rule. */
export function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

/** The whole route table; it upgrades `/ws` and answers everything else with a Response. */
export function route(req: Request, srv: any): Response | Promise<Response> | undefined {
  const url = new URL(req.url);
  if (url.pathname === "/ws") {
    if (srv.upgrade(req, { data: { id: randomUUID() } })) return;
    return new Response("websocket upgrade failed", { status: 400 });
  }
  if (url.pathname === "/health") {
    return json({
      ok: true,
      ...info,
      ...sourceStatus(),
      hasKey: Boolean(process.env.DEEPSEEK_API_KEY),
    });
  }
  if (url.pathname === "/api/snapshot") return json(snapshot());
  if (url.pathname === "/api/tasks" && req.method === "POST") {
    return (async () => {
      try {
        const body = (await req.json()) as { prompt?: unknown };
        const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
        if (!prompt) return json({ error: "prompt is empty" }, 400);
        if (prompt.length > 20000) return json({ error: "prompt is too long (20000 char max)" }, 400);
        return json({ task: createTask(prompt) }, 201);
      } catch (error) {
        return json({ error: String(error) }, 400);
      }
    })();
  }
  const match = url.pathname.match(/^\/api\/tasks\/([^/]+)(?:\/(events|cancel))?$/);
  if (match) {
    const task = taskById.get(decodeURIComponent(match[1]!));
    if (!task) return json({ error: "task not found" }, 404);
    if (!match[2] && req.method === "GET") return json({ task });
    if (match[2] === "events" && req.method === "GET") {
      const limit = Number(url.searchParams.get("limit") ?? 600) || 600;
      return json({ events: readTaskEvents(task.id, limit) });
    }
    if (match[2] === "cancel" && req.method === "POST") {
      cancelTask(task);
      return json({ task });
    }
  }
  if (url.pathname === "/") {
    return new Response(
      [
        `code-tui ${VERSION}`,
        `workspace: ${workspace}`,
        `state:     ${stateDir}`,
        `port:      ${info.port}`,
        `model:     ${model} (${effort})`,
        `key:       ${process.env.DEEPSEEK_API_KEY ? "set" : "missing"}`,
      ].join("\n"),
      { headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  }
  return json({ error: "not found" }, 404);
}
