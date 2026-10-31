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


