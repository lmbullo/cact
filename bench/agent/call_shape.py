"""call_shape: repair the tool-call shapes deepseek-flash actually emits.

WHAT IT IS
  One pure rewrite before dispatch: `exec(command=...)` -> `shell`, `exec(code=...)` -> `python`,
  `exec(argv=[bash,-c,...])` -> `shell`, `read(_OBS[n])` -> `page`, a truncated `_OBS[n`
  handle closed, `git add -A` -> `git add`, and `edit(path, old, new)` -> `edit(edits=[...])`.
  The model keeps one generic process/edit shape in mind; this turns that shape into the typed
  signature instead of spending a turn on bad_arguments.

ENTRY POINTS
  repair(name, args, allows=None) -> Repair(name, args, note)
  Repair  .name / .args / .note / .changed

INVARIANTS
  pure: no kernel, no scope, no I/O; the same call -> the same repair.
  only a shape is repaired, never a missing value: `command` becomes `shell`; `code` becomes
  the python cell (when that tool is allowed); an argv bash -c string becomes shell. An unknown
  key is left for the kernel's bad_args answer.
  `note` is one line, and only when something changed; the loop prefixes it to the observation.
  `allows` is a set/frozenset; when it excludes the repaired target, the call is left untouched.

WHERE TO CHANGE
  a new observed model slip ... repair()
  what a repair says ......... the note strings in repair()
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_OBS = re.compile(r"^_OBS\[\d+\]$")
_OBS_OPEN = re.compile(r"^_OBS\[\d+$")


@dataclass
class Repair:
    """The call after normalization: name, arguments, and the one-line note shown to the model."""

    name: str
    args: dict
    note: str = ""
    changed: bool = False


def _bash_string(argv) -> str | None:
    """The command string when argv is `[shell, -c|-lc, string]`; otherwise None."""
    if not isinstance(argv, (list, tuple)) or len(argv) < 3:
        return None
    program = str(argv[0]).rsplit("/", 1)[-1]
    if program not in _SHELLS:
        return None
    flags = [str(item) for item in argv[1:] if str(item).startswith("-")]
    if not any(flag == "-c" or ("c" in flag[1:] and not flag.startswith("--")) for flag in flags):
        return None
    tail = [item for item in argv[1:] if not str(item).startswith("-")]
    return str(tail[-1]) if tail else None


def parse_args(raw) -> tuple[dict, str]:
    """Parse a tool-call argument string; repair the common trailing-text JSON slip."""
    if isinstance(raw, dict):
        return dict(raw), ""
    text = str(raw or "{}")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        if "Extra data" not in str(exc):
            raise
        value, _end = json.JSONDecoder().raw_decode(text)
        if not isinstance(value, dict):
            raise
        return value, "ignored text after the JSON object"
    if not isinstance(value, dict):
        raise ValueError("arguments must be a JSON object")
    return value, ""


def _closed_obs(value) -> str | None:
    """`_OBS[23` -> `_OBS[23]`; None when it is already closed or not a handle at all."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text + "]" if _OBS_OPEN.match(text) else None


def repair(name: str, args: dict, allows=None) -> Repair:
    """Rewrite one tool call to the typed signature; leave anything uncertain alone."""
    call = dict(args or {})
    allowed = set(allows) if allows is not None else None

    if name == "page":
        closed = _closed_obs(call.get("handle"))
        if closed is not None:
            note = "page handle %r ran as %r" % (call["handle"], closed)
            call["handle"] = closed
            return Repair(name, call, note, True)

    if name == "exec":
        if "commands" in call:
            if "command" in call and "argv" not in call and "shell" not in call:
                call.pop("command")
                return Repair(name, call, "exec(command=...) was ignored because commands=... "
                                          "was present", True)
            return Repair(name, call)
        if "command" in call and not any(key in call for key in ("argv", "shell", "code")):
            call["shell"] = call.pop("command")
            return Repair(name, call, "exec(command=...) ran as shell=<string>", True)
        if "code" in call and not any(key in call for key in ("argv", "shell", "commands")):
            code = call.pop("code")
            if (allowed is None or "python" in allowed or "code" in allowed) and isinstance(code, str):
                return Repair("python", {"code": code},
                              "exec(code=...) ran as the python cell", True)
            return Repair(name, call)
        shell = _bash_string(call.get("argv"))
        if shell is not None and "shell" not in call:
            call.pop("argv", None)
            call["shell"] = shell
            return Repair(name, call, "exec(argv=[shell, -c, ...]) ran as shell=<string>", True)
        return Repair(name, call)

    if name == "read" and isinstance(call.get("path"), str):
        note = ""
        closed = _closed_obs(call["path"])
        if closed is not None:
            note = "read handle %r ran as %r" % (call["path"], closed)
            call["path"] = closed
        if _OBS.match(call["path"].strip()):
            handle = call["path"].strip()
            if allowed is None or "page" in allowed:
                page = {"handle": handle}
                for key in ("offset", "limit", "field"):
                    if key in call:
                        page[key] = call[key]
                return Repair("page", page, note or "read(_OBS[n]) ran as page(handle)", True)
        if note:
            return Repair(name, call, note, True)
        return Repair(name, call)

    if name == "git" and str(call.get("action") or "").lower() == "add":
        paths = call.get("paths")
        if isinstance(paths, list):
            flags = [str(item) for item in paths if str(item).startswith("-")]
            if "-A" in flags or "--all" in flags:
                kept = [item for item in paths if str(item) not in ("-A", "--all")]
                call["paths"] = kept
                return Repair(name, call, "git add(-A) ran as git add with the flag removed", True)
        return Repair(name, call)

    if name == "edit":
        if "edits" not in call and all(key in call for key in ("path", "old", "new")):
            call = {"edits": [{"path": call["path"], "old": call["old"], "new": call["new"]}]}
            return Repair(name, call, "edit(path, old, new) ran as edit(edits=[...])", True)
        if isinstance(call.get("edits"), dict):
            call["edits"] = [call["edits"]]
            return Repair(name, call, "edit(edits={...}) ran as edit(edits=[{...}])", True)
        return Repair(name, call)

    return Repair(name, call)
