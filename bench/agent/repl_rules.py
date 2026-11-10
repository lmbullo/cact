"""repl_rules: system prompts and run() notes of the fenced-python dialect.
WHAT IT IS
  Model-facing text of --mode repl / --mode bare kept as data; repl.py re-exports them and
  worker.py assembles the final rules string.
ENTRY POINTS
  REPL_RULES      the full python-REPL contract (one fenced block per reply, final_answer/ANSWER)
  BARE_RULES      the minimal contract (one block; set ANSWER to finish)
  RUN_NOTE        appended when run() is enabled on a python-only run
  RUN_NOTE_MUT    appended when exec/run() are both available
INVARIANTS
  text only: no imports, no code, no formatting pass; a constant is sent to the model verbatim.
  the two run notes share identical paragraphs (journal rules); only the tools paragraph differs.
  the rules describe the kernel's real verbs; a verb renamed in PRELUDE is renamed here.
WHERE TO CHANGE
  python block contract ..... REPL_RULES
  minimal bare contract ..... BARE_RULES
  run()/exec paragraphs ..... RUN_NOTE, RUN_NOTE_MUT
  string assembly ........... tui/python/worker.py (rules assembly in main)
"""
from __future__ import annotations

REPL_RULES = """PYTHON REPL CONTRACT
1. Reply with exactly one fenced ```python block. It runs in a persistent kernel: variables, imports and data persist between turns.
2. stdout, stderr and the value of the final expression come back to you as an [observation].
3. To finish, call final_answer(<value>) in a cell that prints nothing. If the cell printed output, the answer is not accepted; the output is shown to you and you resubmit.
4. To finish without code, the last line must be exactly "ANSWER: <value>". A reply with neither a python block nor an ANSWER line does not end the task; it is returned to you as a protocol error.
5. The kernel imports only the standard library; do not install packages into it.
6. run() returns a dict: result['stdout'] (or result.stdout), result['stderr'], result['exit_code'], result['kind']. Print only what the answer needs; output is returned in full.
7. Every write and edit is journalled: changes() lists what this session changed, diff(path=None) returns the unified diff as a string, and undo(path=None) restores files to their session-start state. For an existing file, splice(path, start, end, text) replaces 1-based inclusive lines and returns the hunk; edit() replaces exact old text; edit_many([{path, old, new}, ...]) applies a batch atomically; use write() only for a new file or a real rewrite. For several files in one change, prefer apply(<unified diff>) or commit({path: text, ...}) over many write() calls: both land in one diff and one undo. Read diff() before you finish.
8. Earlier cells keep their full output in _OBS. recall(pattern) searches it, including turns a fold removed, and returns matching snippets plus the handle to page the full text; grep() is regex-by-default and for the tree, recall() is for the session, outline(file) lists headings/defs with line numbers, and guide(query) answers "which module owns this?" when the repo documents its modules with headers.
9. This is not tools mode. Never emit DSML, XML, <|...|>, or tool-call markup. Your reply must contain only prose and at most one fenced python block.
"""

RUN_NOTE = """

RUNNING PROGRAMS
In tools modes, exec(argv, timeout=60, cwd=None, env=None, stdin=None) is the one process tool: argv or commands=[[...],[...]] for programs, shell=<string> for explicit shell syntax. Use cwd=... instead of `cd`; env/stdin are explicit. A shell interpreter with -c/-lc in argv is refused (shell_via_exec): pass shell=<string> instead. Use search()/read() for files.

In a python-only run (repl/bare), run(command or [commands], timeout=60, cwd=None) executes one bash command (or a list) from the project root and returns kind, exit_code, stdout, stderr. Reads, searches, tests, builds and read-only git work as written: `cat file`, `sed -n '1,40p' file`, `rg TODO`, `python -m pytest -q`, `git diff`, `python3 -c '...'`.

Mutation inside the workspace goes through the journal, not a process. Use write(), edit(), splice(), delete(), mkdir() or apply() instead of tee, touch, cp, mv, rm, mkdir, sed -i or `>`; exec and run refuse those with kind "wrong_call" because they would bypass diff() and undo(). Scratch paths (/tmp, /var/tmp, /dev/shm, $TMPDIR) are not gated: rm/mkdir/cp there is fine. A program you run can still write files; the journal only covers the typed helpers, so do not claim undo() covers a script's output.
"""

RUN_NOTE_MUT = """

RUNNING PROGRAMS
In tools modes, exec(argv, timeout=60, cwd=None, env=None, stdin=None) is the one process tool: argv or commands=[[...],[...]] for programs, shell=<string> for explicit shell syntax. cwd replaces `cd`; a bash -lc argv is refused (shell_via_exec), so pass shell=<string> instead.

In a python-only run (repl/bare), run(command or [commands], timeout=60, cwd=None) executes one bash command (or a list) from the project root and returns kind, exit_code, stdout, stderr.

Mutation inside the workspace goes through the journal, not a process. Use write(), edit(), splice(), delete(), mkdir() or apply() instead of tee, touch, cp, mv, rm, mkdir, sed -i or `>`; exec and run refuse those with kind "wrong_call" because they would bypass diff() and undo(). Scratch paths (/tmp, /var/tmp, /dev/shm, $TMPDIR) are not gated: rm/mkdir/cp there is fine. A program you run can still write files; the journal only covers the typed helpers, so do not claim undo() covers a script's output.
"""

BARE_RULES = """Write Python in one ```python block. Cells share a kernel; variables persist. Set ANSWER to the final value to end the task.
"""
