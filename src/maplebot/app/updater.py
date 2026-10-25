"""GitHub ZIP updater. Stdlib only: a copy also runs after the app exits.

Network/download work happens before shutdown; only the detached helper replaces
files, under the same installation lock held by ``python -m maplebot``.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile

STATE = ".maplebot-update.json"
MAX_DOWNLOAD = 80 * 1024 * 1024
MAX_EXPANDED = 250 * 1024 * 1024
MAX_FILES = 12000
SHA = re.compile(r"[0-9a-f]{40}")
HOSTS = {"api.github.com", "codeload.github.com"}
REQUIRED = {"start.bat", "pyproject.toml", "configs/updater.yaml",
            "src/maplebot/__main__.py", "src/maplebot/app/updater.py"}


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Update:
    repository: str
    branch: str
    revision: str
    installed: str | None = None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise UpdateError(f"Cannot read {path.name}: {e}") from e


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _relative(name: str) -> str:
    parts = name.split("/")
    if not name or "\\" in name or any(
        p in {"", ".", ".."} or ":" in p or p.rstrip(" .") != p
        or any(ord(c) < 32 for c in p)
        or re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", p)
        for p in parts
    ):
        raise UpdateError(f"Unsafe archive path: {name!r}")
    return PurePosixPath(name).as_posix()


def _managed(name: str) -> bool:
    """User data, environments, recordings and local overrides are never installed."""
    return (name in {"start.bat", "pyproject.toml", "README.md", "CLAUDE.md"}
            or name.startswith(("src/", "tools/", "docs/", "configs/", "tests/"))) and not (
        name.startswith("configs/local") or "__pycache__" in name.split("/")
        or name.endswith((".pyc", ".pyo")))


def _safe_path(root: Path, name: str) -> Path:
    _relative(name)
    path = root
    for part in name.split("/"):
        path = path / part
        # Refuse links/junctions leading outside the installation, even for backups.
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise UpdateError(f"Cannot update linked path: {name}")
        if not path.resolve().is_relative_to(root.resolve()):
            raise UpdateError(f"Path escapes the installation: {name}")
    return path


def _workspace(root: Path) -> Path:
    work = _safe_path(root, ".updates")
    work.mkdir(exist_ok=True)
    return work


@contextlib.contextmanager
def installation_lock(root: Path):
    """OS releases this lock on crash. App and installer both hold it exclusively."""
    lock = _safe_path(root, ".updates/install.lock")
    _workspace(root)
    f = lock.open("a+b")
    try:
        try:
            if sys.platform == "win32":
                import msvcrt
                if not lock.stat().st_size:
                    f.write(b"0")
                    f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise UpdateError("MapleBot is already open or an update is in progress. "
                              "Close the other copy or wait for the update to finish.") from e
        yield
    finally:
        f.close()


class _GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in HOSTS
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise UpdateError("Updates must come from GitHub over HTTPS.")


def _download(url: str, destination: Path, limit: int) -> None:
    _validate_url(url)
    request = Request(url, headers={"User-Agent": "MapleBot-updater", "Accept": "application/vnd.github+json"})
    try:
        with build_opener(_GitHubRedirect()).open(request, timeout=20) as response:
            _validate_url(response.geturl())
            total = 0
            with destination.open("wb") as out:
                while chunk := response.read(128 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise UpdateError("The update download exceeds the size limit.")
                    out.write(chunk)
    except HTTPError as e:
        if e.code in {403, 429}:
            raise UpdateError("GitHub temporarily limited update checks. Try again later.") from e
        if e.code == 404:
            raise UpdateError("Update branch not found or repository is private. "
                              "This updater needs a public GitHub repository.") from e
        raise UpdateError(f"GitHub returned HTTP {e.code}. Try again later.") from e
    except (URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"Could not download the update. Check your connection. ({e})") from e


def _source(root: Path) -> tuple[str, str]:
    # JSON is also valid YAML; keeping this file JSON-compatible avoids a dependency
    # on PyYAML in the standalone installer and on newer Python versions.
    value = _read_json(root / "configs/updater.yaml")
    if not isinstance(value, dict):
        raise UpdateError("Invalid configs/updater.yaml.")
    repo, branch = value.get("repository", ""), value.get("branch", "")
    if (not isinstance(repo, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo)
            or not isinstance(branch, str) or not branch.strip() or len(branch) > 250):
        raise UpdateError("Invalid repository or branch in configs/updater.yaml.")
    return repo, branch


def _state(root: Path) -> dict:
    path = _safe_path(root, STATE)
    if not path.exists():
        return {}
    state = _read_json(path)
    if (not isinstance(state, dict) or state.get("schema") != 1
            or not isinstance(state.get("files"), dict)
            or not isinstance(state.get("revision"), str)
            or not SHA.fullmatch(state["revision"])):
        raise UpdateError("Installed update record is damaged; restore it from .updates/backups.")
    for name, digest in state["files"].items():
        if not _managed(_relative(name)) or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise UpdateError("Invalid file in the installed update record.")
    return state


def _editable_install(root: Path) -> None:
    if (root / ".git").exists():
        raise UpdateError("This is a developer Git checkout. Use git pull --ff-only here. "
                          "The app's updater is for downloaded ZIP installations.")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_local_files(root: Path, state: dict) -> None:
    for name, digest in state.get("files", {}).items():
        path = _safe_path(root, name)
        if not path.is_file() or _digest(path) != digest:
            raise UpdateError(f"App file was edited or removed locally: {name}. "
                              "Back up your edit and restore the original before updating.")


def check_update(root: Path) -> Update | None:
    _editable_install(root)
    repo, branch = _source(root)
    state = _state(root)
    _check_local_files(root, state)
    with tempfile.TemporaryDirectory() as work:
        payload = Path(work) / "commit.json"
        _download(f"https://api.github.com/repos/{repo}/commits/{quote(branch, safe='')}",
                  payload, 4 * 1024 * 1024)
        commit = _read_json(payload)
    revision = commit.get("sha", "") if isinstance(commit, dict) else ""
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise UpdateError("GitHub did not return a valid commit revision.")
    if state.get("repository") == repo and state.get("revision") == revision:
        return None
    return Update(repo, branch, revision, state.get("revision"))


def _extract(archive: Path, destination: Path) -> dict[str, str]:
    files = {}
    seen = set()
    prefix = None
    with zipfile.ZipFile(archive) as zipped:
        infos = zipped.infolist()
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise UpdateError("The update archive exceeds the extraction limit.")
        for info in infos:
            name = _relative(info.filename.rstrip("/") if info.is_dir() else info.filename)
            head, _, relative = name.partition("/")
            if prefix is None:
                prefix = head
            if head != prefix or (not relative and not info.is_dir()):
                raise UpdateError("The update must contain a single repository folder.")
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise UpdateError("Links and special files are not allowed in updates.")
            if name.casefold() in seen:
                raise UpdateError(f"Duplicate archive path: {name}")
            seen.add(name.casefold())
            if info.is_dir() or not relative or not _managed(relative):
                continue
            target = _safe_path(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
            if relative.endswith(".py"):
                compile(target.read_bytes(), relative, "exec")
            files[relative] = _digest(target)
    if not REQUIRED.issubset(files):
        raise UpdateError("This revision does not contain a compatible MapleBot updater.")
    return files


def prepare_update(root: Path, update: Update) -> Path:
    """Download/validate before closing the GUI. Return a self-contained staging folder."""
    _editable_install(root)
    if not SHA.fullmatch(update.revision):
        raise UpdateError("Invalid update revision.")
    if _source(root) != (update.repository, update.branch):
        raise UpdateError("Update source changed; check for updates again.")
    stage = Path(tempfile.mkdtemp(prefix="pending-", dir=_workspace(root)))
    try:
        archive = stage / "source.zip"
        _download(f"https://api.github.com/repos/{update.repository}/zipball/{update.revision}",
                  archive, MAX_DOWNLOAD)
        files = _extract(archive, stage / "files")
        _write_json(stage / "plan.json", {"schema": 1, "repository": update.repository,
                    "branch": update.branch, "revision": update.revision, "files": files})
        shutil.copyfile(__file__, stage / "installer.py")
        archive.unlink()
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _copy_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".update-tmp")
    created = False
    try:
        # Never follow a leftover temporary symlink.
        if temp.exists() or temp.is_symlink():
            raise UpdateError(f"Remove leftover update temporary file: {temp}")
        with temp.open("xb") as out, source.open("rb") as inp:
            created = True
            shutil.copyfileobj(inp, out)
        os.replace(temp, target)
    finally:
        if created and temp.is_file() and not temp.is_symlink():
            temp.unlink()


def restore_backup(root: Path, backup: Path) -> None:
    journal = _read_json(backup / "journal.json")
    for name, existed in journal.items():
        if name != STATE and not _managed(_relative(name)):
            raise UpdateError("Invalid backup path.")
        target = _safe_path(root, name)
        if existed:
            _copy_atomic(_safe_path(backup / "files", name), target)
        elif target.exists():
            target.unlink()


def _refresh_dependencies(root: Path) -> None:
    # The parent is gone, so Windows no longer has its imported dependency DLLs open.
    subprocess.run([_console_python(), "-m", "pip", "install", "-e", str(root)],
                   cwd=root, check=True, timeout=600)
    stamp = root / ".venv/installed-pyproject.txt"
    if stamp.parent.is_dir():
        _copy_atomic(root / "pyproject.toml", stamp)


def apply_update(root: Path, stage: Path) -> Path:
    """Caller must hold installation_lock. Back up *everything* before the first write."""
    _editable_install(root)
    if stage.parent.resolve() != _workspace(root).resolve() or stage.is_symlink():
        raise UpdateError("Invalid staging folder.")
    plan = _read_json(stage / "plan.json")
    # Reuse the strict installed-state validator for the staged manifest.
    _write_json(stage / STATE, plan)
    plan = _state(stage)
    if not REQUIRED.issubset(plan["files"]):
        raise UpdateError("Incomplete update.")
    if _source(root) != (plan["repository"], plan["branch"]):
        raise UpdateError("Update source changed; check again.")
    for name, digest in plan["files"].items():
        source = _safe_path(stage / "files", name)
        if not source.is_file() or _digest(source) != digest:
            raise UpdateError(f"Downloaded file failed verification: {name}")
    old = _state(root)
    _check_local_files(root, old)
    names = sorted(set(plan["files"]) | set(old.get("files", {})) | {STATE})
    backup = _workspace(root) / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    backup = _safe_path(root, backup.relative_to(root).as_posix())
    backup.mkdir(parents=True)
    journal = {}
    for name in names:
        target = _safe_path(root, name)
        if target.exists() and not target.is_file():
            raise UpdateError(f"Expected a file: {name}")
        journal[name] = target.exists()
        if target.exists():
            _copy_atomic(target, _safe_path(backup / "files", name))
    _write_json(backup / "journal.json", journal)
    dependency_change = (not (root / "pyproject.toml").is_file()
                         or _digest(root / "pyproject.toml") != plan["files"]["pyproject.toml"])
    try:
        for name in names:
            target = _safe_path(root, name)
            if name == STATE:
                continue
            if name in plan["files"]:
                _copy_atomic(stage / "files" / name, target)
            elif target.exists():
                target.unlink()
        if dependency_change:
            _refresh_dependencies(root)
        _copy_atomic(stage / STATE, _safe_path(root, STATE))
    except BaseException as e:
        try:
            restore_backup(root, backup)
            # pip may have changed some dependencies before failing. Force the
            # launcher to repair the old project's requirements on the next start.
            if dependency_change:
                (root / ".venv/installed-pyproject.txt").unlink(missing_ok=True)
        except BaseException as rollback_error:
            raise UpdateError(f"Update and rollback failed. Recover using {backup}. "
                              f"Update error: {e}; rollback error: {rollback_error}") from e
        raise UpdateError(f"Update failed; previous app files restored. Backup: {backup}. "
                          f"{e}") from e
    return backup


def _console_python() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return str(executable)


def launch_installer(root: Path, stage: Path) -> None:
    subprocess.Popen([_console_python(), "-I", str(stage / "installer.py"),
                      "--install", str(root), str(stage), str(os.getpid())],
                     cwd=root, close_fds=True,
                     creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0)


def _wait_for_parent(pid: int, timeout: float = 60) -> None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if not handle:
            if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: process gone
                return
            raise UpdateError("Could not wait for MapleBot to exit safely.")
        try:
            if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
                raise UpdateError("MapleBot did not exit. No files were changed.")
        finally:
            kernel.CloseHandle(handle)
    else:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        raise UpdateError("MapleBot did not exit. No files were changed.")


def _restart(root: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(root / "start.bat"))
    else:
        subprocess.Popen([sys.executable, "-m", "maplebot"], cwd=root)


def _main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--install-dependencies":
        root = Path(sys.argv[2]).resolve()
        try:
            with installation_lock(root):
                _refresh_dependencies(root)
            return 0
        except UpdateError as e:
            print(str(e), flush=True)
            return 3
        except Exception:
            import traceback
            traceback.print_exc()
            return 1
    if len(sys.argv) == 4 and sys.argv[1] == "--restore":
        root, backup = Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
        if backup.parent != (_workspace(root) / "backups").resolve():
            raise UpdateError("Select a backup inside this installation's .updates/backups.")
        with installation_lock(root):
            restore_backup(root, backup)
            (root / ".venv/installed-pyproject.txt").unlink(missing_ok=True)
        print("Previous app files restored. Open start.bat to repair dependencies and restart.")
        return 0
    if len(sys.argv) != 5 or sys.argv[1] != "--install":
        print("This helper is launched by MapleBot's update button.\n"
              "Recovery: python -m maplebot.app.updater --restore APP_FOLDER BACKUP_FOLDER")
        return 2
    root, stage, parent = Path(sys.argv[2]).resolve(), Path(sys.argv[3]), int(sys.argv[4])
    logging.basicConfig(filename=_workspace(root) / "update.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        print("Waiting for MapleBot to close…", flush=True)
        _wait_for_parent(parent)
        with installation_lock(root):
            print("Backing up and installing the update…", flush=True)
            backup = apply_update(root, stage)
            logging.info("Update installed; backup: %s", backup)
        shutil.rmtree(stage, ignore_errors=True)
        _restart(root)
        return 0
    except BaseException as e:
        logging.exception("Update failed")
        text = (f"{e}\n\nDetails: {root / '.updates/update.log'}\n"
                "Close this window and use start.bat to reopen MapleBot.")
        print(text, flush=True)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "MapleBot update failed", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
