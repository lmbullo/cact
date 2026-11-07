"""live: failure-mode evals that need a model, each with a hidden oracle and a cost row.

WHAT IT IS
  The cases a scripted client cannot judge: does the model read the contract instead of the
  visible tests, find latent bugs, carry a fact across a compaction, follow an on-demand
  skill, accept a skeptic's objection, and delegate instead of filling its own context. Every
  case has a deterministic oracle from `oracle.py`; the model's prose is never the verdict.
  These cases are opt-in (`--live`) because they cost tokens and need a key.

ENTRY POINTS
  LIVE_CASES   the list `runner` registers; each is a `types.Case` with kind="live"
  run_live(ws, client, prompt, **kwargs) -> (RunResult, ToolLoop)   the shared constructor

INVARIANTS
  - One case, one workspace, one kernel child, stopped in a `finally`. A live case never
    shares a budget or a workspace with another case.
  - The goal is the oracle, not the visible test: a green visible suite is a fixture detail,
    not a pass. `goal_stops=False` keeps the run going after a failing finish, so the row
    measures the model's repair behavior.
  - The row carries `steps`, `usage` and `stop`; a pass with a runaway token count is still
    reported, because cost is part of the result. `Budget.max_total_tokens` is the hard cap.
  - A provider failure is a failed row, not a crash of the suite. The runner catches it.
  - The prompts name the artifact and the expected shape; they do not name the answer. A live
    eval that can be passed from prior knowledge is not measuring the agent.

WHERE TO CHANGE
  A new live behavior ....... a case here plus an oracle
  The prompts ............... each case body
  The budgets ............... the `Budget` each case builds
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..agent.docs import DocsClient
from ..agent.loop import Budget
from ..agent.skeptic import Skeptic
from ..agent.subagent import Subagent
from .fixtures import (bug_hunt_tree, docs_tree, hidden_spec_tree, long_tree, mutation_tree,
                       quality_tree, skill_tree)
from .loops import run_loop
from .oracle import answer_eq, calc_add, calc_spec, has_text, ledger, ruff_clean, slugify_spec
from .types import Case, CaseResult, Context


def run_live(ws: Path, client, prompt: str, **kwargs):
    """Build one live loop, run it to a stop, and always stop its kernel child."""
    loop, kernel = run_loop(ws, client, **kwargs)
    try:
        result = loop.run("live", prompt, None)
    finally:
        kernel.stop()
    return result, loop


def _row(ok: bool, detail: str, result, loop=None) -> CaseResult:
    return CaseResult(
        ok=ok, detail=detail, stop=result.stop, steps=result.steps,
        usage=result.usage, final=result.final, errors=result.errors,
        meta={"compactions": getattr(loop, "compactions", 0)} if loop is not None else {},
    )


def case_spec(ctx: Context) -> CaseResult:
    """The visible test is green; SPEC requires `mul` and `clamp` as well."""
    ws = hidden_spec_tree(ctx.workspace)
    prompt = ("Read SPEC.md, make the implementation satisfy it, and verify the whole contract. "
              "The visible test is incomplete. Reply with a short report when it is complete.")
    result, loop = run_live(ws, ctx.client_factory(), prompt,
                        goal=lambda: calc_spec(ws)[0], autonomy=True, budget=Budget(max_steps=20,
                                                                    max_total_tokens=120_000))
    passed, detail = calc_spec(ws)
    return _row(passed, f"stop={result.stop} oracle={detail}", result, loop)


def _case_bug(bug: str):
    """Build one focused latent-bug case: the oracle checks only `bug`."""

    def case(ctx: Context) -> CaseResult:
        """Find and fix one behavioral bug behind a green visible suite."""
        ws = bug_hunt_tree(ctx.workspace)
        prompt = ("The visible tests pass but one function in ledger/ is wrong. Read BUGS.md "
                  f"for the intended behavior, fix the {bug} behavior, do not edit tests, "
                  "and verify the package.")
        result, loop = run_live(ws, ctx.client_factory(), prompt,
                            goal=lambda: ledger(ws, bug)[0], autonomy=True,
                            budget=Budget(max_steps=18, max_total_tokens=120_000))
        passed, detail = ledger(ws, bug)
        return _row(passed, f"stop={result.stop} oracle={detail}", result, loop)

    return case


def case_long_context(ctx: Context) -> CaseResult:
    """A fact read early must survive a forced compaction while the model scans noise/."""
    ws = long_tree(ctx.workspace)
    prompt = ("Read every file under noise/ to audit them, then report DEPLOY_CODE from "
              "fact.txt. Reply with only the code.")
    result, loop = run_live(ws, ctx.client_factory(), prompt, state=True,
                        context_budget=6_000, keep_messages=6, autonomy=True,
                        budget=Budget(max_steps=30, max_total_tokens=150_000))
    passed, detail = answer_eq(result.final, "ORBIT-7419")
    return _row(passed, f"stop={result.stop} compactions={loop.compactions} {detail}", result, loop)


def case_skill(ctx: Context) -> CaseResult:
    """The release procedure lives in .agent/skills; follow it without editing VERSION."""
    ws = skill_tree(ctx.workspace)
    prompt = ("Release version 2.4.1. Follow the project's own release procedure. "
              "Do not guess the steps and do not edit VERSION.")
    result, loop = run_live(ws, ctx.client_factory(), prompt,
                        autonomy=True, budget=Budget(max_steps=15, max_total_tokens=80_000))
    changed, detail = has_text(ws, "CHANGELOG.md", "2.4.1")
    version = (ws / "VERSION").read_text(encoding="utf-8").strip()
    return _row(changed and version == "2.4.1",
                f"stop={result.stop} changelog={detail} version={version}", result, loop)


def case_skeptic(ctx: Context) -> CaseResult:
    """With no deterministic goal, a read-only skeptic must catch the incomplete contract."""
    ws = hidden_spec_tree(ctx.workspace)
    prompt = ("Read SPEC.md and make the implementation satisfy it. The visible test is "
              "incomplete. Reply when you believe the contract is complete.")
    skeptic = Skeptic(ctx.client_factory(), ws)
    result, loop = run_live(ws, ctx.client_factory(), prompt, skeptic=skeptic, autonomy=True,
                        budget=Budget(max_steps=20, max_total_tokens=140_000))
    passed, detail = calc_spec(ws)
    return _row(passed, f"stop={result.stop} oracle={detail}", result, loop)


def case_delegate(ctx: Context) -> CaseResult:
    """The model is told to delegate the scan instead of reading noise/ into its own context."""
    ws = long_tree(ctx.workspace)
    prompt = ("Use delegate to have a read-only subagent find DEPLOY_CODE in fact.txt at the "
              "workspace root; it may scan noise/ for context. Do not read noise/ yourself. "
              "Reply with only the code.")
    child = Subagent(ctx.client_factory(), ws, max_steps=8, max_total_tokens=60_000)
    result, loop = run_live(ws, ctx.client_factory(), prompt, subagent=child, autonomy=True,
                        budget=Budget(max_steps=15, max_total_tokens=100_000))
    passed, detail = answer_eq(result.final, "ORBIT-7419")
    return _row(passed, f"stop={result.stop} {detail}", result, loop)


def case_mutation(ctx: Context) -> CaseResult:
    """A normal repair through the journal, with the oracle as the goal."""
    ws = mutation_tree(ctx.workspace)
    prompt = ("Fix calc.py so its behavior satisfies SPEC.md. Verify it. Do not edit tests.")
    result, loop = run_live(ws, ctx.client_factory(), prompt,
                        goal=lambda: calc_add(ws)[0], autonomy=True,
                        budget=Budget(max_steps=15, max_total_tokens=60_000))
    passed, detail = calc_add(ws)
    return _row(passed, f"stop={result.stop} oracle={detail}", result, loop)


ROOT = Path(__file__).resolve().parents[2]


def case_worker(ctx: Context) -> CaseResult:
    """The real fire-and-forget path: tui/python/worker.py on a hidden check, graded by the oracle."""
    ws = hidden_spec_tree(ctx.workspace)
    check = Path(tempfile.mkstemp(prefix="eval-check-", suffix=".py")[1])
    check.write_text(
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "from calc import add, mul, clamp\n"
        "assert add(2, 3) == 5\n"
        "assert mul(4, 3) == 12\n"
        "assert clamp(9, 0, 5) == 5\n"
        "assert clamp(-1, 0, 5) == 0\n" % str(ws),
        encoding="utf-8",
    )
    prompt = ws / "PROMPT.txt"
    prompt.write_text("Read SPEC.md, make the implementation satisfy the whole contract, and verify it. "
                      "The visible test is incomplete. This is unattended: do not ask.", encoding="utf-8")
    state = tempfile.mkdtemp(prefix="eval-state-")
    from ..live import load_key

    env = {**os.environ, "XDG_STATE_HOME": state,
           "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", "") or load_key(),
           "CODE_TUI_AUTONOMY": "1"}
    argv = [sys.executable, str(ROOT / "tui/python/worker.py"),
            "--workspace", str(ws), "--prompt-file", str(prompt),
            "--model", str(ctx.options.get("model", "deepseek-flash")),
            "--effort", str(ctx.options.get("effort", "low")),
            "--max-steps", "20", "--max-wall", "300",
            "--checks", json.dumps([{"name": "spec", "cmd": f"{sys.executable} {check}",
                                     "cheap": True}])]
    proc = subprocess.run(argv, cwd=str(ROOT), env=env, capture_output=True, text=True,
                          timeout=420)
    events = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith("@@TUI@@"):
            try:
                events.append(json.loads(line[len("@@TUI@@"):]))
            except json.JSONDecodeError:
                continue
    final = next((item for item in reversed(events) if item.get("kind") == "final"), {})
    passed, detail = calc_spec(ws)
    try:
        check.unlink()
    except OSError:
        pass
    usage = final.get("usage") or {}
    ok = final.get("status") == "done" and passed
    return CaseResult(ok=ok, detail=f"status={final.get('status')} stop={final.get('stop')} "
                                    f"oracle={detail} stderr={(proc.stderr or '')[-300:]}",
                      stop=str(final.get("stop") or ""), steps=int(final.get("steps") or 0),
                      usage=usage, final=str(final.get("final") or ""), meta={"events": len(events)})


def case_docs(ctx: Context) -> CaseResult:
    """The exact API rules are only in a documentation page; the agent must fetch them."""
    page = (b"<html><head><title>Slug SDK</title></head><body>"
            b"<h1>slugify</h1><p>Lowercase the text. Replace every run of non-alphanumeric "
            b"characters with one hyphen. Strip leading and trailing hyphens.</p>"
            b"<pre>slugify(' Hello, World! ') == 'hello-world'</pre>"
            b"</body></html>")

    class Handler(BaseHTTPRequestHandler):
        """Serve the SDK page for one live case."""

        def do_GET(self):
            """Return the fixed HTML document."""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            """Silence the per-request stderr line."""
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/slug"
    ws = docs_tree(ctx.workspace, url)
    prompt = ("Implement slugify in slug.py. The exact rules are in SPEC.md and the linked SDK "
              "documentation; read them with docs(url) instead of guessing. Verify the function.")
    try:
        result, loop = run_live(ws, ctx.client_factory(), prompt,
                            docs=DocsClient(cache=False), autonomy=True,
                            goal=lambda: slugify_spec(ws)[0],
                            budget=Budget(max_steps=15, max_total_tokens=100_000))
    finally:
        server.shutdown()
        server.server_close()
    passed, detail = slugify_spec(ws)
    return _row(passed, f"stop={result.stop} oracle={detail}", result, loop)


def case_quality(ctx: Context) -> CaseResult:
    """The project's own lint config is the goal; a correct but dirty tree does not pass."""
    ws = quality_tree(ctx.workspace)
    prompt = ("Make this project pass its own ruff config without changing behavior. "
              "Read ruff.toml, fix the defect, and verify.")
    result, loop = run_live(ws, ctx.client_factory(), prompt, autonomy=True,
                        goal=lambda: ruff_clean(ws)[0],
                        budget=Budget(max_steps=12, max_total_tokens=60_000))
    passed, detail = ruff_clean(ws)
    return _row(passed, f"stop={result.stop} oracle={detail}", result, loop)


LIVE_CASES = [
    Case("A01_spec", "verification", "read the contract, not only the visible test", "live", case_spec),
    *[Case(f"A02_{bug}", "verification", f"find the latent {bug} bug behind a green suite",
           "live", _case_bug(bug)) for bug in ("money", "dates", "text", "stats", "rounding")],
    Case("A03_long_context", "context", "carry a fact across compaction", "live", case_long_context),
    Case("A04_skill", "tooling", "load and follow an on-demand skill", "live", case_skill),
    Case("A05_skeptic", "verification", "a skeptic catches an incomplete contract", "live", case_skeptic),
    Case("A06_delegate", "context", "delegate a scan instead of filling context", "live", case_delegate),
    Case("A07_mutation", "mutation", "repair through the journal, not the tests", "live", case_mutation),
    Case("A08_worker", "autonomy", "the real worker path ends with a verifiable workspace", "live", case_worker),
    Case("A09_docs", "tooling", "read the documentation instead of guessing the API", "live", case_docs),
    Case("A10_quality", "tooling", "the project's own lint config is part of done", "live", case_quality),
]
