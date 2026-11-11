/**
 * board-help.tsx: the help card and the starter use cases.
 *
 * WHAT IT IS
 *  The one reference card behind `?`: what the board is, the keyboard table and four starter
 *  tasks a new reader can load with a digit. USES is the single list of examples.
 *
 * ENTRY POINTS
 *  USES                               four starter tasks {title, prompt}
 *  helpLines(width) -> {text,color}[] the card's rows, pure and testable
 *  HelpView({width, height})          the card, clipped to the content pane
 *  EmptyState({width})                the first screen, with the starter use cases
 *  handleHelpKey(key, close, load)    the card's modal keys: close, or a digit loads
 *
 * INVARIANTS
 *  - Read-only chrome: it never queues, cancels or touches the server.
 *  - Every row is exactly one line and clipped to width; rows past `height` are dropped.
 *  - The keys listed are exactly the ones client-input.ts handles.
 *
 * WHERE TO CHANGE
 *  A key row ... helpLines      A starter task ... USES      Colours ... theme.ts
 *  Modal keys ... handleHelpKey      First screen ... EmptyState
 */
import type { RGBA } from "@opentui/core";
import type { KeyEvent } from "@opentui/core";
import { C, clip } from "./theme";

/** One starter task: a short label and the prompt a reader can load with a digit. */
export interface UseCase {
  title: string;
  prompt: string;
}

/** Four ways to start, worded as outcomes and left for the check plan to judge. */
export const USES: UseCase[] = [
  {
    title: "fix a bug",
    prompt: "Find a real bug in this repository, fix it, and add a regression test that fails without the fix.",
  },
  {
    title: "add a feature",
    prompt: "Add a small feature: input validation on the public entry point, covered by a test, with a short doc update.",
  },
  {
    title: "make checks green",
    prompt: "Run this project's checks, fix every failure, and leave the whole suite green.",
  },
  {
    title: "write the docs",
    prompt: "Write a quickstart README for this project: what it does, how to install, run and test it.",
  },
];

/** One row of the card: its text and the ink it is drawn in. */
export interface HelpRow {
  text: string;
  color: RGBA;
}

const KEYS: [string, string][] = [
  ["enter", "open the selected run · new line in a long prompt"],
  ["ctrl+enter", "queue the prompt as a task"],
  ["up / down", "move the selection (ctrl+p / ctrl+n too)"],
  ["ctrl+k", "cancel the selected run"],
  ["ctrl+o", "reorder: status → newest → problems"],
  ["ctrl+r", "reconnect to the server"],
  ["esc", "clear the prompt · leave a run · close this card"],
  ["? ", "this card"],
  ["ctrl+c / ctrl+q", "quit; running tasks keep going"],
];

/** The four examples and the help card's one keyboard rule. */
export function handleHelpKey(
  key: KeyEvent,
  close: () => void,
  applyExample: (index: number) => void,
): void {
  key.preventDefault();
  key.stopPropagation();
  const isEnter = key.name === "return" || key.name === "linefeed" || key.name === "kpenter";
  if (key.name === "escape" || key.sequence === "?" || isEnter) {
    close();
    return;
  }
  const digit = Number(key.sequence);
  if (!key.ctrl && !key.meta && key.sequence.length === 1 && digit >= 1 && digit <= 4) {
    applyExample(digit - 1);
    close();
  }
}

/** The first screen: what this thing is for, four ways to start, and the keys that begin. */
export function EmptyState({ width }: { width: number }) {
  const fit = Math.max(10, width - 4);
  return (
    <box style={{ flexDirection: "column", flexShrink: 0, paddingLeft: 2 }}>
      <text fg={C.text}>{"no tasks yet"}</text>
      <text fg={C.faint}>{clip("describe a task below — ctrl+enter queues it · enter opens a run", fit)}</text>
      <text fg={C.dim}>{"start here"}</text>
      {USES.map((use, index) => (
        <box key={use.title} style={{ height: 1, flexShrink: 0 }}>
          <text fg={C.faint}>{clip(`  ${index + 1}  ${use.title} — ${use.prompt}`, fit)}</text>
        </box>
      ))}
      <text fg={C.quiet}>{clip("press 1–4 to load a starter task · ? for keys and use cases · closing the tui leaves runs going", fit)}</text>
    </box>
  );
}
/** The card's rows, already clipped to `width`; pure so a test can read it without a frame. */
export function helpLines(width: number): HelpRow[] {
  const fit = (text: string) => clip(text, Math.max(10, width - 2));
  const rows: HelpRow[] = [
    { text: "code — help", color: C.accent },
    { text: "", color: C.faint },
    { text: "type a task below and press ctrl+enter. runs are fire-and-forget: closing the tui keeps them going.", color: C.faint },
    { text: "keys", color: C.dim },
  ];
  for (const [key, action] of KEYS) {
    rows.push({ text: fit(`  ${key.padEnd(16)}${action}`), color: C.faint });
  }
  rows.push({ text: "", color: C.faint });
  rows.push({ text: "use cases  ·  press 1–4 to load one", color: C.dim });
  USES.forEach((use, index) => {
    rows.push({ text: fit(`  ${index + 1}  ${use.title} — ${use.prompt}`), color: C.text });
  });
  return rows;
}

/** The help card: one clipped text row per line, limited to the content pane's height. */
export function HelpView({ width, height }: { width: number; height: number }) {
  const rows = helpLines(width).slice(0, Math.max(0, height));
  return (
    <box style={{ flexDirection: "column", flexShrink: 0, paddingLeft: 1, paddingRight: 1, overflow: "hidden" }}>
      {rows.map((row, index) => (
        <box key={`help:${index}`} style={{ height: 1, flexShrink: 0 }}>
          <text fg={row.color}>{row.text || " "}</text>
        </box>
      ))}
    </box>
  );
}
