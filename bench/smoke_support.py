"""smoke support: the check counter and the offline fakes every smoke group shares.
WHAT IT IS
  The harness `python3 bench/smoke.py` runs on, kept out of the runner so `smoke_checks/` groups
  import it without a cycle: check(), the failure list, a fixture workspace, the offline fakes.
ENTRY POINTS
  check(name, ok, detail); FAILED; workspace(); kernel_for(root, **kw)  started Kernel always stopped
  _reply/_call/_cell        one scripted model reply, tool call, or kernel Cell
  FakeClient, FakeKernel    the offline loop driver: scripted replies, scripted cells
  _run_loop(...)            ToolLoop over the two fakes -> (result, client, kernel)
  _paired_messages(...)     the assistant tool ids and the tool messages answering them
INVARIANTS
  nothing here touches the repo, the network or a real model: workspace() writes only under
  tempfile.mkdtemp and kernel_for runs a real local kernel child in that root.
  check() never raises: one failed assertion must not hide later checks; FAILED is module state,
  so the runner calls summary() once at the end.
  a fake answers exactly what it was scripted for and raises when the script runs out, so an
  extra turn is a failure, not a hang.
WHERE TO CHANGE
  a new shared fake or fixture ..... this module
  a new check group ................ bench/smoke_checks/, then call it in bench/smoke.py::main
  the pass/fail line or exit code ... check(), summary()
"""
from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # script mode: make `bench` importable before the package imports
    sys.path.insert(0, str(ROOT))

from bench.agent.kernel import Cell, Kernel  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """Print one `ok`/`FAIL` line; a failure also records its name for `summary()`."""
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def summary() -> int:
    """Print the tail line and return the process exit code."""
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: " + ", ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


def workspace() -> Path:
    """The temp workspace every post-import check runs in: one file, a gitignore, a skip."""
    root = Path(tempfile.mkdtemp(prefix="cact-smoke-"))
    (root / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored/\n*.generated\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "secret.txt").write_text("secret\n", encoding="utf-8")
    (root / "skip.generated").write_text("generated\n", encoding="utf-8")
    return root


@contextlib.contextmanager
def kernel_for(root, **kwargs):
    """A real kernel child in `root`, stopped on the way out even when a check raises."""
    kernel = Kernel(root, timeout=20, writable=True, prelude="all", allow_run=True, **kwargs)
    try:
        yield kernel
    finally:
        kernel.stop()


# --- a scripted client and kernel, so the turn machine is testable offline -----------------
def _reply(content: str = "", calls: list | None = None, finish: str = "tool_calls") -> dict:
    return {"content": content, "tool_calls": calls or [], "finish": finish}


def _call(cid: str, code: str | None, name: str = "python", arguments: str | None = None) -> dict:
    return {"id": cid,
            "function": {"name": name,
                         "arguments": arguments if arguments is not None else json.dumps({"code": code})}}


def _cell(final: str | None = None, stdout: str = "", stderr: str = "") -> Cell:
    return Cell(stdout=stdout, stderr=stderr, result=None, final=final, duration_ms=1)


class FakeClient:
    """Answers with a scripted reply per turn and keeps a copy of every request."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.requests: list[list[dict]] = []
        self.limits: list[dict] = []

    def complete(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 0,
                 effort=None) -> dict:
        """The next scripted reply; raises when the script is exhausted, so an extra turn is visible."""
        self.requests.append(json.loads(json.dumps(messages)))
        self.limits.append({"max_tokens": max_tokens, "effort": effort})
        if not self.replies:
            raise AssertionError("the loop asked for a reply after the script ran out")
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return {"content": item.get("content") or "", "reasoning": "",
                "tool_calls": item.get("tool_calls") or [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "latency_ms": 0.0, "finish": item.get("finish") or "tool_calls"}


class FakeKernel:
    """Maps a cell's code to a scripted Cell; `raises` makes dispatch blow up."""

    def __init__(self, cells: dict, raises: set[str] | None = None,
                 typed: dict | None = None) -> None:
        self.cells = dict(cells)
        self.raises = set(raises or ())
        self.typed = dict(typed or {})
        self.codes: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def run(self, code: str, check_cache: dict | None = None,
            extra_obs: list | None = None) -> Cell:
        """The scripted Cell for this code (an empty one when unscripted); `raises` makes it blow up."""
        self.codes.append(code)
        if code in self.raises:
            raise RuntimeError("kernel exploded")
        return self.cells.get(code) or _cell()

    def call(self, tool: str, args: dict | None = None, check_cache: dict | None = None,
             extra_obs: list | None = None) -> Cell:
        """The scripted Cell for this typed tool, stamped with the tool name; records the call."""
        self.calls.append((tool, dict(args or {})))
        if tool in self.raises:
            raise RuntimeError("kernel exploded")
        cell = self.typed.get(tool)
        if cell is None:
            cell = _cell()
        if cell.tool is None:
            cell.tool = tool
        return cell


def _run_loop(replies: list[dict], cells: dict, goal=None, raises: set[str] | None = None,
              typed: dict | None = None, scope=None, budget=None):
    from bench.agent import loop, tool_loop
    client = FakeClient(replies)
    kernel = FakeKernel(cells, raises, typed)
    loop_ = tool_loop.ToolLoop(client, kernel, budget or loop.Budget(), goal=goal, scope=scope)
    return loop_.run("smoke", "do the task"), client, kernel


def _paired_messages(messages: list[dict]) -> tuple[list[str], list[str]]:
    assistant = [m for m in messages if m.get("role") == "assistant"][-1]
    ids = [c.get("id") for c in assistant.get("tool_calls") or []]
    paired = [m.get("tool_call_id") for m in messages[messages.index(assistant) + 1:]
              if m.get("role") == "tool"]
    return ids, paired
