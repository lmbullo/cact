"""env: deterministic environment card prepended to the first task message.

WHAT IT IS
  One cheap workspace probe: resolved root, git root (or none), capped top-level listing, and
  task-mentioned paths that do not exist here plus the closest path that does.
  render_environment -> compact `[env]` block; ToolLoop.run puts it before the task. Not a tool:
  the model must not spend a turn asking where it is.

ENTRY POINTS
  environment(root, task="", checks=None) -> dict   root, git_root, top, files, checks, task_paths
  render_environment(env) -> str        the `[env]` card, or "" when root is missing
  resolve_path(root, given) -> dict|None  None when the path exists; else {given, use|missing}

INVARIANTS
  no mutation, no network, no model. git probe = short local `git rev-parse`; missing git,
  timeout or non-repository -> None, never an error.
  top/files/checks capped and skip generated trees, so node_modules does not bury real entries
  or the gate plan.
  a task path is reported only when missing: `tui/src/x.ts` under a workspace rooted at `tui`
  -> use=`src/x.ts`; prevents the symlink repair the old mismatch provoked.
  no unverified closest file: not found after stripping leading components -> missing=true.

WHERE TO CHANGE
  card content ................ environment, render_environment
  resolved task paths ......... _PATH, resolve_path
  generated-tree skip list .... _SKIP
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
         "target", ".cache", ".tmp", ".next", "coverage"}
_PATH = re.compile(r"(?<![\w./-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?)")
_TOP = 24
_FILES = 32


def environment(root: str | Path, task: str = "", checks=None) -> dict:
    """The root/git/top/files/checks card plus task paths that do not exist at this root."""
    base = Path(root).expanduser().resolve()
    return {
        "root": str(base),
        "git_root": _git_root(base),
        "top": _top(base),
        "files": _files(base),
        "checks": _checks(checks),
        "task_paths": _task_paths(base, task),
    }


def render_environment(env: dict) -> str:
    """The compact `[env]` block; empty when the probe had no root."""
    root = str(env.get("root") or "")
    if not root:
        return ""
    lines = ["[env]", "root=" + root]
    git_root = env.get("git_root")
    lines.append("git=" + (str(git_root) if git_root else "none"))
    top = env.get("top") or []
    if top:
        lines.append("top=" + " ".join(top))
    files = env.get("files") or []
    if files:
        lines.append("files=" + " ".join(files))
    checks = env.get("checks") or []
    if checks:
        lines.append("checks=" + "; ".join(f"{row['name']}={row['cmd']}" for row in checks))
    for row in env.get("task_paths") or []:
        if row.get("use"):
            lines.append("path %s: use %s" % (row["given"], row["use"]))
        else:
            lines.append("path %s: missing" % row["given"])
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


def _files(root: Path) -> list[str]:
    """A capped, sorted path list at depth <= 2, skipping generated and hidden trees."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        rel = here.relative_to(root)
        dirnames[:] = sorted(name for name in dirnames
                             if name not in _SKIP and not name.startswith("."))
        if len(rel.parts) >= 2:
            dirnames[:] = []
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = (rel / name).as_posix() if rel.parts else name
            out.append(path)
            if len(out) >= _FILES:
                return out
    return out


def _checks(checks) -> list[dict]:
    """Normalize `[{name, cmd}]` or CheckSpec objects to at most eight plan rows."""
    rows: list[dict] = []
    for item in checks or ():
        name = str(getattr(item, "name", "") or (item.get("name") if isinstance(item, dict) else "") or "")
        cmd = str(getattr(item, "cmd", "") or (item.get("cmd") if isinstance(item, dict) else "") or "")
        if name and cmd:
            rows.append({"name": name, "cmd": cmd})
        if len(rows) >= 8:
            break
    return rows


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
