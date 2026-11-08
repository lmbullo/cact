"""remote: a Daytona sandbox, used by bench for a disposable machine per run.

WHAT IT IS
  The one adapter between `bench` and Daytona. `bench` is stdlib-only, so the SDK is
  imported lazily: importing this module never requires `daytona`, and every entry point
  answers with a structured refusal when the SDK or the key is missing. A sandbox is a
  throwaway Linux box for a run that must not touch the host - an eval case, an untrusted
  repo, or a check that needs a clean tree - not the normal runtime of the TUI loop.

ENTRY POINTS
  status() -> {sdk, key, hint}
  create(image=None, snapshot=None, env=None, auto_stop=30, auto_delete=120) -> Sandbox
  Sandbox.code(src) / .run(argv_or_str, cwd, timeout) / .upload(bytes, path)
    .download(path) / .upload_tree(src, dest) / .download_tree(src, dest) / .delete()
    .id
  python3 -m bench.remote --check      offline: is the SDK installed and the key found
  python3 -m bench.remote --self-test  create, run, upload/download, delete (spends credit)

INVARIANTS
  - The key comes from `DAYTONA_API_KEY`, else `~/.config/code-tui/{env,.env}` - the same
    files `tui/src/launcher.ts` loads. It is never read from the repo and never logged.
  - `create()` always sets an auto-stop interval, so a crash in the caller cannot leave a
    sandbox billing indefinitely. `delete()` is idempotent.
  - `run()` takes argv, never a shell line: it is joined with `shlex.join` and executed with
    `cwd`, so a caller's argument cannot become shell syntax by accident.
  - Nothing here writes inside the repo, imports `bench.agent`, or needs the TUI.

WHERE TO CHANGE
  Key discovery / environment ... api_key()
  Sandbox sizing and lifetime ... create()
  Transfers .................... upload_tree / download_tree
"""
from __future__ import annotations

import io
import json
import os
import shlex
import tarfile
from pathlib import Path
from typing import Any

CONFIG_FILES = ("env", ".env")
DEFAULT_IMAGE = "python:3.12"


class RemoteError(RuntimeError):
    """Daytona is unavailable or a sandbox operation failed; the message says which."""


def _config_value(name: str) -> str | None:
    """`name` from the environment, else from ~/.config/code-tui/{env,.env}; None if absent."""
    value = os.environ.get(name)
    if value:
        return value.strip()
    for candidate in CONFIG_FILES:
        path = Path.home() / ".config" / "code-tui" / candidate
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() == name:
                return raw.strip().strip("'\"")
    return None


def api_key() -> str | None:
    """The Daytona API key, or None when neither the environment nor the config has one."""
    return _config_value("DAYTONA_API_KEY")


def _sdk() -> Any:
    try:
        import daytona
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise RemoteError("the daytona SDK is not installed: python3 -m pip install --user daytona") from exc
    return daytona


def status() -> dict:
    """`{sdk, key, hint}`: whether the SDK imports and a key is findable, without any network."""
    try:
        _sdk()
        sdk = True
    except RemoteError:
        sdk = False
    key = bool(api_key())
    hint = ""
    if not sdk:
        hint = "python3 -m pip install --user daytona"
    elif not key:
        hint = "set DAYTONA_API_KEY in the environment or ~/.config/code-tui/env"
    return {"sdk": sdk, "key": key, "hint": hint}


class Sandbox:
    """One Daytona sandbox: argv exec, code run, file transfer, and delete.

    Wraps the SDK object so callers never import `daytona` themselves; every method raises
    `RemoteError` on a transport failure instead of leaking an SDK exception type.
    """

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def id(self) -> str:
        """The provider's sandbox id."""
        return str(getattr(self._raw, "id", "") or "")

    def code(self, source: str, timeout: int = 120) -> dict:
        """Run Python `source` in the sandbox; `{ok, exit, output}`."""
        try:
            reply = self._raw.process.code_run(source, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - one error type for callers
            raise RemoteError(f"code_run failed: {exc}") from exc
        return {"ok": int(reply.exit_code or 0) == 0, "exit": int(reply.exit_code or 0),
                "output": reply.result or ""}

    def run(self, argv: Any, cwd: str | None = None, env: dict | None = None,
            timeout: int = 120) -> dict:
        """Run one argv in the sandbox; `{ok, exit, output, argv}`. Never a shell string."""
        command = argv if isinstance(argv, str) else shlex.join(str(part) for part in argv)
        try:
            reply = self._raw.process.exec(command, cwd=cwd, env=env or None, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"exec failed: {exc}") from exc
        code = int(reply.exit_code or 0)
        return {"ok": code == 0, "exit": code, "output": reply.result or "", "argv": command}

    def upload(self, data: bytes, path: str) -> None:
        """Write `data` to `path` (its parent must exist)."""
        try:
            self._raw.fs.upload_file(data, path)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"upload to {path} failed: {exc}") from exc

    def download(self, path: str) -> bytes | None:
        """Read `path` back; None when the provider returns nothing."""
        try:
            return self._raw.fs.download_file(path)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"download of {path} failed: {exc}") from exc

    def upload_tree(self, src: Path, dest: str) -> None:
        """Tar `src` locally and unpack it at `dest` in the sandbox (one transfer, not N)."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            for item in sorted(Path(src).rglob("*")):
                if "__pycache__" in item.parts or ".git" in item.parts:
                    continue
                bundle.add(item, arcname=str(item.relative_to(src)))
        result = self.run(["mkdir", "-p", dest], timeout=60)
        if not result["ok"]:
            raise RemoteError(f"mkdir {dest} failed: {result['output'][:200]}")
        self.upload(buffer.getvalue(), f"{dest.rstrip('/')}/tree.tgz")
        result = self.run(["tar", "xzf", f"{dest.rstrip('/')}/tree.tgz", "-C", dest], timeout=300)
        if not result["ok"]:
            raise RemoteError(f"untar at {dest} failed: {result['output'][:200]}")

    def download_tree(self, src: str, dest: Path) -> None:
        """Tar `src` in the sandbox and unpack it under `dest` locally."""
        archive = "/tmp/bench-download.tgz"
        result = self.run(["tar", "czf", archive, "-C", src, "."], timeout=300)
        if not result["ok"]:
            raise RemoteError(f"tar of {src} failed: {result['output'][:200]}")
        payload = self.download(archive)
        if payload is None:
            raise RemoteError(f"download of {archive} returned nothing")
        Path(dest).mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as bundle:
            bundle.extractall(dest)

    def delete(self) -> None:
        """Delete the sandbox. Safe to call twice."""
        try:
            self._raw.delete()
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"delete failed: {exc}") from exc


def create(image: str | None = None, snapshot: str | None = None, env: dict | None = None,
           auto_stop: int = 30, auto_delete: int = 120, timeout: int = 180) -> Sandbox:
    """Start one sandbox from `snapshot` (preferred) or `image`; auto-stop is always set."""
    daytona = _sdk()
    key = api_key()
    if not key:
        raise RemoteError("no DAYTONA_API_KEY: set it in the environment or ~/.config/code-tui/env")
    params: Any
    if snapshot:
        params = daytona.CreateSandboxFromSnapshotParams(
            snapshot=snapshot, env_vars=env or {}, auto_stop_interval=auto_stop,
            auto_delete_interval=auto_delete)
    else:
        params = daytona.CreateSandboxFromImageParams(
            image=image or DEFAULT_IMAGE, env_vars=env or {}, auto_stop_interval=auto_stop,
            auto_delete_interval=auto_delete)
    try:
        raw = daytona.Daytona(daytona.DaytonaConfig(api_key=key)).create(params, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise RemoteError(f"could not create a sandbox: {exc}") from exc
    return Sandbox(raw)


def main(argv: list[str] | None = None) -> int:
    """`--check` (offline) or `--self-test` (creates and deletes one sandbox)."""
    args = list(argv if argv is not None else __import__("sys").argv[1:])
    if "--self-test" in args:
        box = create(auto_stop=15, auto_delete=60)
        try:
            print(json.dumps({"id": box.id,
                              "code": box.code('print("hello from daytona")'),
                              "exec": box.run(["uname", "-sm"])}, indent=2))
        finally:
            box.delete()
        return 0
    report = status()
    print(json.dumps(report))
    return 0 if report["sdk"] and report["key"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
