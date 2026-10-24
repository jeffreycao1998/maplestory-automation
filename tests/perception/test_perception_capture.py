import sys

import numpy as np
import pytest

from maplebot.perception import ScreenCapture, WindowNotFound, enable_dpi_awareness, find_client_area
from maplebot.perception import capture
from maplebot.types import Frame


def test_dpi_awareness_never_raises():
    enable_dpi_awareness()
    enable_dpi_awareness()


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_find_client_area_off_windows_raises_friendly():
    with pytest.raises(WindowNotFound) as e:
        find_client_area("MapleStory")
    assert "Couldn't find a window with 'MapleStory' in its title" in str(e.value)
    assert issubclass(WindowNotFound, RuntimeError)


class FakeShot:
    def __init__(self, w, h):
        self.arr = np.zeros((h, w, 4), np.uint8)
        self.arr[..., 0] = 10
        self.arr[..., 1] = 20
        self.arr[..., 2] = 30
        self.arr[..., 3] = 255

    def __array__(self, dtype=None, copy=None):
        return self.arr


class FakeMss:
    def __init__(self):
        self.calls = []
        self.closed = False

    def grab(self, mon):
        self.calls.append(dict(mon))
        return FakeShot(mon["width"], mon["height"])

    def close(self):
        self.closed = True


def test_screen_capture_with_fakes(monkeypatch):
    boxes = [(100, 50, 800, 600)]
    monkeypatch.setattr(capture, "find_client_area", lambda title: boxes[0])
    clock = [1000.0]
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    cap = ScreenCapture("MapleStory")
    fake = FakeMss()
    cap._sct = fake

    f = cap.grab()
    assert isinstance(f, Frame)
    assert f.image.shape == (600, 800, 3) and f.image.dtype == np.uint8
    assert tuple(f.image[0, 0]) == (10, 20, 30)            # BGR, alpha dropped
    assert f.window == (100, 50, 800, 600) and f.t == 1000.0
    assert fake.calls[-1] == {"left": 100, "top": 50, "width": 800, "height": 600}

    boxes[0] = (300, 60, 800, 600)       # window moved
    clock[0] += 0.5
    assert cap.grab().window == (100, 50, 800, 600)       # not re-checked yet (<1 s)
    clock[0] += 0.6
    f = cap.grab()
    assert f.window == (300, 60, 800, 600)
    assert fake.calls[-1]["left"] == 300

    def gone(title):
        raise WindowNotFound("x")
    monkeypatch.setattr(capture, "find_client_area", gone)
    clock[0] += 2
    assert cap.grab().window == (300, 60, 800, 600)       # keeps last known box

    cap.close()
    assert fake.closed
    cap.close()   # idempotent
