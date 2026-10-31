"""Reply parsing: model text -> Action. Used by ReplLoop only.

WHAT IT IS
  Models emit Python in many shapes: a clean ```python fence, a bare indented block, DSML/XML
  tool markup, an `ANSWER:` line, or a truncated reply. compile_reply() normalises all of
  them into a single `Action`.

ENTRY POINTS
  Action(kind, code, value, source, error, detail)
    kind: "code" | "answer" | "none"
  compile_reply(text) -> Action        the only entry point callers need
  parse_markup(text) -> Action | None  DSML/XML-ish tool calls, repaired into Python
  _call_source(name, params, body)     markup call -> python source

INVARIANTS
  - Never raise: an unparsable reply is an Action with kind="none" and `error`/`detail`
    explaining what was missing, so the loop can tell the model instead of crashing.
  - A recognised tool call with no runnable body is kind="none" with source="markup" — the
    loop's note then asks for a plain ```python block.
  - `ANSWER: <value>` wins only when there is no code block (`_blocks` runs first).
  - Alias map (`ALIASES`, `CALL_NAMES`, `INT_PARAMS`, `BOOL_PARAMS`) is the only place that
    knows model-specific spellings; extend it there.

WHERE TO CHANGE
  A model's new markup dialect .... MARKUP / TAG / INVOKE / PARAM + parse_markup
  Tool-name aliases ................ CALL_NAMES, ALIASES
  Fence / ANSWER detection ......... FENCE_OPEN, ANSWER_LINE, _blocks
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Action:
    kind: str
    code: str | None = None
    value: str | None = None
    error: str | None = None
    detail: str = ""
    source: str = ""
    raw: str = field(default="", repr=False)


FENCE_OPEN = re.compile(r"(?m)^[ \t]*(`{3,})[ \t]*(?:python|py)?[ \t]*\r?\n")
ANSWER_LINE = re.compile(r"(?im)^[ \t]*ANSWER:[ \t]*(.+?)[ \t]*$")
INVOKE = re.compile(
    r"<[^<>]*?invoke\s+name\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</[^<>]*?invoke\s*>",
    re.S,
)
PARAM = re.compile(
    r"<[^<>]*?parameter\s+name\s*=\s*[\"']([^\"']+)[\"']([^>]*)>(.*?)</[^<>]*?parameter\s*>",
    re.S,
)
MARKUP = re.compile(r"<[｜|]{0,2}(?:DSML|llm)[｜|:][^>]*>", re.I)
TAG = re.compile(r"</?[^<>]*?>")

CALL_NAMES = {"python", "py", "run", "read", "grep", "map", "write", "edit", "kernel", "exec"}
ALIASES = {
    "python": "python", "py": "python", "code": "python", "kernel": "python", "exec": "python",
    "bash": "run", "shell": "run", "sh": "run", "terminal": "run", "command": "run",
    "think": None, "reasoning": None, "final_answer_placeholder": None,
}
INT_PARAMS = {"offset", "limit", "max_results", "timeout", "max_files"}
BOOL_PARAMS = {"all", "regex", "raw", "overwrite"}


def _strip(text: str) -> str:
    return MARKUP.sub("", text or "").strip()


def _literal(token: str, flags: str) -> Any:
    token = token.strip()
    if 'string="false"' in flags or "string='false'" in flags:
        try:
            return int(token)
        except ValueError:
            try:
                return float(token)
            except ValueError:
                return token
    return token


def _call_source(name: str, params: dict[str, Any], body: str) -> str:
    lowered = ALIASES.get(name.lower(), name.lower())
    if lowered is None:
        return ""
    if lowered == "python":
        source = str(params.get("code") or params.get("source") or "")
        if not source.strip():
            source = TAG.sub("", body)
        return source
    if lowered not in CALL_NAMES:
        return ""
    if lowered == "run" and not params:
        command = TAG.sub("", body).strip()
        return f"print(run({command!r}))" if command else ""
    args = [f"{key}={value!r}" for key, value in params.items()]
    return f"print({lowered}({', '.join(args)}))"


THINK_ONLY = re.compile(r"<[^<>]*?invoke\s+name\s*=\s*[\"'](?:think|reasoning)[\"']", re.I)
PLACEHOLDER = re.compile(r"<final_answer_placeholder>", re.I)
ORPHAN_PARAM = re.compile(r"<[^<>]*?parameter\s+name\s*=\s*[\"']", re.I)


def parse_markup(text: str) -> Action | None:
    if "DSML" not in text and "invoke name" not in text:
        return None
    blocks = list(INVOKE.finditer(text))
    if not blocks:
        return None
    bodies: list[str] = []
    if "```" in text:
        for _, chunk in _blocks(text):
            bodies.append(chunk)
    for block in blocks:
        name = block.group(1)
        params: dict[str, Any] = {}
        for param in PARAM.finditer(block.group(2)):
            params[param.group(1)] = _literal(param.group(3), param.group(2))
        source = _call_source(name, params, block.group(2))
        if not source.strip():
            continue
        bodies.append(source)
    if not bodies:
        if THINK_ONLY.search(text):
            return Action("none", error="reasoning-only reply; no code ran",
                          detail="a think block is not an action. Reply with one ```python block.",
                          source="markup", raw=text)
        return Action("none", error="tool markup with no executable body",
                      detail="the call carried no code and no command. Reply with one ```python block.",
                      source="markup", raw=text)
    code = "\n\n".join(part.strip() for part in bodies if part.strip())
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return Action("none", error=f"SyntaxError: {exc.msg} (line {exc.lineno})",
                      detail=code[-400:], source="markup", raw=text)
    return Action("code", code=code, source="markup", raw=text)


def _blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for opening in FENCE_OPEN.finditer(text):
        ticks = opening.group(1)
        start = opening.end()
        closes = list(re.finditer(re.escape(ticks) + r"[ \t]*(?=\r?\n|$)", text[start:]))
        if not closes:
            continue
        for close in closes:
            blocks.append((opening.group(0), text[start:start + close.start()]))
    return blocks


def compile_reply(text: str) -> Action:
    body = text or ""
    blocks = _blocks(body)
    if blocks:
        parsed: list[str] = []
        first_code = ""
        for _, chunk in blocks:
            code = chunk.strip()
            if not first_code:
                first_code = code
            try:
                ast.parse(code)
            except SyntaxError:
                continue
            parsed.append(code)
        if parsed:
            code = max(parsed, key=len)
            return Action("code", code=code, source="fence", raw=body)
        code = first_code
        try:
            ast.parse(code)
        except SyntaxError as exc:
            lines = code.splitlines()
            where = max(0, (exc.lineno or 1) - 2)
            return Action(
                "none",
                error=f"SyntaxError: {exc.msg} (line {exc.lineno}, offset {exc.offset})",
                detail="\n".join(lines[where:where + 3]),
                source="fence",
                raw=body,
            )
    markup = parse_markup(body)
    if markup is not None:
        return markup
    answer = ANSWER_LINE.search(body)
    if answer:
        return Action("answer", value=answer.group(1).strip(), source="answer", raw=body)
    if PLACEHOLDER.search(body):
        return Action("none", error="placeholder, not an answer",
                      detail="final_answer_placeholder is not a value. Call final_answer(<text>) inside a python block.",
                      source="markup", raw=body)
    if ORPHAN_PARAM.search(body):
        return Action("none", error="parameter tags without an invoke name",
                      detail="the reply has <parameter> tags but no <invoke name=\"...\"> wrapper, so the call cannot be reconstructed. Emit one ```python block instead.",
                      source="markup", raw=body)
    cleaned = _strip(body)
    if not cleaned and MARKUP.search(body):
        return Action("none", error="empty tool markup", detail="", source="markup", raw=body)
    return Action("none", error="no code block and no ANSWER line", detail="", source="none", raw=body)
