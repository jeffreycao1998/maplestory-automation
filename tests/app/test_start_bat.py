"""start.bat: structure checks plus every launch path walked in a cmd.exe simulator."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from batsim import Batch, World

BAT = Path(__file__).resolve().parents[2] / "start.bat"
RAW = BAT.read_bytes()
TEXT = RAW.decode("ascii")


def labels():
    # A label is a whole line ":name" (anchored on line breaks, so "goto :x" never counts).
    return re.findall(r"(?:^|\r\n):([A-Za-z_]+)(?=\r\n)", TEXT)


def targets():
    return re.findall(r"(?i)\b(?:goto|call) :([A-Za-z_]+)", TEXT)


def test_crlf_only():
    assert b"\r\n" in RAW and b"\n" not in RAW.replace(b"\r\n", b"")


def test_every_label_defined_exactly_once():
    seen = labels()
    dupes = sorted({l for l in seen if seen.count(l) > 1})
    assert not dupes, f"duplicated labels: {dupes}"


def test_every_goto_and_call_target_exists():
    missing = sorted(set(targets()) - set(labels()))
    assert not missing, f"goto/call to undefined labels: {missing}"


def test_no_unused_labels():
    unused = sorted(set(labels()) - set(targets()))
    assert not unused, f"labels nothing jumps to: {unused}"


def test_no_parenthesised_blocks_and_safe_powershell_quoting():
    assert not re.search(r"(?m)^\s*if [^\r\n]*\($", TEXT)
    assert "$env:MAPLEBOT_SELF" in TEXT and "'%~f0'" not in TEXT
    assert "imagename eq pythonw.exe" not in TEXT


def test_helpers_are_only_reached_by_call():
    # Code right before each helper must end the main flow, so it never falls into one.
    lines = TEXT.split("\r\n")
    for name in ("pid_alive", "pyw_alive"):
        k = lines.index(":" + name)
        prev = [l.strip() for l in lines[:k] if l.strip() and not l.strip().lower().startswith("rem")]
        assert prev[-1].lower().startswith(("exit /b", "goto ")), (name, prev[-1])
        assert f"goto :{name}" not in TEXT


# ---------------------------------------------------------------- traced paths

def run(**kw) -> World:
    return Batch(TEXT, World(**kw)).run()


def test_path_normal_run():
    w = run()
    assert w.exit_code == 0 and w.started_pythonw == 1 and w.started_console == 0
    assert w.pip_installs == 0 and w.pauses == 0 and w.t <= 6
    assert not w.pid_deleted_while_alive


def test_path_first_run():
    w = run(venv_exists=False, installed_matches=None)
    assert w.pip_installs == 1 and w.started_pythonw == 1 and w.exit_code == 0
    assert w.pauses == 0


def test_path_pyproject_changed_reinstalls_then_launches():
    w = run(installed_matches=False)
    assert w.pip_installs == 1 and w.started_pythonw == 1 and w.exit_code == 0
    assert w.trace.index("install") < w.trace.index("launch") if "install" in w.trace else True


def test_path_install_failed():
    w = run(installed_matches=False, pip_ok=False)
    assert w.exit_code == 1 and w.started_pythonw == 0 and w.pauses == 1
    assert any("Installing failed" in e for e in w.echoed)


@pytest.mark.parametrize("busy", [{"update_in_progress": True}, {"already_open": True}])
def test_dependency_install_does_not_race_app_or_updater(busy):
    w = run(installed_matches=False, **busy)
    assert w.exit_code == 3 and w.pip_installs == 0
    assert w.started_pythonw == 0 and w.started_console == 0
    assert any("open or updating" in e for e in w.echoed)


def test_path_app_already_open():
    w = run(already_open=True)
    assert w.exit_code == 0 and not w.pid_deleted_while_alive
    assert w.started_console == 0 and w.pauses == 0
    assert any("already open" in e for e in w.echoed)


def test_path_stale_pid_file_is_replaced():
    w = run(stale_pid=True)
    assert w.exit_code == 0 and w.started_pythonw == 1 and w.started_console == 0


def test_path_slow_start_waits_without_second_copy():
    w = run(launch="slow", launch_delay_s=40)
    assert w.exit_code == 0 and w.started_pythonw == 1 and w.started_console == 0
    assert w.pauses == 0
    assert any("taking a while" in e for e in w.echoed)


def test_path_hang_never_starts_second_copy():
    w = run(launch="hang")
    assert w.started_console == 0 and w.started_pythonw == 1
    assert w.pauses == 1 and any("still starting" in e for e in w.echoed)


def test_path_pythonw_crashes_after_start_shows_console():
    w = run(launch="crash")
    assert w.started_console == 1 and w.exit_code == 1 and w.pauses == 1


def test_path_pythonw_dies_immediately_shows_console():
    w = run(launch="dead")
    assert w.started_console == 1 and w.exit_code == 1 and w.pauses == 1
    assert w.t <= 20


def test_path_no_pythonw_shows_console():
    w = run(pythonw_exists=False)
    assert w.started_pythonw == 0 and w.started_console == 1 and w.exit_code == 1


def test_path_venv_failure():
    w = run(venv_exists=False, installed_matches=None, venv_create_ok=False)
    assert w.exit_code == 1 and w.pauses == 1 and w.started_pythonw == 0
    assert any("Couldn't create the .venv folder" in e for e in w.echoed)


def test_path_python_missing():
    w = run(python_ok=False)
    assert w.exit_code == 1 and w.pauses == 1 and w.started_pythonw == 0
    assert any("Python 3.10 or newer is needed" in e for e in w.echoed)


def test_path_not_admin_relaunches_elevated():
    w = run(admin=False)
    assert w.exit_code == 0 and w.started_pythonw == 0 and w.pauses == 0


def test_path_elevation_refused():
    w = run(admin=False, elevation_ok=False)
    assert w.exit_code == 1 and w.pauses == 1 and w.started_pythonw == 0
    assert any("Admin rights were not granted" in e for e in w.echoed)


def test_path_elevated_but_still_not_admin_does_not_loop():
    w = run(admin=False, elevated_arg=True)
    assert w.exit_code == 1 and w.pauses == 1


def test_simulator_catches_duplicate_labels():
    """The bug from review: an old launch block left after the new one."""
    stale = (":launch\r\n" 'if exist "logs\\app.pid" del /q "logs\\app.pid" >nul 2>&1\r\n'
             'start "MapleBot" /b ".venv\\Scripts\\pythonw.exe" -m maplebot\r\n'
             "exit /b 0\r\n")
    # goto searches forward first, so a stale copy placed before the real label wins
    assert TEXT.count("\r\n:install\r\n") == 1
    bad = TEXT.replace("\r\n:install\r\n", "\r\n" + stale + ":install\r\n", 1)
    assert len(re.findall(r"(?:^|\r\n):launch(?=\r\n)", bad)) == 2
    w = Batch(bad, World(already_open=True)).run()
    assert w.pid_deleted_while_alive            # the stale block wipes the live pid file
    assert w.started_pythonw == 1 and not any("already open" in e for e in w.echoed)
