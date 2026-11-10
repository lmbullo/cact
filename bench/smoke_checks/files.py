"""files: the journalled file action space an agent actually gets in a cell.

WHAT IT IS
  Drives a real kernel child through read (ranges, paging, truncation), write, an unclosed
  open() write, edit, an untyped deletion and a created-then-deleted file, diff/changes/
  session_diff, undo, batched edit, delete/mkdir, grep, map, a typed missing-file read, a
  broad search aggregated by section, `read(section=)`, guide() in both a hit and a miss, and
  git status/log/files/branch plus a paged git diff.

ENTRY POINTS
  run(check, root, kernel)   called by bench/smoke.py::main with the shared kernel

INVARIANTS
  - The first check asserts typed mode removes the python `run()` helper: if that ever
    regresses, every later run()/exec check is measuring the wrong surface.
  - Files created here (`range.txt`, `b.txt` before undo, `sections.md`, `demo.py`) are the
    shared fixture the later groups expect; keep the order in bench/smoke.py::main.
  - Everything goes through the kernel, never through this process's file API, except to
    assert the tree after the fact.

WHERE TO CHANGE
  A read/journal helper .................. kernel/prelude.py (this file only scores it)
  Read delivery budget ................... kernel/prelude.py `_READ_MAX_LINES/CHARS`
  guide() ................................ kernel/prelude.py + bench/agent/headers.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from bench.agent.kernel import Kernel
import bench.agent.state_loop as state_loop


def run(check, root, kernel):
    """The journalled file action space in a writable kernel: read/write/edit/undo/diff/map/guide."""
    # --- kernel: the action space an agent actually gets ---------------------------------
    typed_kernel = Kernel(root, timeout=20, writable=True, prelude="all", allow_run=True,
                          allow_shell=False)
    try:
        cell = typed_kernel.run("print('run' in globals())")
        check("kernel: typed mode removes the python run() helper",
              cell.stdout.strip() == "False", cell.stdout[:120])
    finally:
        typed_kernel.stop()

    cell = kernel.run("print(read('a.txt')['total_lines'])")
    check("kernel: read", "2" in cell.stdout, cell.stdout[:120])
    kernel.run(r"write('range.txt', 'aa\nbb\ncc\n')")
    cell = kernel.call("read", {"path": "range.txt", "start": 1, "end": 1})
    check("kernel: an explicit complete range has_more but is not truncated",
          '"has_more": true' in (cell.result or "") and '"truncated": false' in (cell.result or "")
          and cell.envelope.get("page", {}).get("complete") is True
          and cell.envelope.get("page", {}).get("has_more") is True,
          (cell.result or "")[:240])
    kernel.run(r"write('longread.txt', '\n'.join('line %d %s' % (n, 'x' * 60) for n in range(600)))")
    cell = kernel.call("read", {"path": "longread.txt"})
    check("kernel: the default page limit is truncated",
          '"has_more": true' in (cell.result or "") and '"truncated": true' in (cell.result or ""),
          (cell.result or "")[:240])

    cell = kernel.call("read", {"path": "longread.txt", "start": 1, "end": 5,
                              "repeat": True})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: a bounded read page names its offset, next cursor and completeness",
          page.get("shown") == 5 and page.get("total") == 600 and page.get("offset") == 1
          and page.get("next") == {"start": 6} and page.get("unit") == "line"
          and page.get("has_more") is True and page.get("truncated") is False
          and page.get("complete") is True and not page.get("pointer"), str(page)[:280])
    nxt = page["next"]["start"]
    following = kernel.call("read", {"path": "longread.txt", "start": nxt,
                                     "end": nxt + 4, "repeat": True})
    next_page = (following.envelope or {}).get("page") or {}
    check("kernel: replaying page.next reads the following window with no overlap",
          next_page.get("offset") == nxt and next_page.get("next") == {"start": nxt + 5}
          and next_page.get("complete") is True and next_page.get("truncated") is False
          and following.stdout.lstrip().startswith("6|")
          and "line 4 " not in following.stdout, str(next_page)[:280])

    cell = kernel.run("write('b.txt', 'one\\ntwo\\n')")
    check("kernel: write is journalled", cell.changed == ["b.txt"], str(cell.changed))

    cell = kernel.run("open('c.txt', 'w').write('written\\n')")
    check("kernel: an unclosed open() write is journalled",
          (root / "c.txt").exists() and "c.txt" in cell.changed,
          f"{cell.changed} {cell.result}")

    cell = kernel.run("edit('b.txt', 'one', 'uno')")
    check("kernel: edit reports the file", cell.changed == ["b.txt"], str(cell.changed))
    check("kernel: diff shows the hunk", "-one" in cell.delta and "+uno" in cell.delta, cell.delta[:120])
    check("kernel: deltas carries the per-file hunk",
          "+uno" in cell.deltas.get("b.txt", "") and "b.txt" in cell.changed, str(cell.deltas)[:120])

    kernel.run("write('gone.txt', 'x')")
    cell = kernel.run("import os; os.remove('gone.txt')")
    check("kernel: an untyped deletion is a change",
          cell.changed == ["gone.txt"], str(cell.changed))

    cell = kernel.run("write('ghost.txt', 'x'); import os; os.remove('ghost.txt')")
    check("kernel: a created-then-deleted file is not a change",
          cell.changed == [] and not (root / "ghost.txt").exists(), str(cell.changed))

    cell = kernel.run("print(type(diff()).__name__); print(diff()[:20])")
    check("kernel: diff() returns text, not a dict",
          "str" in cell.stdout and "---" in cell.stdout, cell.stdout[:160])

    cell = kernel.call("session_diff", {"path": "b.txt"})
    check("kernel: call(session_diff) is the typed journal view",
          cell.tool == "session_diff" and "---" in cell.stdout and "+++" in cell.stdout,
          (cell.stdout + cell.stderr)[:200])
    cell = kernel.run("print(changes())")
    check("kernel: changes lists the file", "b.txt" in cell.stdout, cell.stdout[:120])

    cell = kernel.run("undo('b.txt')")
    check("kernel: undo drops a file created this session", not (root / "b.txt").exists(), cell.stdout[:120])

    kernel.run("edit('a.txt', 'beta', 'gamma')")
    check("kernel: edit applied", "gamma" in (root / "a.txt").read_text())
    kernel.run("undo('a.txt')")
    check("kernel: undo restores session-start text",
          (root / "a.txt").read_text() == "alpha\nbeta\n", (root / "a.txt").read_text()[:80])

    kernel.run("write('batch_a.txt', 'one\\ntwo\\n'); write('batch_b.txt', 'red\\nblue\\n')")
    cell = kernel.call("edit", {"edits": [
        {"path": "batch_a.txt", "old": "two", "new": "TWO"},
        {"path": "batch_b.txt", "old": "blue", "new": "BLUE"},
    ]})
    check("kernel: call(edit) applies a batch in one cell",
          cell.tool == "edit" and set(cell.changed) == {"batch_a.txt", "batch_b.txt"}
          and "TWO" in (root / "batch_a.txt").read_text()
          and "BLUE" in (root / "batch_b.txt").read_text(), str(cell.changed))
    cell = kernel.call("edit", {"path": "batch_a.txt",
                                    "edits": [{"old": "TWO", "new": "two"}]})
    check("kernel: call(edit) accepts a default path",
          "two" in (root / "batch_a.txt").read_text(), (cell.result or "")[:200])
    cell = kernel.call("edit", {"edits": [
        {"path": "batch_a.txt", "old": "one", "new": "ONE"},
        {"path": "batch_b.txt", "old": "missing", "new": "x"},
    ]})
    check("kernel: call(edit) is atomic on a bad old string",
          "not_found" in (cell.result or "") and cell.rejected and not cell.failed
          and (root / "batch_a.txt").read_text().startswith("one"),
          (cell.result or "")[:240])

    kernel.run("write('remove_me.txt', 'gone')")
    cell = kernel.call("delete", {"path": "remove_me.txt"})
    check("kernel: call(delete) removes through the tree journal",
          cell.envelope.get("kind") == "delete" and not (root / "remove_me.txt").exists(),
          str(cell.envelope))
    cell = kernel.call("mkdir", {"path": "new_dir"})
    check("kernel: call(mkdir) creates a directory",
          cell.envelope.get("kind") == "mkdir" and (root / "new_dir").is_dir(),
          str(cell.envelope))

    cell = kernel.run("grep('beta', '.')")
    check("kernel: grep", "beta" in str(cell.result), str(cell.result)[:120])

    cell = kernel.run("map('.')")
    check("kernel: map", "a.txt" in str(cell.result), str(cell.result)[:120])
    mapped = kernel.call("map", {"path": "."})
    check("kernel: map offset is 0 on the first page",
          mapped.envelope.get("page", {}).get("offset") == 0,
          str(mapped.envelope.get("page")))
    batch = kernel.call("read", {"path": ["a.txt", "batch_a.txt"]})
    check("kernel: a batch read counts files, not windows",
          batch.envelope.get("page", {}).get("unit") == "file",
          str(batch.envelope.get("page")))
    kernel.run("write('partial_good.txt', 'alpha\\nbeta\\n')")
    cell = kernel.call("read", {"path": ["partial_good.txt", "no-such-batch.txt"]})
    env = cell.envelope or {}
    data = env.get("data") or {}
    check("kernel: a batched read with one missing path is a partial_failure",
          (env.get("error") or {}).get("code") == "partial_failure"
          and "no-such-batch.txt" in (data.get("missing") or [])
          and "alpha" in cell.stdout and len(data.get("missing") or []) == 1,
          str(env)[:320])
    cell = kernel.call("map", {"path": ["partial_good.txt", "no-such-map.txt"]})
    env = cell.envelope or {}
    data = env.get("data") or {}
    check("kernel: a batched map with one missing path is a partial_failure",
          (env.get("error") or {}).get("code") == "partial_failure"
          and "no-such-map.txt" in (data.get("missing") or [])
          and "partial_good.txt" in cell.stdout, str(env)[:320])
    cell = kernel.call("outline", {"path": ["partial_good.txt", "no-such-outline.txt"]})
    env = cell.envelope or {}
    data = env.get("data") or {}
    check("kernel: a batched outline with one missing path is a partial_failure",
          (env.get("error") or {}).get("code") == "partial_failure"
          and "no-such-outline.txt" in (data.get("missing") or [])
          and "partial_good.txt" in cell.stdout, str(env)[:320])
    cell = kernel.call("read", {"path": "missing.txt"})
    card, _ = state_loop.render_observation(cell, 1)
    check("state: structured data survives the card", "[data]" in card and "did_you_mean" in card,
          card[:300])

    body = "# Top\n\n## Alpha\n" + "".join("typed %d\n" % n for n in range(11))
    body += "\n## Beta\n" + "".join("typed %d\n" % n for n in range(11))
    kernel.run("write('sections.md', %r)" % body)
    cell = kernel.call("search", {"pattern": "typed", "path": "sections.md"})
    check("kernel: a broad single-file search aggregates by section",
          cell.envelope.get("kind") == "search_sections"
          and "Alpha" in cell.stdout and "Beta" in cell.stdout
          and "sections=false" in " ".join(cell.envelope.get("notes") or []),
          (cell.stdout + str(cell.envelope))[:240])
    cell = kernel.call("search", {"pattern": "typed", "path": "sections.md",
                                   "sections": False})
    check("kernel: sections=false keeps the raw hit table",
          cell.envelope.get("kind") == "matches" and "typed" in cell.stdout,
          (cell.stdout + str(cell.envelope))[:200])
    cell = kernel.call("read", {"path": "sections.md", "section": "Beta"})
    check("kernel: read(section=) resolves through outline",
          "Beta" in cell.stdout and "typed 10" in cell.stdout,
          (cell.stdout + str(cell.envelope))[:200])

    (root / "demo.py").write_text(
        '"""A demo module for the index.\n\nWHAT IT IS\n  demo only\n\nENTRY POINTS\n'
        '  demo()\n\nINVARIANTS\n  none\n\nWHERE TO CHANGE\n  here\n"""\n', encoding="utf-8")
    cell = kernel.run("r = guide('demo'); print(r['kind'], [m['path'] for m in r['modules']])")
    check("kernel: guide() finds a documented module",
          "demo.py" in cell.stdout, (cell.stdout + cell.stderr)[:200])
    cell = kernel.run("print(guide('nothing-matches-this')['text'])")
    check("kernel: guide() reports a miss as text",
          ("no documented module" in cell.stdout
           or "not in the contract index" in cell.stdout), (cell.stdout + cell.stderr)[:200])
    (root / "offset_demo.py").write_text(
        '"""An offset demo.\n\nWHAT IT IS\n  zzquuxoffset marker\n\nENTRY POINTS\n'
        '  none\n\nINVARIANTS\n  none\n\nWHERE TO CHANGE\n  here\n"""\n', encoding="utf-8")
    cell = kernel.call("guide", {"query": "zzquuxoffset", "offset": 1})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: guide offset past the end keeps the true total",
          page.get("total") == 1 and page.get("shown") == 0
          and page.get("next") is None
          and "past the end" in " ".join((cell.envelope or {}).get("notes") or []),
          str(cell.envelope)[:280])

    cell = kernel.run("run('cat a.txt')")
    allowed = str(cell.result)
    check("kernel: run() allows a read-only command",
          "command" in allowed and "alpha" in allowed, allowed[:200])

    cell = kernel.run("run('python3 -c \"print(1)\"')")
    allowed = str(cell.result)
    check("kernel: run() allows -c scripting",
          "command" in allowed or "exit_code" in allowed, allowed[:200])

    cell = kernel.run("r = run(['echo one', 'echo two']); print(r['kind'], r['count'], 'one' in r['stdout'], 'two' in r['stdout'])")
    check("kernel: run() accepts a list and concatenates its output",
          "commands 2 True True" in cell.stdout, (cell.stdout + cell.stderr)[:200])
    cell = kernel.run("r = run(['echo ok', 'rm -rf a.txt']); print(r['ok'], [c.get('kind') for c in r['commands']], read('a.txt')['total_lines'])")
    check("kernel: every command in a list is gated",
          "False" in cell.stdout and "wrong_call" in cell.stdout and " 2" in cell.stdout,
          (cell.stdout + cell.stderr)[:200])

    cell = kernel.run("run('sed -i s/alpha/omega/ a.txt')")
    refusal = str(cell.result)
    check("kernel: run() refuses a mutating shell verb",
          "wrong_call" in refusal and "edit(path" in refusal, refusal[:200])
    check("kernel: the refused mutation did not run",
          "alpha" in (root / "a.txt").read_text(), (root / "a.txt").read_text()[:80])

    cell = kernel.run("run('git add a.txt')")
    allowed = str(cell.result)
    check("kernel: run() allows git add/commit",
          "wrong_call" not in allowed and "command" in allowed, allowed[:200])

    cell = kernel.run("run('git checkout -- a.txt')")
    refusal = str(cell.result)
    check("kernel: run() refuses a working-tree overwrite",
          "wrong_call" in refusal and "git checkout" in refusal, refusal[:200])

    kernel.run("run('git init -q && git config user.email a@b && git config user.name a && git add a.txt && git commit -qm init')")
    kernel.run("write('git_new.txt', 'x')")
    cell = kernel.call("git", {"action": "status"})
    check("kernel: call(git) status parses the working tree",
          cell.tool == "git" and "git_new.txt" in cell.stdout and "??" in cell.stdout,
          (cell.stdout + cell.stderr)[:240])
    cell = kernel.call("git", {"action": "log", "limit": 1})
    check("kernel: call(git) log returns commits",
          "init" in cell.stdout and '"sha"' in (cell.result or ""),
          (cell.stdout + (cell.result or ""))[:240])
    cell = kernel.call("git", {"action": "files"})
    check("kernel: call(git) files lists tracked paths",
          "a.txt" in cell.stdout and "git_new.txt" not in cell.stdout,
          (cell.stdout + cell.stderr)[:240])
    cell = kernel.call("git", {"action": "branch"})
    check("kernel: call(git) branch names the current branch",
          bool(cell.stdout.strip()), (cell.stdout + cell.stderr)[:200])
    kernel.run("write('a.txt', 'alpha\\nbeta\\ngamma\\n')")
    cell = kernel.call("git", {"action": "diff", "max_chars": 5})
    page = (cell.envelope or {}).get("page") or {}
    check("kernel: call(git) diff pages on the delivered window, not the resume cursor",
          page.get("offset") == 0 and page.get("unit") == "char"
          and isinstance(page.get("total"), int)
          and page.get("complete") is True and page.get("has_more") is True,
          str(page)[:280])

    cell = kernel.run("r=run('echo shape'); print(r.stdout.strip(), r['stdout'].strip())")
    check("kernel: run() answers both r.stdout and r['stdout']",
          "shape shape" in cell.stdout, (cell.stdout + cell.stderr)[:200])
    cell = kernel.run("print(read('a.txt', raw=True)[:3])")
    check("kernel: read(raw=True) can be sliced", "alp" in cell.stdout, (cell.stdout + cell.stderr)[:200])
    cell = kernel.run("print(read('a.txt', repeat=True)['content'][:20])")
    check("kernel: read()['content'] is the delivered text", "alpha" in cell.stdout, (cell.stdout + cell.stderr)[:200])

    # A workspace larger than the snapshot budget must bound the tree it keeps in memory and
    # say so, instead of reading everything (a home directory is the failure case).
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "big.txt").write_text("x" * 100)
        bounded = Kernel(tmp, timeout=20, writable=True, prelude="all", allow_run=True,
                         env={"KERNEL_SNAPSHOT_BYTES": "1"})
        try:
            cell = bounded.run("print(changes())")
            check("kernel: a tree past the snapshot budget reports snapshot_partial",
                  "'snapshot_partial': True" in cell.stdout, cell.stdout[:240])
            cell = bounded.run("write('new.txt', 'hello\\nworld\\n')")
            check("kernel: a typed write past the snapshot budget keeps its hunk",
                  "new.txt" in cell.changed and "+hello" in cell.deltas.get("new.txt", ""),
                  (cell.delta or "")[:240])
            cell = bounded.run("print(changes())")
            check("kernel: changes() lists a journaled path past the snapshot budget",
                  "new.txt" in cell.stdout, cell.stdout[:240])
            cell = bounded.run("undo('new.txt')")
            check("kernel: undo removes a created file past the snapshot budget",
                  not Path(tmp, "new.txt").exists(), cell.stdout[:240])
            bounded.run("write('big.txt', 'y' * 200)")
            cell = bounded.run("undo('big.txt')")
            check("kernel: undo restores a pre-existing file past the snapshot budget",
                  Path(tmp, "big.txt").read_text() == "x" * 100, cell.stdout[:240])
        finally:
            bounded.stop()
