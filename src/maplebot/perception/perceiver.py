"""
Frame -> Perception.

Pipeline per frame (all OpenCV, no models):
  1. Minimap: HSV mask of the player-dot colour inside calibration.minimap_box, largest
     sane blob -> player_map (fractions of the box).
  2. Mobs: grayscale TM_CCOEFF_NORMED template matching on a copy downscaled by
     detection.downscale (templates scaled the same, plus mirrored copies), local-maxima
     above detection.mob_threshold, greedy non-max suppression across all templates,
     then anything centred inside the minimap / HP / MP boxes is dropped.
  3. Player: name-tag template matched at full resolution; body centre is placed
     PLAYER_TAG_TO_BODY_PX above the tag centre. Otherwise a fixed screen estimate.
  4. HP/MP: calib.bar_fill on the calibrated bar boxes.
  5. Confidence: see `confidence_for`.

`perceive` never raises: on any internal error it logs and returns an empty,
zero-confidence Perception flagged MINIMAP_MISSING + LOW_CONFIDENCE.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from maplebot.settings import Settings
from maplebot.types import Box, Flag, Frame, Mob, Perception, PointPx

from . import calib

log = logging.getLogger(__name__)

# --- player name tag -> body centre ----------------------------------------------------
# The name tag sits just under the character's feet. A Classic character sprite is about
# 60-80 px tall, i.e. a 30-40 px half-height. From the tag centre we go up ~1.8 half-heights:
# ~1 half-height to reach the body centre from the feet, plus ~0.8 for the gap between
# feet and tag and the tag's own half-height.
CHARACTER_HALF_HEIGHT_PX = 35
TAG_TO_BODY_FACTOR = 1.8
PLAYER_TAG_TO_BODY_PX = int(round(TAG_TO_BODY_FACTOR * CHARACTER_HALF_HEIGHT_PX))  # 63 px

# Where the character usually is when we can't see the tag (the camera follows the player).
PLAYER_ESTIMATE_X_FRAC = 0.5
PLAYER_ESTIMATE_Y_FRAC = 0.62

# --- mob matching ------------------------------------------------------------------------
MIN_TEMPLATE_PX = 6          # a template smaller than this after downscaling is skipped
MAX_CANDIDATES_PER_TEMPLATE = 200   # bound NMS work on noisy frames
MAX_MOBS = 40
NMS_IOU = 0.3                # boxes overlapping more than this are the same mob

# --- confidence heuristic ----------------------------------------------------------------
CONF_MINIMAP_MISSING_PENALTY = 0.5
CONF_PLAYER_ESTIMATED_PENALTY = 0.2   # only when a player template is configured
CONF_LOW_THRESHOLD = 0.5


def confidence_for(minimap_found: bool, player_estimated_with_template: bool) -> float:
    """1.0, minus 0.5 if the minimap dot wasn't found, minus 0.2 if a configured
    name-tag template wasn't found. Below 0.5 the Perception gets Flag.LOW_CONFIDENCE."""
    c = 1.0
    if not minimap_found:
        c -= CONF_MINIMAP_MISSING_PENALTY
    if player_estimated_with_template:
        c -= CONF_PLAYER_ESTIMATED_PENALTY
    return max(0.0, min(1.0, c))


@dataclass
class _Template:
    name: str
    gray: np.ndarray          # full-resolution grayscale
    w: int                    # full-resolution size
    h: int
    mirrored: bool = False


@dataclass
class _Prepared:
    name: str
    small: np.ndarray         # grayscale at the matching scale
    w: int                    # full-resolution size (for Mob.w/h)
    h: int


def _load_gray(path: Path) -> Optional[np.ndarray]:
    """Read an image as grayscale; works with non-ASCII Windows paths. None on failure."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)   # drops alpha
        if img is None or img.size == 0:
            return None
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    except Exception:
        return None


def _usable_template(gray: np.ndarray) -> bool:
    # TM_CCOEFF_NORMED is undefined for a flat template.
    return gray.shape[0] >= 2 and gray.shape[1] >= 2 and float(gray.std()) > 1.0


def _iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    iw = min(ax1, bx1) - max(ax0, bx0)
    ih = min(ay1, by1) - max(ay0, by0)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / ((ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter)


def _centre_in(x: float, y: float, box: Optional[Box]) -> bool:
    if box is None:
        return False
    bx, by, bw, bh = box
    return bx <= x < bx + bw and by <= y < by + bh


def _as_box(v) -> Optional[Box]:
    try:
        if v is None or len(v) != 4:
            return None
        b = tuple(int(round(float(c))) for c in v)
        return b if b[2] > 0 and b[3] > 0 else None
    except (TypeError, ValueError):
        return None


class Perceiver:
    def __init__(self, settings: Settings, mob_templates: List[Path], player_template: Optional[Path]):
        self._mob_raw: List[_Template] = []
        for p in mob_templates or []:
            p = Path(p)
            g = _load_gray(p)
            if g is None or not _usable_template(g):
                log.warning("skipping mob template %s (unreadable or blank)", p)
                continue
            self._mob_raw.append(_Template(p.name, g, g.shape[1], g.shape[0]))

        self._player: Optional[_Template] = None
        self.player_template_configured = player_template is not None
        if player_template is not None:
            g = _load_gray(Path(player_template))
            if g is None or not _usable_template(g):
                log.warning("skipping player template %s (unreadable or blank)", player_template)
            else:
                self._player = _Template(Path(player_template).name, g, g.shape[1], g.shape[0])

        self.settings = settings
        self._prepared: List[_Prepared] = []
        self._prep_key = None
        self.update_settings(settings)

    # ---- settings ---------------------------------------------------------------------
    def update_settings(self, settings: Settings) -> None:
        self.settings = settings
        scale = self._scale()
        key = (scale, bool(settings.detection.mirror_templates))
        if key == self._prep_key:
            return
        prepared: List[_Prepared] = []
        for t in self._mob_raw:
            variants = [(t.name, t.gray)]
            if settings.detection.mirror_templates:
                variants.append((t.name, cv2.flip(t.gray, 1)))
            for name, g in variants:
                if scale != 1.0:
                    sw = max(1, int(round(t.w * scale)))
                    sh = max(1, int(round(t.h * scale)))
                    g = cv2.resize(g, (sw, sh), interpolation=cv2.INTER_AREA)
                if min(g.shape[:2]) < MIN_TEMPLATE_PX or not _usable_template(g):
                    log.warning("mob template %s too small/flat at downscale %.2f; skipped", name, scale)
                    continue
                prepared.append(_Prepared(name, np.ascontiguousarray(g), t.w, t.h))
        self._prepared = prepared
        self._prep_key = key

    def _scale(self) -> float:
        try:
            s = float(self.settings.detection.downscale)
        except (TypeError, ValueError):
            s = 1.0
        if not np.isfinite(s) or s <= 0:
            s = 1.0
        return min(s, 1.0)

    # ---- main entry -------------------------------------------------------------------
    def perceive(self, frame: Frame) -> Perception:
        t = float(getattr(frame, "t", 0.0) or 0.0)
        try:
            return self._perceive(frame, t)
        except Exception:
            log.exception("perceive failed; returning empty perception")
            img = getattr(frame, "image", None)
            size = (0, 0)
            if isinstance(img, np.ndarray) and img.ndim >= 2:
                size = (int(img.shape[1]), int(img.shape[0]))
            return Perception(
                t=t, screen_size=size, player_map=None, player_screen=None,
                player_screen_estimated=True, mobs=(), hp=None, mp=None,
                flags=frozenset({Flag.MINIMAP_MISSING, Flag.LOW_CONFIDENCE}), confidence=0.0,
            )

    def _perceive(self, frame: Frame, t: float) -> Perception:
        img = calib.as_bgr(frame.image)
        if img is None:
            raise ValueError("frame has no usable image")
        h, w = img.shape[:2]
        cal = self.settings.calibration
        det = self.settings.detection
        flags = set()

        minimap_box = _as_box(cal.minimap_box)
        hp_box = _as_box(cal.hp_bar_box)
        mp_box = _as_box(cal.mp_bar_box)

        # 1. minimap
        player_map = None
        if minimap_box is not None:
            player_map = calib.minimap_player_position(
                img, minimap_box, cal.player_dot_hsv_low, cal.player_dot_hsv_high)
        if player_map is None:
            flags.add(Flag.MINIMAP_MISSING)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 2. mobs
        mobs = self._find_mobs(gray, float(det.mob_threshold),
                               [b for b in (minimap_box, hp_box, mp_box) if b is not None])

        # 3. player
        player_screen: Optional[PointPx] = None
        estimated = True
        if self._player is not None:
            found = self._find_player(gray, float(det.player_threshold))
            if found is not None:
                player_screen, estimated = found, False
        if estimated:
            player_screen = (int(w * PLAYER_ESTIMATE_X_FRAC), int(h * PLAYER_ESTIMATE_Y_FRAC))
            if self.player_template_configured:
                flags.add(Flag.PLAYER_OFFSCREEN)

        # 4. bars
        hp = calib.bar_fill(img, hp_box) if hp_box is not None else None
        mp = calib.bar_fill(img, mp_box) if mp_box is not None else None

        # 5. confidence
        conf = confidence_for(player_map is not None,
                              estimated and self.player_template_configured)
        if conf < CONF_LOW_THRESHOLD:
            flags.add(Flag.LOW_CONFIDENCE)

        return Perception(
            t=t, screen_size=(w, h), player_map=player_map, player_screen=player_screen,
            player_screen_estimated=estimated, mobs=tuple(mobs), hp=hp, mp=mp,
            flags=frozenset(flags), confidence=conf,
        )

    # ---- mobs -------------------------------------------------------------------------
    def _find_mobs(self, gray: np.ndarray, threshold: float, exclude: Sequence[Box]) -> List[Mob]:
        if not self._prepared:
            return []
        scale = self._scale()
        if scale != 1.0:
            small = cv2.resize(gray, (max(1, int(round(gray.shape[1] * scale))),
                                      max(1, int(round(gray.shape[0] * scale)))),
                               interpolation=cv2.INTER_AREA)
        else:
            small = gray
        sh, sw = small.shape[:2]
        # Actual scale per axis (rounding) so coordinates map back exactly.
        fx = gray.shape[1] / sw
        fy = gray.shape[0] / sh

        cands = []   # (score, x0, y0, x1, y1, name, w, h) full-res box
        for tp in self._prepared:
            th, tw = tp.small.shape[:2]
            if th > sh or tw > sw:
                continue
            res = cv2.matchTemplate(small, tp.small, cv2.TM_CCOEFF_NORMED)
            np.nan_to_num(res, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            if float(res.max()) < threshold:
                continue
            # Local maxima within half a template: one peak per object per template.
            k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, tw // 2) | 1, max(3, th // 2) | 1))
            peaks = (res >= threshold) & (res >= cv2.dilate(res, k))
            ys, xs = np.nonzero(peaks)
            if xs.size == 0:
                continue
            scores = res[ys, xs]
            if scores.size > MAX_CANDIDATES_PER_TEMPLATE:
                top = np.argpartition(-scores, MAX_CANDIDATES_PER_TEMPLATE)[:MAX_CANDIDATES_PER_TEMPLATE]
                xs, ys, scores = xs[top], ys[top], scores[top]
            for x, y, s in zip(xs.tolist(), ys.tolist(), scores.tolist()):
                cx = (x + tw / 2.0) * fx
                cy = (y + th / 2.0) * fy
                cands.append((s, cx - tp.w / 2.0, cy - tp.h / 2.0, cx + tp.w / 2.0,
                              cy + tp.h / 2.0, tp.name, tp.w, tp.h))

        cands.sort(key=lambda c: c[0], reverse=True)
        kept = []
        for c in cands:
            box = c[1:5]
            cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
            dup = False
            for k_ in kept:
                kb = k_[1:5]
                if _iou(box, kb) > NMS_IOU or (kb[0] <= cx < kb[2] and kb[1] <= cy < kb[3]):
                    dup = True
                    break
            if dup:
                continue
            if any(_centre_in(cx, cy, b) for b in exclude):
                continue
            kept.append(c)
            if len(kept) >= MAX_MOBS:
                break

        return [Mob(x=int(round((c[1] + c[3]) / 2.0)), y=int(round((c[2] + c[4]) / 2.0)),
                    w=int(c[6]), h=int(c[7]), score=float(min(1.0, max(0.0, c[0]))), template=c[5])
                for c in kept]

    # ---- player -----------------------------------------------------------------------
    def _find_player(self, gray: np.ndarray, threshold: float) -> Optional[PointPx]:
        tp = self._player
        if tp is None or tp.h > gray.shape[0] or tp.w > gray.shape[1]:
            return None
        res = cv2.matchTemplate(gray, tp.gray, cv2.TM_CCOEFF_NORMED)
        np.nan_to_num(res, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        _, best, _, (x, y) = cv2.minMaxLoc(res)
        if best < threshold:
            return None
        cx = x + tp.w // 2
        cy = y + tp.h // 2 - PLAYER_TAG_TO_BODY_PX
        return (int(cx), int(max(0, cy)))
