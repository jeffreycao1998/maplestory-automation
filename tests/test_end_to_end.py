"""End-to-end: the real BotRunner driving a simulated world (no game, no real keys)."""
import json
import threading
import time
from pathlib import Path

import pytest

from maplebot.controls import FakeBackend
from maplebot.replay import synthetic as S
from maplebot.replay.folder_capture import SequenceCapture
from maplebot.runner import BotRunner, RunnerError
from maplebot.settings import Profile


def _fast(settings):
    settings.safety.start_delay_s = 0
    settings.skill.hold_s = 0.005
    settings.behavior.turn_tap_s = 0.005
    settings.behavior.jump_tap_s = 0.005
    settings.potions.tap_s = 0.005
    return settings


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPLEBOT_LOGS", str(tmp_path / "logs"))
    seq = S.SceneSequence(seed=1)
    settings = _fast(seq.settings())
    profile = Profile(tmp_path / "profile")
    profile.ensure()
    S.write_templates(profile.templates_dir)
    return seq, settings, profile


def _runner(seq, settings, profile, frames, **kw):
    cap = SequenceCapture(seq, fps=settings.behavior.loop_hz, max_frames=frames)
    backend = FakeBackend()
    statuses = []
    r = BotRunner(profile, settings, backend=backend, capture=cap, use_hotkeys=False,
                  on_status=statuses.append, on_decision=lambda d: cap.on_action(d.action), **kw)
    return r, backend, statuses


def test_closed_loop_kills_mobs_and_cleans_up(world):
    seq, settings, profile = world
    previews = []
    r, backend, statuses = _runner(seq, settings, profile, frames=450, on_preview=previews.append)
    r.run()

    assert seq.kills >= 2, f"only {seq.kills} kills in 30 s of sim time"
    assert backend.held == set()
    assert statuses[-1].running is False
    assert statuses[-1].message == "Recording ended"
    assert previews and previews[0].shape[1] <= 640

    run_dir = Path(r.run_dir)
    lines = (run_dir / "ticks.jsonl").read_text().strip().splitlines()
    assert len(lines) >= 400
    json.loads(lines[0])
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["stop_reason"] == "Recording ended"
    assert summary["attacks"] > 0


def test_stop_from_another_thread_releases_keys(world):
    seq, settings, profile = world
    r, backend, statuses = _runner(seq, settings, profile, frames=None)
    t = threading.Thread(target=r.run, daemon=True)
    t.start()
    time.sleep(1.0)
    r.stop()
    t.join(5)
    assert not t.is_alive()
    assert backend.held == set()
    assert statuses[-1].message == "Stopped by you"


def test_supervisor_pause_then_user_stop(world):
    seq, settings, profile = world
    # Point the minimap box at an empty corner: the character "disappears".
    settings.calibration.minimap_box = [900, 600, 100, 60]
    r, backend, statuses = _runner(seq, settings, profile, frames=None)
    t = threading.Thread(target=r.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline and not r.status.paused:
        time.sleep(0.05)
    assert r.status.paused, "supervisor should pause when the character is lost"
    assert "minimap" in r.status.message
    assert backend.held == set()
    r.stop()
    t.join(5)
    assert not t.is_alive()


def test_pause_and_resume_by_user(world):
    seq, settings, profile = world
    r, backend, statuses = _runner(seq, settings, profile, frames=None)
    t = threading.Thread(target=r.run, daemon=True)
    t.start()
    time.sleep(0.5)
    r.pause()
    time.sleep(0.3)
    assert backend.held == set()
    assert r.status.paused
    r.resume()
    time.sleep(0.5)
    assert not r.status.paused
    r.stop()
    t.join(5)
    assert backend.held == set()


def test_refuses_to_start_without_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPLEBOT_LOGS", str(tmp_path / "logs"))
    profile = Profile(tmp_path / "profile")
    from maplebot.settings import Settings
    with pytest.raises(RunnerError, match="setup wizard"):
        BotRunner(profile, Settings(), backend=FakeBackend(), use_hotkeys=False).run()
    s = Settings()
    s.calibration.minimap_box = [10, 10, 100, 60]
    with pytest.raises(RunnerError, match="mob pictures"):
        BotRunner(profile, s, backend=FakeBackend(), use_hotkeys=False).run()
