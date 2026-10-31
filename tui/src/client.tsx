import {
  createCliRenderer,
  type TextareaRenderable,
  type KeyEvent,
  TextAttributes,
  RGBA,
} from "@opentui/core";
import { createRoot, useKeyboard, useRenderer, useTerminalDimensions } from "@opentui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ServerInfo, Snapshot, Task, TaskEvent, TaskStatus, WsMessage } from "./types";
import {
  buildRows,
  orderTasks,
  scrollWindow,
  selectedRowIndex,
  stepSelection,
  taskHandle,
  type ListRow,
} from "./list-model";

/**
 * Gruvbox dark palette. Explicit hex values rather than ansi() slots, so the app keeps
 * the same look no matter how the host terminal's own scheme is configured.
 */
const hex = (value: string): RGBA => RGBA.fromHex(value);

const C = {
  bg: hex("#282828"), // bg0
  bg1: hex("#3c3836"), // bg1, for subtle raised surfaces
  bg2: hex("#504945"), // bg2, selection fill
  bg4: hex("#7c6f64"), // bg4, scrollbar thumb
  text: hex("#ebdbb2"), // fg1
  dim: hex("#a89984"), // fg4
  faint: hex("#928374"), // gray
  accent: hex("#83a598"), // blue
  green: hex("#b8bb26"), // green
  yellow: hex("#fabd2f"), // yellow
  red: hex("#fb4934"), // red
  tool: hex("#d3869b"), // gruvbox purple, tool calls in the run log
  orange: hex("#fe8019"), // orange
};

/** One short word per status, so a row reads `3 running fix the flaky test`. */
const STATUS: Record<TaskStatus, { word: string; color: RGBA }> = {
  running: { word: "running", color: C.green },
  queued: { word: "queue", color: C.yellow },
  done: { word: "done", color: C.dim },
  partial: { word: "partial", color: C.yellow },
  failed: { word: "failed", color: C.red },
  cancelled: { word: "cancelled", color: C.faint },
};

/** The prompt grows with its content up to this many rows, then scrolls internally. */
const PROMPT_MAX_ROWS = 5;

function clip(text: string, width: number): string {
  if (width <= 1) return "";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= width ? flat : `${flat.slice(0, Math.max(0, width - 1))}…`;
}

/** Collapse any multi-line blob onto one space-separated line. */
function oneLine(text: string): string {
  return text.replace(/\r/g, "").replace(/\s+/g, " ").trim();
}

/** Count non-empty lines, so a big error dump reads `(+42L)` instead of 42 rows. */
function lineCount(text: string): number {
  return text.replace(/\r/g, "").split("\n").filter((line) => line.trim().length > 0).length;
}

/**
 * A cell parsed down to the one line the log shows: only the arguments of the
 * call, since the log keeps tool calls to their bare arguments.
 */
function toolArgs(code: string): string {
  const lines = code
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) return "(empty cell)";
  const flat = oneLine(lines[0]!);
  const call = flat.match(/^([A-Za-z_][\w.]*)\s*\(/);
  if (!call) return flat;
  const start = call[0].length;
  let depth = 1;
  let end = start;
  for (; end < flat.length; end++) {
    const ch = flat[end];
    if (ch === "(") depth += 1;
    else if (ch === ")") {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  return flat.slice(start, end).trim();
}

function Rule({ width, note }: { width: number; note?: string }) {
  // Keep the hint visible: shrink the rule instead of letting the note run off-screen.
  const hint = note ? ` ${note}` : "";
  const line = "─".repeat(Math.max(0, width - hint.length));
  return note ? <text fg={C.faint}>{`${line}${hint}`}</text> : <text fg={C.faint}>{line}</text>;
}

function SectionHeader({ status, count }: { status: TaskStatus; count: number }) {
  const meta = STATUS[status];
  return (
    <box style={{ height: 1, width: "100%", flexShrink: 0 }}>
      <text>
        <span fg={meta.color} attributes={TextAttributes.BOLD}>{`─ ${meta.word} `}</span>
        <span fg={C.faint}>{`(${count})`}</span>
      </text>
    </box>
  );
}

function TaskRow(props: {
  task: Task;
  selected: boolean;
  width: number;
  onSelect: (id: string) => void;
}) {
  const { task, selected, width, onSelect } = props;
  const meta = STATUS[task.status];
  const handle = taskHandle(task.id);
  const head = `${selected ? "›" : " "} ${handle} `;
  // Selection is shown with an arrow, not a background highlight.
  const marker = selected ? " ←" : "";
  const prompt = clip(
    task.prompt,
    Math.max(8, width - head.length - meta.word.length - marker.length - 2),
  );
  return (
    <box
      style={{
        height: 1,
        width: "100%",
        flexShrink: 0,
      }}
      onMouseDown={() => onSelect(task.id)}
    >
      <text>
        <span fg={selected ? C.accent : C.faint}>{head}</span>
        <span fg={meta.color}>{meta.word.padEnd(9)}</span>
        <span fg={selected ? C.text : C.dim}>{` ${prompt}`}</span>
        <span fg={selected ? C.accent : C.faint}>{marker}</span>
      </text>
    </box>
  );
}
/**
 * One event, one row. The full payload still reaches the model; this view only
 * truncates what fits on a terminal line so a run never dumps a wall of text.
 */
