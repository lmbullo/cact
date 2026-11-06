"""verbs: the typed verbs (map/read/search/splice/exec/outline/help) through Kernel.call.

WHAT IT IS
  The typed-call half of the action space on the same kernel as `files`: map with include and
  paging, a batch read and its page unit, a repeated read as a pointer (and a commit
  invalidating it), search by path list / serialized list / exact long line / context merge /
  locators / count / include+ignore_case, splice, exec by argv and by command list, the
  refusal codes (shell_string, bad_arguments, shell_via_exec, mutation_via_exec), and outline
  for Markdown, Python, HTML and HTML-in-a-JS-template, plus help().

ENTRY POINTS
  run(check, root, kernel)   called by bench/smoke.py::main after files

INVARIANTS
  - Each check asserts both the answer (`cell.stdout`) and the structured part it came from
    (`cell.result`/`cell.envelope`), because an agent reads the envelope, not the printout.
  - The exec refusals assert the *code* (`shell_via_exec`, `mutation_via_exec`), not the prose.
  - It depends on `files` having run first on the same kernel instance.

WHERE TO CHANGE
  A verb's answer shape .................. kernel/prelude.py (or run_prelude.py for exec)
  The refusal codes ...................... run_prelude.py `_mutating_command` / exec_argv
  The envelope ........................... bench/agent/envelope.py
"""
from __future__ import annotations

import bench.agent.state_loop as state_loop


def run(check, root, kernel):
    """The typed verbs through Kernel.call: map/read/search/splice/exec/outline/help."""
    kernel.run("write('call_a.txt', 'one\\ntwo\\n'); write('call_b.txt', 'red\\nblue\\n')")
    cell = kernel.call("map", {"path": ".", "include": "*.txt"})
    check("kernel: call(map) lists files without a shell and filters include",
          cell.tool == "map" and "call_a.txt" in cell.stdout
          and "outline.md" not in cell.stdout
          and "secret.txt" not in cell.stdout
          and "skip.generated" not in cell.stdout,
          (cell.stdout + cell.stderr)[:240])
    cell = kernel.call("read", {"path": "call_a.txt", "start": 1, "end": 1})
    check("kernel: a typed call carries the page envelope",
          cell.envelope is not None and cell.envelope.get("page", {}).get("unit") == "line"
          and cell.envelope.get("handle", "").startswith("_OBS["),
          str(cell.envelope)[:240])
    card, visible = state_loop.render_observation(cell, 1, 0, None, 600, {})
    check("state: a typed observation is rendered verbatim, not digested",
          "[observe] read ok" in card and "[page]" in card and "[data]" in card
          and "[stdout]" not in card and visible.get("call_a.txt") == [[1, 1]],
          card[:240])
    cell = kernel.call("map", {"path": "call_a.txt"})
    check("kernel: map of a file returns that row, not the root",
          '"total_files": 1' in (cell.result or "") and "call_a.txt" in cell.stdout
          and "call_b.txt" not in cell.stdout,
          (cell.stdout + cell.result or "")[:240])
    cell = kernel.call("map", {"path": ".", "include": "*.txt", "max_files": 1})
    check("kernel: map pages its file list",
          '"has_more": true' in (cell.result or "") and '"next"' in (cell.result or ""),
          (cell.result or "")[:240])
    cell = kernel.call("outline", {"path": ["call_a.txt", "call_b.txt"]})
    check("kernel: outline accepts a path list",
          cell.result is not None and '"kind": "outlines"' in (cell.result or "")
          and "call_a.txt" in cell.stdout, (cell.result or "")[:240])
    cell = kernel.call("recall", {"pattern": "call_a", "limit": 2})
    check("kernel: recall is a typed session search",
          cell.result is not None and "call_a" in cell.stdout,
          (cell.stdout + (cell.result or ""))[:240])
    cell = kernel.call("read", {"ranges": [
        {"path": "call_a.txt", "start": 1, "end": 1},
        {"path": "call_b.txt", "start": 2, "end": 2},
    ], "repeat": True})
    check("kernel: read batches different ranges",
          "call_a.txt" in cell.stdout and "call_b.txt" in cell.stdout
          and "one" in cell.stdout and "blue" in cell.stdout,
          (cell.stdout + cell.stderr)[:240])
    cell = kernel.call("read", {"path": ["call_a.txt", "call_b.txt"]})
    check("kernel: call(read) runs a typed verb through the same pipeline",
          cell.tool == "read" and "call_a.txt" in cell.stdout
          and cell.reads.get("call_a.txt", {}).get("total") == 2, (cell.stdout + cell.stderr)[:200])
    cell = kernel.call("read", {"path": "call_a.txt", "start": 1, "end": 1})
    check("kernel: call(read) points at a range already delivered",
          "already delivered" in cell.stdout and (cell.result or "").find("already_read") >= 0,
          (cell.stdout + cell.stderr)[:220])
    cell = kernel.call("read", {"path": "call_a.txt", "start": 1, "end": 1, "repeat": True})
    check("kernel: call(read) repeat=True re-delivers the range",
          "one" in cell.stdout and "two" not in cell.stdout,
          (cell.stdout + cell.stderr)[:200])
    kernel.run(r"write('dedup.txt', 'alpha\n')")
    kernel.call("read", {"path": "dedup.txt"})
    cell = kernel.call("read", {"path": "dedup.txt"})
    check("kernel: a repeated read is a pointer",
          "already delivered" in cell.stdout and not cell.reads
          and cell.envelope.get("page", {}).get("pointer") is True
          and cell.envelope.get("page", {}).get("shown") == 0,
          (cell.stdout + str(cell.envelope))[:300])
    kernel.call("edit", {"edits": [{"path": "dedup.txt", "old": "alpha", "new": "beta"}]})
    cell = kernel.call("edit", {"edits": [
        {"old": "beta", "new": "B"},
        {"path": "dedup.txt", "old": "", "new": "C"},
    ]})
    failed = cell.result or ""
    check("kernel: a malformed edit batch names every bad item",
          "missing path" in failed and "non-empty string" in failed
          and '"index": 0' in failed and '"index": 1' in failed
          and (root / "dedup.txt").read_text() == "beta\n",
          failed[:420])
    cell = kernel.call("read", {"path": "dedup.txt"})
    check("kernel: a commit invalidates the read pointer",
          "beta" in cell.stdout and "already delivered" not in cell.stdout,
          (cell.stdout + cell.stderr)[:220])
    cell = kernel.call("search", {"pattern": "red", "path": ["call_a.txt", "call_b.txt"]})
    check("kernel: call(search) searches a path list",
          "call_b.txt:1" in cell.stdout, (cell.stdout + cell.stderr)[:200])
    cell = kernel.call("search", {"pattern": "alpha", "path": "['a.txt']"})
    check("kernel: search accepts a serialized path list",
          "a.txt:1" in cell.stdout, (cell.stdout + cell.stderr)[:200])
    kernel.run(r"write('longline.txt', 'prefix ' + 'x' * 400 + ' END\n')")
    cell = kernel.call("search", {"pattern": "END", "path": "longline.txt"})
    check("kernel: search delivers exact line text, not a 200-char clip",
          "END" in cell.stdout and "x" * 400 in cell.stdout
          and "END" not in (cell.result or ""),
          (cell.stdout + cell.stderr)[:240])
    kernel.run(r"write('merge.txt', 'aa\nbb\ncc\n')")
    cell = kernel.call("search", {"pattern": "a|b", "path": "merge.txt", "context": 2})
    check("kernel: search merges overlapping context lines",
          cell.stdout.count("cc") == 1 and cell.stdout.count("aa") == 1,
          (cell.stdout + cell.stderr)[:240])
    kernel.run(r"write('many.txt', '\n'.join('row %d value' % n for n in range(30)))")
    cell = kernel.call("search", {"pattern": "row", "path": "many.txt",
                                  "context": 1, "max_chars": 100})
    check("kernel: broad search falls back to locators",
          '"text_elided": true' in (cell.result or "")
          and "row" in cell.stdout and "text_chars" in (cell.result or "")
          and '"total": 30' in (cell.result or ""),
          (cell.result or "")[:260])
    cell = kernel.call("search", {"pattern": "one", "path": "call_a.txt", "count": True})
    check("kernel: search count mode returns per-file counts, not hit text",
          "search_counts" in (cell.result or "") and "call_a.txt:1" in cell.stdout,
          (cell.stdout + (cell.result or ""))[:240])
    cell = kernel.call("search", {"pattern": "RED", "path": ".", "include": "*.txt",
                                  "context": 1, "ignore_case": True})
    check("kernel: call(search) filters include and adds context",
          "call_b.txt:1:" in cell.stdout and "call_b.txt:2-" in cell.stdout
          and "call_b.txt:2- blue" in cell.stdout, (cell.stdout + cell.stderr)[:300])
    cell = kernel.call("splice", {"path": "call_a.txt", "start": 2, "end": 2, "text": "TWO"})
    check("kernel: call(splice) journals through the typed path",
          cell.tool == "splice" and "call_a.txt" in cell.changed
          and "TWO" in (root / "call_a.txt").read_text(), (cell.stdout + cell.stderr)[:200])
    cell = kernel.call("exec", {"argv": ["echo", "one"]})
    check("kernel: call(exec) runs a program by argv",
          cell.tool == "exec" and "one" in cell.stdout and not cell.failed,
          (cell.stdout + cell.stderr)[:200])
    cell = kernel.call("exec", {"commands": [["echo", "one"], ["echo", "two"]]})
    check("kernel: call(exec) runs several argv commands",
          cell.tool == "exec" and "one" in cell.stdout and "two" in cell.stdout,
          (cell.stdout + cell.stderr)[:200])
    cell = kernel.call("exec", {"argv": "echo one"})
    check("kernel: call(exec) refuses a shell string",
          "shell_string" in (cell.result or ""), (cell.result or "")[:200])
    cell = kernel.call("exec", {"code": "print(1)"})
    check("kernel: call(exec) turns a bad keyword into bad_args",
          "bad_arguments" in (cell.result or "") and not cell.failed,
          (cell.result or "")[:240])
    check("kernel: a bad keyword answer carries the accepted signature",
          all(word in (cell.result or "") for word in ("argv", "commands", "shell", "valid keywords"))
          and "signature" in (cell.result or ""), (cell.result or "")[:420])
    cell = kernel.call("exec", {"argv": ["bash", "-lc", "echo one"]})
    check("kernel: call(exec) points a shell -c string at the shell field",
          "shell_via_exec" in (cell.result or "") and "exec(shell=" in (cell.result or "")
          and not cell.stdout, (cell.result or "")[:260])
    cell = kernel.call("exec", {"shell": "echo one"})
    check("kernel: call(exec) runs an explicit shell string",
          "one" in cell.stdout and cell.tool == "exec", (cell.stdout + cell.stderr)[:200])
    cell = kernel.call("exec", {"argv": ["sed", "-i", "s/one/1/", "call_a.txt"]})
    check("kernel: call(exec) refuses a mutating program",
          "mutation_via_exec" in (cell.result or "") and "one" in (root / "call_a.txt").read_text(),
          (cell.result or "")[:200])
    (root / "outline.md").write_text("# Title\n\n## Section\ntext\n", encoding="utf-8")
    (root / "outline.py").write_text("class A:\n    def m(self):\n        pass\n\ndef top():\n    pass\n", encoding="utf-8")
    cell = kernel.call("outline", {"path": "outline.md"})
    check("kernel: call(outline) maps markdown headings",
          "h1 Title" in cell.stdout and "h2 Section" in cell.stdout,
          (cell.stdout + cell.stderr)[:220])
    cell = kernel.call("outline", {"path": "outline.md"})
    check("kernel: a Markdown parent heading owns its subtree",
          "1-4: h1 Title" in cell.stdout and "3-4: h2 Section" in cell.stdout,
          cell.stdout[:200])
    cell = kernel.call("outline", {"path": "outline.py"})
    check("kernel: call(outline) maps python defs and methods",
          "1: class A" in cell.stdout and "method A.m" in cell.stdout
          and "function top" in cell.stdout, (cell.stdout + cell.stderr)[:220])
    cell = kernel.call("outline", {"path": "outline.md", "max_entries": 1})
    check("kernel: call(outline) respects max_entries",
          "Title" in cell.stdout and "Section" not in cell.stdout, cell.stdout[:160])
    (root / "outline.html").write_text(
        '<header id="top">\n  <h1>Title</h1>\n</header>\n'
        '<section class="card" id="hero">\n  <h2>Question</h2>\n</section>\n',
        encoding="utf-8")
    cell = kernel.call("outline", {"path": "outline.html"})
    check("kernel: call(outline) maps HTML sections and headings",
          "section#hero" in cell.stdout and "heading h2: Question" in cell.stdout
          and "header#top" in cell.stdout, (cell.stdout + cell.stderr)[:240])
    (root / "outline.js").write_text(
        'const view = `\n<section id="main">\n  <h2>Inside JS</h2>\n</section>\n`\n',
        encoding="utf-8")
    cell = kernel.call("outline", {"path": "outline.js"})
    check("kernel: call(outline) sees HTML landmarks in a JS template",
          "section#main" in cell.stdout and "heading h2: Inside JS" in cell.stdout,
          (cell.stdout + cell.stderr)[:240])
    cell = kernel.run("print(help()['text'])")
    check("kernel: help() documents the typed tools",
          "exec(" in cell.stdout and "edit(" in cell.stdout,
          (cell.stdout + cell.stderr)[:260])
    cell = kernel.run("print(help('read')['text'])")
    check("kernel: help(topic) returns live signatures",
          "read(" in cell.stdout, (cell.stdout + cell.stderr)[:200])
    kernel.run("undo('call_a.txt'); undo('call_b.txt')")
