"""env: the deterministic environment card prepended to the first task message.

WHAT IT IS
  One cheap probe of the workspace the model is standing in: the resolved root, the git root
  (or none), a capped top-level listing, and the task-mentioned paths that do not exist here
  with the closest path that does. `render_environment` turns it into the compact
  `[environment]` block `ToolLoop.run` puts before the task. It is deliberately not a tool:
  the model should not spend a turn asking where it is.

ENTRY POINTS
  environment(root, task="") -> dict   root, git_root, top, task_paths
  render_environment(env) -> str        the `[environment]` card, or "" when root is missing
  resolve_path(root, given) -> dict|None  None when the path exists; else {given, use|missing}

INVARIANTS
  - No mutation, no network, no model. The git probe is a short local `git rev-parse`; a
    missing git, a timeout or a non-repository is `None`, not an error.
  - `top` is capped and skips generated trees, so a workspace with `node_modules` does not
    bury the real entries in the card.
  - A task path is reported only when it is missing. For `tui/src/x.ts` in a workspace whose
    root is the `tui` package, `use` is `src/x.ts`; that is the sentence that prevents the
    symlink repair the environment mismatch used to provoke.
  - A path that is not found by stripping leading components is `missing: true`; the card
    does not invent a closest file it did not verify.

WHERE TO CHANGE
  What the card says ...................... environment, render_environment
  Which task paths are resolved ........... _PATH, resolve_path
  The generated-tree skip list ............ _SKIP
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
         "target", ".cache", ".tmp", ".next", "coverage"}
_PATH = re.compile(r"(?<![\w./-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?)")
_TOP = 24


def environment(root: str | Path, task: str = "") -> dict:
    """The root/git/top card plus the task paths that do not exist at this root."""
    base = Path(root).expanduser().resolve()
    return {
        "root": str(base),
        "git_root": _git_root(base),
        "top": _top(base),
        "task_paths": _task_paths(base, task),
    }


def render_environment(env: dict) -> str:
    """The compact `[environment]` block; empty when the probe had no root."""
    root = str(env.get("root") or "")
    if not root:
        return ""
    lines = ["[environment]", "root: " + root]
    git_root = env.get("git_root")
    lines.append("git: " + (str(git_root) if git_root else "none"))
    top = env.get("top") or []
    if top:
        lines.append("top: " + " ".join(top))
    for row in env.get("task_paths") or []:
        if row.get("use"):
            lines.append("task path %s -> not here; use %s" % (row["given"], row["use"]))
        else:
            lines.append("task path %s -> not found" % row["given"])
    return "\n".join(lines)


def resolve_path(root: str | Path, given: str) -> dict | None:
    """None when `given` exists under `root`; otherwise the stripped candidate or a missing row."""
    base = Path(root).expanduser().resolve()
    value = given.strip().strip("\"'`").rstrip(".,:;)")
    if not value:
        return None
    if (base / value).exists():
        return None
    parts = [part for part in value.strip("/").split("/") if part and part != "."]
    for index in range(1, len(parts)):
        candidate = "/".join(parts[index:])
        if (base / candidate).exists():
            return {"given": value, "use": candidate}
    return {"given": value, "missing": True}


def _task_paths(root: Path, task: str) -> list[dict]:
    """Resolve every path-like token in the task once; keep only the mismatches."""
    out: list[dict] = []
    seen: set[str] = set()
    for match in _PATH.finditer(task or ""):
        value = match.group(1).rstrip(".,:;)")
        if not value or value in seen or value.startswith(("http", "//")):
            continue
        seen.add(value)
        row = resolve_path(root, value)
        if row is not None:
            out.append(row)
    return out


def _git_root(root: Path) -> str | None:
    """The work-tree root from `git rev-parse`, or None when git is absent/not a repository."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _top(root: Path) -> list[str]:
    """A capped, sorted top-level listing with directories marked and generated trees skipped."""
    try:
        entries = sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except OSError:
        return []
    out: list[str] = []
    for entry in entries:
        if entry.name in _SKIP:
            continue
        out.append(entry.name + ("/" if entry.is_dir() else ""))
        if len(out) >= _TOP:
            break
    return out
