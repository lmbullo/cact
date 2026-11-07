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
