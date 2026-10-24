"""
Pixel helpers shared by the Perceiver and the setup wizard.

Everything here is pure numpy/OpenCV on a BGR uint8 image, never raises on odd
input (returns None instead), and runs on any OS.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

from maplebot.types import Box, PointF

log = logging.getLogger(__name__)

# --- minimap dot -----------------------------------------------------------------
MIN_DOT_AREA_PX = 2            # blobs smaller than this are noise (single stray pixels)
MAX_DOT_AREA_FRACTION = 0.05   # blobs bigger than 5% of the minimap are not the dot

# --- hsv_range_from_sample ---------------------------------------------------------
SAMPLE_RADIUS_PX = 2           # 5x5 patch around the click
HUE_TOLERANCE = 8              # +- on OpenCV's 0..179 hue scale
SV_LOW_FACTOR = 0.6            # lower S/V bound = 60% of the darkest/least saturated dot pixel

# --- bar_fill ------------------------------------------------------------------------
BAR_FILLED_SV_THRESHOLD = 0.25  # column mean of (S/255)*(V/255) above this = filled


def as_bgr(image) -> Optional[np.ndarray]:
    """Return `image` as a contiguous BGR uint8 array, or None if it can't be one."""
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        return None
    img = image
    if img.dtype != np.uint8:
        if np.issubdtype(img.dtype, np.floating):
            img = np.nan_to_num(img)
            if img.max(initial=0) <= 1.0:
                img = img * 255.0
        img = np.clip(img, 0, 255).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.ndim == 3 and img.shape[2] == 1:
        img = cv2.cvtColor(img[:, :, 0], cv2.COLOR_GRAY2BGR)
    elif img.ndim == 3 and img.shape[2] == 4:
        img = img[:, :, :3]
    elif not (img.ndim == 3 and img.shape[2] == 3):
        return None
    return np.ascontiguousarray(img)


def box_inside(image: np.ndarray, box) -> Optional[Box]:
    """`box` as an int (x, y, w, h) if it is non-empty and fully inside `image`, else None."""
    try:
        if box is None or len(box) != 4:
            return None
        x, y, w, h = (int(round(float(v))) for v in box)
    except (TypeError, ValueError):
        return None
    ih, iw = image.shape[:2]
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > iw or y + h > ih:
        return None
    return (x, y, w, h)


def hsv_mask(hsv: np.ndarray, low: Sequence[int], high: Sequence[int]) -> np.ndarray:
    """cv2.inRange that also understands a wrapped hue range (low hue > high hue, e.g. red)."""
    lo = np.array([int(v) for v in low[:3]], dtype=np.uint8)
    hi = np.array([int(v) for v in high[:3]], dtype=np.uint8)
    if lo[0] <= hi[0]:
        return cv2.inRange(hsv, lo, hi)
    a = cv2.inRange(hsv, lo, np.array([179, hi[1], hi[2]], dtype=np.uint8))
    b = cv2.inRange(hsv, np.array([0, lo[1], lo[2]], dtype=np.uint8), hi)
    return cv2.bitwise_or(a, b)


def minimap_player_position(image, minimap_box, hsv_low, hsv_high) -> Optional[PointF]:
    """Centre of the player dot as (x, y) fractions of the minimap box, or None.

    Masks `hsv_low..hsv_high` inside the box and takes the largest connected blob,
    ignoring blobs under MIN_DOT_AREA_PX and over MAX_DOT_AREA_FRACTION of the box.
    """
    try:
        img = as_bgr(image)
        if img is None:
            return None
        box = box_inside(img, minimap_box)
        if box is None:
            return None
        x, y, w, h = box
        hsv = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)
        mask = hsv_mask(hsv, hsv_low, hsv_high)
        if not mask.any():
            return None
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        max_area = MAX_DOT_AREA_FRACTION * w * h
        best, best_area = None, 0
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if MIN_DOT_AREA_PX <= area <= max_area and area > best_area:
                best, best_area = i, area
        if best is None:
            return None
        cx, cy = cents[best]
        # Pixel i covers [i, i+1): its centre is i + 0.5.
        return (float((cx + 0.5) / w), float((cy + 0.5) / h))
    except Exception:  # never let a bad frame crash the tick loop
        log.exception("minimap_player_position failed")
        return None


def hsv_range_from_sample(image, x: int, y: int) -> Tuple[list, list]:
    """Tolerant HSV range ([h,s,v] low, [h,s,v] high) for the dot the user clicked at (x, y).

    Looks at a 5x5 patch, takes its most saturated+bright pixel as the dot colour (so a
    click one pixel off the dot still works), keeps patch pixels of similar hue and
    brightness, and widens: hue +-HUE_TOLERANCE, S/V lower bounds at SV_LOW_FACTOR of
    the observed minimum, upper bounds 255. Values are clipped to OpenCV ranges
    (H 0..179, S/V 0..255). Falls back to the Calibration defaults on bad input.
    """
    default = ([20, 150, 180], [35, 255, 255])
    try:
        img = as_bgr(image)
        if img is None:
            return default
        ih, iw = img.shape[:2]
        x, y = int(x), int(y)
        if not (0 <= x < iw and 0 <= y < ih):
            return default
        r = SAMPLE_RADIUS_PX
        patch = img[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.int32)
        sv = hsv[:, 1] * hsv[:, 2]
        ref = hsv[int(np.argmax(sv))]
        dh = np.abs(hsv[:, 0] - ref[0])
        dh = np.minimum(dh, 180 - dh)                      # hue is circular
        keep = hsv[(dh <= 6) & (sv >= 0.5 * sv.max())]
        if len(keep) == 0:
            keep = ref[None, :]
        hues = keep[:, 0]
        # Hues near the wrap (red) would give min/max far apart; centre them on ref first.
        rel = ((hues - ref[0] + 90) % 180) - 90
        h_lo = int(np.clip(ref[0] + rel.min() - HUE_TOLERANCE, 0, 179))
        h_hi = int(np.clip(ref[0] + rel.max() + HUE_TOLERANCE, 0, 179))
        s_lo = int(np.clip(keep[:, 1].min() * SV_LOW_FACTOR, 0, 255))
        v_lo = int(np.clip(keep[:, 2].min() * SV_LOW_FACTOR, 0, 255))
        return [h_lo, s_lo, v_lo], [h_hi, 255, 255]
    except Exception:
        log.exception("hsv_range_from_sample failed")
        return default


def bar_fill(image, box) -> Optional[float]:
    """Fill fraction 0..1 of a horizontal HP/MP bar at `box`, or None if the box is invalid.

    Per column, the mean of (S/255)*(V/255) over the middle half of the rows; a column is
    filled if that exceeds BAR_FILLED_SV_THRESHOLD (red/blue fill is saturated and
    bright, the empty part is dark or grey, white text has no saturation).
    Result = (rightmost filled column + 1) / width, 0.0 if nothing is filled.
    """
    try:
        img = as_bgr(image)
        if img is None:
            return None
        b = box_inside(img, box)
        if b is None:
            return None
        x, y, w, h = b
        r0 = h // 4
        r1 = max(r0 + 1, h - h // 4)
        hsv = cv2.cvtColor(img[y + r0:y + r1, x:x + w], cv2.COLOR_BGR2HSV).astype(np.float32)
        score = (hsv[:, :, 1] * hsv[:, :, 2]).mean(axis=0) / (255.0 * 255.0)
        filled = np.flatnonzero(score > BAR_FILLED_SV_THRESHOLD)
        if filled.size == 0:
            return 0.0
        return float((filled[-1] + 1) / w)
    except Exception:
        log.exception("bar_fill failed")
        return None
