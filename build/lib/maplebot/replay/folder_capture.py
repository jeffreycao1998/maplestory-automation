"""
Capture sources that stand in for ``perception.ScreenCapture`` (same ``grab()/close()``).

Owner: test-harness-engineer.

* ``FolderCapture(folder)`` -- plays back PNG frames from a recording (``<rec>/frames/`` or
  a folder of PNGs) in file-name order, with synthetic monotonic timestamps at ``fps``.
* ``SequenceCapture(sequence)`` -- wraps ``synthetic.SceneSequence`` for closed-loop tests.

At the end, ``grab()`` raises ``EndOfRecording`` (a ``StopIteration`` subclass, so
``BotRunner._grab`` treats it as "recording ended").
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional, Sequence

import numpy as np

from maplebot.types import Action, Frame

__all__ = ["EndOfRecording", "FolderCapture", "SequenceCapture", "list_frames", "read_image",
           "ActionForwardingBackend"]

IMAGE_SUFFIXES = (".png",)


class EndOfRecording(StopIteration):
    """No more frames."""


def read_image(path: Path) -> Optional[np.ndarray]:
    """BGR uint8 image from ``path`` (works with non-ASCII Windows paths), or None."""
    import cv2
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img if img is not None and img.size else None
    except Exception:
        return None


def list_frames(folder: Path) -> List[Path]:
    """PNG frames of a recording or folder, sorted by name.

    ``folder`` may be a recording dir (uses ``folder/frames``) or a folder of PNGs.
    """
    folder = Path(folder)
    if (folder / "frames").is_dir():
        folder = folder / "frames"
    if not folder.is_dir():
        raise FileNotFoundError(f"No such folder: {folder}")
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file())


class FolderCapture:
    """Frames from PNG files, in name order. ``t = start_t + i / fps``.

    ``loop=True`` restarts from the first frame forever (t keeps increasing).
    ``fps=None`` reads ``meta.json``'s ``fps`` if present (else 15).
    Unreadable files are skipped. ``window`` is (0, 0, w, h) unless ``meta.json`` has
    ``window`` = [x, y, w, h].
    """

    def __init__(self, folder, fps: Optional[float] = 15, loop: bool = False, start_t: float = 0.0):
        self.folder = Path(folder)
        self.files = list_frames(self.folder)
        self.meta = self._read_meta()
        if fps is None:
            fps = float(self.meta.get("fps") or 15)
        if fps <= 0:
            raise ValueError("fps must be > 0")
        self.fps = float(fps)
        self.loop = bool(loop)
        self.start_t = float(start_t)
        self.index = 0          # frames returned so far
        self.current: Optional[Path] = None
        self._closed = False

    def _read_meta(self) -> dict:
        for cand in (self.folder / "meta.json", self.folder.parent / "meta.json"):
            if cand.is_file():
                try:
                    return json.loads(cand.read_text(encoding="utf-8"))
                except Exception:
                    return {}
        return {}

    def __len__(self) -> int:
        return len(self.files)

    def grab(self) -> Frame:
        if self._closed:
            raise EndOfRecording("capture closed")
        n = len(self.files)
        skipped = 0
        while True:
            if n == 0 or (not self.loop and self.index >= n) or skipped >= n:
                raise EndOfRecording(f"end of recording {self.folder}")
            path = self.files[self.index % n]
            t = self.start_t + self.index / self.fps
            self.index += 1
            img = read_image(path)
            if img is None:
                skipped += 1
                continue
            self.current = path
            win = self.meta.get("window")
            if isinstance(win, (list, tuple)) and len(win) == 4:
                window = tuple(int(v) for v in win)
            else:
                window = (0, 0, int(img.shape[1]), int(img.shape[0]))
            return Frame(image=img, t=t, window=window)  # type: ignore[arg-type]

    def __iter__(self):
        while True:
            try:
                yield self.grab()
            except EndOfRecording:
                return

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "FolderCapture":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class SequenceCapture:
    """``grab()`` returns successive ``SceneSequence`` frames, ``1/fps`` s apart.

    ``on_action(action)`` forwards an Action to the sequence (wire it to the behaviour's
    decision, e.g. ``BotRunner(on_decision=lambda d: cap.on_action(d.action))``), or use
    ``ActionForwardingBackend`` to drive the world from Controller key events.
    ``max_frames`` ends the run with ``EndOfRecording``.
    """

    def __init__(self, scene_sequence, fps: float = 15.0, max_frames: Optional[int] = None):
        if fps <= 0:
            raise ValueError("fps must be > 0")
        self.sequence = scene_sequence
        self.fps = float(fps)
        self.max_frames = max_frames
        self.index = 0
        self._t0 = float(scene_sequence.t)
        self._closed = False
        self.truths: List[dict] = []

    def grab(self) -> Frame:
        if self._closed or (self.max_frames is not None and self.index >= self.max_frames):
            raise EndOfRecording("end of synthetic sequence")
        t = self._t0 + self.index / self.fps
        self.index += 1
        f = self.sequence.frame(t)
        self.truths.append(self.sequence.last_truth)
        return f

    def on_action(self, action) -> None:
        self.sequence.apply(Action(action))

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "SequenceCapture":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class ActionForwardingBackend:
    """An InputBackend that records like FakeBackend AND turns key events into sim Actions.

    Wraps ``maplebot.controls.FakeBackend`` (if available) so tests can still inspect
    ``events`` / ``held``. Key-down of the left/right keys -> WALK_*, key-up of the walking
    key -> STOP_WALK, attack/jump/potion key-down -> ATTACK/JUMP/USE_*_POT.
    Note: a turn tap (FACE_*) shows up as a short walk; the sim moves a pixel or two.
    """

    def __init__(self, sequence, settings, clock: Optional[Callable[[], float]] = None):
        from maplebot.controls import FakeBackend, normalize_key
        self.inner = FakeBackend(clock=clock) if clock is not None else FakeBackend()
        self.sequence = sequence
        k = settings.keys
        norm = lambda v: normalize_key(v) if v else None  # noqa: E731
        self._map = {norm(k.attack): Action.ATTACK, norm(k.jump): Action.JUMP,
                     norm(k.hp_potion): Action.USE_HP_POT, norm(k.mp_potion): Action.USE_MP_POT}
        self._left, self._right = norm(k.left), norm(k.right)
        self._map.pop(None, None)

    @property
    def events(self):
        return self.inner.events

    @property
    def held(self):
        return self.inner.held

    def key_down(self, key: str) -> None:
        self.inner.key_down(key)
        if key == self._left:
            self.sequence.apply(Action.WALK_LEFT)
        elif key == self._right:
            self.sequence.apply(Action.WALK_RIGHT)
        elif key in self._map:
            self.sequence.apply(self._map[key])

    def key_up(self, key: str) -> None:
        self.inner.key_up(key)
        if key in (self._left, self._right):
            held_dir = [d for d, kk in (("left", self._left), ("right", self._right))
                        if kk in self.inner.held]
            if held_dir:
                self.sequence.apply(Action.WALK_LEFT if held_dir[0] == "left" else Action.WALK_RIGHT)
            else:
                self.sequence.apply(Action.STOP_WALK)
