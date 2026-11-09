"""state_card: one cell rendered as [s]/[obs]/[page]/[data]/[read] lines.

WHAT IT IS
  The renderer behind StateLoop._observe. A cell becomes a card, not a transcript:
    [s]          step, cost, elapsed of this call + the run's plan lines
    chg: / [diff]  paths the cell changed + the journal's unified diff
    [check]      cheap check: `pass`, or `<spec> : <first failing line>` + its _OBS handle
    [obs]/[page] typed verb envelope: op/status, shown/total/offset/next/unit, completeness
                 triple; a paged search also gets one `[hits]` line per file
    [data]       typed result's structured fields; an oversized value is clipped per key and
                 named in one `[data] clipped:` note
    [note] / [error]  explicit notes + structured refusal (code, message, use,
                 signature/valid/argument), never a prose-only failure
    [stderr]/[stdout]  python/exec output, digested when over the output budget
    [result]     last expression, clipped (python only)
    [read]       `path a-b/total (unread: holes)` from Cell.reads + the text rendered here

ENTRY POINTS
  render_observation(cell, steps, prompt_tokens=0, check=None, output_budget=12_000,
                     coverage=None, check_handle=None, plan=None)
                       -> (card text, {path: [[a, b], ...]} the card made visible)
  _VERBATIM_TOOLS ... typed verbs whose observation is never digested (they page themselves)

INVARIANTS
  pure: opens no workspace file; reads the loop's coverage ledger, never writes it. _observe
  (state_loop.py) folds the returned ranges into coverage.
  typed observation delivered whole; only python/exec output is digested, only above
  output_budget: a verb already pages and names its own loss.
  every card names the _OBS[n] entry holding the full text: the handle, not the text, is the
  contract; page(handle) works across a kernel restart.
  [read] built from Cell.reads + the text actually rendered: it can never claim a line the card
  dropped; state_ranges.delivered_ranges owns that split.
  refusal is structured: `[error] kind: code: message; use ...` (one line); a check failure is
  one line whose full output is an _OBS handle.

WHERE TO CHANGE
  card layout / section order ......... render_observation
  typed observations kept verbatim .... _VERBATIM_TOOLS
  clipping, digesting, [data] fields .. state_text.py
  read ranges / what counts as read ... state_ranges.py
"""
from __future__ import annotations

import json

from .state_ranges import delivered_ranges, gaps, merge_ranges
from .state_text import clip, compact_stream, data_block, digest_card

# Typed verbs already page and name their own loss. The card renders their observation
# verbatim; only process/python output is digested, because only it has no paging contract.
_VERBATIM_TOOLS = frozenset({"read", "search", "map", "outline", "guide", "recall", "page",
                            "session_diff", "git", "checks", "status"})


def render_observation(cell, steps: int, prompt_tokens: int = 0, check=None,
                       output_budget: int = 12_000,
                       coverage: dict | None = None,
                       check_handle: str | None = None,
                       plan: list[str] | None = None) -> tuple[str, dict[str, list[list[int]]]]:
    """A cell -> `(the state card, the read ranges the card made visible)`.

    Pure: it reads no workspace file and does not mutate the loop's coverage ledger. The ranges
    come from `Cell.reads` (exact, recorded by the kernel) and from the text rendered right here,
    so `[read] path a-b/total (next N)` and the `reads:` fold line cannot drift from the card.
    """
    parts = [f"[s] step={steps} ms={cell.duration_ms} tok={prompt_tokens}"]
    parts.extend(plan or ())
    if getattr(cell, "tool", None) and not getattr(cell, "envelope", None):
        parts.append(f"[tool] {cell.tool}")
    if cell.changed:
        parts.append("chg: " + ", ".join(cell.changed))
    if cell.timeout:
        parts.append("[state] timeout: cell interrupted; namespace and variables are intact")
    if cell.restarted:
        parts.append("[state] kernel restarted: the namespace was lost; variables and imports "
                     "are gone (earlier output survives in recall())")
    if cell.delta:
        parts.append("[diff]\n" + clip(cell.delta, 6000))
    if check is not None:
        if check.ok:
            parts.append("[check] pass")
        else:
            name = check.cmd.split(": ", 1)[0] if ": " in check.cmd else check.cmd
            line = "%s : %s" % (name, check.first_failure() or "failed")
            if check_handle:
                line += " (full: %s)" % check_handle
            parts.append("[check] " + clip(line, 240))
    handle = f"_OBS[{cell.obs_index}]" if cell.obs_index is not None else "_OBS[-1]"
    body: list[str] = []
    digested = False
    typed = bool(getattr(cell, "tool", None))
    verbatim = typed and cell.tool in _VERBATIM_TOOLS
    env = getattr(cell, "envelope", None)
    if typed and env:
        page = env.get("page")
        status = "error" if not env.get("ok", True) else "ok"
        parts.append(f"[obs] {cell.tool} {status} {handle}")
        if page:
            bits = [f"shown={page.get('shown')}", f"total={page.get('total')}"]
            if page.get("unit"):
                bits.append(f"unit={page['unit']}")
            if page.get("offset") is not None:
                bits.append(f"offset={page['offset']}")
            if page.get("next") is not None:
                bits.append(f"next={page['next']}")
            if (env.get("data") or {}).get("hits") is not None:
                bits.append(f"hits={(env.get('data') or {}).get('hits')}")
            if page.get("pointer"):
                bits.append("pointer=true")
            bits.append("has_more=" + ("true" if page.get("has_more") else "false"))
            bits.append("truncated=" + ("true" if page.get("truncated") else "false"))
            bits.append("complete=" + ("true" if page.get("complete") else "false"))
            parts.append("[page] " + " ".join(bits))
        for note in env.get("notes") or []:
            parts.append("[note] " + str(note))
        data = env.get("data") or {}
        # Structured-only facts (did_you_mean, include_unmatched, edit closest/context, a
        # map's dirs_matched, an exec argv/results, a git sha) have no stdout delivery.
        # Render every non-duplicated field; clip each value per key and name what was cut.
        if data:
            shown, clipped = data_block(data)
            if shown:
                try:
                    payload = json.dumps(shown, sort_keys=True, default=repr)
                except (TypeError, ValueError):
                    payload = repr(shown)
                parts.append("[data] " + payload)
                if clipped:
                    parts.append("[data] clipped: " + ", ".join(clipped))
        by_file = data.get("by_file")
        if cell.tool == "search" and by_file and page and not page.get("complete"):
            for path, info in list(by_file.items())[:6]:
                numbers = info.get("lines") or []
                ranges = []
                for number in numbers:
                    number = int(number)
                    if ranges and number <= ranges[-1][1] + 1:
                        ranges[-1][1] = number
                    else:
                        ranges.append([number, number])
                where = ", ".join("%d-%d" % (a, b) if a != b else str(a)
                                  for a, b in ranges[:6])
                if len(ranges) > 6:
                    where += " +%d more" % (len(ranges) - 6)
                total_hits = page.get("total") or 0
                remain = max(0, int(total_hits) - int(page.get("offset") or 0) - int(page.get("shown") or 0))
                where_text = where or "(none)"
                parts.append("[hits] %s: %s hit(s) at %s; page of %d, %d remain"
                             % (path, info.get("count"), where_text, total_hits, remain))
        if not env.get("ok", True) and env.get("error"):
            err = env["error"]
            code = err.get("code") or "error"
            message = err.get("message") or err.get("path") or ""
            hint = err.get("use")
            if not hint and code == "outside_root":
                hint = "typed editors are workspace-only; use exec(shell=...) for a scratch path"
            line = "[error] %s: %s: %s" % (env.get("kind") or "error", code, message)
            if hint:
                line += "; use " + str(hint)
            if err.get("signature"):
                line += "; signature=" + clip(str(err["signature"]), 300)
            if err.get("valid"):
                valid = err["valid"]
                if isinstance(valid, (list, tuple)):
                    valid = ", ".join(str(item) for item in valid)
                line += "; valid=" + clip(str(valid), 300)
            if err.get("argument"):
                line += "; argument=" + str(err["argument"])
            parts.append(line)
    if cell.stderr:
        if verbatim:
            body.append("[error]\n" + cell.stderr)
        else:
            rendered, cut = digest_card(compact_stream(cell.stderr), output_budget, handle,
                                        errors=True)
            digested = digested or cut
            body.append("[stderr]\n" + clip(rendered, output_budget))
    if cell.stdout:
        if verbatim:
            body.append("[data]\n" + cell.stdout)
            if env and env.get("page") and not env["page"].get("complete"):
                body.append("[more] page.next=%r; use it to continue" % (env["page"].get("next"),))
        else:
            rendered, cut = digest_card(compact_stream(cell.stdout), output_budget, handle)
            digested = digested or cut
            body.append("[stdout]\n" + clip(rendered, output_budget))
    if cell.result is not None and not typed:
        body.append("[result] " + clip(cell.result, 1500))
    visible = "\n".join(body)
    shown = delivered_ranges(cell.reads, visible, digested)
    for path, ranges in sorted(shown.items()):
        total = int((cell.reads.get(path) or {}).get("total") or 0)
        session = (coverage or {}).get(path) or {}
        missing = gaps(list(session.get("ranges") or []) + ranges, total)
        tail = ""
        if missing:
            if len(missing) <= 6:
                shown_gaps = ", ".join("%d-%d" % (a, b) for a, b in missing)
            else:
                shown_gaps = "%d ranges / %d lines" % (
                    len(missing), sum(b - a + 1 for a, b in missing))
            tail = f" unread {shown_gaps}"
        text = ", ".join(f"{a}-{b}" for a, b in merge_ranges(ranges))
        parts.append(f"[read] {path} {text}/{total}{tail}")
    parts.extend(body)
    return "\n".join(parts), shown
