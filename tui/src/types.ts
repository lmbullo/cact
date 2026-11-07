/**
 * The wire contract shared by server.ts, client.tsx, launcher.ts and stop.ts.
 *
 * WHAT IT IS
 *  Types only. `Task` is one queued/running/finished agent task (the board row and the
 *  detail view); `TaskEvent` is one line of events/<id>.jsonl; `ServerInfo` is server.json
 *  and GET /health; `CheckSpec` is one entry of the verification plan;
 *  `Snapshot`/`WsMessage` are the WebSocket messages; `WorkerMessage` is the `@@TUI@@`
 *  payload emitted by python/worker.py.
 *
 * ENTRY POINTS
 *  Task, TaskStatus, TaskEvent, ServerInfo, CheckSpec, Snapshot, WsMessage, WorkerMessage
 *
 * INVARIANTS
 *  - Types only: no runtime code, nothing to import at runtime. Changing a field here means
 *    changing server.ts (producer) and client.tsx (consumer) in the same commit.
 *  - `TaskStatus` is the closed vocabulary the board colours and sections by; the mapping
 *    from stop reason to status lives in tui/python/worker.py, not here.
 *  - `Task.progress` is the live estimate from turn events; `steps`/`usage` are the final
 *    counters, and `context`/`window` keep the last turn's prompt size and the model's window.
 *    The client derives every counter from them in list-model.taskUsage.
 *  - `WorkerMessage` mirrors the `emit({...})` payloads in python/worker.py; that file is the
 *    source of truth for the message kinds.
 *  - `CheckSpec` mirrors `bench.agent.check_plan.CheckSpec`: {name, cmd, cheap, scope?}, where
 *    `scope` is a list of path prefixes. It is parsed in detect-checks.ts from
 *    CODE_TUI_CHECKS / CODE_TUI_CHECK / package.json.
 *
 * WHERE TO CHANGE
 *  The task shape ................. Task / TaskStatus
 *  The journal line shape ......... TaskEvent
 *  The detection plan ............ CheckSpec; detectChecks in detect-checks.ts
 *  A worker message .............. WorkerMessage + handleWorkerLine in server.ts
 */
export type TaskStatus = "queued" | "running" | "done" | "partial" | "failed" | "cancelled";

/** One queued/running/finished task as tasks/<id>.json stores it; the board reads only this. */
export type Task = {
  id: string;
  prompt: string;
  status: TaskStatus;
  createdAt: number;
  startedAt?: number;
  finishedAt?: number;
  pid?: number;
  exitCode?: number;
  stop?: string;
  final?: string;
  error?: string;
  steps?: number;
  /**
   * Live counters derived from turn events while a task runs (server.ts), so the board can
   * show progress before the final event replaces them. `tokensIn`/`tokensOut` are the
   * provider's prompt and completion tokens summed over turns, `cacheHit`/`cacheMiss` its
   * prompt cache split, `context` the latest turn's prompt size and `window` the model's
   * context window.
   */
  progress?: {
    steps?: number;
    tokensIn?: number;
    tokensOut?: number;
    cacheHit?: number;
    cacheMiss?: number;
    context?: number;
    window?: number;
  };
  /** The last turn's prompt size and the context window it had to fit in, kept after `progress`. */
  context?: number;
  window?: number;
  calls?: number;
  llmCalls?: number;
  errors?: number;
  wallMs?: number;
  usage?: Record<string, number>;
  model?: string;
  effort?: string;
  cancelRequested?: boolean;
};

/** One line of events/<id>.jsonl: what the log view renders, in `seq` order. */
export type TaskEvent = {
  seq: number;
  ts: number;
  kind: string;
  turn?: number;
  cell?: number;
  text?: string;
  code?: string;
  tool?: string;
  args?: Record<string, unknown>;
  data?: Record<string, unknown>;
};

/** server.json: how to reach this workspace's server and what it was started with. */
export type ServerInfo = {
  workspace: string;
  stateDir: string;
  pid: number;
  port: number;
  version: string;
  startedAt: number;
  model: string;
  effort: string;
  concurrency: number;
  allowRun: boolean;
  hasKey: boolean;
  python: string;
  check: string;
  checks: CheckSpec[];
};

/** One entry of the verification plan; mirrors bench.agent.check_plan.CheckSpec. */
export type CheckSpec = {
  name: string;
  cmd: string;
  cheap: boolean;
  scope?: string[];
};

/** The first WebSocket message: the whole board, so a reconnect needs no replay. */
export type Snapshot = {
  type: "snapshot";
  server: ServerInfo;
  tasks: Task[];
};

/** Everything the server pushes over the WebSocket: a snapshot, then task/event deltas. */
export type WsMessage =
  | Snapshot
  | { type: "task"; task: Task }
  | { type: "event"; taskId: string; event: TaskEvent }
  | { type: "ping"; ts: number };

/** One `@@TUI@@` line from python/worker.py; server.ts is the only reader. */
export type WorkerMessage =
  | { kind: "cell.start"; cell: number; code?: string; tool?: string; args?: Record<string, unknown> }
  | { kind: "turn"; turn: number; data: Record<string, unknown> }
  | { kind: "final"; status: "done" | "partial" | "failed"; final?: string; stop?: string; usage?: Record<string, number>; steps?: number; calls?: number; llmCalls?: number; errors?: number; wallMs?: number }
  // A `turn`'s `data` carries {usage, cache, window, ...}; see python/worker.py EventTrace.
  | { kind: "error"; text: string }
  | { kind: "log"; text: string };