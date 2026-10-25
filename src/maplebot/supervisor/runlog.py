"""
Per-run logging: logs/<YYYYmmdd-HHMMSS>/{ticks.jsonl, frames/, settings.yaml, summary.json}.

One JSON line per tick with the TickLog fields from docs/architecture.md:
    t, state, action, reason, verdict, player_map, player_screen, n_mobs, target,
    hp, mp, flags, confidence
(`verdict_reason` is added when the verdict is not OK.)

`log_tick` and `save_frame` never raise: an error is logged once and the run carries on.
All public methods are thread-safe.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import threading
import time
from collections import Counter
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


from maplebot.settings import Settings
from maplebot.types import Action, Decision, Frame, Perception, Verdict, VerdictKind

log = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 1.0
FRAME_TAG_INTERVAL_S = 2.0
MAX_FRAMES_PER_RUN = 300


def _jsonable(x: Any) -> Any:
    """Convert numpy / enum / tuple / set / dataclass values into JSON-safe types."""
    if isinstance(x, Enum):              # before str: Flag/Action are str-mixin enums
        return _jsonable(x.value)
    if x is None or isinstance(x, bool):
        return x
    if isinstance(x, str):
        return str(x)
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    try:
        import numpy as np
        if isinstance(x, np.generic):
            return _jsonable(x.item())
        if isinstance(x, np.ndarray):
            return _jsonable(x.tolist())
    except ImportError:  # pragma: no cover
        pass
    if isinstance(x, dict):
        return {str(_jsonable(k)): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (set, frozenset)):
        return sorted((_jsonable(v) for v in x), key=lambda v: str(v))
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {f.name: _jsonable(getattr(x, f.name)) for f in dataclasses.fields(x)}
    return str(x)


def _round(x: Any, nd: int = 4) -> Any:
    v = _jsonable(x)
    if isinstance(v, float):
        return round(v, nd)
    if isinstance(v, list):
        return [round(e, nd) if isinstance(e, float) else e for e in v]
    return v


def _target(d: Optional[Decision]) -> Optional[dict]:
    m = getattr(d, "target", None) if d is not None else None
    if m is None:
        return None
    return {"x": _jsonable(getattr(m, "x", None)), "y": _jsonable(getattr(m, "y", None))}


def _safe_tag(tag: str) -> str:
    s = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(tag))
    return s[:40] or "frame"


class RunLogger:
    def __init__(self, logs_dir: Path, settings: Settings):
        logs_dir = Path(logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = logs_dir / stamp
        n = 1
        while run_dir.exists():          # two runs started in the same second
            run_dir = logs_dir / f"{stamp}-{n}"
            n += 1
        run_dir.mkdir(parents=True)
        self.run_dir: Path = run_dir
        self.frames_dir: Path = run_dir / "frames"
        self.frames_dir.mkdir()
        self.ticks_path: Path = run_dir / "ticks.jsonl"
        self.settings = settings

        try:
            with open(run_dir / "settings.yaml", "w", encoding="utf-8") as f:
                from ..settings import dump_yaml_or_json
                f.write(dump_yaml_or_json(_jsonable(settings.to_dict())))
        except Exception:
            log.exception("Couldn't write settings snapshot")

        self._lock = threading.Lock()
        self._fh = open(self.ticks_path, "a", encoding="utf-8")
        self._last_flush = time.monotonic()
        self._closed = False
        self._error_logged = False
        self._frame_error_logged = False

        self._started_wall = time.time()
        self._started_mono = time.monotonic()
        self._first_t: Optional[float] = None
        self._last_t: Optional[float] = None
        self.ticks = 0
        self.attacks = 0
        self.frames_saved = 0
        self.state_ticks: Counter = Counter()
        self.verdict_counts: Counter = Counter()
        self.last_verdict_reason: str = ""
        self._last_frame_by_tag: Dict[str, float] = {}

    # ---- ticks ------------------------------------------------------------
    def make_record(self, p: Perception, d: Optional[Decision], v: Verdict) -> dict:
        kind = getattr(v, "kind", None)
        rec = {
            "t": _round(getattr(p, "t", None), 3),
            "state": _jsonable(getattr(d, "state", None)) if d is not None else None,
            "action": _jsonable(getattr(d, "action", None)) if d is not None else None,
            "reason": _jsonable(getattr(d, "reason", "")) if d is not None else "",
            "verdict": _jsonable(kind),
            "player_map": _round(getattr(p, "player_map", None)),
            "player_screen": _jsonable(getattr(p, "player_screen", None)),
            "n_mobs": len(getattr(p, "mobs", None) or ()),
            "items": _jsonable(p.items),
            "hp_stock": p.hp_stock, "mp_stock": p.mp_stock, "mesos": p.mesos,
            "inventory_full": p.inventory_full, "hud_t": p.hud_t,
            "transition": d.transition if d else None,
            "route": list(d.route) if d else [],
            "retreat_reason": d.retreat_reason if d else "",
            "target": _target(d),
            "hp": _round(getattr(p, "hp", None), 3),
            "mp": _round(getattr(p, "mp", None), 3),
            "flags": _jsonable(frozenset(getattr(p, "flags", None) or ())),
            "confidence": _round(getattr(p, "confidence", None), 3),
        }
        if kind is not None and kind != VerdictKind.OK:
            rec["verdict_reason"] = _jsonable(getattr(v, "reason", ""))
        return rec

    def log_tick(self, p: Perception, d: Optional[Decision], v: Verdict) -> None:
        try:
            rec = self.make_record(p, d, v)
            line = json.dumps(rec, ensure_ascii=False, allow_nan=False)
            with self._lock:
                if self._closed:
                    return
                self._fh.write(line + "\n")
                self.ticks += 1
                if rec["t"] is not None:
                    if self._first_t is None:
                        self._first_t = rec["t"]
                    self._last_t = rec["t"]
                if rec["state"] is not None:
                    self.state_ticks[str(rec["state"])] += 1
                if d is not None and getattr(d, "action", None) == Action.ATTACK:
                    self.attacks += 1
                if rec["verdict"] is not None:
                    self.verdict_counts[str(rec["verdict"])] += 1
                if rec.get("verdict_reason"):
                    self.last_verdict_reason = rec["verdict_reason"]
                now = time.monotonic()
                if now - self._last_flush >= FLUSH_INTERVAL_S:
                    self._fh.flush()
                    self._last_flush = now
        except Exception:
            if not self._error_logged:
                self._error_logged = True
                log.exception("RunLogger.log_tick failed (further errors suppressed)")

    def flush(self) -> None:
        try:
            with self._lock:
                if not self._closed:
                    self._fh.flush()
                    self._last_flush = time.monotonic()
        except Exception:
            log.exception("RunLogger.flush failed")

    # ---- frames -------------------------------------------------------------
    def save_frame(self, frame: Frame, tag: str) -> Optional[Path]:
        try:
            if not self.settings.safety.save_anomaly_frames:
                return None
            image = getattr(frame, "image", None)
            if image is None or getattr(image, "size", 0) == 0:
                return None
            t = getattr(frame, "t", None)
            if not isinstance(t, (int, float)) or not math.isfinite(float(t)):
                t = time.monotonic()
            t = float(t)
            stag = _safe_tag(tag)
            with self._lock:
                if self._closed or self.frames_saved >= MAX_FRAMES_PER_RUN:
                    return None
                last = self._last_frame_by_tag.get(stag)
                if last is not None and abs(t - last) < FRAME_TAG_INTERVAL_S:
                    return None
                self._last_frame_by_tag[stag] = t
                self.frames_saved += 1      # reserve the slot under the lock
            path = self.frames_dir / f"{t:.3f}_{stag}.png"
            import cv2  # lazy: keeps import of this module light
            if not cv2.imwrite(str(path), image):
                raise OSError(f"cv2.imwrite returned False for {path}")
            return path
        except Exception:
            if not self._frame_error_logged:
                self._frame_error_logged = True
                log.exception("RunLogger.save_frame failed (further errors suppressed)")
            return None

    # ---- end of run -----------------------------------------------------------
    def close(self, summary: Optional[dict] = None) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            out = {
                "run_dir": str(self.run_dir),
                "started_at": datetime.fromtimestamp(self._started_wall).isoformat(timespec="seconds"),
                "ended_at": datetime.now().isoformat(timespec="seconds"),
                "duration_s": round(time.monotonic() - self._started_mono, 1),
                "ticks": self.ticks,
                "state_ticks": dict(self.state_ticks),
                "attacks": self.attacks,
                "verdict_ticks": dict(self.verdict_counts),
                "frames_saved": self.frames_saved,
                "verdict_reason": self.last_verdict_reason,
            }
            if self._first_t is not None and self._last_t is not None:
                out["tick_span_s"] = round(float(self._last_t) - float(self._first_t), 3)
            try:
                out.update(_jsonable(dict(summary or {})))
            except Exception:
                log.exception("Bad summary passed to RunLogger.close")
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                log.exception("Closing ticks.jsonl failed")
            try:
                tmp = self.run_dir / "summary.json.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(out, f, indent=2, ensure_ascii=False, default=str)
                tmp.replace(self.run_dir / "summary.json")
            except Exception:
                log.exception("Writing summary.json failed")

    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, *exc) -> None:
        self.close({})
