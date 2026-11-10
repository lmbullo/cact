"""driver: DRIVER, the child loop: one request line -> one @@K@@ reply.

WHAT IT IS
  Source string, not a parent import: the child execs it.  This file concatenates four source
  fragments in dependency order, then applies the one protocol substitution; the assembled
  DRIVER is byte-identical to the old single string.
    driver_boot.py   imports, cell timeout signal, namespace, preludes, Final/final_answer/emit
    driver_args.py   _error_shape, _BadArg, _scalar, _coerce, _coerce_args
    driver_loop.py   stdin request loop and typed-call / python-cell dispatch (opens the try)
    driver_cell.py   its except arms, changed/delta bookkeeping, the reply emit

ENTRY POINTS
  DRIVER   the complete child program: _BOOT + _ARGS + _LOOP + _CELL, protocol substituted
  the child wire: one JSON request per line in, one @@K@@ JSON line out.

INVARIANTS
  fragment order = syntax and behavior: _CELL continues the try: opened at the end of _LOOP.
  Do not reorder, trim or add a separator; bytes must match the pre-split string.
  __PROTOCOL__ appears once, in _BOOT, and is replaced after concatenation.

WHERE TO CHANGE
  cell boot and timeout .. driver_boot.py;  argument coercion .. driver_args.py;  request loop
  and dispatch .. driver_loop.py;  outcome and reply .. driver_cell.py;  order/protocol .. here
"""
from __future__ import annotations

from .driver_args import _ARGS
from .driver_boot import _BOOT
from .driver_cell import _CELL
from .driver_loop import _LOOP
from .protocol import PROTOCOL

# One child program: the loop opens a `try:` and the cell fragment closes it. No separator.
DRIVER = (_BOOT + _ARGS + _LOOP + _CELL).replace("__PROTOCOL__", str(PROTOCOL))
