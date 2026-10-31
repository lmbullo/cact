/**
 * The wire contract shared by server.ts, client.tsx, launcher.ts and stop.ts.
 *
 * Task          one queued/running/finished agent task (the board row and the detail view)
 * TaskEvent     one line of events/<id>.jsonl: {seq, ts, kind, text?, data?, ...}
 * ServerInfo    server.json and GET /health: workspace, port, pid, model, check, hasKey
 * Snapshot      WebSocket open message: {type:"snapshot", server, tasks}
 * WsMessage     server -> client: snapshot | task | event | ping
 * WorkerMessage server <- worker: the `@@TUI@@` payloads emitted by python/worker.py
 *
 * INVARIANTS
 *  - Types only: no runtime code, nothing to import at runtime. Changing a field here means
 *    changing server.ts (producer) and client.tsx (consumer) in the same commit.
 *  - `TaskStatus` is the closed vocabulary the board colours and sections by; the mapping
 *    from stop reason to status lives in tui/python/worker.py, not here.
 *  - `WorkerMessage` mirrors the `emit({...})` payloads in python/worker.py; that file is the
 *    source of truth for the message kinds.
 */
export type TaskStatus = "queued" | "running" | "done" | "partial" | "failed" | "cancelled";

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
  calls?: number;
  llmCalls?: number;
  errors?: number;
  wallMs?: number;
  usage?: Record<string, number>;
  model?: string;
  effort?: string;
  cancelRequested?: boolean;
};

export type TaskEvent = {
  seq: number;
  ts: number;
  kind: string;
  turn?: number;
  cell?: number;
  text?: string;
  code?: string;
  data?: Record<string, unknown>;
};

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
};

export type Snapshot = {
  type: "snapshot";
  server: ServerInfo;
  tasks: Task[];
};

export type WsMessage =
  | Snapshot
  | { type: "task"; task: Task }
  | { type: "event"; taskId: string; event: TaskEvent }
  | { type: "ping"; ts: number };

export type WorkerMessage =
  | { kind: "cell.start"; cell: number; code: string }
  | { kind: "turn"; turn: number; data: Record<string, unknown> }
  | { kind: "final"; status: "done" | "partial" | "failed"; final?: string; stop?: string; usage?: Record<string, number>; steps?: number; calls?: number; llmCalls?: number; errors?: number; wallMs?: number }
  | { kind: "error"; text: string }
  | { kind: "log"; text: string };
