"""snapshot: freeze a tree into a sha256 manifest, clone it, diff against it.

WHAT IT IS
  `freeze(src, dst)` copies a tree and writes `dst.manifest.json`; `changed(run)` reports
  added/modified/deleted relative to that manifest; `clone(src, dst)` copies without a
  manifest for throwaway runs. `bench/ws/tui-base` and `bench/ws/tui-broken` are frozen this
  way. (`bench/ws/` itself is generated output; this module used to be `bench/ws.py`, which
  shadowed that directory name.)

ENTRY POINTS
  python3 -m bench.snapshot freeze SRC DST     clone, then write DST.manifest.json
  python3 -m bench.snapshot clone  SRC DST     copy only (no manifest)
  python3 -m bench.snapshot diff   DST         added/modified/deleted vs DST.manifest.json
  manifest(root, write=True) -> dict           {"root": ..., "files": {rel: sha256[:16]}}
  clone(src, dst)                              copy, ignoring generated dirs, symlinking node_modules
  changed(run) -> {"added", "modified", "deleted"}

INVARIANTS
  - Hashes are truncated to 16 hex chars; the manifest is the reference state, so a frozen
    workspace must never be edited in place — clone it first (`snapshot.clone`).
  - `IGNORE_PARTS` is the one list of generated directories, used by both the copytree
    filter and `_files`; a new generated directory goes there once or every diff is noise.
  - Symlinks are skipped: a symlinked fixture will not appear in the manifest.

WHERE TO CHANGE
  Ignore rules ......... IGNORE_PARTS
  Manifest format ...... `manifest`, `changed` (used by bench/live.py)
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

# One list, two uses: the copy filter and the manifest walk must agree or diffs gain noise.
IGNORE_PARTS = ("node_modules", ".git", "dist", "build", "__pycache__", ".cache", ".next", ".bun",
                "coverage")
IGNORE = shutil.ignore_patterns(*IGNORE_PARTS)


def _files(root: Path) -> dict[str, str]:
    """{relpath: sha256[:16]} for every regular file not under an IGNORE_PARTS directory."""
    rows: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in IGNORE_PARTS for part in Path(rel).parts):
            continue
        rows[rel] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return rows


def manifest(root: Path, write: bool = True) -> dict:
    """Hash the tree; write `<root>.manifest.json` next to it unless write=False."""
    rows = _files(root)
    data = {"root": str(root.resolve()), "files": rows}
    if write:
        Path(str(root.resolve()) + ".manifest.json").write_text(json.dumps(data, indent=2))
    return data


def clone(src: Path, dst: Path) -> None:
    """Copy `src` to `dst` (replacing `dst`), symlinking node_modules and carrying a manifest."""
    src, dst = src.resolve(), dst.resolve()
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=IGNORE, symlinks=True)
    modules = src / "node_modules"
    if modules.exists() and not (dst / "node_modules").exists():
        (dst / "node_modules").symlink_to(modules)
    base = Path(str(src) + ".manifest.json")
    if base.exists():
        Path(str(dst) + ".manifest.json").write_text(base.read_text())


def changed(run: Path) -> dict:
    """Three sorted relpath lists against `<run>.manifest.json`; all empty when there is none."""
    base_path = Path(str(run.resolve()) + ".manifest.json")
    if not base_path.exists():
        return {"added": [], "modified": [], "deleted": []}
    base = json.loads(base_path.read_text())["files"]
    now = _files(run.resolve())
    added = sorted(set(now) - set(base))
    deleted = sorted(set(base) - set(now))
    modified = sorted(rel for rel in set(base) & set(now) if base[rel] != now[rel])
    return {"added": added, "modified": modified, "deleted": deleted}


def main() -> int:
    """CLI: freeze|clone|diff. Exit 2 on usage or an unknown action."""
    if len(sys.argv) < 3:
        print("usage: python3 -m bench.snapshot freeze|clone|diff SRC DST", file=sys.stderr)
        return 2
    action, src = sys.argv[1], Path(sys.argv[2])
    if action == "freeze":
        dst = Path(sys.argv[3]).resolve()
        clone(src, dst)
        manifest(dst)
        print(f"frozen {dst} ({len(_files(dst))} files)")
        return 0
    if action == "clone":
        dst = Path(sys.argv[3]).resolve()
        clone(src, dst)
        print(f"cloned {dst}")
        return 0
    if action == "diff":
        print(json.dumps(changed(src), indent=2))
        return 0
    print(f"unknown action {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
