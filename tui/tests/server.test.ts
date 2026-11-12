/**
 * server.test.ts: the real server process — queue, cancellation, restart and the machine log.
 *
 * WHAT IT IS
 *  Starts `src/server.ts` with CODE_TUI_WORKER at a fake shell worker and drives the HTTP API:
 *  one slot queues the rest, a queued cancel spawns nothing, a SIGKILLed server fails its runner
 *  and promotes the oldest queued task, and logs/<id>.jsonl stays one JSON record per line.
 *
 * ENTRY POINTS
 *  launch()      spawn src/server.ts; resolve when server.json names a port
 *  post()/get()  the HTTP API the client uses
 *  until()       poll GET /api/tasks/:id for a status
 *
 * INVARIANTS
 *  - The fake worker is a shell script keyed on the prompt file; no model, no key, no fixed port.
 *  - Every state root is a fresh tmpdir and every spawned worker pid is killed in afterAll.
 *
 * WHERE TO CHANGE
 *  queue, cancel, restart, machine log, task ids ... src/server.ts
 */
import { afterAll, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const SERVER = join(import.meta.dir, "..", "src", "server.ts");
const roots: string[] = [];
const procs: any[] = [];
const stateDirs: string[] = [];
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function temp(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  roots.push(dir);
  return dir;
}

/** A fake worker: the prompt text decides whether it sleeps, finishes or prints a long line. */
function fakeWorker(): string {
  const dir = temp("code-tui-fake-");
  const path = join(dir, "python3");
  writeFileSync(path, `#!/bin/sh
body=$(cat "$4")
case "$body" in
  *slow*) exec sleep 30 ;;
  *noise*)
    printf '%s\\n' "plain stdout from the worker"
    printf '%s\\n' '@@TUI@@{"kind":"final","status":"done","final":"finished","stop":"DONE","steps":1,"calls":0,"llmCalls":1,"errors":0,"usage":{},"wallMs":5}'
    ;;
  *long*)
    head -c 13000 /dev/zero | tr '\\0' 'x'
    printf '\\n'
    printf '%s\\n' '@@TUI@@{"kind":"final","status":"done","final":"long","stop":"DONE","steps":1,"calls":0,"llmCalls":1,"errors":0,"usage":{},"wallMs":5}'
    ;;
  *structured*)
    printf '%s\\n' '@@TUI@@{"kind":"final","status":"done","final":{"summary":"structured report"},"stop":"DONE","steps":1,"calls":0,"llmCalls":1,"errors":0,"usage":{},"wallMs":5}'
    ;;
  *) exit 0 ;;
esac
`);
  chmodSync(path, 0o755);
  return path;
}
type Server = { proc: any; base: string; state: string; stateRoot: string };

/** Spawn the real server and wait for the server.json it writes after listening. */
async function launch(workspace: string, stateRoot: string, worker: string): Promise<Server> {
  const state = join(stateRoot, "state");
  stateDirs.push(state);
  const proc = Bun.spawn({
    cmd: ["bun", SERVER, "--workspace", workspace, "--state", state,
          "--log", join(state, "server.log")],
    cwd: join(import.meta.dir, ".."),
    env: {
      ...process.env,
      CODE_TUI_STATE_ROOT: stateRoot,
      CODE_TUI_WORKER: worker,
      CODE_TUI_CONCURRENCY: "1",
      CODE_TUI_CHECK: "",
      DEEPSEEK_API_KEY: "",
    },
    stdout: "pipe",
    stderr: "pipe",
  });
  procs.push(proc);
  const file = join(state, "server.json");
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (existsSync(file)) {
      try {
        const info = JSON.parse(readFileSync(file, "utf8"));
        if (info.port) return { proc, base: `http://127.0.0.1:${info.port}`, state, stateRoot };
      } catch {
        // the file is being replaced
      }
    }
    if (proc.exitCode !== null) throw new Error(`server exited early: ${proc.exitCode}`);
    await sleep(50);
  }
  throw new Error("server never wrote server.json");
}

async function get(server: Server, path: string): Promise<any> {
  return (await fetch(server.base + path)).json();
}

async function post(server: Server, path: string, body?: unknown): Promise<any> {
  const response = await fetch(server.base + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: response.status, body: await response.json() };
}

async function until(server: Server, id: string, status: string): Promise<any> {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const { task } = await get(server, `/api/tasks/${id}`);
    if (task.status === status) return task;
    await sleep(40);
  }
  const { task } = await get(server, `/api/tasks/${id}`);
  throw new Error(`task ${id} stayed ${task.status}, wanted ${status}`);
}

/** Kill every worker pid the servers recorded, so a slow fake never outlives the suite. */
function reap(): void {
  for (const state of stateDirs) {
    const tasks = join(state, "tasks");
    if (!existsSync(tasks)) continue;
    for (const file of readdirSync(tasks)) {
      try {
        const task = JSON.parse(readFileSync(join(tasks, file), "utf8"));
        if (task.pid) process.kill(task.pid, "SIGKILL");
      } catch {
        // already gone
      }
    }
  }
}

afterAll(() => {
  reap();
  for (const proc of procs) {
    try {
      proc.kill("SIGKILL");
    } catch {
      // already gone
    }
  }
  for (const root of roots) rmSync(root, { recursive: true, force: true });
});

test("one slot queues the later tasks, cancels a queued one, and a restart fails the runner", async () => {
  const workspace = temp("code-tui-ws-");
  const stateRoot = temp("code-tui-state-");
  const server = await launch(workspace, stateRoot, fakeWorker());

  const first = (await post(server, "/api/tasks", { prompt: "slow first" })).body.task;
  const second = (await post(server, "/api/tasks", { prompt: "slow second" })).body.task;
  const third = (await post(server, "/api/tasks", { prompt: "slow third" })).body.task;
  expect(first.status).toBe("running");
  expect(second.status).toBe("queued");
  expect(third.status).toBe("queued");
  // Real ids: base36 milliseconds between the t- prefix and a five-hex suffix.
  const id = /^t-([0-9a-z]+)-([0-9a-f]{5})$/.exec(first.id);
  expect(id).not.toBeNull();
  const created = parseInt(id![1]!, 36);
  expect(Math.abs(created - Date.now())).toBeLessThan(60000);
  expect(parseInt(/^t-([0-9a-z]+)-/.exec(third.id)![1]!, 36)).toBeGreaterThanOrEqual(created);
  expect(existsSync(join(server.state, "tasks", `${first.id}.json`))).toBe(true);

  const cancelled = (await post(server, `/api/tasks/${second.id}/cancel`)).body.task;
  expect(cancelled.status).toBe("cancelled");
  expect(typeof cancelled.finishedAt).toBe("number");
  const snapshot = await get(server, "/api/snapshot");
  const byId = Object.fromEntries(snapshot.tasks.map((task: any) => [task.id, task.status]));
  expect(byId[first.id]).toBe("running");
  expect(byId[second.id]).toBe("cancelled");
  expect(byId[third.id]).toBe("queued");
  // A queued cancel never spawns a worker: no pid is recorded for it.
  expect(existsSync(join(server.state, "prompts", `${second.id}.txt`))).toBe(true);
  expect(JSON.parse(readFileSync(join(server.state, "tasks", `${second.id}.json`), "utf8")).pid).toBeUndefined();

  const runner = JSON.parse(readFileSync(join(server.state, "tasks", `${first.id}.json`), "utf8"));
  expect(runner.pid).toBeGreaterThan(0);
  server.proc.kill("SIGKILL");
  await server.proc.exited;
  try {
    process.kill(runner.pid, "SIGKILL");
  } catch {
    // already gone
  }
  rmSync(join(server.state, "server.json"), { force: true });

  const again = await launch(workspace, stateRoot, fakeWorker());
  const restarted = await until(again, first.id, "failed");
  expect(restarted.error).toBe("server restarted while this task was running");
  expect(existsSync(join(again.state, "events", `${first.id}.jsonl`))).toBe(true);
  const events = (await get(again, `/api/tasks/${first.id}/events`)).events;
  expect(events.some((event: any) => event.kind === "error" && /restarted/.test(event.text))).toBe(true);
  expect(events.some((event: any) => event.kind === "finished" && event.text === "failed")).toBe(true);
  // The oldest queued task is the one the new server's pump starts; the cancelled one stays out.
  const promoted = await until(again, third.id, "running");
  expect(promoted.pid).toBeGreaterThan(0);
  expect((await get(again, `/api/tasks/${second.id}`)).task.status).toBe("cancelled");
}, 20000);

test("the machine log is JSONL, append-only and truncates a long stdout line", async () => {
  const workspace = temp("code-tui-ws-");
  const stateRoot = temp("code-tui-state-");
  const server = await launch(workspace, stateRoot, fakeWorker());

  const noisy = (await post(server, "/api/tasks", { prompt: "noise" })).body.task;
  const done = await until(server, noisy.id, "done");
  const logPath = done.log;
  expect(logPath).toBe(join(server.state, "logs", `${noisy.id}.jsonl`));
  const lines = readFileSync(logPath, "utf8").split("\n").filter(Boolean);
  const records = lines.map((line) => JSON.parse(line));
  expect(records.length).toBe(lines.length);
  expect(records.every((record: any) => typeof record.ts === "number")).toBe(true);
  expect(records[0].kind).toBe("prompt");
  expect(records[0].text).toBe("noise");
  const kinds = new Set(records.map((record: any) => record.kind));
  expect(kinds).toContain("event");
  expect(kinds).toContain("stdout");
  expect(kinds).toContain("usage");
  const journalKinds = records.filter((record: any) => record.kind === "event")
    .map((record: any) => record.event.kind);
  for (const kind of ["queued", "started", "pid", "final", "finished"]) expect(journalKinds).toContain(kind);
  const raw = records.find((record: any) => record.kind === "stdout" && record.text === "plain stdout from the worker");
  expect(raw).toBeDefined();
  const unparsed = records.find((record: any) => record.kind === "event" && record.event.kind === "log");
  expect(unparsed.event.text).toBe("plain stdout from the worker");
  const usage = records.find((record: any) => record.kind === "usage");
  expect(usage.status).toBe("done");
  expect(usage.stop).toBe("DONE");
  expect(usage.steps).toBe(1);

  const long = (await post(server, "/api/tasks", { prompt: "long line" })).body.task;
  const longDone = await until(server, long.id, "done");
  const longRecords = readFileSync(longDone.log, "utf8").split("\n").filter(Boolean)
    .map((line) => JSON.parse(line));
  const truncated = longRecords.find((record: any) => record.kind === "stdout");
  expect(truncated.text.startsWith("x".repeat(100))).toBe(true);
  expect(truncated.text.length).toBe(12000 + "\n… truncated".length);
  expect(truncated.text.endsWith("… truncated")).toBe(true);
  const fallback = longRecords.find((record: any) => record.kind === "event" && record.event.kind === "log");
  expect(fallback.event.text.endsWith("… truncated")).toBe(true);
  expect(fallback.event.text.length).toBe(3000 + "\n… truncated".length);
}, 20000);


test("a structured final_answer is stored as text, not an object", async () => {
  const workspace = temp("code-tui-ws-");
  const stateRoot = temp("code-tui-state-");
  const server = await launch(workspace, stateRoot, fakeWorker());

  const task = (await post(server, "/api/tasks", { prompt: "structured" })).body.task;
  const done = await until(server, task.id, "done");
  expect(typeof done.final).toBe("string");
  expect(done.final).toContain('"summary": "structured report"');
  const events = (await get(server, `/api/tasks/${done.id}/events`)).events;
  const final = events.find((event: any) => event.kind === "final");
  expect(typeof final.text).toBe("string");
  expect(final.text).toContain("structured report");
}, 20000);
