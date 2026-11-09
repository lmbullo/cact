"""state_ranges: read ranges: the line arithmetic behind [read] and what a card delivered.

WHAT IT IS
  read() records served ranges in Cell.reads; a card may serve them whole (typed verbatim
  observation) or partially (digested python/exec stream). This module turns recorded ranges +
  the text the card rendered into the ranges the model really saw, and computes the holes.
  Coverage = guidance, never a finish gate.

ENTRY POINTS
  merge_ranges(ranges) -> [[a, b], ...]        overlapping/adjacent, sorted, disjoint
  gaps(ranges, total) -> [[a, b], ...]          lines of 1..total the ranges miss
  covered(ranges) -> int                       distinct lines in the merged ranges
  numbered_lines(text) -> set[int]             `   12| line` numbers a rendered block carries
  numbered_lines_by_path(text, paths) -> {path: set[int]}   same, split by `# path` headers
  delivered_ranges(reads, body, digested) -> {path: [[a, b], ...]}

INVARIANTS
  opens no file, compares no text against one: a range is claimed only from Cell.reads or from
  numbered lines still present in the rendered body. A digested card cannot claim an elided line;
  a raw (non-numbered) read is never guessed.
  digested batch read split by `# path` headers: one file's line numbers are never credited to
  another file with a line at that number.
  merge_ranges accepts unsorted input with any b >= a pairing, never mutates its argument;
  gaps([]) = whole file; gaps([(1, total)], total) = empty.
  pure, deterministic: same reads + same body -> same answer; no state between calls.

WHERE TO CHANGE
  what counts as a delivered line ... delivered_ranges, numbered_lines, _NUMBERED
  [read] wording .................... state_card.render_observation (not here)
  fold reads: summary ............... state_fold.session_state_message
"""
from __future__ import annotations

import re

_NUMBERED = re.compile(r"^\s*(\d+)\| ?(.*)$")


def merge_ranges(ranges) -> list[list[int]]:
    """Overlapping/adjacent (a, b) ranges -> sorted disjoint [[a, b], ...]."""
    out: list[list[int]] = []
    for a, b in sorted((max(1, int(a)), int(b)) for a, b in ranges if int(b) >= int(a)):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def gaps(ranges, total: int) -> list[list[int]]:
    """The line ranges of 1..total that `ranges` does not cover."""
    merged = merge_ranges(ranges)
    out: list[list[int]] = []
    cursor = 1
    for a, b in merged:
        if a > cursor:
            out.append([cursor, min(total, a - 1)])
        cursor = max(cursor, b + 1)
    if total and cursor <= total:
        out.append([cursor, total])
    return [item for item in out if item[1] >= item[0]]


def covered(ranges) -> int:
    """How many distinct lines the merged ranges cover."""
    return sum(b - a + 1 for a, b in merge_ranges(ranges))


def numbered_lines(text: str) -> set[int]:
    """The line numbers the numbered read format (`f"{n:>6}| line"`) puts in `text`.

    A deterministic parse of what was rendered: no file is opened and no text is compared
    against one, so a digested card can only claim the lines it actually kept.
    """
    out: set[int] = set()
    for line in (text or "").splitlines():
        match = _NUMBERED.match(line)
        if match:
            out.add(int(match.group(1)))
    return out


def numbered_lines_by_path(text: str, paths) -> dict[str, set[int]]:
    """`{path: line numbers}` for a digested batch read.

    `read([a, b])` joins the files with `# path` headers. Without this split, a number kept
    for one file would be credited to every file with a line at that number; with it, a
    digested batch card can only claim what each section actually kept.
    """
    wanted = {str(path) for path in (paths or ())}
    out: dict[str, set[int]] = {}
    current: str | None = None
    for line in (text or "").splitlines():
        if line.startswith("# ") and line[2:].strip() in wanted:
            current = line[2:].strip()
            out.setdefault(current, set())
            continue
        match = _NUMBERED.match(line)
        if match and current is not None:
            out[current].add(int(match.group(1)))
    return out


def delivered_ranges(reads, body: str = "", digested: bool = False) -> dict[str, list[list[int]]]:
    """`{path: [[a, b], ...]}`: the ranges this card actually delivered to the model.

    Undigested, that is exactly what `read()` recorded in `Cell.reads`. Digested, it is only the
    numbered lines still present in `body`; a batch read is split by its `# path` headers so a
    line number is never credited to the wrong file. A raw (non-numbered) read is never guessed.
    """
    kept = numbered_lines(body) if digested and len(reads or {}) <= 1 else None
    by_path = numbered_lines_by_path(body, reads) if digested and kept is None else {}
    out: dict[str, list[list[int]]] = {}
    for path, entry in (reads or {}).items():
        ranges: list[list[int]] = []
        path_kept = kept if kept is not None else by_path.get(path, set())
        for a, b in entry.get("ranges") or []:
            a, b = max(1, int(a)), int(b)
            if not digested:
                ranges.append([a, b])
            else:
                ranges.extend([n, n] for n in range(a, b + 1) if n in path_kept)
        merged = merge_ranges(ranges)
        if merged:
            out[path] = merged
    return out
