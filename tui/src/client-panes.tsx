/**
 * client-panes.tsx: the four stateless panes the screen is assembled from.
 *
 * WHAT IT IS
 *  The prompt box, the log header wrapper, the hairline above the prompt and the content pane
 *  (board rows or one run log). They take values and callbacks; none of them owns state.
 *
 * ENTRY POINTS
 *  PromptBox(props)    the one prompt box, rendered outside the view switch
 *  HeaderBar(props)    the log header wrapper; zero rows on the board, one on the log
 *  RuleAbove(props)    the prompt box's top line: live spinner + status note
 *  RuleBelow(props)    the prompt box's bottom line
 *  ContentPane(props)  board rows, the empty state, the help card or the live log
 *
 * INVARIANTS
 *  - The prompt box never unmounts across a view switch, so its text, cursor row, row count and
 *    focus survive a move between the board and a run log.
 *  - The log header wrapper is always rendered (height 0 on the board) so every element below it
 *    keeps its position in the tree and the view switch never remounts the prompt.
 *  - No pane reads state or fetches: the board in client.tsx owns everything they draw.
 *
 * WHERE TO CHANGE
 *  Prompt box and its keys .... PromptBox     Board rows ..... ContentPane
 *  The hairline note .......... RuleAbove     Log header ..... HeaderBar
 */
import type { TextareaRenderable } from "@opentui/core";
import type { KeyEvent } from "@opentui/core";
import type { Dispatch, RefObject, SetStateAction } from "react";
import { EmptyState, LogHeader, Rule, SectionHeader, TaskRow } from "./board-view";
import { HelpView } from "./board-help";
import { LogView } from "./log-view";
import { C, clip } from "./theme";
import type { CheckState, Task, TaskEvent } from "./types";
import type { ListRow } from "./list-model";

/** What the prompt box needs: its own text/geometry plus the actions its keys fire. */
export interface PromptBoxProps {
  /** Terminal width, so the placeholder is clipped to the box. */
  width: number;
  /** Box height in rows, grown from the wrapped prompt text. */
  height: number;
  /** The textarea itself, so the board can read, set and focus it. */
  inputRef: RefObject<TextareaRenderable | null>;
  /** Prompt text, mirrored into state on every edit. */
  input: string;
  /** The workspace named in the placeholder. */
  workspace: string;
  /** Records the interaction (typing restarts the auto view changer's idle clock). */
  bumpActivity: () => void;
  /** Prompt state setter, kept in step with the textarea. */
  setInput: Dispatch<SetStateAction<string>>;
  /** Re-measure the wrapped rows after an edit or a cursor move. */
  measurePrompt: () => void;
  /** Queue the prompt as a new task (Ctrl+Enter inside the textarea). */
  queue: (text: string) => Promise<void>;
}

/** The one prompt box: a `›` marker and a wrapping textarea, outside the view switch. */
export function PromptBox({
  width,
  height,
  inputRef,
  input,
  workspace,
  bumpActivity,
  setInput,
  measurePrompt,
  queue,
}: PromptBoxProps) {
  return (
    <box
      style={{
        height,
        flexShrink: 0,
        flexDirection: "row",
        paddingLeft: 1,
        paddingRight: 1,
        overflow: "hidden",
      }}
    >
      <text fg={C.accent}>{"› "}</text>
      <textarea
        ref={inputRef}
        placeholder={`task for ${clip(workspace, Math.max(10, width - 20))}…`}
        focused={true}
        initialValue={input}
        onContentChange={() => {
          bumpActivity();
          setInput(inputRef.current?.plainText ?? "");
          measurePrompt();
        }}
        onCursorChange={() => measurePrompt()}
        onKeyDown={(key: KeyEvent) => {
          // Ctrl+Enter queues; Enter inserts a line in the textarea.
          if ((key.ctrl || key.meta) && key.name === "return") {
            key.preventDefault();
            key.stopPropagation();
            void queue(inputRef.current?.plainText ?? "");
          }
        }}
        textColor={C.text}
        focusedTextColor={C.text}
        placeholderColor={C.faint}
        cursorColor={C.accent}
        wrapMode="word"
        style={{ flexGrow: 1 }}
      />
    </box>
  );
}

/** The log header wrapper: always rendered, one row on the log and zero on the board. */
export function HeaderBar({
  task,
  width,
  now,
  check,
  showingLog,
}: {
  /** The open task, or null on the board. */
  task: Task | null;
  /** Terminal width the header lays itself out in. */
  width: number;
  /** Wall clock for the header's live duration. */
  now: number;
  /** How the run's last verification ended, or null while none ran. */
  check: CheckState | null;
  /** True while the run log is on screen; the wrapper collapses to zero rows otherwise. */
  showingLog: boolean;
}) {
  return (
    <box
      style={{
        height: showingLog ? 1 : 0,
        flexShrink: 0,
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {task ? <LogHeader task={task} width={width} now={now} check={check} /> : null}
    </box>
  );
}

/**
 * The prompt box's top line: the live spinner, then the status note and the overflow marker,
 * all anchored left against the rule.
 */
export function RuleAbove({ width, note, spinner }: { width: number; note: string; spinner?: string }) {
  return (
    <box style={{ height: 1, flexShrink: 0 }}>
      <Rule width={width} note={note} lead={spinner} />
    </box>
  );
}

/** The prompt box's bottom line: the same hairline, closing the box on the terminal's last row. */
export function RuleBelow({ width }: { width: number }) {
  return (
    <box style={{ height: 1, flexShrink: 0 }}>
      <Rule width={width} />
    </box>
  );
}

/** One content pane, two views: the task board, or the live log of the selected task. */
export function ContentPane({
  task,
  events,
  width,
  height,
  help,
  listRows,
  visible,
  selected,
  now,
  onSelect,
}: {
  /** The open task; when set the log is drawn instead of the board. */
  task: Task | null;
  /** The open task's events, streamed while its log is on screen. */
  events: TaskEvent[];
  /** Terminal width every row lays itself out in. */
  width: number;
  /** Rows the content pane may fill, so the help card is clipped instead of overflowing. */
  height: number;
  /** True while the help card replaces the board or the log. */
  help: boolean;
  /** Every board row, so an empty board can draw the empty state. */
  listRows: ListRow[];
  /** The rows on screen this frame (a slice of `listRows`). */
  visible: ListRow[];
  /** The selected task id, so its row is drawn highlighted. */
  selected: string | null;
  /** Wall clock for live row durations. */
  now: number;
  /** Row click/drag selection. */
  onSelect: (id: string | null) => void;
}) {
  return (
    <box style={{ flexGrow: 1, flexBasis: 0, minHeight: 0, flexDirection: "column" }}>
      {help ? (
        <HelpView width={width} height={height} />
      ) : task ? (
        <LogView task={task} events={events} width={width} />
      ) : listRows.length === 0 ? (
        <EmptyState width={width} />
      ) : (
        visible.map((row, index) =>
          row.kind === "space" ? (
            <box key={`space:${index}`} style={{ height: 1, flexShrink: 0 }}>
              <text>{" "}</text>
            </box>
          ) : row.kind === "section" ? (
            <SectionHeader key={`section:${row.status}`} status={row.status} count={row.count} />
          ) : (
            <TaskRow
              key={`task:${row.task.id}`}
              task={row.task}
              selected={row.task.id === selected}
              width={width}
              now={now}
              onSelect={onSelect}
            />
          ),
        )
      )}
    </box>
  );
}
