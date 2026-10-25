"""Offline update integration: real ZIPs, filesystem transactions and process locks."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest

from maplebot.app import updater as U

REPO = "jeffreycao1998/maplestory-automation"
BRANCH = "codex/smarter-navigation-and-looting"
REV = "a" * 40


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def package(extra=None):
    contents = {name: "# app code\n" for name in U.REQUIRED}
    contents["pyproject.toml"] = "[project]\nname = 'maplebot'\nversion = '0.1.0'\n"
    contents["configs/updater.yaml"] = json.dumps({"repository": REPO, "branch": BRANCH})
    contents.update(extra or {})
    return contents


def archive(contents):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, value in contents.items():
            zipped.writestr("owner-repo-abcdef0/" + name, value)
    return buffer.getvalue()


@pytest.fixture
def app(tmp_path):
    for name, value in package({"src/maplebot/old.py": "old = True\n"}).items():
        write(tmp_path, name, value)
    for name in ["profile/settings.yaml", "logs/run.jsonl", "data/reference-bank/a.png",
                 "data/recordings/run/frame.png", ".venv/keep.txt", "configs/local.yaml"]:
        write(tmp_path, name, "user data")
    return tmp_path


def prepare(monkeypatch, root, contents=None, revision=REV):
    payload = archive(contents or package({"src/maplebot/new.py": "new = True\n"}))
    def download(url, path, limit):
        assert url.endswith("/zipball/" + revision)
        path.write_bytes(payload)
    monkeypatch.setattr(U, "_download", download)
    return U.prepare_update(root, U.Update(REPO, BRANCH, revision))


def test_installs_and_preserves_user_data_then_removes_only_previous_managed_files(app, monkeypatch):
    before = {p.relative_to(app): p.read_bytes() for p in app.rglob("*") if p.is_file()}
    first = prepare(monkeypatch, app)
    backup = U.apply_update(app, first)
    assert (backup / "journal.json").is_file()
    assert U._state(app)["revision"] == REV
    for name, contents in before.items():
        if not U._managed(name.as_posix()):
            assert (app / name).read_bytes() == contents
    # Pre-updater files of unknown ownership are preserved. After the first update,
    # the installed manifest tells us which removed files belong to the app.
    assert (app / "src/maplebot/old.py").exists()
    second = prepare(monkeypatch, app, package({"src/maplebot/replacement.py": "new = 2\n"}), "b" * 40)
    U.apply_update(app, second)
    assert not (app / "src/maplebot/new.py").exists()
    assert (app / "src/maplebot/replacement.py").is_file()
    assert (app / "src/maplebot/old.py").exists()


def test_partial_copy_failure_rolls_back_all_app_files(app, monkeypatch):
    stage = prepare(monkeypatch, app, package({"src/maplebot/new.py": "new = True\n",
                                               "src/maplebot/z.py": "last = True\n"}))
    before = {p.relative_to(app): p.read_bytes() for p in app.rglob("*")
              if p.is_file() and ".updates" not in p.parts}
    real_copy = U._copy_atomic
    failed = False
    def failing_copy(source, target):
        nonlocal failed
        if target == app / "src/maplebot/z.py" and not failed:
            failed = True
            raise OSError("simulated disk write failure")
        real_copy(source, target)
    monkeypatch.setattr(U, "_copy_atomic", failing_copy)
    with pytest.raises(U.UpdateError, match="previous app files restored"):
        U.apply_update(app, stage)
    after = {p.relative_to(app): p.read_bytes() for p in app.rglob("*")
             if p.is_file() and ".updates" not in p.parts}
    assert after == before


def test_dependency_failure_restores_code_and_invalidates_launcher_stamp(app, monkeypatch):
    original = (app / "pyproject.toml").read_text()
    stamp = write(app, ".venv/installed-pyproject.txt", original)
    stage = prepare(monkeypatch, app, package({"pyproject.toml": original + "# changed\n"}))
    def fail(root):
        raise subprocess.CalledProcessError(1, ["pip"])
    monkeypatch.setattr(U, "_refresh_dependencies", fail)
    with pytest.raises(U.UpdateError, match="previous app files restored"):
        U.apply_update(app, stage)
    assert (app / "pyproject.toml").read_text() == original
    assert not stamp.exists()
    assert not (app / U.STATE).exists()


def test_dependencies_only_reinstalled_when_project_requirements_change(app, monkeypatch):
    calls = []
    monkeypatch.setattr(U, "_refresh_dependencies", lambda root: calls.append(root))
    U.apply_update(app, prepare(monkeypatch, app))
    assert calls == []
    new_project = (app / "pyproject.toml").read_text() + "# new requirements\n"
    stage = prepare(monkeypatch, app, package({"pyproject.toml": new_project}), "b" * 40)
    U.apply_update(app, stage)
    assert calls == [app]


def test_detects_tampered_download_before_writing_anything(app, monkeypatch):
    stage = prepare(monkeypatch, app)
    write(stage / "files", "src/maplebot/new.py", "tampered = True\n")
    with pytest.raises(U.UpdateError, match="failed verification"):
        U.apply_update(app, stage)
    assert not (app / U.STATE).exists()
    assert not (app / "src/maplebot/new.py").exists()
    assert not (app / ".updates/backups").exists()


def test_local_edits_are_protected(app, monkeypatch):
    U.apply_update(app, prepare(monkeypatch, app))
    write(app, "src/maplebot/new.py", "my_custom_changes = True\n")
    with pytest.raises(U.UpdateError, match="edited or removed locally"):
        U.check_update(app)
    stage = prepare(monkeypatch, app, revision="b" * 40)
    with pytest.raises(U.UpdateError, match="edited or removed locally"):
        U.apply_update(app, stage)
    assert "my_custom_changes" in (app / "src/maplebot/new.py").read_text()


def test_git_checkout_is_never_overwritten(app, monkeypatch):
    write(app, ".git", "gitdir: another-worktree")
    with pytest.raises(U.UpdateError, match="developer Git checkout"):
        U.check_update(app)
    with pytest.raises(U.UpdateError, match="developer Git checkout"):
        prepare(monkeypatch, app)


def test_new_revision_check_and_up_to_date(app, monkeypatch):
    urls = []
    def download(url, path, limit):
        urls.append(url)
        path.write_text(json.dumps({"sha": REV}))
    monkeypatch.setattr(U, "_download", download)
    found = U.check_update(app)
    assert found == U.Update(REPO, BRANCH, REV, None)
    assert urls[0].endswith("/commits/codex%2Fsmarter-navigation-and-looting")
    U._write_json(app / U.STATE, {"schema": 1, "revision": REV, "repository": REPO, "files": {}})
    assert U.check_update(app) is None


@pytest.mark.parametrize("revision", ["not-a-commit", "../etc", "A" * 40, "a" * 39, None])
def test_rejects_invalid_api_revision(app, monkeypatch, revision):
    monkeypatch.setattr(U, "_download", lambda url, p, limit: p.write_text(json.dumps({"sha": revision})))
    with pytest.raises(U.UpdateError, match="valid commit"):
        U.check_update(app)


@pytest.mark.parametrize("name", ["../outside.py", "/absolute.py", "src/../../escape.py",
                                  "src/back\\slash.py", "src/C:escape.py", "src/NUL.py",
                                  "src/enddot./x", "src/endspace /x", "src//x"])
def test_rejects_unsafe_archives_before_install(app, monkeypatch, name):
    with pytest.raises(U.UpdateError, match="Unsafe archive path"):
        prepare(monkeypatch, app, package({name: "bad"}))
    assert list((app / ".updates").iterdir()) == []


def test_rejects_case_collisions_and_symbolic_links(app, monkeypatch):
    with pytest.raises(U.UpdateError, match="Duplicate archive path"):
        prepare(monkeypatch, app, package({"src/a.py": "", "src/A.py": ""}))
    path = app / "symlink.zip"
    with zipfile.ZipFile(path, "w") as zipped:
        info = zipfile.ZipInfo("folder/src/link.py")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(info, "../../elsewhere")
    with pytest.raises(U.UpdateError, match="Links and special files"):
        U._extract(path, app / "extract")


def test_preserves_paths_not_owned_by_app_even_if_in_archive(app, monkeypatch):
    stage = prepare(monkeypatch, app, package({"profile/settings.yaml": "wrong",
        "data/reference-bank/a.png": "wrong", "configs/local.yaml": "wrong",
        ".venv/keep.txt": "wrong", "logs/run.jsonl": "wrong"}))
    U.apply_update(app, stage)
    for name in ["profile/settings.yaml", "data/reference-bank/a.png", "configs/local.yaml",
                 ".venv/keep.txt", "logs/run.jsonl"]:
        assert (app / name).read_text() == "user data"


def test_archive_limits_and_incompatible_versions(app, monkeypatch):
    monkeypatch.setattr(U, "MAX_EXPANDED", 10)
    with pytest.raises(U.UpdateError, match="extraction limit"):
        prepare(monkeypatch, app)
    monkeypatch.setattr(U, "MAX_EXPANDED", 250 * 1024 * 1024)
    with pytest.raises(U.UpdateError, match="compatible MapleBot updater"):
        prepare(monkeypatch, app, {"README.md": "old version"})
    with pytest.raises(SyntaxError):
        prepare(monkeypatch, app, package({"src/broken.py": "if ("}))


def test_existing_symlink_is_not_followed(app, tmp_path_factory, monkeypatch):
    outside = tmp_path_factory.mktemp("outside")
    original = write(outside, "code.py", "outside = True\n")
    (app / "src/linked.py").symlink_to(original)
    stage = prepare(monkeypatch, app, package({"src/linked.py": "outside = False\n"}))
    with pytest.raises(U.UpdateError, match="linked path"):
        U.apply_update(app, stage)
    assert original.read_text() == "outside = True\n"


def test_manually_restoring_backup_removes_new_files(app, monkeypatch):
    backup = U.apply_update(app, prepare(monkeypatch, app))
    assert (app / "src/maplebot/new.py").exists()
    U.restore_backup(app, backup)
    assert not (app / "src/maplebot/new.py").exists()
    assert not (app / U.STATE).exists()
    assert (app / "src/maplebot/old.py").exists()


def test_installation_lock_excludes_another_process_and_releases_on_exit(app):
    code = ("import pathlib,sys\nfrom maplebot.app.updater import installation_lock,UpdateError\n"
            "try:\n with installation_lock(pathlib.Path(sys.argv[1])): print('acquired')\n"
            "except UpdateError: print('busy')\n")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    with U.installation_lock(app):
        result = subprocess.run([sys.executable, "-c", code, str(app)], capture_output=True, text=True, env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "busy"
    result = subprocess.run([sys.executable, "-c", code, str(app)], capture_output=True, text=True, env=env)
    assert result.stdout.strip() == "acquired"


@pytest.mark.parametrize("url", ["http://api.github.com/a", "https://evil.com/a",
    "https://api.github.com.evil.com/a", "https://user:pass@api.github.com/a", "https://api.github.com:8443/a"])
def test_rejects_untrusted_download_and_redirect_urls(url):
    with pytest.raises(U.UpdateError, match="HTTPS"):
        U._validate_url(url)
    with pytest.raises(U.UpdateError, match="HTTPS"):
        U._GitHubRedirect().redirect_request(None, None, 302, "", {}, url)


def test_download_size_limit(app, monkeypatch):
    class Response(io.BytesIO):
        def geturl(self):
            return "https://api.github.com/a"
    class Opener:
        def open(self, request, timeout):
            return Response(b"too much data")
    monkeypatch.setattr(U, "build_opener", lambda handler: Opener())
    with pytest.raises(U.UpdateError, match="size limit"):
        U._download("https://api.github.com/a", app / "download", 3)


def test_detached_installer_uses_argument_list_not_shell(app, monkeypatch):
    calls = []
    monkeypatch.setattr(U.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))
    stage = app / ".updates/pending-with spaces"
    U.launch_installer(app, stage)
    args, kwargs = calls[0]
    assert args[1:3] == ["-I", str(stage / "installer.py")]
    assert args[3] == "--install"
    assert kwargs.get("shell", False) is False
    assert kwargs["close_fds"] is True


def test_copy_does_not_delete_preexisting_temporary_file(app):
    source = write(app, "source", "new")
    target = write(app, "target", "original")
    temporary = write(app, "target.update-tmp", "leftover")
    with pytest.raises(U.UpdateError, match="leftover"):
        U._copy_atomic(source, target)
    assert target.read_text() == "original"
    assert temporary.read_text() == "leftover"


def test_standalone_installer_transaction_and_restart_without_importing_app(app, monkeypatch):
    stage = prepare(monkeypatch, app)
    parent = subprocess.Popen([sys.executable, "-c", "pass"])
    parent.wait(timeout=5)
    code = ("import sys,pathlib\n"
            "sys.path.insert(0, sys.argv[1])\nimport installer\n"
            "installer._restart = lambda root: (root / 'restarted').write_text('yes')\n"
            "sys.argv = ['installer.py', '--install', sys.argv[2], sys.argv[1], sys.argv[3]]\n"
            "raise SystemExit(installer._main())\n")
    result = subprocess.run([sys.executable, "-I", "-c", code, str(stage), str(app), str(parent.pid)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / "restarted").read_text() == "yes"
    assert U._state(app)["revision"] == REV
    assert not stage.exists()


def test_failed_download_cleans_staging_and_keeps_installed_files(app, monkeypatch):
    def fail(url, path, limit):
        path.write_bytes(b"incomplete")
        raise U.UpdateError("Connection interrupted")
    monkeypatch.setattr(U, "_download", fail)
    with pytest.raises(U.UpdateError, match="Connection interrupted"):
        U.prepare_update(app, U.Update(REPO, BRANCH, REV))
    assert list((app / ".updates").iterdir()) == []
    assert not (app / U.STATE).exists()


def test_gui_blocks_start_and_update_while_busy():
    from maplebot.app.gui import App
    from types import SimpleNamespace
    app = App.__new__(App)
    app._closing = False
    app._updating = True
    app.thread = None
    # Guards must return before accessing any widgets or starting a bot/network worker.
    app.start()
    app.start(observe_only=True)
    app.check_for_updates()
    app._updating = False
    app.thread = SimpleNamespace(is_alive=lambda: True)
    app.check_for_updates()


def test_failed_helper_launch_keeps_gui_open(app, monkeypatch):
    from maplebot.app.gui import App
    from types import SimpleNamespace
    from unittest.mock import Mock
    stage = prepare(monkeypatch, app)
    window = App.__new__(App)
    window.thread = None
    window.root = object()
    window._updating = True
    window.update_lbl = Mock()
    window._refresh_buttons = Mock()
    window.on_close = Mock()
    messages = SimpleNamespace(showerror=Mock())
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(messagebox=messages))
    def fail(root, stage):
        raise OSError("Cannot launch installer")
    monkeypatch.setattr(U, "launch_installer", fail)
    window._update_event("update_prepared", stage)
    window.on_close.assert_not_called()
    messages.showerror.assert_called_once()
    assert window._updating is False
    assert not stage.exists()


def test_dependency_bootstrap_holds_same_lock_as_app_and_installer(app, monkeypatch):
    calls = []
    monkeypatch.setattr(U, "_refresh_dependencies", lambda root: calls.append(root))
    monkeypatch.setattr(sys, "argv", ["updater.py", "--install-dependencies", str(app)])
    with U.installation_lock(app):
        assert U._main() == 3
        assert not calls
    assert U._main() == 0
    assert calls == [app]
