"""
Synthetic MapleStory-like scenes for tests, evals and closed-loop runs (no game needed).

Owner: test-harness-engineer.

Two entry points:

* ``make_scene(seed, ...) -> (image, truth)`` -- one still frame plus its ground truth in
  the ``data/README.md`` label format (extra keys are allowed and ignored by the eval).
* ``SceneSequence`` -- a tiny world sim. ``apply(action)`` takes ``types.Action`` values
  with game-like semantics, ``frame(t)`` advances the world to ``t`` and renders it.

What a scene looks like (all deterministic for a given seed):
  gradient sky with low-frequency texture + mild noise, clouds / hills / trees / a rope /
  a sign as distractors, one horizontal grass platform, mobs (textured blob sprites that
  face left by default and are mirrored to face right), a magician-ish character with a
  dark name tag (white text) under the feet, a dark minimap with a light platform line and
  a yellow (BGR (0, 230, 255)) player dot, and a bottom status bar with red HP / blue MP
  bars whose empty part is grey.

Geometry contract (matches ``maplebot.perception.perceiver`` defaults):
  ``player_screen`` is the body centre. The feet are ``CHAR_HALF_H`` px below it and the
  name tag's centre is ``TAG_BELOW_BODY_PX`` (63) px below it, so a name-tag match maps
  straight back to the body centre.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from maplebot.settings import Settings
from maplebot.types import Action, Frame

__all__ = [
    "DOT_BGR", "HP_BGR", "MP_BGR", "EMPTY_BGR", "CHAR_HALF_H", "TAG_BELOW_BODY_PX",
    "MOB_SIZE", "DEFAULT_NAME", "SKY_AT_GROUND_BGR",
    "default_layout", "make_scene", "random_scene", "mob_sprite", "mob_mask",
    "name_tag_sprite", "settings_for_scene", "write_templates", "SceneSequence",
]

# ---- colours (BGR) ------------------------------------------------------------------
DOT_BGR = (0, 230, 255)          # player dot on the minimap (yellow)
HP_BGR = (40, 40, 225)           # red
MP_BGR = (225, 120, 30)          # blue
EMPTY_BGR = (105, 105, 105)      # grey, unsaturated -> "empty" for bar_fill
MINIMAP_BG = (42, 36, 30)
MINIMAP_LINE = (175, 175, 170)
STATUS_BAR_BG = (52, 50, 48)
TAG_BG = (28, 26, 24)
TAG_FG = (255, 255, 255)
SKY_TOP = np.array([235, 185, 120], np.float32)       # light blue
SKY_GROUND = np.array([215, 225, 205], np.float32)    # pale, near the horizon
SKY_AT_GROUND_BGR = tuple(int(v) for v in SKY_GROUND)  # template backdrop colour

# ---- geometry ----------------------------------------------------------------------
CHAR_HALF_H = 42                 # body centre -> feet
TAG_BELOW_BODY_PX = 63           # body centre -> name-tag centre (perceiver constant)
MOB_SIZE = (56, 44)              # (w, h) of every mob sprite
DEFAULT_NAME = "MapleMage"
PLATFORM_THICK = 20
STATUS_BAR_H = 48
MINIMAP_LINE_LEFT, MINIMAP_LINE_RIGHT = 0.05, 0.95    # platform span on the minimap
DOT_RADIUS = 3


def default_layout(size: Tuple[int, int] = (1024, 768)) -> Dict[str, object]:
    """Default UI boxes and positions for a frame of ``size`` = (w, h)."""
    w, h = size
    player_y = int(h * 0.62)
    return {
        "minimap_box": (10, 10, 180, 100),
        "hp_box": (w // 2 - 190, h - 32, 170, 14),
        "mp_box": (w // 2 + 20, h - 32, 170, 14),
        "player_x": w // 2,
        "player_y": player_y,
        "ground_y": player_y + CHAR_HALF_H,
    }


# ======================================================================================
# sprites
# ======================================================================================
_MOB_PALETTE = [
    ((40, 120, 235), (60, 170, 250)),    # orange mushroom
    ((60, 170, 70), (110, 210, 120)),    # green slime
    ((180, 110, 60), (220, 160, 100)),   # blue snail
    ((120, 60, 160), (170, 110, 210)),   # purple
    ((50, 70, 150), (90, 110, 200)),     # brown/red
]


@lru_cache(maxsize=32)
def _mob_sprite_cached(seed: int) -> Tuple[np.ndarray, np.ndarray]:
    w, h = MOB_SIZE
    rng = np.random.default_rng(7919 + int(seed))
    body, cap = _MOB_PALETTE[int(seed) % len(_MOB_PALETTE)]
    body = np.array(body, np.float32) + rng.integers(-20, 21, 3)
    cap = np.array(cap, np.float32) + rng.integers(-20, 21, 3)

    mask = np.zeros((h, w), np.uint8)
    img = np.zeros((h, w, 3), np.float32)
    # body (lower), cap (upper, wider), two feet, a tail on the back (right) side
    parts = [
        ((w // 2 + 2, int(h * 0.62)), (int(w * 0.36), int(h * 0.30)), body),
        ((w // 2 - 2, int(h * 0.34)), (int(w * 0.44), int(h * 0.26)), cap),
        ((int(w * 0.34), h - 5), (7, 4), body * 0.6),
        ((int(w * 0.64), h - 5), (7, 4), body * 0.6),
        ((w - 8, int(h * 0.55)), (6, 9), cap * 0.8),
    ]
    for (cx, cy), axes, col in parts:
        m = np.zeros_like(mask)
        cv2.ellipse(m, (cx, cy), axes, 0, 0, 360, 255, -1)
        img[m > 0] = col
        mask |= m

    # texture: low-frequency shading + spots on the cap
    lf = cv2.resize(rng.normal(0, 18, (h // 6 + 1, w // 6 + 1)).astype(np.float32), (w, h),
                    interpolation=cv2.INTER_CUBIC)
    img += lf[:, :, None]
    for _ in range(int(rng.integers(3, 6))):
        sx = int(rng.integers(int(w * 0.2), int(w * 0.8)))
        sy = int(rng.integers(int(h * 0.15), int(h * 0.45)))
        cv2.circle(img, (sx, sy), int(rng.integers(2, 5)), (235, 240, 245), -1)
    # face on the LEFT (facing side): eyes + mouth
    for ex in (int(w * 0.24), int(w * 0.40)):
        cv2.circle(img, (ex, int(h * 0.60)), 4, (250, 250, 250), -1)
        cv2.circle(img, (ex - 1, int(h * 0.61)), 2, (10, 10, 10), -1)
    cv2.line(img, (int(w * 0.26), int(h * 0.78)), (int(w * 0.38), int(h * 0.76)), (20, 20, 40), 2)
    img[mask == 0] = 0
    # dark outline
    edge = cv2.subtract(mask, cv2.erode(mask, np.ones((3, 3), np.uint8)))
    img[edge > 0] = (body * 0.35)
    img = np.clip(img, 0, 255).astype(np.uint8)
    return img, mask


def mob_mask(seed: int = 0, facing: str = "left") -> np.ndarray:
    """uint8 mask (255 = mob pixel) of mob kind ``seed``; mirrored for ``facing='right'``."""
    m = _mob_sprite_cached(int(seed))[1]
    return np.ascontiguousarray(m[:, ::-1] if facing == "right" else m).copy()


def mob_sprite(seed: int = 0, facing: str = "left",
               backdrop: Optional[Tuple[int, int, int]] = SKY_AT_GROUND_BGR) -> np.ndarray:
    """BGR crop of mob kind ``seed`` (``MOB_SIZE``), as a user would box it in the wizard.

    Pixels outside the mob's shape are ``backdrop`` (the sky colour just above the
    platform, which is what surrounds a mob in a scene). ``backdrop=None`` leaves them 0.
    In a rendered scene, ``image[y:y+h, x:x+w]`` at the truth box equals this crop on every
    mask pixel (see ``mob_mask``). Faces left; ``facing='right'`` is the mirror image.
    """
    img, mask = _mob_sprite_cached(int(seed))
    out = img.copy()
    if backdrop is not None:
        out[mask == 0] = backdrop
    if facing == "right":
        out = out[:, ::-1]
    return np.ascontiguousarray(out)


@lru_cache(maxsize=16)
def _name_tag_cached(text: str) -> np.ndarray:
    font, scale = cv2.FONT_HERSHEY_SIMPLEX, 0.42
    (tw, th), base = cv2.getTextSize(text, font, scale, 1)
    tag = np.full((th + base + 6, tw + 10, 3), TAG_BG, np.uint8)
    cv2.rectangle(tag, (0, 0), (tag.shape[1] - 1, tag.shape[0] - 1), (70, 66, 62), 1)
    cv2.putText(tag, text, (5, th + 3), font, scale, TAG_FG, 1, cv2.LINE_AA)
    return tag


def name_tag_sprite(text: str = DEFAULT_NAME) -> np.ndarray:
    """Exact BGR crop of the player's name tag (opaque) -- the ``player.png`` template."""
    return _name_tag_cached(str(text)).copy()


# ======================================================================================
# drawing helpers
# ======================================================================================
def _background(seed: int, w: int, h: int, ground_y: int) -> np.ndarray:
    """Sky gradient + texture + distractors + platform + dirt. Deterministic in seed."""
    rng = np.random.default_rng(int(seed))
    gy = max(1, ground_y)
    yy = np.clip(np.arange(h, dtype=np.float32) / gy, 0, 1)[:, None, None]
    sky = SKY_TOP[None, None, :] * (1 - yy) + SKY_GROUND[None, None, :] * yy
    img = np.broadcast_to(sky, (h, w, 3)).astype(np.float32).copy()
    tint = rng.integers(-8, 9, 3).astype(np.float32)
    img += tint
    # low-frequency texture
    lf = rng.normal(0, 7, (h // 48 + 2, w // 48 + 2, 3)).astype(np.float32)
    img += cv2.resize(lf, (w, h), interpolation=cv2.INTER_CUBIC)
    img = np.clip(img, 0, 255).astype(np.uint8)

    # far hills
    for _ in range(max(1, w // 400)):
        cx = int(rng.integers(0, w))
        rx = int(rng.integers(150, 320))
        ry = int(rng.integers(60, 130))
        col = (int(rng.integers(120, 150)), int(rng.integers(170, 200)), int(rng.integers(130, 160)))
        cv2.ellipse(img, (cx, gy), (rx, ry), 0, 180, 360, col, -1)
    # clouds
    for _ in range(max(2, w // 250)):
        cx = int(rng.integers(0, w))
        cy = int(rng.integers(30, max(31, gy // 2)))
        for k in range(3):
            cv2.ellipse(img, (cx + k * 26 - 26, cy + (k % 2) * 6), (34, 16), 0, 0, 360,
                        (250, 248, 245), -1)
    # trees (behind the platform, away from the ground line where mobs stand)
    for _ in range(max(1, w // 450)):
        tx = int(rng.integers(20, max(21, w - 20)))
        top = gy - int(rng.integers(150, 210))
        cv2.rectangle(img, (tx - 7, top + 40), (tx + 7, gy - 60), (40, 70, 100), -1)
        for dx, dy, r in ((0, 0, 38), (-28, 20, 28), (28, 18, 30)):
            cv2.circle(img, (tx + dx, top + dy + 30), r, (60, 140, 70), -1)
    # a rope hanging from the sky
    rx = int(rng.integers(40, max(41, w - 40)))
    cv2.line(img, (rx, 0), (rx, gy - 70), (120, 170, 210), 3)
    # a sign post
    sx = int(rng.integers(40, max(41, w - 80)))
    cv2.rectangle(img, (sx + 18, gy - 50), (sx + 24, gy), (50, 80, 110), -1)
    cv2.rectangle(img, (sx, gy - 72), (sx + 44, gy - 48), (70, 120, 170), -1)
    cv2.putText(img, "Hene", (sx + 4, gy - 55), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (30, 40, 60), 1)

    # platform: grass top + dirt; ground below
    cv2.rectangle(img, (0, gy), (w, h), (45, 85, 125), -1)
    cv2.rectangle(img, (0, gy), (w, gy + PLATFORM_THICK), (60, 105, 150), -1)
    cv2.rectangle(img, (0, gy), (w, gy + 6), (60, 175, 90), -1)
    for _ in range(w // 25):           # pebbles / flowers on and under the platform
        px = int(rng.integers(0, w))
        py = int(rng.integers(gy + 8, min(h, gy + 120)))
        col = tuple(int(v) for v in rng.integers(30, 120, 3))
        cv2.circle(img, (px, py), int(rng.integers(2, 5)), col, -1)

    # 2x2-block sensor-like noise: texture for the matcher, but keeps PNGs ~half the size
    small = rng.normal(0, 2.0, ((h + 1) // 2, (w + 1) // 2, 3)).astype(np.float32)
    noise = np.repeat(np.repeat(small, 2, axis=0), 2, axis=1)[:h, :w]
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _paste_masked(img: np.ndarray, spr: np.ndarray, mask: np.ndarray, x0: int, y0: int) -> None:
    H, W = img.shape[:2]
    h, w = spr.shape[:2]
    ax0, ay0 = max(0, x0), max(0, y0)
    ax1, ay1 = min(W, x0 + w), min(H, y0 + h)
    if ax0 >= ax1 or ay0 >= ay1:
        return
    sub = (slice(ay0 - y0, ay1 - y0), slice(ax0 - x0, ax1 - x0))
    m = mask[sub] > 0
    region = img[ay0:ay1, ax0:ax1]
    region[m] = spr[sub][m]


def _paste(img: np.ndarray, spr: np.ndarray, x0: int, y0: int) -> None:
    _paste_masked(img, spr, np.full(spr.shape[:2], 255, np.uint8), x0, y0)


def _draw_mob(img: np.ndarray, kind: int, cx: int, cy: int, facing: str) -> List[int]:
    w, h = MOB_SIZE
    x0, y0 = int(cx) - w // 2, int(cy) - h // 2
    _paste_masked(img, mob_sprite(kind, facing, backdrop=None), mob_mask(kind, facing), x0, y0)
    return [x0, y0, w, h]


def _visible_fraction(img: np.ndarray, box: Sequence[int], kind: int, facing: str) -> float:
    """Fraction of the mob's shape pixels that still show the sprite in the final image
    (others are covered by another mob, the character, damage numbers, UI or the frame edge)."""
    x0, y0, w, h = (int(v) for v in box)
    spr = mob_sprite(kind, facing, backdrop=None)
    mask = mob_mask(kind, facing) > 0
    total = int(mask.sum())
    H, W = img.shape[:2]
    ax0, ay0, ax1, ay1 = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if total == 0 or ax0 >= ax1 or ay0 >= ay1:
        return 0.0
    sub = (slice(ay0 - y0, ay1 - y0), slice(ax0 - x0, ax1 - x0))
    same = np.all(img[ay0:ay1, ax0:ax1] == spr[sub], axis=2) & mask[sub]
    return round(float(same.sum()) / total, 3)


def _draw_character(img: np.ndarray, px: int, py: int, facing: str) -> None:
    """Magician-ish body centred at (px, py); feet at py + CHAR_HALF_H."""
    s = 1 if facing == "right" else -1
    feet = py + CHAR_HALF_H
    robe = np.array([[px - 10, py - 8], [px + 10, py - 8], [px + 17, feet - 4], [px - 17, feet - 4]],
                    np.int32)
    cv2.fillPoly(img, [robe], (140, 55, 85))
    cv2.line(img, (px - 13, py + 14), (px + 13, py + 14), (60, 170, 220), 2)  # belt
    cv2.rectangle(img, (px - 12, feet - 5), (px - 3, feet), (40, 30, 30), -1)   # shoes
    cv2.rectangle(img, (px + 3, feet - 5), (px + 12, feet), (40, 30, 30), -1)
    cv2.circle(img, (px, py - 20), 12, (165, 200, 240), -1)                     # head
    cv2.circle(img, (px + 5 * s, py - 21), 2, (20, 20, 20), -1)                 # eye
    hat = np.array([[px - 16, py - 26], [px + 16, py - 26], [px - 4 * s, py - CHAR_HALF_H - 4]],
                   np.int32)
    cv2.fillPoly(img, [hat], (120, 40, 70))
    sx = px + 20 * s                                                            # staff
    cv2.line(img, (sx, py - 28), (sx, feet - 2), (40, 90, 130), 3)
    cv2.circle(img, (sx, py - 30), 5, (240, 160, 60), -1)


def _draw_name_tag(img: np.ndarray, px: int, py: int, text: str) -> List[int]:
    tag = _name_tag_cached(str(text))
    th, tw = tag.shape[:2]
    x0 = int(px) - tw // 2
    y0 = int(py) + TAG_BELOW_BODY_PX - th // 2
    _paste(img, tag, x0, y0)
    return [x0, y0, tw, th]


def _draw_minimap(img: np.ndarray, box: Sequence[int], player_map: Optional[Tuple[float, float]],
                  seed: int) -> Optional[List[float]]:
    """Draw the minimap; returns the dot centre actually drawn, as box fractions."""
    x, y, w, h = (int(v) for v in box)
    rng = np.random.default_rng(10007 + int(seed))
    cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), MINIMAP_BG, -1)
    cv2.rectangle(img, (x, y), (x + w - 1, y + 11), (62, 56, 50), -1)            # title bar
    cv2.putText(img, "Henesys", (x + 4, y + 9), cv2.FONT_HERSHEY_PLAIN, 0.6, (230, 230, 230), 1)
    cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), (125, 125, 125), 1)       # border
    fy_line = player_map[1] if player_map is not None else 0.62
    ly = y + int(round(fy_line * h)) + DOT_RADIUS + 1
    cv2.line(img, (x + int(MINIMAP_LINE_LEFT * w), ly), (x + int(MINIMAP_LINE_RIGHT * w), ly),
             MINIMAP_LINE, 2)
    upper = y + int(h * 0.35)                                                    # upper ledge
    cv2.line(img, (x + int(0.20 * w), upper), (x + int(0.45 * w), upper), MINIMAP_LINE, 2)
    # distractor markers: NPC (green), portal (blue), another player (red)
    for col in ((40, 200, 40), (230, 140, 40), (40, 40, 220)):
        mx = x + int(rng.integers(int(0.1 * w), int(0.9 * w)))
        if player_map is not None and abs(mx - (x + player_map[0] * w)) < 14:
            mx += 30 if mx < x + w / 2 else -30
        cv2.circle(img, (mx, ly - DOT_RADIUS - 1), 2, col, -1)
    if player_map is None:
        return None
    cx = int(round(player_map[0] * w - 0.5))
    cy = int(round(player_map[1] * h - 0.5))
    cx = min(max(cx, DOT_RADIUS + 1), w - DOT_RADIUS - 2)
    cy = min(max(cy, 12 + DOT_RADIUS), h - DOT_RADIUS - 2)
    cv2.circle(img, (x + cx, y + cy), DOT_RADIUS, DOT_BGR, -1, cv2.LINE_8)
    # Perception reports the blob centre as (pixel + 0.5) / size.
    return [round((cx + 0.5) / w, 5), round((cy + 0.5) / h, 5)]


def _draw_bar(img: np.ndarray, box: Sequence[int], frac: Optional[float], colour, label: str) -> Optional[float]:
    x, y, w, h = (int(v) for v in box)
    cv2.rectangle(img, (x - 1, y - 1), (x + w, y + h), (18, 18, 18), 1)
    cv2.putText(img, label, (x - 26, y + h - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (235, 235, 235), 1)
    cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), EMPTY_BGR, -1)
    if frac is None:
        return None
    frac = float(min(max(frac, 0.0), 1.0))
    n = int(round(frac * w))
    if n > 0:
        cv2.rectangle(img, (x, y), (x + n - 1, y + h - 1), colour, -1)
        hi = tuple(min(255, int(c * 0.6 + 100)) for c in colour)
        cv2.line(img, (x, y + 1), (x + n - 1, y + 1), hi, 1)   # glossy highlight
    return round(n / w, 5)


def _draw_damage(img: np.ndarray, x: int, y: int, value: int) -> None:
    cv2.putText(img, str(value), (int(x) - 14, int(y)), cv2.FONT_HERSHEY_DUPLEX, 0.8,
                (20, 60, 120), 4, cv2.LINE_AA)
    cv2.putText(img, str(value), (int(x) - 14, int(y)), cv2.FONT_HERSHEY_DUPLEX, 0.8,
                (40, 150, 255), 2, cv2.LINE_AA)


def _compose(bg: np.ndarray, *, seed: int, player: Optional[Tuple[int, int]], facing: str,
             mobs: Sequence[Tuple[int, int, str]], mob_kind: int, minimap_box, player_map,
             hp, mp, hp_box, mp_box, with_name_tag: bool, name: str,
             damage: Sequence[Tuple[int, int, int]] = ()) -> Tuple[np.ndarray, dict]:
    img = bg
    h, w = img.shape[:2]
    mob_truth = []
    drawn = []          # (truth dict, kind, facing) for the visibility pass
    for m in mobs:
        mx, my, mf = int(m[0]), int(m[1]), str(m[2]) if len(m) > 2 else "left"
        kind = int(m[3]) if len(m) > 3 else int(mob_kind)
        box = _draw_mob(img, kind, mx, my, mf)
        if 0 <= mx < w and 0 <= my < h:
            t = {"box": box, "kind": f"synthetic-{kind}", "facing": mf}
            mob_truth.append(t)
            drawn.append((t, kind, mf))
    tag_box = None
    if player is not None:
        _draw_character(img, player[0], player[1], facing)
        if with_name_tag:
            tag_box = _draw_name_tag(img, player[0], player[1], name)
    for dx, dy, val in damage:
        _draw_damage(img, dx, dy, val)
    # status bar + UI on top
    cv2.rectangle(img, (0, h - STATUS_BAR_H), (w, h), STATUS_BAR_BG, -1)
    cv2.line(img, (0, h - STATUS_BAR_H), (w, h - STATUS_BAR_H), (120, 118, 115), 2)
    hp_t = _draw_bar(img, hp_box, hp, HP_BGR, "HP") if hp_box is not None else None
    mp_t = _draw_bar(img, mp_box, mp, MP_BGR, "MP") if mp_box is not None else None
    pm_t = _draw_minimap(img, minimap_box, player_map, seed) if minimap_box is not None else None
    for t, kind, mf in drawn:
        t["visible"] = _visible_fraction(img, t["box"], kind, mf)
    in_frame = player is not None and 0 <= player[0] < w and 0 <= player[1] < h
    truth = {
        "map": "synthetic",
        "minimap_box": list(minimap_box) if minimap_box is not None else None,
        "player_map": pm_t,
        "player_screen": [int(player[0]), int(player[1])] if in_frame else None,
        "mobs": mob_truth,
        "hp": hp_t,
        "mp": mp_t,
        "flags": [],
        # extras (not in the label format; ignored by the eval)
        "hp_bar_box": list(hp_box) if hp_box is not None else None,
        "mp_bar_box": list(mp_box) if mp_box is not None else None,
        "name_tag_box": tag_box,
        "player_facing": facing,
        "size": [w, h],
        "seed": int(seed),
    }
    return img, truth


# ======================================================================================
# public: still scenes
# ======================================================================================
def make_scene(seed: int = 0, size: Tuple[int, int] = (1024, 768),
               player_x: Optional[int] = None, player_y: Optional[int] = None,
               mobs: Optional[Sequence[Tuple]] = None,
               minimap_box: Sequence[int] = (10, 10, 180, 100),
               player_map: Optional[Tuple[float, float]] = (0.5, 0.62),
               hp: Optional[float] = 0.8, mp: Optional[float] = 0.5,
               hp_box: Optional[Sequence[int]] = None, mp_box: Optional[Sequence[int]] = None,
               with_name_tag: bool = True, facing: str = "right", mob_kind: int = 0,
               name: str = DEFAULT_NAME, damage: Sequence[Tuple[int, int, int]] = (),
               ) -> Tuple[np.ndarray, dict]:
    """Render one frame. Returns (BGR uint8 image, truth dict in the label format).

    ``player_x/player_y`` -- body centre on screen (default: centre x, 62% down).
        ``player_x=None`` and ``player_y=-1`` hides the character entirely.
    ``mobs`` -- [(x, y, facing[, kind])] mob centres; default: two mobs on the platform.
        Truth lists mobs whose centre is inside the frame.
    ``player_map`` -- requested dot position (box fractions); ``None`` draws no dot.
        Truth holds the centre actually drawn (within half a minimap pixel of the request).
    ``hp``/``mp`` -- fill fractions; truth is the drawn fill (rounded to whole pixels).
    """
    w, h = int(size[0]), int(size[1])
    lay = default_layout((w, h))
    hidden = player_y == -1
    px = int(lay["player_x"] if player_x is None else player_x)
    py = int(lay["player_y"] if player_y is None or hidden else player_y)
    ground_y = py + CHAR_HALF_H
    hp_box = tuple(lay["hp_box"]) if hp_box is None else tuple(hp_box)
    mp_box = tuple(lay["mp_box"]) if mp_box is None else tuple(mp_box)
    mob_cy = ground_y - MOB_SIZE[1] // 2
    if mobs is None:
        mobs = [(px + 220, mob_cy, "left"), (px - 300, mob_cy, "right")]
    bg = _background(seed, w, h, ground_y)
    return _compose(bg, seed=seed, player=None if hidden else (px, py), facing=facing,
                    mobs=mobs, mob_kind=mob_kind, minimap_box=minimap_box,
                    player_map=player_map, hp=hp, mp=mp, hp_box=hp_box, mp_box=mp_box,
                    with_name_tag=with_name_tag and not hidden, name=name, damage=damage)


def random_scene(seed: int, size: Tuple[int, int] = (1024, 768)) -> Tuple[np.ndarray, dict]:
    """A varied scene for datasets/evals: 0-4 mobs both ways, some overlapping, some near
    the screen edge, random HP/MP/minimap position, occasionally no name tag or damage
    numbers. Deterministic in ``seed``."""
    w, h = size
    rng = np.random.default_rng(424242 + int(seed))
    lay = default_layout(size)
    px = int(rng.integers(int(w * 0.3), int(w * 0.7)))
    py = int(lay["player_y"])
    mob_cy = py + CHAR_HALF_H - MOB_SIZE[1] // 2
    n = int(rng.choice([0, 1, 2, 3, 4], p=[0.1, 0.25, 0.3, 0.2, 0.15]))
    mobs: List[Tuple[int, int, str]] = []
    half = MOB_SIZE[0] // 2
    for _ in range(n):
        x = None
        for _try in range(30):
            if rng.random() < 0.15:        # near the screen edge (fully visible)
                cand = half + 2 if rng.random() < 0.5 else w - half - 3
            else:
                cand = int(rng.integers(half + 2, w - half - 3))
            # random placements stay one mob-width apart; overlap only comes from the
            # explicit "overlapping neighbour" below, so difficulty stays controlled
            if all(abs(cand - m[0]) >= MOB_SIZE[0] + 6 for m in mobs):
                x = cand
                break
        if x is None:
            continue
        mobs.append((x, mob_cy, "left" if rng.random() < 0.5 else "right"))
        if rng.random() < 0.2 and len(mobs) < 5:   # an overlapping neighbour
            ox = x + int(rng.choice([-1, 1]) * rng.integers(30, 45))
            if half + 2 <= ox <= w - half - 3:
                mobs.append((ox, mob_cy, "left" if rng.random() < 0.5 else "right"))
    damage = []
    if mobs and rng.random() < 0.3:
        m = mobs[0]
        damage.append((m[0], m[1] - 40, int(rng.integers(10, 999))))
    return make_scene(
        seed=int(seed), size=size, player_x=px, player_y=py, mobs=mobs,
        player_map=(float(rng.uniform(0.08, 0.92)), float(rng.uniform(0.45, 0.8))),
        hp=round(float(rng.uniform(0.05, 1.0)), 3), mp=round(float(rng.uniform(0.0, 1.0)), 3),
        with_name_tag=bool(rng.random() < 0.9),
        facing="left" if rng.random() < 0.5 else "right", damage=damage)


def settings_for_scene(truth: dict, base: Optional[Settings] = None) -> Settings:
    """Settings whose calibration matches a synthetic scene (minimap, HP/MP boxes, patrol)."""
    s = Settings.from_dict(base.to_dict()) if base is not None else Settings()
    c = s.calibration
    c.map_reference = None  # Synthetic minimaps use viewport coordinates.
    if truth.get("minimap_box") is not None:
        c.minimap_box = [int(v) for v in truth["minimap_box"]]
    if truth.get("hp_bar_box") is not None:
        c.hp_bar_box = [int(v) for v in truth["hp_bar_box"]]
    if truth.get("mp_bar_box") is not None:
        c.mp_bar_box = [int(v) for v in truth["mp_bar_box"]]
    left, right = truth.get("patrol", (0.2, 0.8))
    c.patrol_left, c.patrol_right = float(left), float(right)
    return s


def write_templates(templates_dir, mob_kinds: Sequence[int] = (0,), name: str = DEFAULT_NAME,
                    player: bool = True) -> List:
    """Write ``mob_synthetic-<k>.png`` (+ ``player.png``) into a profile templates folder."""
    from pathlib import Path
    d = Path(templates_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for k in mob_kinds:
        p = d / f"mob_synthetic-{int(k)}.png"
        cv2.imwrite(str(p), mob_sprite(int(k)))
        out.append(p)
    if player:
        p = d / "player.png"
        cv2.imwrite(str(p), name_tag_sprite(name))
        out.append(p)
    return out


# ======================================================================================
# public: world sim
# ======================================================================================
@dataclass
class SimMob:
    id: int
    wx: float                     # world x of the centre
    facing: str = "left"
    hits: int = 0
    alive: bool = True
    respawn_at: float = 0.0
    vx: float = 0.0
    next_turn: float = 0.0
    kind: int = 0


@dataclass
class _Damage:
    wx: float
    until: float
    value: int


class SceneSequence:
    """A one-platform world that reacts to Actions like the game (roughly).

    * ``WALK_LEFT/RIGHT`` start walking (and face that way) until ``STOP_WALK``, a FACE_*,
      ``ATTACK`` or the other walk. Walls at the world edges and optional ``blockers``
      (world x positions, crossed only while jumping) stop movement -> a stuck test.
    * ``FACE_LEFT/RIGHT`` turn without walking. ``JUMP`` hops for ``jump_s``.
    * ``ATTACK`` casts (at most once per ``attack_cooldown_s``) and hits the nearest live
      mob in front within ``attack_range`` px; the ``hits_to_kill``-th hit kills it. Dead
      mobs respawn after ``respawn_s`` at a random x on the platform.
    * ``USE_HP_POT`` / ``USE_MP_POT`` add ``potion_amount``. Each cast costs ``mp_per_cast``.
    * Camera follows the player (player at screen centre) until the world edge.
    * The minimap dot follows player x: ``0.05 + 0.9 * world_x / world_width``.

    ``frame(t)`` advances the world to time ``t`` (non-decreasing) and renders it.
    """

    def __init__(self, seed: int = 0, size: Tuple[int, int] = (1024, 768),
                 world_width: int = 2400, n_mobs: int = 5, player_x: Optional[float] = None,
                 walk_speed: float = 125.0, attack_range: float = 350.0,
                 attack_cooldown_s: float = 0.4, hits_to_kill: int = 2, respawn_s: float = 5.0,
                 mob_speed: float = 0.0, hp: float = 0.9, mp: float = 0.8,
                 mp_per_cast: float = 0.0, potion_amount: float = 0.4,
                 blockers: Sequence[float] = (), jump_s: float = 0.6,
                 minimap_box: Sequence[int] = (10, 10, 180, 100),
                 hp_box: Optional[Sequence[int]] = None, mp_box: Optional[Sequence[int]] = None,
                 mob_kind: int = 0, name: str = DEFAULT_NAME, start_t: float = 0.0,
                 mob_xs: Optional[Sequence[float]] = None):
        self.seed = int(seed)
        self.size = (int(size[0]), int(size[1]))
        w, h = self.size
        self.world_width = max(int(world_width), w)
        lay = default_layout(self.size)
        self.player_y = int(lay["player_y"])
        self.ground_y = int(lay["ground_y"])
        self.minimap_box = tuple(minimap_box)
        self.hp_box = tuple(hp_box) if hp_box is not None else tuple(lay["hp_box"])
        self.mp_box = tuple(mp_box) if mp_box is not None else tuple(lay["mp_box"])
        self.walk_speed = float(walk_speed)
        self.attack_range = float(attack_range)
        self.attack_cooldown_s = float(attack_cooldown_s)
        self.hits_to_kill = int(hits_to_kill)
        self.respawn_s = float(respawn_s)
        self.mob_speed = float(mob_speed)
        self.mp_per_cast = float(mp_per_cast)
        self.potion_amount = float(potion_amount)
        self.blockers = sorted(float(b) for b in blockers)
        self.jump_s = float(jump_s)
        self.mob_kind = int(mob_kind)
        self.name = name
        self.hp, self.mp = float(hp), float(mp)
        self._rng = np.random.default_rng(99991 + self.seed)
        self._bg = _background(self.seed, self.world_width, h, self.ground_y)

        self.t = float(start_t)
        self.player_wx = float(self.world_width / 2 if player_x is None else player_x)
        self.facing = "right"
        self.walking: Optional[str] = None
        self.jump_until = -math.inf
        self._last_cast = -math.inf
        self.casts = 0
        self.hits = 0
        self.kills = 0
        self.actions: List[Tuple[float, Action]] = []
        self._damage: List[_Damage] = []
        self.mobs: List[SimMob] = []
        xs = list(mob_xs) if mob_xs is not None else [self._spawn_x() for _ in range(int(n_mobs))]
        for i, x in enumerate(xs):
            self.mobs.append(SimMob(id=i, wx=float(x), kind=self.mob_kind,
                                    facing="left" if self._rng.random() < 0.5 else "right",
                                    next_turn=self.t + float(self._rng.uniform(1.5, 4.0))))
        self.last_truth: Optional[dict] = None

    # ---- geometry ----------------------------------------------------------------
    @property
    def margin(self) -> float:
        return 30.0

    def map_x(self, wx: Optional[float] = None) -> float:
        wx = self.player_wx if wx is None else wx
        return MINIMAP_LINE_LEFT + (MINIMAP_LINE_RIGHT - MINIMAP_LINE_LEFT) * wx / self.world_width

    def world_x_for_map(self, fx: float) -> float:
        return (fx - MINIMAP_LINE_LEFT) / (MINIMAP_LINE_RIGHT - MINIMAP_LINE_LEFT) * self.world_width

    @property
    def camera_x(self) -> float:
        w = self.size[0]
        return float(min(max(self.player_wx - w / 2, 0.0), self.world_width - w))

    @property
    def jumping(self) -> bool:
        return self.t < self.jump_until

    @property
    def alive_mobs(self) -> List[SimMob]:
        return [m for m in self.mobs if m.alive]

    def _spawn_x(self) -> float:
        lo, hi = MOB_SIZE[0], self.world_width - MOB_SIZE[0]
        for _ in range(20):
            x = float(self._rng.uniform(lo, hi))
            if abs(x - getattr(self, "player_wx", -1e9)) > 120:
                return x
        return x

    # ---- actions -----------------------------------------------------------------
    def apply(self, action, t: Optional[float] = None) -> None:
        """Apply an ``Action`` at time ``t`` (default: the current sim time)."""
        if t is not None:
            self.advance(t)
        a = Action(action)
        self.actions.append((self.t, a))
        if a is Action.WALK_LEFT:
            self.walking = self.facing = "left"
        elif a is Action.WALK_RIGHT:
            self.walking = self.facing = "right"
        elif a is Action.STOP_WALK:
            self.walking = None
        elif a is Action.FACE_LEFT:
            self.walking, self.facing = None, "left"
        elif a is Action.FACE_RIGHT:
            self.walking, self.facing = None, "right"
        elif a is Action.JUMP:
            if not self.jumping:
                self.jump_until = self.t + self.jump_s
        elif a is Action.ATTACK:
            self.walking = None
            self._cast()
        elif a is Action.USE_HP_POT:
            self.hp = min(1.0, self.hp + self.potion_amount)
        elif a is Action.USE_MP_POT:
            self.mp = min(1.0, self.mp + self.potion_amount)

    def _cast(self) -> None:
        if self.t - self._last_cast < self.attack_cooldown_s:
            return
        self._last_cast = self.t
        self.casts += 1
        self.mp = max(0.0, self.mp - self.mp_per_cast)
        sign = 1.0 if self.facing == "right" else -1.0
        best = None
        for m in self.alive_mobs:
            dx = (m.wx - self.player_wx) * sign
            if -12.0 <= dx <= self.attack_range and (best is None or dx < best[0]):
                best = (dx, m)
        if best is None:
            return
        m = best[1]
        m.hits += 1
        self.hits += 1
        self._damage.append(_Damage(m.wx, self.t + 0.8, int(self._rng.integers(20, 300))))
        if m.hits >= self.hits_to_kill:
            m.alive = False
            m.respawn_at = self.t + self.respawn_s
            self.kills += 1

    # ---- time --------------------------------------------------------------------
    def advance(self, t: float) -> None:
        t = float(t)
        while self.t < t - 1e-9:
            dt = min(1.0 / 60.0, t - self.t)
            self._step(dt)
            self.t += dt
        self.t = max(self.t, t)

    def _step(self, dt: float) -> None:
        if self.walking is not None:
            v = self.walk_speed * (1 if self.walking == "right" else -1)
            nx = self.player_wx + v * dt
            if not self.jumping:
                for b in self.blockers:
                    if self.player_wx <= b < nx:
                        nx = min(nx, b - 0.01)
                    elif nx < b <= self.player_wx:
                        nx = max(nx, b + 0.01)
            self.player_wx = float(min(max(nx, self.margin), self.world_width - self.margin))
        now = self.t + dt
        for m in self.mobs:
            if not m.alive:
                if now >= m.respawn_at:
                    m.alive, m.hits = True, 0
                    m.wx = self._spawn_x()
                    m.facing = "left" if self._rng.random() < 0.5 else "right"
                continue
            if self.mob_speed > 0:
                if now >= m.next_turn:
                    m.facing = "left" if self._rng.random() < 0.5 else "right"
                    m.next_turn = now + float(self._rng.uniform(1.5, 4.0))
                d = 1 if m.facing == "right" else -1
                m.wx += d * self.mob_speed * dt
                lo, hi = MOB_SIZE[0], self.world_width - MOB_SIZE[0]
                if not lo <= m.wx <= hi:
                    m.wx = min(max(m.wx, lo), hi)
                    m.facing = "left" if d > 0 else "right"
        self._damage = [d for d in self._damage if d.until > now]

    # ---- rendering ---------------------------------------------------------------
    def render(self) -> Tuple[np.ndarray, dict]:
        w, h = self.size
        cam = int(round(self.camera_x))
        bg = self._bg[:, cam:cam + w].copy()
        py = self.player_y
        if self.jumping:
            phase = 1.0 - (self.jump_until - self.t) / self.jump_s
            py -= int(round(60 * math.sin(math.pi * min(max(phase, 0.0), 1.0))))
        mob_cy = self.ground_y - MOB_SIZE[1] // 2
        mobs = [(int(round(m.wx - cam)), mob_cy, m.facing, m.kind) for m in self.alive_mobs]
        dmg = [(int(round(d.wx - cam)), mob_cy - 40, d.value) for d in self._damage]
        img, truth = _compose(
            bg, seed=self.seed, player=(int(round(self.player_wx - cam)), py), facing=self.facing,
            mobs=mobs, mob_kind=self.mob_kind, minimap_box=self.minimap_box,
            player_map=(self.map_x(), 0.62), hp=self.hp, mp=self.mp,
            hp_box=self.hp_box, mp_box=self.mp_box, with_name_tag=True, name=self.name,
            damage=dmg)
        truth.update({"t": round(self.t, 4), "player_world_x": round(self.player_wx, 2),
                      "walking": self.walking, "jumping": self.jumping,
                      "kills": self.kills, "casts": self.casts, "hits": self.hits})
        self.last_truth = truth
        return img, truth

    def frame(self, t: Optional[float] = None) -> Frame:
        if t is not None:
            self.advance(t)
        img, _ = self.render()
        return Frame(image=img, t=float(self.t), window=(0, 0, self.size[0], self.size[1]))

    def truth(self) -> dict:
        """Label dict of the current state (renders if nothing has been rendered yet)."""
        if self.last_truth is None or self.last_truth.get("t") != round(self.t, 4):
            self.render()
        return self.last_truth  # type: ignore[return-value]

    def settings(self, base: Optional[Settings] = None) -> Settings:
        """Settings calibrated for this world (minimap, bars, patrol 0.2..0.8)."""
        return settings_for_scene({"minimap_box": list(self.minimap_box),
                                   "hp_bar_box": list(self.hp_box),
                                   "mp_bar_box": list(self.mp_box)}, base)
