/**
 * The wire contract shared by server.ts, client.tsx, launcher.ts and stop.ts.
 *
 * WHAT IT IS
 *  Types only. `Task` is one queued/running/finished agent task (the board row and the
 *  detail view); `TaskEvent` is one line of events/<id>.jsonl; `CheckState` is the verdict a
 *  `check` event carries; `ServerInfo` is server.json and GET /health; `CheckSpec` is one entry
 *  of the verification plan;
 *  `Snapshot`/`WsMessage` are the WebSocket messages; `WorkerMessage` is the `@@TUI@@`
 *  payload emitted by python/worker.py.
 *
 * ENTRY POINTS
 *  Task, TaskStatus, TaskEvent, CheckState, ServerInfo, SourceFingerprint, CheckSpec, Snapshot,
 *  WsMessage, WorkerMessage
 *
 * INVARIANTS
 *  - Types only: no runtime code, nothing to import at runtime. Changing a field here means
 *    changing server.ts (producer) and client.tsx (consumer) in the same commit.
 *  - `TaskStatus` is the closed vocabulary the board colours and sections by; the mapping
 *    from stop reason to status lives in tui/python/worker.py, not here.
 *  - `Task.progress` is the live estimate from turn events; `steps`/`usage` are the final
 *    counters, and `context`/`window` keep the last turn's prompt size and the model's window.
 *    The client derives every counter from them in list-model.taskUsage.
 *  - `Task.stop`/`Task.errors` are the final verdict and the error count; `CheckState` is the
 *    same verdict for one verification run, carried by a `check` event. The log header reads
 *    the three of them and nothing else about how a run ended.
 *  - `WorkerMessage` mirrors the `emit({...})` payloads in python/worker.py; that file is the
 *    source of truth for the message kinds.
 *  - `ServerInfo.source` is the boot fingerprint of the files the detached server imported
 *    (source-hash.ts); `stale`/`changed` are measured on each read, never stored, so a reader can
 *    tell a running process from the tree on disk.
 *  - `CheckSpec` mirrors `bench.agent.check_plan.CheckSpec`: {name, cmd, cheap, scope?}, where
 *    `scope` is a list of path prefixes. It is parsed in detect-checks.ts from
 *    CODE_TUI_CHECKS / CODE_TUI_CHECK / package.json.
 *
 * WHERE TO CHANGE
 *  The task shape ................. Task / TaskStatus
 *  The journal line shape ......... TaskEvent
 *  A verification verdict ......... CheckState
 *  The detection plan ............ CheckSpec; detectChecks in detect-checks.ts
 *  A worker message .............. WorkerMessage + journalWorkerEvent in worker-events.ts
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
  /** The model the server ran this task on, as `ServerInfo.model` had it; pricing.ts prices it. */
  model?: string;
  effort?: string;
  cancelRequested?: boolean;
};

/** One verification run as the journal records it: the verdict, the check's name and the first
 * failing line of its output (empty when it passed). Written by python/worker.py, journaled by
 * server.ts as a `check` event, read by board-view.LogHeader from the task's last one. */
export type CheckState = {
  ok: boolean;
  name: string;
  line?: string;
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
  /** cell.end: the compact per-call result record (journal history, not log output). */
  ok?: boolean;
  verb_kind?: string;
  page?: Record<string, unknown> | null;
  chars?: number;
  duration_ms?: number;
  notes?: string[];
  /** check: the verification verdict this event carries (see CheckState). */
  check?: CheckState;
};

/**
 * The server's own source at boot: the files the detached process imported once and the digest of
 * their contents. Only the server's own modules — `src/server.ts`, `src/worker-events.ts`,
 * `src/detect-checks.ts` — are in it; everything else is re-imported by a `code` launch or by a
 * task's worker, so it cannot be stale under a live pid.
 */
export type SourceFingerprint = {
  /** One digest over `files`, in order; the identity of the code this process is running. */
  hash: string;
  /** The relative paths the digest covers, so a reader can name the file that changed. */
  files: string[];
  /** When the boot fingerprint was taken (`Date.now()`). */
  hashedAt: number;
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
  /** The code this process booted with; see source-hash.ts. */
  source: SourceFingerprint;
  /**
   * Measured per read, never a boot value: the process's own files no longer match the tree, and
   * which of them. `/health`, `/api/snapshot` and the WebSocket snapshot carry them; a restart is
   * the only cure, so the board names `bin/code-stop` as the fix.
   */
  stale?: boolean;
  changed?: string[];
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
  | { kind: "cell.end"; cell: number; tool?: string; ok?: boolean; verb_kind?: string; code?: string; page?: Record<string, unknown> | null; chars?: number; duration_ms?: number; notes?: string[] }
  | { kind: "turn"; turn: number; data: Record<string, unknown> }
  // Emitted right after a turn whose record carried a verdict; the last one is the current state.
  | { kind: "check"; ok: boolean; name: string; line?: string }
  | { kind: "final"; status: "done" | "partial" | "failed"; final?: string; stop?: string; usage?: Record<string, number>; steps?: number; calls?: number; llmCalls?: number; errors?: number; wallMs?: number }
  // A `turn`'s `data` carries {usage, cache, window, ...}; see python/worker.py EventTrace.
  | { kind: "error"; text: string }
  | { kind: "log"; text: string };