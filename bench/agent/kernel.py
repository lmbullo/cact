from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

DRIVER = r'''
import ast, contextlib, io, json, os, signal, sys, time, traceback

class _CellTimeout(Exception):
    pass

def _on_alarm(_signum, _frame):
    raise _CellTimeout()

signal.signal(signal.SIGALRM, _on_alarm)
_CELL_END = 0.0
_SUPERVISE = os.environ.get("KERNEL_SUPERVISE", "1") != "0"

def _extend(seconds):
    global _CELL_END
    if not _SUPERVISE:
        return
    _CELL_END = max(_CELL_END, time.monotonic() + float(seconds))
    signal.setitimer(signal.ITIMER_REAL, max(0.05, _CELL_END - time.monotonic()))
    try:
        sys.__stdout__.write("@@K@@" + json.dumps({"extend": float(seconds)}) + "\n")
        sys.__stdout__.flush()
    except Exception:
        pass

def _arm(seconds):
    global _CELL_END
    if not _SUPERVISE:
        return
    _CELL_END = time.monotonic() + max(0.5, float(seconds))
    signal.setitimer(signal.ITIMER_REAL, max(0.5, float(seconds)))

def _disarm():
    signal.setitimer(signal.ITIMER_REAL, 0)

ns = {"__name__": "__agent__", "_CELL_EXTEND": _extend}
_prelude = os.environ.get("KERNEL_PRELUDE")
if _prelude:
    exec(_prelude, ns)
    if os.environ.get("KERNEL_PRELUDE_MODE") == "mut":
        for _n in ("map", "read", "grep"):
            ns.pop(_n, None)
_run_prelude = os.environ.get("KERNEL_RUN_PRELUDE")
if _run_prelude:
    exec(_run_prelude, ns)

class Final(Exception):
    def __init__(self, value):
        self.value = value

