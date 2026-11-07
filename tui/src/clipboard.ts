/**
 * Clipboard: the one place the TUI writes selected text out of the terminal.
 *
 * WHAT IT IS
 *  `copyText` puts a selection on the user's clipboard. Locally it tries the host clipboard
 *  first (X11/Wayland/macOS/Windows through the native library) and falls back to OSC 52;
 *  over a remote terminal it goes straight to OSC 52 so the text lands on the machine the
 *  user is typing at, not the one the server runs on.
 *
 * ENTRY POINTS
 *  copyText(renderer, text)     true when at least one destination accepted the text
 *
 * INVARIANTS
 *  - No host clipboard is created until the first copy: a headless run never pays for it.
 *  - A failure is a boolean, never a throw: a copy is a convenience, not a task error.
 *  - The host service is process-lifetime; the renderer owns its terminal lifetime.
 *
 * WHERE TO CHANGE
 *  Destination policy or timeout ..... copyText / hostClipboard
 *  When a selection is copied ........ client.tsx (useSelectionHandler)
 */
import {
  createHostClipboard,
  type CliRenderer,
  type HostClipboardService,
} from "@opentui/core";

let host: HostClipboardService | null | undefined;

/** The host clipboard service, or null when this environment has no provider; created once. */
function hostClipboard(): HostClipboardService | null {
  if (host !== undefined) return host;
  try {
    host = createHostClipboard({ timeoutMs: 1500 });
  } catch {
    host = null;
  }
  return host;
}

/** Copy `text` to the user's clipboard; true when the host or the terminal accepted it. */
export async function copyText(renderer: CliRenderer, text: string): Promise<boolean> {
  if (!text) return false;
  const remote = renderer.capabilities?.remote === true;
  if (!remote) {
    const service = hostClipboard();
    if (service) {
      try {
        const result = await service.writeText(text);
        if (result.status === "written") return true;
      } catch {
        // No usable host provider: OSC 52 is the remaining destination.
      }
    }
  }
  return renderer.copyToClipboardOSC52(text);
}
