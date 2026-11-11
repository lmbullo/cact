"""kernel_driver: the persistent Python cell child, driven by the Rust parent.

WHAT IT IS
  One JSON request line in, one `@@K@@` JSON line out. A `cell` request runs Python in a globals
  dict that persists between requests; a `call` request invokes one typed verb. While a cell runs,
  a verb call becomes an `rpc` line to the parent and blocks until the `rpc_result` line arrives.

ENTRY POINTS
  main loop over stdin; `final_answer(value)` raises Final, which ends the cell with a handoff;
  help() lists the verbs; `_OBS` holds the numbered observations the parent reports.

INVARIANTS
  the child never writes the protocol to stdout: cell output is captured, then restored.
  a cell's exception is reported as `error` with a traceback tail, never a dead child.
  the namespace survives across cells; a parent timeout kills this process and the parent restarts.
"""
import ast
import builtins
import io
import json
import sys
import traceback

PROTO = "@@K@@"
VERBS = (
    "guide", "skill", "map", "read", "search", "page", "outline", "edit", "splice", "write",
    "delete", "mkdir", "apply", "exec", "git", "status", "checks", "session_diff", "plan", "task",
    "undo",
)
ns = {"__name__": "__main__", "__builtins__": builtins}
ns["_OBS"] = []


def _send(payload):
    """Write one protocol line on the real stdout and flush."""
    sys.__stdout__.write(PROTO + json.dumps(payload) + "\n")
    sys.__stdout__.flush()


class Final(Exception):
    """final_answer(value): ends the cell and hands `value` to the loop."""

    def __init__(self, value):
        self.value = value


def final_answer(value):
    """End the cell with the task's answer; the loop stages or accepts it."""
    raise Final(value)


def _rpc(name, args):
    """Ask the parent to run one verb and return the observation text."""
    _send({"kind": "rpc", "name": name, "args": args})
    line = sys.__stdin__.readline()
    if not line:
        raise RuntimeError("kernel parent closed the connection")
    reply = json.loads(line)
    if reply.get("kind") != "rpc_result":
        raise RuntimeError("unexpected line from parent: %s" % reply.get("kind"))
    return reply.get("text", "")


def _verb(name):
    """Build one callable verb that forwards to the parent."""

    def call(*args, **kwargs):
        params = dict(kwargs)
        if args:
            if len(args) == 1 and isinstance(args[0], dict):
                params.update(args[0])
            else:
                raise TypeError("%s takes keyword arguments" % name)
        return _rpc(name, params)

    call.__name__ = name
    call.__doc__ = "Typed verb %s, executed by the Rust engine." % name
    return call


for _name in VERBS:
    ns[_name] = _verb(_name)

ns["final_answer"] = final_answer
ns["help"] = lambda: _rpc("help", {})
ns["undo"] = _verb("undo")


def _run_cell(source):
    """Run one cell: the last expression is the result, everything else is a statement."""
    tree = compile(source, "<cell>", "exec", ast.PyCF_ONLY_AST)
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body.pop()
    exec(compile(tree, "<cell>", "exec"), ns)
    if last is None:
        return None
    return eval(compile(ast.Expression(last.value), "<cell>", "eval"), ns)


def _reply(request):
    """Handle one request and send exactly one `done` line back."""
    kind = request.get("kind")
    if kind == "call":
        text = _rpc(request["name"], request.get("args") or {})
        _send({"kind": "done", "stdout": text})
        return
    if kind != "cell":
        _send({"kind": "done", "stdout": "", "error": "unknown request %r" % (kind,)})
        return
    out, err = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    final, error, value = None, None, None
    sys.stdout, sys.stderr = out, err
    try:
        value = _run_cell(request.get("code", ""))
    except Final as stop:
        final = stop.value
    except BaseException:
        error = traceback.format_exc()[-4000:]
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    _send({
        "kind": "done",
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
        "result": "" if value is None else repr(value)[:4000],
        "final": final,
        "error": error,
    })


def main():
    """Read requests until stdin closes."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        _reply(request)


if __name__ == "__main__":
    main()
