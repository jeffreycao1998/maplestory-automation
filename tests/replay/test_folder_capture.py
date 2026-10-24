"""FolderCapture / SequenceCapture / ActionForwardingBackend."""
from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from maplebot.replay.folder_capture import (ActionForwardingBackend, EndOfRecording,
                                            FolderCapture, SequenceCapture, list_frames)
from maplebot.replay.synthetic import SceneSequence
from maplebot.types import Action


def _write_frames(folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    for i, n in enumerate(names):
        img = np.full((20, 30, 3), i * 10, np.uint8)
        cv2.imwrite(str(folder / n), img)


def test_order_times_and_end(tmp_path):
    _write_frames(tmp_path / "rec" / "frames", ["000003.png", "000001.png", "000002.png"])
    cap = FolderCapture(tmp_path / "rec", fps=10)
    assert len(cap) == 3
    frames = [cap.grab() for _ in range(3)]
    assert [cap.files[i].name for i in range(3)] == ["000001.png", "000002.png", "000003.png"]
    # file 000001 was written second (value 10)
    assert [int(f.image[0, 0, 0]) for f in frames] == [10, 20, 0]
    assert [f.t for f in frames] == pytest.approx([0.0, 0.1, 0.2])
    assert frames[0].window == (0, 0, 30, 20)
    with pytest.raises(EndOfRecording):
        cap.grab()


def test_end_is_stopiteration(tmp_path):
    assert issubclass(EndOfRecording, StopIteration)
    _write_frames(tmp_path, ["a.png"])
    cap = FolderCapture(tmp_path)
    assert len(list(cap)) == 1


def test_loop_keeps_time_monotonic(tmp_path):
    _write_frames(tmp_path, ["1.png", "2.png"])
    cap = FolderCapture(tmp_path, fps=2, loop=True)
    ts = [cap.grab().t for _ in range(5)]
    assert ts == pytest.approx([0, 0.5, 1.0, 1.5, 2.0])


def test_skips_unreadable_and_ignores_other_files(tmp_path):
    _write_frames(tmp_path, ["1.png", "3.png"])
    (tmp_path / "2.png").write_bytes(b"not a png")
    (tmp_path / "notes.txt").write_text("x")
    cap = FolderCapture(tmp_path)
    assert len(list(cap)) == 2


def test_meta_fps_and_window(tmp_path):
    _write_frames(tmp_path / "frames", ["1.png", "2.png"])
    (tmp_path / "meta.json").write_text(json.dumps({"fps": 4, "window": [100, 50, 30, 20]}))
    cap = FolderCapture(tmp_path, fps=None)
    f0, f1 = cap.grab(), cap.grab()
    assert f1.t - f0.t == pytest.approx(0.25)
    assert f0.window == (100, 50, 30, 20)


def test_empty_and_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        FolderCapture(tmp_path / "nope")
    cap = FolderCapture(tmp_path)
    with pytest.raises(EndOfRecording):
        cap.grab()
    assert list_frames(tmp_path) == []


def test_closed_capture_ends(tmp_path):
    _write_frames(tmp_path, ["1.png"])
    with FolderCapture(tmp_path) as cap:
        pass
    with pytest.raises(EndOfRecording):
        cap.grab()


def test_sequence_capture_forwards_actions():
    seq = SceneSequence(seed=0, n_mobs=0)
    cap = SequenceCapture(seq, fps=10, max_frames=11)
    cap.grab()
    x0 = seq.player_wx
    cap.on_action(Action.WALK_RIGHT)
    frames = [cap.grab() for _ in range(10)]
    assert frames[-1].t == pytest.approx(1.0)
    assert seq.player_wx == pytest.approx(x0 + seq.walk_speed, rel=0.02)
    assert len(cap.truths) == 11
    with pytest.raises(EndOfRecording):
        cap.grab()


def test_action_forwarding_backend_drives_sim():
    from maplebot.controls import Controller
    seq = SceneSequence(seed=0, n_mobs=0, player_x=1000, mob_xs=[1200.0])
    settings = seq.settings()
    be = ActionForwardingBackend(seq, settings)
    with Controller(settings, be, sleep=lambda s: None) as c:
        c.execute(Action.WALK_RIGHT, 0.0)
        assert seq.walking == "right"
        seq.advance(0.5)
        c.execute(Action.ATTACK, 0.5)
        assert seq.walking is None and seq.mobs[0].hits == 1
    assert not be.held
