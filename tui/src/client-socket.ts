/**
 * client-socket.ts: the screen's one server link — probe, socket, snapshot and watchdog.
 *
 * WHAT IT IS
 *  The `useServerLink` hook: it owns the board's server copy (`tasks`, `server`, `connected`,
 *  `baseUrl`, `socketEpoch`) and the two effects that keep it fresh. Nothing is drawn here.
 *
 * ENTRY POINTS
 *  useServerLink(options) -> { tasks, server, connected, baseUrl, socketEpoch, bumpSocketEpoch }
 *
 * INVARIANTS
 *  - A socket close is not the only way to lose a server, so every attempt probes first: the
 *    same URL while it answers, otherwise the port `server.json` publishes now.
 *  - A different healthy URL moves `baseUrl`, which tears the effect down and re-opens there.
 *  - `baseUrl` is the only address used: socket, event fetch, queue and cancel all read it.
 *  - Nothing here starts or stops a server; the screen only ever reads one.
 *
 * WHERE TO CHANGE
 *  Socket loop and reconnect ..... the first effect; RETRY_MS
 *  Half-open socket watchdog ..... the second effect; WATCHDOG_MS
 *  Open log's event backfill ..... client-events.ts   Wire shapes ..... types.ts
 */
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { ServerInfo, Snapshot, Task, TaskEvent, WsMessage } from "./types";
import { probeServer, publishedUrl, sameServerRead } from "./client-probe";

/** Delay between reconnect attempts; every miss also re-reads `server.json`. */
const RETRY_MS = 1000;
/** How often a screen that thinks it is connected re-probes, so a half-open socket cannot stick. */
const WATCHDOG_MS = 5000;

/** What `useServerLink` needs from the board: the address it starts at and its shapers. */
export interface ServerLinkOptions {
  /** The launcher's URL for this workspace (CODE_TUI_URL). */
  httpUrl: string;
  /** Absolute workspace path a probed server must match before it is adopted. */
  workspace: string;
  /** The launcher's state dir (CODE_TUI_STATE); `server.json` is how a restart is found. */
  stateDir: string | null;
  /** Toast writer, used for `server is back`. */
  notify: (message: string) => void;
  /** Board selection setter; a snapshot that loses the open task falls back to the list. */
  selectTask: (id: string | null) => void;
  /** View setter, so a vanished open task returns the screen to the list. */
  setView: Dispatch<SetStateAction<"list" | "log">>;
  /** Readable current view, for a socket callback that must not become an effect dep. */
  viewRef: { current: "list" | "log" };
  /** Readable current selection, same reason. */
  selectedRef: { current: string | null };
  /** Highest event `seq` already held, so a replayed event is dropped. */
  eventsSeqRef: { current: number };
  /** Event list setter: the socket appends to the open log's buffer (capped at 1000). */
  setEvents: Dispatch<SetStateAction<TaskEvent[]>>;
}

/** What `useServerLink` hands the board: the last server read and the address it came from. */
export interface ServerLink {
  /** Every task the last snapshot listed, live-updated by `task` messages. */
  tasks: Task[];
  /** The server's own description of itself, refreshed by snapshots and the watchdog. */
  server: ServerInfo | null;
  /** True while a socket is open; false after a close until one re-opens. */
  connected: boolean;
  /** Where the server answers now: the launcher's URL until a restart publishes another port. */
  baseUrl: string;
  /** Bumped to force the connection effect to tear down and try again (Ctrl-R, watchdog). */
  socketEpoch: number;
  /** Force one reconnect attempt without waiting for the retry timer. */
  bumpSocketEpoch: () => void;
}

/** Own the socket, the snapshot it feeds and the watchdog; the board only reads the result. */
export function useServerLink({
  httpUrl,
  workspace,
  stateDir,
  notify,
  selectTask,
  setView,
  viewRef,
  selectedRef,
  eventsSeqRef,
  setEvents,
}: ServerLinkOptions): ServerLink {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [server, setServer] = useState<ServerInfo | null>(null);
  const [connected, setConnected] = useState(false);
  const [baseUrl, setBaseUrl] = useState(httpUrl);
  const [socketEpoch, setSocketEpoch] = useState(0);
  /** Readable from a timer without making it an effect dep; mirrors `connected`. */
  const connectedRef = useRef(false);
  /** False until the first socket opens, so only a *return* is announced as `server is back`. */
  const everConnectedRef = useRef(false);

  /** Force one reconnect attempt without waiting for the retry timer. */
  const bumpSocketEpoch = useCallback(() => setSocketEpoch((value) => value + 1), []);

  /**
   * The connection loop. A socket close is not the only way to lose a server, so every attempt
   * is a probe first: the same URL while it answers, otherwise the port `server.json` publishes
   * now. A different healthy URL moves `baseUrl`, which tears this effect down and opens the
   * next one there; the snapshot on open then refreshes the board, the masthead and `stale`.
   */
  useEffect(() => {
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let socket: WebSocket | null = null;

    const pushEvent = (taskId: string, event: TaskEvent) => {
      if (viewRef.current !== "log") return;
      if (taskId !== selectedRef.current) return;
      if (event.seq <= eventsSeqRef.current) return;
      eventsSeqRef.current = event.seq;
      setEvents((current) => {
        const next = [...current, event];
        return next.length > 1000 ? next.slice(-1000) : next;
      });
    };

    /** One pending attempt at a time: a new schedule replaces the old timer. */
    const schedule = (run: () => void) => {
      if (stopped) return;
      if (retry) clearTimeout(retry);
      retry = setTimeout(run, RETRY_MS);
    };

    const open = () => {
      socket = new WebSocket(`${baseUrl.replace(/^http/, "ws")}/ws`);
      socket.onopen = () => {
        const back = everConnectedRef.current && !connectedRef.current;
        everConnectedRef.current = true;
        connectedRef.current = true;
        setConnected(true);
        if (back) notify("server is back");
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        connectedRef.current = false;
        setConnected(false);
        schedule(() => void reconnect());
      };
      socket.onmessage = (message) => {
        let data: WsMessage;
        try {
          data = JSON.parse(String(message.data)) as WsMessage;
        } catch {
          return;
        }
        if (data.type === "snapshot") {
          const snapshot = data as Snapshot;
          setServer(snapshot.server);
          setTasks(snapshot.tasks);
          const current = selectedRef.current;
          if (current && !snapshot.tasks.some((task) => task.id === current)) {
            // The task we were watching disappeared; fall back to the list.
            viewRef.current = "list";
            selectTask(null);
            setView("list");
          }
          return;
        }
        if (data.type === "task") {
          setTasks((current) => {
            const index = current.findIndex((task) => task.id === data.task.id);
            if (index < 0) return [...current, data.task];
            const next = [...current];
            next[index] = data.task;
            return next;
          });
          return;
        }
        if (data.type === "event") {
          pushEvent(data.taskId, data.event);
        }
      };
    };

    /**
     * One reconnect attempt. The current URL wins when it answers (the common case: the socket
     * dropped but the process never did); otherwise the published port is probed before it is
     * adopted, so a dead or foreign entry in `server.json` cannot move the screen anywhere.
     */
    const reconnect = async () => {
      if (stopped) return;
      if (await probeServer(baseUrl, workspace)) {
        if (stopped) return;
        open();
        return;
      }
      const published = publishedUrl(stateDir, workspace, baseUrl);
      if (published && (await probeServer(published, workspace))) {
        if (stopped) return;
        // The effect re-runs on the new address and opens the socket there.
        setBaseUrl(published);
        return;
      }
      schedule(() => void reconnect());
    };

    open();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [baseUrl, stateDir, workspace, notify, selectTask, socketEpoch]);

  /**
   * The watchdog. A socket can die without a close event (a killed server, a suspended laptop)
   * and `stale`/`changed` are re-hashed per read, so one light probe keeps the masthead honest
   * between snapshots. A failed probe drops the socket and restarts the loop, which is what
   * re-reads `server.json`; a healthy one only refreshes the read-time bits.
   */
  useEffect(() => {
    const timer = setInterval(() => {
      void (async () => {
        const fresh = await probeServer(baseUrl, workspace);
        if (!fresh) {
          connectedRef.current = false;
          setConnected(false);
          setSocketEpoch((value) => value + 1);
          return;
        }
        setServer((current) =>
          sameServerRead(current, fresh) ? current : { ...(current ?? fresh), ...fresh },
        );
      })();
    }, WATCHDOG_MS);
    return () => clearInterval(timer);
  }, [baseUrl, workspace]);
  return {
    tasks,
    server,
    connected,
    baseUrl,
    socketEpoch,
    bumpSocketEpoch,
  };
}
