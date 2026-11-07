"""prelude_setup: the namespace a kernel cell starts in, plus the path-matching helpers.

WHAT IT IS
  Fragment 1/5 of `PRELUDE` (see prelude.py for how the pieces are joined). It defines what a
  fresh cell can already see: the exec'd header index (`_HEADERS`, `guide`), `_ROOT`, the
  per-cell and per-session read ledgers, the mutation flags, `.gitignore`/include matching
  (`_ignore_patterns`, `_ignored`, `_glob_match`, `_include_match`, `_include_patterns`) and
  `_coerce_paths`-free basics every later fragment uses.

ENTRY POINTS
  guide(query, path, limit, chars, offset, headers)   the one verb the header index adds
  _fail(kind, code, message, use, **extra)    the one error-dict builder every verb uses
  Inside the cell namespace: _ROOT, _READS_CELL, _READS_SESSION, _READ_MAX_CHARS,
  _READ_MAX_LINES, _MUT, _JOURNAL, _SNAP, _JSEQ, _CELL_CHANGED, _SKIP_DIRS, _IGNORE

INVARIANTS
  - This fragment runs first, so it may only use the stdlib modules it imports itself.
  - `_fail` is the one shape for a refusal: {ok:false, kind, error:{code,message,use}, path?}.
    Every verb returns that, so the model never parses a second dialect; the driver adds the
    live signature to it at the typed-call boundary.
  - `guide()` is a thin wrapper over `_HEADERS["guide"]` and must stay read-only; the rest of
    the prelude never calls it.
  - path matching is shared by map/search/read: a change here changes which files those
    three report, so keep the three consumers in mind.

WHERE TO CHANGE
  Which files are skipped ................. _SKIP_DIRS, _ignore_patterns, _ignored
  What `include=` matches ................. _glob_match, _include_match, _include_patterns
  The read ledger defaults ................ _READ_MAX_CHARS, _READ_MAX_LINES
"""
from __future__ import annotations

# --- part 1/5: imports, namespace, read ledgers, path matching ---------------------------
_SETUP = r"""
import ast as _ast, difflib as _difflib, fnmatch as _fnmatch, hashlib as _hashlib, os as _os, re as _re

_HEADERS = {}
exec(_os.environ.get("KERNEL_HEADERS") or "", _HEADERS)


def guide(query=None, path=None, limit=None, chars=20000, offset=0, headers=0):
    fn = _HEADERS.get("guide")
    if fn is None:
        return _fail("guide", "no_index", "the header index is not loaded",
                     "use map() and search() instead")
    return fn(_ROOT, query=query, path=path, limit=limit, chars=chars, offset=offset,
              headers=headers)


def _fail(kind, code, message, use, **extra):
    # The one error shape. `use` is part of the JSON, not only the rendered [error] line, so
    # a parser reads the same hint the card prints. Extra keys (path, did_you_mean, ...) sit
    # at the top level where the success shape puts them.
    out = {"ok": False, "kind": str(kind),
           "error": {"code": str(code), "message": str(message), "use": str(use)}}
    out.update({key: value for key, value in extra.items() if value is not None})
    return (globals().get("_Result") or dict)(out)


_ROOT = _os.getcwd()
# The one story for scratch paths: exec/run may mutate /tmp, /var/tmp, /dev/shm and $TMPDIR
# because they run outside the journal; the typed editors are workspace-only, and this hint is
# what every outside_root refusal returns instead of implying /tmp works.
_OUTSIDE_USE = ("typed editors are workspace-only (the journal cannot see a path outside it); "
                "for scratch output use exec(shell=...) or python open(), or keep the file "
                "inside the workspace under bench/.tmp/")
_READS_CELL = {}
# Session-level delivery ledger. A repeated read of a range that was already shown, while
# the file is unchanged, returns a pointer instead of the same text again. `repeat=True`
# opts back into a full delivery, so this is a context optimisation, not a cap.
_READS_SESSION = {}
_READ_MAX_CHARS = 60000
_READ_MAX_LINES = 550
_MUT = False
_JOURNAL = []
_SNAP = {}
_JSEQ = 0
_CELL_CHANGED = []
_open = open
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", "vendor", ".cache",
}
_IGNORE = None


def _ignore_patterns():
    # The root .gitignore patterns; map/search/_snapshot skip generated trees. This is
    # intentionally small: directory prefixes, basename globs and path globs. A `!` negation
    # line is ignored rather than pretending to implement full gitignore.
    global _IGNORE
    if _IGNORE is not None:
        return _IGNORE
    _IGNORE = []
    try:
        with _open(_os.path.join(_ROOT, ".gitignore"), encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue
                _IGNORE.append(line.lstrip("/").rstrip("/"))
    except OSError:
        pass
    return _IGNORE


def _ignored(rel, is_dir=False):
    rel = str(rel).replace("\\", "/").lstrip("./")
    if not rel or rel == ".":
        return False
    base = rel.rsplit("/", 1)[-1]
    for pattern in _ignore_patterns():
        if "/" not in pattern:
            if _fnmatch.fnmatch(base, pattern):
                return True
        elif _fnmatch.fnmatch(rel, pattern) or rel == pattern or rel.startswith(pattern + "/"):
            return True
    return False


def _glob_match(path, pattern):
    # Include globs use shell path semantics: `*`/`?` do not cross `/`, `**` does. This
    # keeps `bench/*.md` to direct children while `**/*.md` walks the tree.
    path = str(path).replace("\\", "/").lstrip("./")
    pattern = str(pattern).replace("\\", "/").lstrip("./")
    if pattern.startswith("**/"):
        if _glob_match(path, pattern[3:]):
            return True
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(_re.escape(ch))
            i += 1
    return _re.fullmatch("".join(out), path) is not None


def _include_match(name, pattern):
    name = str(name).replace("\\", "/").lstrip("./")
    pattern = str(pattern).replace("\\", "/").lstrip("./")
    if any(ch in pattern for ch in "*?["):
        return _glob_match(name, pattern) or _glob_match(name.rsplit("/", 1)[-1], pattern)
    # A literal include is an exact relative path; only a glob is a basename-anywhere
    # pattern. This keeps include=["README.md"] to the root README.
    return name == pattern


def _include_patterns(include):
    value = _coerce_paths(include)
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value else []
"""
