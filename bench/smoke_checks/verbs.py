"""verbs: the typed verbs (map/read/search/splice/exec/outline/help) through Kernel.call.

WHAT IT IS
  The typed-call half of the action space on the same kernel as `files`: map with include and
  paging, a batch read and its page unit, a repeated read as a pointer (and a commit
  invalidating it), search by path list / serialized list / exact long line / context merge /
  locators / count / include+ignore_case, splice, exec by argv, by command list and as a bare
  shell string, the refusal codes (bad_arguments, shell_via_exec, mutation_via_exec,
  unknown_verb, outside_root), and outline for Markdown, Python, HTML and HTML-in-a-JS-template,
  plus help() and guide(headers=N). It also pins the paging contract: one error shape with a
  `use` hint, `ok:false` for a miss or an unknown name, `offset` = start of the delivery,
  `next` = resume cursor, `truncated` = a default limit cut it and `complete` = not truncated.
  The boundary checks cover registry scalar coercion (`map(max_files='x')`), a verb that
  raises (`internal_error`, rejected, no false `ok`), and the state card's clipped `[data]`
  (map `dirs_matched`, an edit's `closest`/`context`) and kind/signature refusal line.

ENTRY POINTS
  run(check, root, kernel)   called by bench/smoke.py::main after files

INVARIANTS
  - Each check asserts both the answer (`cell.stdout`) and the structured part it came from
    (`cell.result`/`cell.envelope`), because an agent reads the envelope, not the printout.
  - The exec refusals assert the *code* (`shell_via_exec`, `mutation_via_exec`), not the prose.
  - The contract checks are regression pins for an audit: a defect that comes back fails here.
  - It depends on `files` having run first on the same kernel instance.

WHERE TO CHANGE
  A verb's answer shape .................. the kernel/prelude_*.py fragment that owns it
                                           (or run_prelude.py for exec)
  The refusal codes ...................... run_prelude.py `_mutating_command` / exec_argv
  The envelope ........................... bench/agent/envelope.py
"""
from __future__ import annotations

import bench.agent.state_loop as state_loop
from bench.smoke_support import kernel_for


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
    kernel.run(r"write('indent.md', '- alpha\n  beta\n- gamma\n')")
    cell = kernel.call("edit", {"edits": [{
        "path": "indent.md", "old": "   - alpha\n     beta", "new": "  - ALPHA\n    BETA",
    }]})
    check("kernel: edit accepts a unique leading-indentation-only match",
          '"indentation"' in (cell.result or "")
          and (root / "indent.md").read_text() == "  - ALPHA\n    BETA\n- gamma\n",
          (cell.result or "")[:300])
    kernel.run(r"write('ambiguous.md', '- same\n- same\n')")
    cell = kernel.call("edit", {"edits": [{
        "path": "ambiguous.md", "old": "- same", "new": "no",
    }]})
    check("kernel: edit still refuses a non-unique match",
          (cell.result or "").find('"ambiguous"') >= 0 and cell.rejected
          and (root / "ambiguous.md").read_text() == "- same\n- same\n",
          (cell.result or "")[:300])
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
    cell = kernel.call("exec", {"argv": "echo shell-string"})
    check("kernel: call(exec) runs an argv shell string once, with a note",
          "shell-string" in cell.stdout and not cell.rejected
          and any("argv was a shell string" in note
                  for note in (cell.envelope or {}).get("notes") or []),
          (cell.stdout + (cell.result or ""))[:240])
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
    cell = kernel.call("exec", {"argv": ["grep", "-n", "TWO", "call_a.txt"]})
    notes = " ".join((cell.envelope or {}).get("notes") or [])
    check("kernel: a grep exec carries one note naming the search() call",
          not cell.rejected and "search(pattern, path=[...], max_results=0)" in notes
          and "TWO" in cell.stdout, notes[:280])
    cell = kernel.call("exec", {"argv": ["grep", "-o", "TWO", "call_a.txt"]})
    check("kernel: grep -o gets no note because search cannot express it",
          not cell.rejected
          and not any("search(" in note for note in (cell.envelope or {}).get("notes") or []),
          str((cell.envelope or {}).get("notes"))[:240])
    cell = kernel.call("exec", {"argv": ["cat", "/tmp/cact-smoke-outside.txt"]})
    notes = " ".join((cell.envelope or {}).get("notes") or [])
    check("kernel: a scratch-path cat gets no read() note (read would refuse it)",
          "read(" not in notes, notes[:240])
    cell = kernel.run("a = exec_argv(['grep', '-n', 'TWO', 'call_a.txt']); "
                      "b = exec_argv(['grep', '-n', 'one', 'call_a.txt']); "
                      "print('note' in a, 'note' in b)")
    check("kernel: the duplicate-verb note is not repeated twice in one cell",
          "True False" in cell.stdout, (cell.stdout + cell.stderr)[:240])
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
    check("kernel: help(read) shows the schema's start/end, not offset/limit",
          "start=" in cell.stdout and "offset=" not in cell.stdout, cell.stdout[:240])

    # --- the contract shapes a model parses: one error dict, ok means success, paging ------
    cell = kernel.call("write", {"path": "/tmp/cact-smoke-outside.txt", "content": "x"})
    error = (cell.envelope or {}).get("error") or {}
    check("kernel: every refusal is one error shape with code/message/use/signature",
          cell.rejected and error.get("code") == "outside_root" and error.get("message")
          and error.get("use") and error.get("signature")
          and (cell.envelope or {}).get("kind") == "wrong_call",
          str(cell.result)[:320])
    check("kernel: the outside_root refusal states the one scratch story",
          "workspace-only" in str(error.get("use")), str(error)[:240])
    cell = kernel.call("read", {"path": "definitely-missing.txt"})
    check("kernel: a missing path is not_found, not ok:true",
          not (cell.envelope or {}).get("ok", True)
          and ((cell.envelope or {}).get("error") or {}).get("code") == "not_found",
          str(cell.result)[:240])
    cell = kernel.call("outline", {"path": "definitely-missing.txt"})
    check("kernel: outline of a missing path is not_found",
          not (cell.envelope or {}).get("ok", True)
          and ((cell.envelope or {}).get("error") or {}).get("code") == "not_found",
          str(cell.result)[:240])
    cell = kernel.call("map", {"path": "definitely-missing-dir"})
    check("kernel: map of a missing path is not_found",
          not (cell.envelope or {}).get("ok", True)
          and ((cell.envelope or {}).get("error") or {}).get("code") == "not_found",
          str(cell.result)[:240])
    cell = kernel.call("help", {"topic": "nope"})
    check("kernel: help(unknown topic) is unknown_verb with near names",
          not (cell.envelope or {}).get("ok", True)
          and ((cell.envelope or {}).get("error") or {}).get("code") == "unknown_verb"
          and ((cell.envelope or {}).get("error") or {}).get("available"),
          str(cell.result)[:240])
    cell = kernel.call("nope", {})
    check("kernel: an unknown verb is a refusal, not a traceback",
          cell.rejected and not cell.failed
          and ((cell.envelope or {}).get("error") or {}).get("code") == "unknown_verb",
          (str(cell.result) + cell.stderr)[:240])

    kernel.run("write('tail.txt', 'one\\ntwo\\nthree\\n')")
    cell = kernel.call("write", {"path": "tail.txt", "content": "zzz\n"})
    check("kernel: write refuses any shorter body without force",
          cell.rejected and "shorter_write" in str(cell.result)
          and (root / "tail.txt").read_text() == "one\ntwo\nthree\n",
          str(cell.result)[:280])
    cell = kernel.call("write", {"path": "tail.txt", "content": "zzz\n", "force": True})
    check("kernel: write force=True replaces the file",
          not cell.rejected and (root / "tail.txt").read_text() == "zzz\n",
          str(cell.result)[:240])
    cell = kernel.call("write", {"path": "escaped.md", "content": "line one\\nline two"})
    check("kernel: a literal backslash-n body is noted, not silently one line",
          any("backslash-n" in note for note in (cell.envelope or {}).get("notes") or [])
          and (root / "escaped.md").read_text() == "line one\\nline two",
          str(cell.envelope)[:300])

    kernel.run("write('spl.txt', 'one\\ntwo\\n')")
    cell = kernel.call("splice", {"path": "spl.txt", "start": 1, "text": "zero"})
    check("kernel: splice without end is bad_arguments, not a silent replace",
          cell.rejected and "bad_arguments" in str(cell.result)
          and (root / "spl.txt").read_text() == "one\ntwo\n",
          str(cell.result)[:280])
    cell = kernel.call("splice", {"path": "spl.txt", "start": 2, "end": 1, "text": "INSERT"})
    check("kernel: splice end=start-1 inserts before the line",
          not cell.rejected and (root / "spl.txt").read_text() == "one\nINSERT\ntwo\n",
          str(cell.result)[:240])

    cell = kernel.call("read", {"path": "call_a.txt", "start": "five"})
    check("kernel: a non-integer read start is bad_arguments, not a traceback",
          cell.rejected and not cell.failed and "bad_arguments" in str(cell.result),
          (str(cell.result) + cell.stderr)[:280])
    cell = kernel.call("read", {"path": "call_a.txt", "start": 0})
    check("kernel: read(start=0) is bad_arguments, not a silent clamp",
          cell.rejected and "bad_arguments" in str(cell.result), str(cell.result)[:240])
    cell = kernel.call("read", {"path": "call_a.txt", "start": 2, "end": 1})
    check("kernel: read(start > end) is bad_arguments",
          cell.rejected and "bad_arguments" in str(cell.result), str(cell.result)[:240])

    kernel.run("write('paged.txt', '\\n'.join('row %d' % n for n in range(1, 701)))")
    cell = kernel.call("read", {"path": "paged.txt"})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: the default read page is truncated and complete is its inverse",
          page.get("truncated") is True and page.get("complete") is False
          and page.get("has_more") is True and page.get("offset") == 1,
          str(page)[:280])
    cell = kernel.call("read", {"path": "paged.txt", "start": 1, "end": 5})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: an explicit read window is complete with has_more",
          page.get("truncated") is False and page.get("complete") is True
          and page.get("has_more") is True, str(page)[:280])
    cell = kernel.call("read", {"path": "paged.txt"})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: a pointer read is complete and not truncated",
          page.get("pointer") is True and page.get("truncated") is False
          and page.get("complete") is True and page.get("has_more") is True,
          str(page)[:280])
    cell = kernel.call("map", {"path": ".", "include": "*.txt", "max_files": 2})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: map's explicit page size is served (complete with has_more)",
          page.get("complete") is True and page.get("truncated") is False
          and page.get("has_more") is True and page.get("offset") == 0
          and page.get("unit") == "file", str(page)[:280])
    cell = kernel.call("map", {"path": ".", "include": "*.txt", "max_files": 0})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: map max_files=0 returns every row in one answer",
          page.get("next") is None and page.get("shown") == page.get("total")
          and int(page.get("total") or 0) > 2, str(page)[:280])
    kernel.run("write('many.md', '\\n'.join('## sec %d' % n for n in range(1, 30)))")
    cell = kernel.call("outline", {"path": "many.md", "max_entries": 4})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: outline's first page offset is 0 and next is the resume cursor",
          page.get("offset") == 0 and page.get("next") == {"offset": 4}
          and page.get("truncated") is False and page.get("unit") == "entry",
          str(page)[:280])
    cell = kernel.call("outline", {"path": "many.md"})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: outline's truncated page is page.truncated (default page size)",
          page.get("truncated") is False, str(page)[:240])
    cell = kernel.call("search", {"pattern": "row", "path": "paged.txt", "count": True})
    page = (cell.envelope or {}).get("page") or {}
    data = (cell.envelope or {}).get("data") or {}
    check("kernel: search count mode reports rows delivered, with hits labelled",
          data.get("hits") == 700 and page.get("shown") == 1 and page.get("total") == 1
          and page.get("unit") == "file", str(page) + str(data)[:120])
    cell = kernel.call("search", {"pattern": "row", "path": "paged.txt", "max_results": 5})
    data = (cell.envelope or {}).get("data") or {}
    check("kernel: search labels hits vs hit_ranges",
          data.get("hits") == 700 and "hit_ranges" in data and "ranges" not in data,
          str(data)[:280])
    kernel.run("write('dupe_a.txt', '\\n'.join('alpha %d' % n for n in range(30))); "
               "write('dupe_b.txt', '\\n'.join('beta %d' % n for n in range(30)))")
    cell = kernel.call("search", {"pattern": "alpha|beta", "path": ["dupe_a.txt", "dupe_b.txt"],
                                  "max_results": 0})
    data = (cell.envelope or {}).get("data") or {}
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: search max_results=0 is one whole multi-file grep (path list + alternation)",
          data.get("hits") == 60 and page.get("shown") == 60 and page.get("next") is None
          and page.get("truncated") is False
          and {"dupe_a.txt", "dupe_b.txt"} <= set(data.get("by_file") or {}),
          str(page) + str(data)[:160])
    cell = kernel.call("search", {"pattern": "alpha|beta", "path": ["dupe_a.txt", "dupe_b.txt"]})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: search's default page is still 50 hits and says truncated",
          page.get("shown") == 50 and page.get("truncated") is True, str(page)[:280])
    kernel.run("write('agg.py', '\\n'.join('def f%d():\\n    return TOPIC' % n for n in range(25)))")
    cell = kernel.call("search", {"pattern": "TOPIC", "path": "agg.py", "max_results": 2})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: an explicit search page size is honored under section aggregation",
          page.get("shown") == 2 and page.get("total") == 25
          and page.get("complete") is True and page.get("next") == {"offset": 2}
          and (cell.envelope or {}).get("kind") == "matches", str(page)[:280])

    kernel.run("write('bigdiff.txt', '\\n'.join('x' * 12 for _ in range(1200)))")
    cell = kernel.call("session_diff", {})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: session_diff is a paged envelope with page.truncated",
          page.get("unit") == "char" and page.get("truncated") is True
          and page.get("has_more") is True and "...[diff truncated" in cell.stdout,
          str(page)[:280])
    kernel.run("write('bench/.tmp/scratch.txt', 'kept out of the walk')")
    cell = kernel.call("session_diff", {})
    data = (cell.envelope or {}).get("data") or {}
    check("kernel: session_diff counts journaled writes to an ignored path",
          int(data.get("ignored") or 0) >= 1
          and any("not in this diff" in note
                  for note in (cell.envelope or {}).get("notes") or []),
          str(cell.envelope)[:320])

    kernel.call("exec", {"argv": ["git", "init", "-q"]})
    cell = kernel.call("status", {})
    data = (cell.envelope or {}).get("data") or {}
    check("kernel: status is one read-only call with branch and journal summary",
           cell.tool == "status" and "journal:" in cell.stdout
           and "changed_total" in data and "adds" in data
           and not cell.changed and not cell.mutated,
           (cell.stdout + str(cell.envelope))[:300])
    cell = kernel.call("git", {"action": "status"})
    notes = " ".join((cell.envelope or {}).get("notes") or [])
    check("kernel: git(action='status') carries one note naming status()",
           "status()" in notes and "session journal" in notes
           and cell.envelope.get("kind") == "git_status", notes[:300])

    # The plan is what the worker was started with; the kernel only has to name the typed call.
    with kernel_for(root, check_commands=[{"name": "probe", "cmd": "echo plan-ok"}]) as plan_kernel:
        probe = plan_kernel.run("print(_check_note('bun test tests/'))")
        check("kernel: an unrelated bun test tests/ gets no checks() note",
               "None" in probe.stdout, probe.stdout[:200])
        cell = plan_kernel.call("exec", {"argv": ["echo", "plan-ok"]})
        notes = " ".join((cell.envelope or {}).get("notes") or [])
        check("kernel: an exec of a plan command names checks('<spec>')",
               "checks('probe')" in notes and "plan-ok" in cell.stdout, notes[:300])

    kernel.run("write('ctx.py', 'def alpha():\\n    return 1\\n')")
    cell = kernel.call("edit", {"edits": [{"path": "ctx.py", "old": "return 2",
                                                 "new": "return 3"}]})
    context = ((cell.envelope or {}).get("data") or {}).get("context") or {}
    check("kernel: a failed edit shows the enclosing definition from outline",
          context.get("name") == "alpha"
          and any("return 1" in str(text) for _, text in context.get("lines") or []),
          str(context)[:280])
    cell = kernel.call("guide", {"query": "demo", "limit": 1, "headers": 1})
    check("kernel: guide(headers=N) inlines the top hit's header in one call",
          "WHAT IT IS" in cell.stdout and "ENTRY POINTS" in cell.stdout,
          cell.stdout[:240])
    cell = kernel.call("guide", {"path": ["outline.py", "ctx.py"]})
    check("kernel: guide(path=[...]) returns several modules' headers in one call",
           '"kind": "guides"' in (cell.result or "")
           and "outline.py" in cell.stdout and "ctx.py" in cell.stdout,
           (cell.stdout + (cell.result or ""))[:240])
    cell = kernel.call("skill", {})
    check("kernel: skill lists the project procedures without a shell",
           '"kind": "skills"' in (cell.result or "") and not cell.rejected,
           (cell.stdout + (cell.result or ""))[:240])

    # --- the boundary: registry coercion, a crashed verb, and data that must survive -----
    cell = kernel.call("map", {"max_files": "x"})
    result = str(cell.result)
    check("kernel: map(max_files='x') is bad_arguments with a signature, no traceback",
          cell.rejected and not cell.failed and "bad_arguments" in result
          and "signature" in result and "max_files" in result
          and "Traceback" not in cell.stderr, (result + cell.stderr)[:320])
    cell = kernel.call("session_diff", {"max_chars": "x"})
    result = str(cell.result)
    check("kernel: session_diff(max_chars='x') is bad_arguments, not a traceback",
          cell.rejected and not cell.failed and "bad_arguments" in result
          and "signature" in result and "max_chars" in result
          and "Traceback" not in cell.stderr, (result + cell.stderr)[:320])
    card, _ = state_loop.render_observation(cell, 1)
    check("state: a refusal line carries the kind, signature and valid keywords",
          "[error] bad_args: bad_arguments" in card and "signature=" in card
          and "valid=" in card, card[:420])
    with kernel_for(
        root,
        extra_verbs={"boom": {"function": "boom", "typed": True, "mutates": False,
                              "summary": "raise on purpose",
                              "schema": {"type": "object", "properties": {},
                                         "additionalProperties": False}}},
        extra_prelude="def boom():\n    raise RuntimeError('kaboom')\n",
    ) as crash_kernel:
        cell = crash_kernel.call("boom", {})
    env = cell.envelope or {}
    check("kernel: a verb that raises is internal_error and rejected, not ok:true",
          cell.rejected and not cell.failed and env.get("ok") is False
          and env.get("kind") == "tool_crashed"
          and (env.get("error") or {}).get("code") == "internal_error"
          and "kaboom" in str((env.get("error") or {}).get("message")),
          str(cell.result)[:320])

    kernel.run("for n in range(30): mkdir('dm%02d' % n); write('dm%02d/f.txt' % n, 'x')")
    cell = kernel.call("map", {"path": "."})
    card, _ = state_loop.render_observation(cell, 1)
    check("state: map dirs_matched survives the data gate and says what was clipped",
          "dirs_matched" in card and "[data] clipped:" in card, card[:400])
    kernel.run("write('bigctx.py', 'def big():\\n' + ''.join('    v%d = %r\\n' % (n, 'x' * 80) for n in range(60)))")
    cell = kernel.call("edit", {"edits": [{"path": "bigctx.py", "old": "v99 = 99",
                                                 "new": "x"}]})
    card, _ = state_loop.render_observation(cell, 1)
    check("state: a failing edit keeps closest/context and names the clipped keys",
          "closest" in card and "context" in card and "[data] clipped:" in card, card[:500])

    kernel.run("undo('call_a.txt'); undo('call_b.txt')")
