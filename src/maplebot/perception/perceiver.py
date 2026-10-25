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
     c. Partly hidden: a whole-template score just under the threshold gets a second try
        with each half of the template alone (HALF_BAND / HALF_PENALTY).
     Then greedy non-max suppression across all templates, and anything centred inside
     the minimap / HP / MP boxes is dropped (UI proposals are skipped before verifying).
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
from maplebot.types import Box, Flag, Frame, Item, Mob, Perception, PointPx

from . import calib
from .sprites import SpriteMatcher, interleave_references
from .interruptions import AlertMatcher, central_panel
from .hud import HUDReader

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

COARSE_MARGIN = 0.20         # proposals need coarse gray score >= mob_threshold - this (worst true mob on the real frame: 0.56 vs 0.50 cut)
VERIFY_BLUR_SIGMA = 1.5      # Gaussian sigma (px) applied before the colour verification
VERIFY_BLUR_KSIZE = 7        # fixed kernel (~2.3 sigma each side): much faster than auto size
# Best proposals per template variant that get verified. On the real frame true mobs rank
# 0-2 among proposals; 10 leaves room for a crowded screen and bounds the cost (~0.3 ms each).
MAX_VERIFY_PER_TEMPLATE = 10
# Partly hidden mobs (another mob or the player in front of one side): if the whole-template
# score lands within HALF_BAND below mob_threshold, the left and right halves are scored
# alone. A half needs mob_threshold + HALF_PENALTY, and is reported as (half score -
# HALF_PENALTY), because half a sprite is weaker evidence than a whole one.
HALF_BAND = 0.15
HALF_PENALTY = 0.10

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


def _blur(img: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(img, (VERIFY_BLUR_KSIZE, VERIFY_BLUR_KSIZE), VERIFY_BLUR_SIGMA)


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
    def __init__(self, settings: Settings, mob_templates: List[Path], player_template: Optional[Path],
                 item_templates: Optional[List[Path]] = None, alert_templates: Optional[List[Path]] = None, hud_sync: bool = False):
        self._hud_sync = hud_sync
        self._hud = None
        self._alerts = AlertMatcher(alert_templates)
        self._sprites = SpriteMatcher()
        self._mob_raw: List[_Template] = []
        for p in interleave_references(mob_templates or []):
            p = Path(p)
            if self._sprites.add(p):
                continue
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
        self._raw_cursor = 0
        self._player_small: Optional[np.ndarray] = None
        self._prep_key = None
        self.update_settings(settings)
        self._items = (Perceiver(settings, item_templates, None) if item_templates else None)

    # ---- settings ---------------------------------------------------------------------
    def update_settings(self, settings: Settings) -> None:
        previous = getattr(self, "settings", None)
        changed = (previous is None or previous.ocr != settings.ocr or any(
            getattr(previous.calibration,key) != getattr(settings.calibration,key)
            for key in ("hp_stock_box","mp_stock_box","mesos_box","notification_box")))
        if self._hud is not None and (not settings.ocr.enabled or changed):
            self._hud.close()
            self._hud = None
        if settings.ocr.enabled and self._hud is None:
            self._hud = HUDReader(settings, synchronous=self._hud_sync)
        self.settings = settings
        if getattr(self, "_items", None) is not None:
            self._items.update_settings(settings)
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
                verify = _blur(bgr)
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
        if cal.reference_size is not None and list(cal.reference_size)!=[w,h]:
            flags.add(Flag.VIEW_CHANGED)

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
        if self.settings.safety.pause_on_dialog and central_panel(img):
            flags.add(Flag.UNEXPECTED_DIALOG)
        if self._alerts.matches(gray, self.settings.safety.alert_threshold):
            flags.add(Flag.ALERT_MATCH)
        scale = self._scale()
        small = None
        if scale < 1.0:
            small = cv2.resize(gray, (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                               interpolation=cv2.INTER_AREA)

        # 2. mobs
        mobs = self._find_mobs(img, small if small is not None else gray, float(det.mob_threshold),
                               [b for b in (minimap_box, hp_box, mp_box) if b is not None])

        items = ()
        if self._items is not None and self._items._prepared:
            matches = self._items._find_mobs(img, small if small is not None else gray,
                float(det.item_threshold), [b for b in (minimap_box, hp_box, mp_box) if b is not None],
                include_sprites=False)
            items = tuple(Item(m.x, m.y, m.w, m.h, m.score, m.template) for m in matches)

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

        if self._items is not None and self._items._sprites.templates and not estimated:
            loot = self.settings.loot
            feet = player_screen[1] + loot.player_feet_offset_px
            y0, y1 = max(0, feet-loot.floor_tolerance_px-32), min(h, feet+loot.floor_tolerance_px+32)
            matches = self._items._sprites.find(img, max(det.item_threshold, 0.90),
                [b for b in (minimap_box, hp_box, mp_box) if b is not None], False,
                (0, y0, w, y1-y0), budget=det.sprite_templates_per_tick)
            items += tuple(Item(m.x, m.y, m.w, m.h, m.score, m.template) for m in matches)

        # 4. bars
        hp = calib.bar_fill(img, hp_box) if hp_box is not None else None
        mp = calib.bar_fill(img, mp_box) if mp_box is not None else None

        hud = self._hud.poll(img,t) if self._hud is not None else {}
        if hud.get("inventory_full"):
            flags.add(Flag.INVENTORY_FULL)
        if self.settings.ocr.enabled and any(getattr(cal,kind+"_stock_box") is not None and
                hud.get(kind+"_stock") is None for kind in ("hp","mp")):
            flags.add(Flag.STOCK_UNREADABLE)

        # 5. confidence
        conf = confidence_for(player_map is not None,
                              estimated and self.player_template_configured)
        if conf < CONF_LOW_THRESHOLD:
            flags.add(Flag.LOW_CONFIDENCE)

        return Perception(
            t=t, screen_size=(w, h), player_map=player_map, player_screen=player_screen,
            player_screen_estimated=estimated, mobs=tuple(mobs), hp=hp, mp=mp,
            flags=frozenset(flags), confidence=conf, items=items, **hud,
        )

    # ---- mobs -------------------------------------------------------------------------
    def _find_mobs(self, img: np.ndarray, small: np.ndarray, threshold: float,
                   exclude: Sequence[Box], include_sprites: bool = True) -> List[Mob]:
        """`img` is the full BGR frame; `small` its grayscale copy downscaled by
        detection.downscale (the full-size grayscale at 1.0)."""
        sprite_mobs = self._sprites.find(img, max(threshold, 0.90), exclude,
                                          self.settings.detection.mirror_templates,
                                          budget=self.settings.detection.sprite_templates_per_tick) if include_sprites else []
        if not self._prepared:
            return sprite_mobs
        H_, W_ = img.shape[:2]
        sh, sw = small.shape[:2]
        # Actual scale per axis (rounding) so coordinates map back exactly.
        fx = W_ / sw
        fy = H_ / sh
        pad = int(np.ceil(max(fx, fy))) + 1            # search slack around a proposal
        bpad = VERIFY_BLUR_KSIZE // 2                  # so the ROI blur has real context
        coarse_th = threshold - COARSE_MARGIN

        # Stage a: proposals from every template variant.
        proposals = []   # (coarse score, gx, gy, template)
        budget = max(1,int(self.settings.detection.sprite_templates_per_tick))*2
        pool = self._prepared
        if len(pool)>budget:
            pool = [pool[(self._raw_cursor+i)%len(pool)] for i in range(budget)]
            self._raw_cursor = (self._raw_cursor+budget)%len(self._prepared)
        for tp in pool:
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
                if any(_centre_in(gx + tp.w / 2.0, gy + tp.h / 2.0, b) for b in exclude):
                    continue   # UI (minimap / bars): don't spend a verification on it
                proposals.append((float(res[y, x]), gx, gy, tp))

        # Stages b + c, best proposal first. A proposal centred inside an already accepted
        # mob would be removed by NMS anyway (same mob via its mirror or another template),
        # so it is not verified at all; that is where most of the verification time went.
        proposals.sort(key=lambda q: q[0], reverse=True)
        kept = []    # (score, x0, y0, x1, y1, name, w, h) full-res box
        for _, gx, gy, tp in proposals:
            pcx, pcy = gx + tp.w / 2.0, gy + tp.h / 2.0
            if any(k_[1] <= pcx < k_[3] and k_[2] <= pcy < k_[4] for k_ in kept):
                continue
            c = self._verify(img, tp, gx, gy, pad, bpad, threshold)
            if c is None:
                continue
            box = c[1:5]
            cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
            if any(_iou(box, k_[1:5]) > NMS_IOU or (k_[1] <= cx < k_[3] and k_[2] <= cy < k_[4])
                   for k_ in kept):
                continue
            if any(_centre_in(cx, cy, b) for b in exclude):
                continue
            kept.append(c)
            if len(kept) >= MAX_MOBS:
                break

        return [Mob(x=int(round((c[1] + c[3]) / 2.0)), y=int(round((c[2] + c[4]) / 2.0)),
                    w=int(c[6]), h=int(c[7]), score=float(min(1.0, max(0.0, c[0]))), template=c[5])
                for c in kept] + [m for m in sprite_mobs if not any(
                    abs(m.x - (c[1]+c[3])/2) < m.w/2 and abs(m.y-(c[2]+c[4])/2) < m.h/2 for c in kept)]

    def _verify(self, img: np.ndarray, tp: "_Prepared", gx: int, gy: int, pad: int, bpad: int,
                threshold: float):
        """Colour check of one proposal (top-left guess gx, gy at full resolution).

        Returns (score, x0, y0, x1, y1, name, w, h) or None if below `threshold`.
        """
        H_, W_ = img.shape[:2]
        x0 = max(0, gx - pad - bpad)
        y0 = max(0, gy - pad - bpad)
        x1 = min(W_, gx + tp.w + pad + bpad)
        y1 = min(H_, gy + tp.h + pad + bpad)
        if x1 - x0 < tp.w or y1 - y0 < tp.h:
            return None
        roi = _blur(img[y0:y1, x0:x1])
        # Only accept positions within `pad` of the proposal (not in the blur margin).
        ix0, iy0 = max(0, gx - pad - x0), max(0, gy - pad - y0)
        ix1 = min(x1 - x0 - tp.w, gx + pad - x0)
        iy1 = min(y1 - y0 - tp.h, gy + pad - y0)
        if ix1 < ix0 or iy1 < iy0:
            return None
        sub = roi[iy0:iy1 + tp.h, ix0:ix1 + tp.w]
        rr = cv2.matchTemplate(sub, tp.verify, cv2.TM_CCOEFF_NORMED)
        np.nan_to_num(rr, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        _, s, _, (bx, by) = cv2.minMaxLoc(rr)
        # Gaussian blur needs surrounding pixels. A tight training crop has no real
        # surrounding scene, so its border can disagree even for an exact sprite.
        # Permit a strong interior match only when the whole crop is already plausible.
        if threshold-HALF_BAND <= s < threshold and min(tp.w,tp.h)>2*bpad+4:
            core = cv2.matchTemplate(sub[bpad:-bpad,bpad:-bpad],
                                    tp.verify[bpad:-bpad,bpad:-bpad],cv2.TM_CCOEFF_NORMED)
            np.nan_to_num(core,copy=False,nan=0,posinf=0,neginf=0)
            _,core_score,_,(cx,cy) = cv2.minMaxLoc(core)
            if core_score >= threshold+.15 and core_score-.15>s:
                s,bx,by = core_score-.15,cx,cy
        if threshold - HALF_BAND <= s < threshold:
            s, bx, by = self._half_match(sub, tp, threshold, s, bx, by)
        if s < threshold:
            return None
        cx = x0 + ix0 + bx + tp.w / 2.0
        cy = y0 + iy0 + by + tp.h / 2.0
        return (s, cx - tp.w / 2.0, cy - tp.h / 2.0, cx + tp.w / 2.0, cy + tp.h / 2.0,
                tp.name, tp.w, tp.h)

    @staticmethod
    def _half_match(sub: np.ndarray, tp: "_Prepared", threshold: float, s: float, bx: int, by: int):
        """Best of the whole-template result and the left/right half templates (see HALF_BAND).

        `sub` is the blurred search window for the whole template; a half is searched over
        the same top-left positions, so (bx, by) stays the whole sprite's top-left.
        """
        hw = tp.w // 2
        nx = sub.shape[1] - tp.w + 1          # whole-template positions in x
        for hx, half in ((0, tp.verify[:, :hw]), (tp.w - hw, tp.verify[:, tp.w - hw:])):
            win = sub[:, hx:hx + nx - 1 + half.shape[1]]
            if win.shape[1] < half.shape[1] or win.shape[0] < half.shape[0]:
                continue
            hr = cv2.matchTemplate(win, half, cv2.TM_CCOEFF_NORMED)
            np.nan_to_num(hr, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            _, hs, _, (hbx, hby) = cv2.minMaxLoc(hr)
            if hs >= threshold + HALF_PENALTY and hs - HALF_PENALTY > s:
                s, bx, by = hs - HALF_PENALTY, hbx, hby
        return s, bx, by

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

    def close(self):
        if self._hud is not None:
            self._hud.close()
        if self._items is not None:
            self._items.close()
