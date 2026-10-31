from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .check import evaluate
from .kernel import Cell, Kernel
from .llm import Client, LLMError
from .loop import Budget, RunResult, _usage_add
from .reply import Action, compile_reply

REPL_RULES = """PYTHON REPL CONTRACT
1. Reply with exactly one fenced ```python block. It runs in a persistent kernel: variables, imports and data persist between turns.
2. stdout, stderr and the value of the final expression come back to you as an [observation].
3. To finish, call final_answer(<value>) in a cell that prints nothing. If the cell printed output, the answer is not accepted; the output is shown to you and you resubmit.
4. To finish without code, the last line must be exactly "ANSWER: <value>". A reply with neither a python block nor an ANSWER line does not end the task; it is returned to you as a protocol error.
5. The kernel imports only the standard library; do not install packages into it.
6. Print only what the answer needs. Output is returned in full.
7. Every write and edit is journalled: changes() lists what this session changed, diff(path=None) returns the unified diff, and undo(path=None) restores files to their session-start state. Read diff() before you finish.
8. This is not tools mode. Never emit DSML, XML, <|...|>, or tool-call markup. Your reply must contain only prose and at most one fenced python block.
"""

RUN_NOTE = """

RUNNING PROGRAMS
run(command, timeout=60, cwd=None) executes one bash command from the project root and returns kind, exit_code, stdout, stderr. Use it only for programs that do work: test runners, compilers, linters, formatters, package managers, git, servers, downloads, builds.

run() is not a file interface. Every file action already has a direct call:
  read a file      -> read(path, offset=1, limit=0)  (whole file; raw=True for unnumbered text)
  search text      -> grep(pattern, path='.', max_results=50)
  list files       -> map(path='.')
  write a file     -> write(path, content)  (force=True only to intentionally drop a tail)
  edit a file      -> edit(path, old, new)
Passing any of these to run() is a protocol violation, not a shortcut: cat, head, tail, less, more, wc, stat, grep, rg, ag, sed, awk, find, ls, tree, du, tee, touch, cp, mv, rm, truncate, echo with >, printf with >, and python3 -c or python3 -m that imports, prints or inspects a project file. run() executes whole programs only: python3 app.py ledger.csv, pytest, make, git, docker.

Why: importing a module to print one constant is file inspection wearing a program's clothes. Shell output is also unparsed and unnumbered. The calls above return path, line numbers, total_lines, the next offset, and conflict/ambiguity/no-match diagnostics. A shell command returns text you must parse. run() checks the command and refuses file verbs with a wrong_call result naming the call to use. If the command you are about to pass begins with one of those words, you have picked the wrong call.
"""

RUN_NOTE_MUT = """

RUNNING PROGRAMS
run(command, timeout=60, cwd=None) executes one bash command from the project root and returns kind, exit_code, stdout, stderr. Use it only for programs that do work: test runners, compilers, linters, formatters, package managers, git, servers, downloads, builds.

run() is not a file interface. Every file action already has a direct call:
  read a file      -> open(path).read()
  search text      -> a string or regex check on that text
  list files       -> os.walk / os.listdir
  write a file     -> write(path, content)  (force=True only to intentionally drop a tail)
  edit a file      -> edit(path, old, new)
Passing any of these to run() is a protocol violation, not a shortcut: cat, head, tail, less, more, wc, stat, grep, rg, ag, sed, awk, find, ls, tree, du, tee, touch, cp, mv, rm, truncate, echo with >, printf with >, and python3 -c or python3 -m that imports, prints or inspects a project file. run() executes whole programs only: python3 app.py ledger.csv, pytest, make, git, docker.

Why: importing a module to print one constant is file inspection wearing a program's clothes. Shell output is also unparsed and unnumbered. write() and edit() return conflict, ambiguity and closest-match diagnostics; a shell command returns text you must parse, truncated to its last 6000 characters. If the command you are about to pass begins with one of those words, you have picked the wrong call.
"""

BARE_RULES = """Write Python in one ```python block. Cells share a kernel; variables persist. Set ANSWER to the final value to end the task.
"""


