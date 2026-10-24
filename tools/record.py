"""
Record the game client to disk (Windows, run by the user -- never by Claude).

    python -m tools.record --name henesys1 --fps 5 --seconds 120 [--keys]

Writes ``data/recordings/<name>/``:
    frames/000001.png ...      client-area screenshots (no title bar)
    meta.json                  window box/size, fps, date, window title, per-frame times
    keys.jsonl                 with --keys: {"t": 12.34, "key": "ctrl", "down": true}

``t`` in meta.json / keys.jsonl is seconds since the recording started. Ctrl+C stops
cleanly (meta.json is always written). This tool only reads the screen and, with --keys,
listens to your keyboard; it never presses anything.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import tools  # noqa: F401  (puts src/ on sys.path)
from tools import REPO_ROOT
from tools._common import load_profile, rel, write_json, write_png


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.record", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True, help="recording name, e.g. henesys1")
    p.add_argument("--fps", type=float, default=5.0, help="frames per second (default 5)")
    p.add_argument("--seconds", type=float, default=120.0,
                   help="stop after this many seconds (default 120; 0 = until Ctrl+C)")
    p.add_argument("--title", default=None,
                   help="window title to look for (default: settings.calibration.window_title)")
    p.add_argument("--profile", default=None, help="profile folder (default: ./profile)")
    p.add_argument("--out", default=str(REPO_ROOT / "data" / "recordings"),
                   help="recordings folder (default data/recordings)")
    p.add_argument("--keys", action="store_true",
                   help="also log your key presses to keys.jsonl (needs pynput)")
    p.add_argument("--map", default="", help="map name to store in meta.json")
    p.add_argument("--delay", type=float, default=3.0,
                   help="seconds to wait before the first frame (default 3)")
    p.add_argument("--overwrite", action="store_true", help="reuse an existing recording folder")
    return p


class KeyLogger:
    """Logs key down/up with timestamps via pynput. Never presses keys."""

    def __init__(self, path: Path, t0: float):
        self.path = path
        self.t0 = t0
        self._fh = None
        self._listener = None
        self._lock = threading.Lock()
        self._down: set = set()

    @staticmethod
    def _name(key) -> str:
        try:
            if getattr(key, "char", None):
                return key.char.lower()
            return str(getattr(key, "name", key)).replace("Key.", "").replace("_l", "").replace("_r", "")
        except Exception:
            return str(key)

    def _event(self, key, down: bool) -> None:
        import json
        name = self._name(key)
        with self._lock:
            if down and name in self._down:       # OS auto-repeat
                return
            (self._down.add if down else self._down.discard)(name)
            if self._fh:
                self._fh.write(json.dumps({"t": round(time.monotonic() - self.t0, 3),
                                           "key": name, "down": down}) + "\n")

    def start(self) -> None:
        from pynput import keyboard   # lazy: optional dependency
        self._fh = open(self.path, "a", encoding="utf-8")
        self._listener = keyboard.Listener(on_press=lambda k: self._event(k, True),
                                           on_release=lambda k: self._event(k, False))
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.fps <= 0:
        print("--fps must be greater than 0")
        return 2

    profile = load_profile(args.profile)
    settings = profile.load()
    title = args.title or settings.calibration.window_title

    from maplebot.perception import ScreenCapture, WindowNotFound, enable_dpi_awareness
    enable_dpi_awareness()
    try:
        cap = ScreenCapture(title)
    except WindowNotFound as e:
        print(f"ERROR: {e}")
        print(f"  Looked for a window whose title contains '{title}'.")
        print("  Start the game in windowed mode, make sure it's not minimised, or pass")
        print("  --title \"<part of the window title>\".")
        return 1

    rec_dir = Path(args.out) / args.name
    frames_dir = rec_dir / "frames"
    if frames_dir.exists() and any(frames_dir.iterdir()) and not args.overwrite:
        print(f"ERROR: {rel(rec_dir)} already has frames. Pick another --name or pass --overwrite.")
        cap.close()
        return 1
    frames_dir.mkdir(parents=True, exist_ok=True)

    box = cap.box
    meta = {
        "name": args.name, "map": args.map, "fps": args.fps,
        "window_title": title, "window": list(box), "window_size": [box[2], box[3]],
        "resolution": f"{box[2]}x{box[3]}",
        "date": datetime.now().isoformat(timespec="seconds"),
        "platform": sys.platform, "frames": 0, "frame_t": [], "keys": bool(args.keys),
        "complete": False,
    }
    write_json(rec_dir / "meta.json", meta)

    print(f"Recording '{title}' client area {box[2]}x{box[3]} at {args.fps:g} fps "
          f"-> {rel(rec_dir)}")
    if args.delay > 0:
        print(f"Starting in {args.delay:g} s -- click into the game. Ctrl+C to stop.")
        time.sleep(args.delay)

    t0 = time.monotonic()
    keylog = None
    if args.keys:
        try:
            keylog = KeyLogger(rec_dir / "keys.jsonl", t0)
            keylog.start()
        except Exception as e:      # pynput missing / no permission
            print(f"WARNING: key logging unavailable ({e}); recording frames only.")
            keylog = None

    period = 1.0 / args.fps
    n = 0
    stop_reason = "time limit"
    last_print = t0
    try:
        while True:
            now = time.monotonic()
            if args.seconds and now - t0 >= args.seconds:
                break
            frame = cap.grab()
            n += 1
            write_png(frames_dir / f"{n:06d}.png", frame.image, compression=1)
            meta["frame_t"].append(round(frame.t - t0, 3))
            if list(frame.window) != meta["window"]:
                meta.setdefault("window_changes", []).append(
                    {"frame": n, "window": list(frame.window)})
            if now - last_print >= 5:
                last_print = now
                print(f"  {n} frames, {now - t0:.0f} s")
            sleep = (t0 + n * period) - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        stop_reason = "Ctrl+C"
    finally:
        if keylog:
            keylog.stop()
        cap.close()
        meta.update(frames=n, duration_s=round(time.monotonic() - t0, 2),
                    stop_reason=stop_reason, complete=True)
        write_json(rec_dir / "meta.json", meta)

    print(f"Stopped ({stop_reason}). Saved {n} frames to {rel(frames_dir)}")
    if meta.get("window_changes"):
        print("WARNING: the game window moved or resized during the recording "
              f"({len(meta['window_changes'])} times). Keep it still for usable data.")
    print(f"Replay it with: python -m tools.replay {rel(rec_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
