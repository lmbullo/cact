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
