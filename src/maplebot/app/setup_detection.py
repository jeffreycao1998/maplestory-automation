"""Automatic wizard calibration, independent of Tk and the optional OCR runtime."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2

from maplebot.perception.autosetup import suggest_regions
from maplebot.perception.calib import as_bgr, bar_fill, box_inside, hsv_mask, hsv_range_from_sample

PIXEL_REGIONS = ("minimap_box", "hp_bar_box", "mp_bar_box", "hp_stock_box",
                 "mp_stock_box", "mesos_box", "notification_box")


def unique_player_dot(image, box, low, high):
    """Abstain if multiple yellow blobs could be the player; never choose by size."""
    image = as_bgr(image)
    if image is None:
        return None
    box = box_inside(image, box)
    if box is None:
        return None
    x, y, w, h = box
    hsv = cv2.cvtColor(image[y:y+h, x:x+w], cv2.COLOR_BGR2HSV)
    low, high = list(low), list(high)
    if low[0] == 20 and high[0] == 35:
        low[0] = 24             # orange party-dot rims overlap the broad default yellow range
    n, _, stats, centres = cv2.connectedComponentsWithStats(hsv_mask(hsv, low, high), connectivity=8)
    candidates = [i for i in range(1, n) if 2 <= stats[i, cv2.CC_STAT_AREA] <= w*h*.05]
    if len(candidates) != 1:
        return None
    cx, cy = centres[candidates[0]]
    return (float((cx+.5)/w), float((cy+.5)/h))


@dataclass
class SetupDetection:
    applied: dict = field(default_factory=dict)
    owned: dict = field(default_factory=dict)
    resized: bool = False
    hp: float | None = None
    mp: float | None = None


def detect_screen(ctx, image, owned=None):
    """Fill missing/previously automatic regions; preserve same-size manual edits.

    A changed client size invalidates pixel coordinates before any new proposals are
    applied. Only this wizard's in-memory copy changes; Save publishes the settings.
    """
    image = as_bgr(image)
    if image is None:
        raise ValueError("No usable screenshot.")
    h, w = image.shape[:2]
    c = ctx.settings.calibration
    previous = c.reference_size or ctx.screenshot_size
    result = SetupDetection(owned={k: list(v) for k, v in (owned or {}).items()})
    result.resized = bool(previous and list(previous) != [w, h])
    if result.resized:
        for name in PIXEL_REGIONS:
            setattr(c, name, None)
        result.owned.clear()
        ctx.patrol_preset = False
        ctx.patrol_left_set = ctx.patrol_right_set = None
        ctx.patrol_left_y = ctx.patrol_right_y = None
    proposals = suggest_regions(image)
    for name in ("minimap_box", "hp_bar_box", "mp_bar_box"):
        current = getattr(c, name)
        automatic = name in result.owned and current == result.owned[name]
        if automatic and name not in proposals:
            setattr(c, name, None)          # stale automatic boxes must not survive a lost HUD
            result.owned.pop(name, None)
        elif name in proposals and (not current or automatic):
            value = list(proposals[name])
            setattr(c, name, value)
            result.applied[name] = value
            result.owned[name] = value.copy()
        elif current and not automatic:
            result.owned.pop(name, None)
    ctx.screenshot_size = (w, h)
    c.reference_size = [w, h]
    ctx.dot_pos = unique_player_dot(image, c.minimap_box, c.player_dot_hsv_low, c.player_dot_hsv_high)
    if ctx.dot_pos is not None:
        x, y, bw, bh = c.minimap_box
        px, py = int(x + ctx.dot_pos[0]*bw), int(y + ctx.dot_pos[1]*bh)
        low, high = hsv_range_from_sample(image, px, py)
        sampled = unique_player_dot(image, c.minimap_box, low, high)
        if sampled is not None:
            c.player_dot_hsv_low, c.player_dot_hsv_high = low, high
            ctx.dot_pos = sampled
    result.hp = bar_fill(image, c.hp_bar_box)
    result.mp = bar_fill(image, c.mp_bar_box)
    return result
