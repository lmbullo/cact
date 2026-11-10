/**
 * client-actions.ts: the requests and writes the board makes — queue, cancel, copy, backfill.
 *
 * WHAT IT IS
 *  `useTaskActions` is the only POSTs the screen sends, `useEventBackfill` is its one GET on an
 *  open log, and `useCopyGesture` is the one host write. Nothing here draws.
 *
 * ENTRY POINTS
 *  useTaskActions(options) -> { queue(text), cancelSelected() }
 *  useEventBackfill(options)   re-read `?limit=800` when the open task or the link changes
 *  useCopyGesture(options)     a left drag copies the selection and clears the highlight
 *
 * INVARIANTS
 *  - `queue` always requests a *new* task; it never edits or re-runs one, and a failed one
 *    restores the exact text that was typed and refocuses the prompt.
 *  - Nothing is cancelled optimistically: the server's snapshot is what redraws the row.
 *  - The backfill sets the `seq` watermark from the batch's newest event, so a replayed live
 *    event is dropped instead of drawn twice; a failed read is silent.
 *  - Every request reads `baseUrl`, so a restarted server is talked to at the port it publishes.
 *  - A plain click only selects; only a drag (or a word/line expansion) copies.
 *
 * WHERE TO CHANGE
 *  Queue .... queue    Cancel .... cancelSelected    Copy .... useCopyGesture
 *  Open log's events .... useEventBackfill    What the server says .... types.ts
 */
import type { CliRenderer, TextareaRenderable } from "@opentui/core";
import { useSelectionHandler } from "@opentui/react";
import { useCallback, useEffect, type Dispatch, type SetStateAction } from "react";
import type { Task, TaskEvent } from "./types";

/** What the copy gesture needs: the renderer it clears, the clipboard writer and the toast. */
export interface CopyGestureOptions {
  /** Records one interaction, restarting the auto view changer's clock. */
  bumpActivity: () => void;
  /** The renderer whose selection highlight is cleared after the copy. */
  renderer: CliRenderer;
  /** Clipboard writer; tests inject a spy so they never touch the host clipboard. */
  copy: (renderer: CliRenderer, text: string) => Promise<boolean>;
  /** Toast writer for `copied N chars` / `copy failed`. */
  notify: (message: string) => void;
}

/** What `useTaskActions` needs: the current address, the prompt box and the selection. */
export interface TaskActionOptions {
  /** The address the server answers at now (probe/published URL, never a stale one). */
  baseUrl: string;
  /** Toast writer for the failure and confirmation lines. */
  notify: (message: string) => void;
  /** Open the new task's log as soon as the server answers with its id. */
  openLog: (id: string | null) => void;
  /** The board's current tasks, so a cancel only targets a task that exists. */
  tasks: Task[];
  /** The prompt box, so a failed queue can restore its text. */
  inputRef: { current: TextareaRenderable | null };
  /** Prompt state setter, kept in step with the textarea. */
  setInput: Dispatch<SetStateAction<string>>;
  /** Readable selection, so cancel acts on the row the board shows as selected. */
  selectedRef: { current: string | null };
}

/** The board's two writes, as stable callbacks. */
export interface TaskActions {
  /** Queue `text` as a new task and open its log; on failure restore the prompt and toast. */
  queue: (text: string) => Promise<void>;
  /** Ask the server to cancel the selected task when it is queued or running. */
  cancelSelected: () => Promise<void>;
}

/** Queue the prompt as a new task, and cancel the selected one; the only writes the board makes. */
export function useTaskActions({
  baseUrl,
  notify,
  openLog,
  tasks,
  inputRef,
  setInput,
  selectedRef,
}: TaskActionOptions): TaskActions {
  const queue = useCallback(
    async (text: string) => {
      const prompt = text.trim();
      if (!prompt) {
        notify("task is empty");
        return;
      }
      const previous = inputRef.current?.plainText ?? prompt;
      if (inputRef.current) inputRef.current.clear();
      setInput("");
      try {
        const response = await fetch(`${baseUrl}/api/tasks`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ prompt }),
        });
        const data = (await response.json()) as { task?: Task; error?: string };
        if (!response.ok || !data.task) {
          if (inputRef.current) inputRef.current.setText(previous);
          setInput(previous);
          notify(data.error ?? `queue failed (${response.status})`);
          return;
        }
        openLog(data.task.id);
        // No "queued" toast: the log header already shows the task's state, live, and the
        // server may have started it before this response even arrived.
      } catch (error) {
        if (inputRef.current) inputRef.current.setText(previous);
        setInput(previous);
        notify(`queue failed: ${error instanceof Error ? error.message : String(error)}`);
      } finally {
        inputRef.current?.focus();
      }
    },
    [baseUrl, notify, openLog],
  );

  const cancelSelected = useCallback(async () => {
    const task = tasks.find((item) => item.id === selectedRef.current);
    if (!task) return;
    if (task.status !== "queued" && task.status !== "running") {
      notify("only queued or running tasks can be cancelled");
      return;
    }
    try {
      const response = await fetch(
        `${baseUrl}/api/tasks/${encodeURIComponent(task.id)}/cancel`,
        { method: "POST" },
      );
      if (!response.ok) notify(`cancel failed (${response.status})`);
      else notify("cancellation requested");
    } catch (error) {
      notify(`cancel failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }, [baseUrl, notify, tasks]);

  return { queue, cancelSelected };
}

/** Drag-to-copy: release copies the selection and clears the highlight, drag or not it selects. */
export function useCopyGesture({ bumpActivity, renderer, copy, notify }: CopyGestureOptions): void {
  // A left-drag is the whole copy gesture: releasing the mouse copies the selection and
  // clears the highlight immediately, whether or not the clipboard write then succeeds.
  // A plain click is only a task selection (or a double-click word), so it never copies
  // unless the gesture actually moved or the terminal expanded it to a word/line.
  useSelectionHandler((selection) => {
    bumpActivity();
    const text = selection.getSelectedText();
    const { anchor, focus } = selection;
    const dragged = anchor.x !== focus.x || anchor.y !== focus.y;
    const expanded = selection.behavior !== "cell";
    renderer.clearSelection();
    if ((!dragged && !expanded) || !text.trim()) return;
    void copy(renderer, text).then((ok) =>
      notify(ok ? `copied ${text.length} chars` : "copy failed"),
    );
  });
}

/** What the backfill needs: the address, the open task and the buffer it fills. */
export interface EventBackfillOptions {
  /** The address the server answers at now. */
  baseUrl: string;
  /** The open task, or null when the board is showing. */
  selected: string | null;
  /** Whether the socket is open; a reconnect re-reads the batch. */
  connected: boolean;
  /** The screen in view; only "log" keeps events. */
  view: "list" | "log";
  /** Highest event `seq` held; set from the batch so replayed live events are dropped. */
  eventsSeqRef: { current: number };
  /** Event buffer setter the batch is installed into. */
  setEvents: Dispatch<SetStateAction<TaskEvent[]>>;
}

/** Read the open task's events once per link/open task, then let the socket stream the rest. */
export function useEventBackfill({
  baseUrl,
  selected,
  connected,
  view,
  eventsSeqRef,
  setEvents,
}: EventBackfillOptions): void {
  useEffect(() => {
    if (!selected || view !== "log") {
      setEvents([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(
          `${baseUrl}/api/tasks/${encodeURIComponent(selected)}/events?limit=800`,
        );
        if (!response.ok) return;
        const data = (await response.json()) as { events?: TaskEvent[] };
        if (cancelled) return;
        const next = data.events ?? [];
        eventsSeqRef.current = next[next.length - 1]?.seq ?? 0;
        setEvents(next);
      } catch {
        // WebSocket reconnect will refresh the snapshot.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [baseUrl, selected, connected, view]);
}
