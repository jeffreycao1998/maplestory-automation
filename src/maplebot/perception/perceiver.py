"""
Frame -> Perception.

Pipeline per frame (all OpenCV, no models):
  1. Minimap: HSV mask of the player-dot colour inside calibration.minimap_box, largest
     sane blob -> player_map (fractions of the box).
  2. Mobs, two stages per template (and its mirror if detection.mirror_templates):
     a. Propose: grayscale TM_CCOEFF_NORMED on a copy downscaled by detection.downscale;
        local maxima >= mob_threshold - COARSE_MARGIN (at most MAX_VERIFY_PER_TEMPLATE).
     b. Verify: colour (BGR) TM_CCOEFF_NORMED at full resolution in a small window
        around each proposal, on images blurred with VERIFY_BLUR_SIGMA. Keep if
        >= detection.mob_threshold; that verified score is Mob.score.
     Why: a mob's other animation frames differ from the cropped frame by a few pixels of
     shape (grayscale scores ~0.55-0.62), while leafy background has similar gray
     texture (also ~0.6). Light blur forgives the small shape change and colour rejects
     green-on-green leaves: on the real Tree That Grew II frame this moves the worst true
     mob to 0.74 and the best background patch to 0.63.
     Then greedy non-max suppression across all templates, and anything centred inside
     the minimap / HP / MP boxes is dropped.
  3. Player: name-tag template, coarse-to-fine; body centre is placed
     detection.player_tag_offset_px above the tag centre. Otherwise a fixed screen estimate.
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
# ^ default only; settings.detection.player_tag_offset_px is used when set.

# Where the character usually is when we can't see the tag (the camera follows the player).
PLAYER_ESTIMATE_X_FRAC = 0.5
PLAYER_ESTIMATE_Y_FRAC = 0.62

# --- mob matching ------------------------------------------------------------------------
PLAYER_COARSE_PEAKS = 3      # name-tag candidates refined at full resolution
PLAYER_COARSE_MARGIN = 0.25  # coarse score may be this much below player_threshold

COARSE_MARGIN = 0.25         # proposals need coarse gray score >= mob_threshold - this
VERIFY_BLUR_SIGMA = 1.5      # Gaussian sigma (px) applied before the colour verification
MAX_VERIFY_PER_TEMPLATE = 25 # best proposals per template variant that get verified

MIN_TEMPLATE_PX = 6         # a template smaller than this after downscaling is skipped
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
    bgr: Optional[np.ndarray] = None   # full-resolution colour


@dataclass
class _Prepared:
    name: str
    small: np.ndarray         # grayscale at the matching scale (proposal stage)
    verify: np.ndarray        # full-resolution BGR, blurred (verification stage)
    w: int                    # full-resolution size (for Mob.w/h)
    h: int


def _load_bgr(path: Path) -> Optional[np.ndarray]:
    """Read an image as BGR; works with non-ASCII Windows paths. None on failure."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)   # drops alpha
        if img is None or img.size == 0:
            return None
        return img
    except Exception:
        return None


def _load_gray(path: Path) -> Optional[np.ndarray]:
    img = _load_bgr(path)
    return None if img is None else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


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
            bgr = _load_bgr(p)
            g = None if bgr is None else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            if g is None or not _usable_template(g):
                log.warning("skipping mob template %s (unreadable or blank)", p)
                continue
            self._mob_raw.append(_Template(p.name, g, g.shape[1], g.shape[0], bgr))

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
        self._player_small: Optional[np.ndarray] = None
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
            variants = [(t.name, t.gray, t.bgr)]
            if settings.detection.mirror_templates:
                variants.append((t.name, cv2.flip(t.gray, 1), cv2.flip(t.bgr, 1)))
            for name, g, bgr in variants:
                if scale != 1.0:
                    sw = max(1, int(round(t.w * scale)))
                    sh = max(1, int(round(t.h * scale)))
                    g = cv2.resize(g, (sw, sh), interpolation=cv2.INTER_AREA)
                if min(g.shape[:2]) < MIN_TEMPLATE_PX or not _usable_template(g):
                    log.warning("mob template %s too small/flat at downscale %.2f; skipped", name, scale)
                    continue
                verify = cv2.GaussianBlur(bgr, (0, 0), VERIFY_BLUR_SIGMA)
                prepared.append(_Prepared(name, np.ascontiguousarray(g), verify, t.w, t.h))
        self._prepared = prepared
        self._player_small = None
        if self._player is not None and scale < 1.0:
            g = cv2.resize(self._player.gray, (max(1, int(round(self._player.w * scale))),
                                               max(1, int(round(self._player.h * scale)))),
                           interpolation=cv2.INTER_AREA)
            if min(g.shape[:2]) >= 4 and _usable_template(g):
                self._player_small = np.ascontiguousarray(g)
        self._prep_key = key

    def _tag_offset(self) -> int:
        try:
            v = int(getattr(self.settings.detection, "player_tag_offset_px", PLAYER_TAG_TO_BODY_PX))
        except (TypeError, ValueError):
            v = PLAYER_TAG_TO_BODY_PX
        return v

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
        scale = self._scale()
        small = None
        if scale < 1.0:
            small = cv2.resize(gray, (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                               interpolation=cv2.INTER_AREA)

        # 2. mobs
        mobs = self._find_mobs(img, small if small is not None else gray, float(det.mob_threshold),
                               [b for b in (minimap_box, hp_box, mp_box) if b is not None])

        # 3. player
        player_screen: Optional[PointPx] = None
        estimated = True
        if self._player is not None:
            found = self._find_player(gray, small)
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
    def _find_mobs(self, img: np.ndarray, small: np.ndarray, threshold: float,
                   exclude: Sequence[Box]) -> List[Mob]:
        """`img` is the full BGR frame; `small` its grayscale copy downscaled by
        detection.downscale (the full-size grayscale at 1.0)."""
        if not self._prepared:
            return []
        H_, W_ = img.shape[:2]
        sh, sw = small.shape[:2]
        # Actual scale per axis (rounding) so coordinates map back exactly.
        fx = W_ / sw
        fy = H_ / sh
        pad = int(np.ceil(max(fx, fy))) + 2            # search slack around a proposal
        bpad = int(np.ceil(3 * VERIFY_BLUR_SIGMA))     # so the ROI blur has real context
        coarse_th = threshold - COARSE_MARGIN

        cands = []   # (score, x0, y0, x1, y1, name, w, h) full-res box
        for tp in self._prepared:
            th, tw = tp.small.shape[:2]
            if th > sh or tw > sw or tp.h > H_ or tp.w > W_:
                continue
            res = cv2.matchTemplate(small, tp.small, cv2.TM_CCOEFF_NORMED)
            np.nan_to_num(res, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            if float(res.max()) < coarse_th:
                continue
            # Local maxima within half a template: one proposal per object per template.
            k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, tw // 2) | 1, max(3, th // 2) | 1))
            peaks = (res >= coarse_th) & (res >= cv2.dilate(res, k))
            ys, xs = np.nonzero(peaks)
            if xs.size == 0:
                continue
            if xs.size > MAX_VERIFY_PER_TEMPLATE:
                top = np.argpartition(-res[ys, xs], MAX_VERIFY_PER_TEMPLATE)[:MAX_VERIFY_PER_TEMPLATE]
                xs, ys = xs[top], ys[top]
            for x, y in zip(xs.tolist(), ys.tolist()):
                gx, gy = int(round(x * fx)), int(round(y * fy))     # full-res top-left guess
                x0 = max(0, gx - pad - bpad)
                y0 = max(0, gy - pad - bpad)
                x1 = min(W_, gx + tp.w + pad + bpad)
                y1 = min(H_, gy + tp.h + pad + bpad)
                if x1 - x0 < tp.w or y1 - y0 < tp.h:
                    continue
                roi = cv2.GaussianBlur(img[y0:y1, x0:x1], (0, 0), VERIFY_BLUR_SIGMA)
                # Only accept positions within `pad` of the proposal (not in the blur margin).
                ix0, iy0 = max(0, gx - pad - x0), max(0, gy - pad - y0)
                ix1 = min(x1 - x0 - tp.w, gx + pad - x0)
                iy1 = min(y1 - y0 - tp.h, gy + pad - y0)
                if ix1 < ix0 or iy1 < iy0:
                    continue
                rr = cv2.matchTemplate(roi[iy0:iy1 + tp.h, ix0:ix1 + tp.w], tp.verify,
                                       cv2.TM_CCOEFF_NORMED)
                np.nan_to_num(rr, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
                _, s, _, (bx, by) = cv2.minMaxLoc(rr)
                if s < threshold:
                    continue
                cx = x0 + ix0 + bx + tp.w / 2.0
                cy = y0 + iy0 + by + tp.h / 2.0
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
    def _find_player(self, gray: np.ndarray, small: Optional[np.ndarray] = None) -> Optional[PointPx]:
        """Best name-tag match >= detection.player_threshold over the whole screen.

        Coarse-to-fine for speed: score every position on the downscaled image, then
        re-match at full resolution in a small window around the PLAYER_COARSE_PEAKS best
        coarse peaks. The final score is always the full-resolution one.
        """
        tp = self._player
        if tp is None or tp.h > gray.shape[0] or tp.w > gray.shape[1]:
            return None
        threshold = float(self.settings.detection.player_threshold)
        scale = self._scale()
        st = self._player_small
        cands: List[Tuple[int, int]] = []
        if (small is not None and st is not None and scale < 1.0
                and st.shape[0] <= small.shape[0] and st.shape[1] <= small.shape[1]):
            fx = gray.shape[1] / small.shape[1]
            fy = gray.shape[0] / small.shape[0]
            res = cv2.matchTemplate(small, st, cv2.TM_CCOEFF_NORMED)
            np.nan_to_num(res, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            for _ in range(PLAYER_COARSE_PEAKS):
                _, v, _, (x, y) = cv2.minMaxLoc(res)
                if v < threshold - PLAYER_COARSE_MARGIN:
                    break
                cands.append((int(round(x * fx)), int(round(y * fy))))
                # suppress this peak before taking the next one
                cv2.rectangle(res, (x - st.shape[1] // 2, y - st.shape[0] // 2),
                              (x + st.shape[1] // 2, y + st.shape[0] // 2), -1.0, -1)
            pad = int(np.ceil(2.0 / scale)) + 2
        else:
            cands = [(0, 0)]
            pad = None   # full search

        best, best_xy = -1.0, None
        H_, W_ = gray.shape[:2]
        for (x, y) in cands:
            if pad is None:
                x0, y0, x1, y1 = 0, 0, W_, H_
            else:
                x0, y0 = max(0, x - pad), max(0, y - pad)
                x1, y1 = min(W_, x + tp.w + pad), min(H_, y + tp.h + pad)
            roi = gray[y0:y1, x0:x1]
            if roi.shape[0] < tp.h or roi.shape[1] < tp.w:
                continue
            res = cv2.matchTemplate(roi, tp.gray, cv2.TM_CCOEFF_NORMED)
            np.nan_to_num(res, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            _, v, _, (rx, ry) = cv2.minMaxLoc(res)
            if v > best:
                best, best_xy = v, (x0 + rx, y0 + ry)
        if best_xy is None or best < threshold:
            return None
        cx = best_xy[0] + tp.w // 2
        cy = best_xy[1] + tp.h // 2 - self._tag_offset()
        return (int(cx), int(max(0, cy)))
