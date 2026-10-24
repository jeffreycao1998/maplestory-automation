"""Synthetic game-like frames for perception tests (no game needed)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np

W, H = 1366, 768
MINIMAP_BOX = [10, 30, 200, 120]
HP_BOX = [500, 740, 160, 14]
MP_BOX = [680, 740, 160, 14]
DOT_BGR = (0, 230, 255)   # yellow


def background(seed: int = 0, w: int = W, h: int = H) -> np.ndarray:
    """Smooth sky-to-ground gradient with mild noise and a few platforms."""
    rng = np.random.default_rng(seed)
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    b = 200 - 80 * yy + 10 * np.sin(xx * 6)
    g = 170 - 60 * yy + 10 * np.cos(xx * 4)
    r = 120 - 40 * yy
    img = np.stack([np.broadcast_to(c, (h, w)) for c in (b, g, r)], axis=-1)
    img = img + rng.normal(0, 3, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    for py in (300, 520, 690):
        cv2.rectangle(img, (0, py), (w, py + 12), (60, 110, 140), -1)
    return img


def mob_sprite(seed: int, w: int = 48, h: int = 40) -> np.ndarray:
    """Textured, left-right asymmetric blob on its own backdrop (a crop-like template)."""
    rng = np.random.default_rng(1000 + seed)
    spr = np.zeros((h, w, 3), np.uint8)
    spr[:] = rng.integers(40, 90, 3)
    base = tuple(int(v) for v in rng.integers(60, 255, 3))
    cv2.ellipse(spr, (w // 2, h // 2 + 3), (max(1, w // 2 - 4), max(1, h // 2 - 6)), 0, 0, 360, base, -1)
    # strongly one-sided shading so the mirror image is a different picture
    ramp = np.linspace(-50, 50, w, dtype=np.float32)[None, :, None]
    spr = np.clip(spr.astype(np.float32) + ramp, 0, 255).astype(np.uint8)
    # asymmetric features: eye on one side, stripe on the other
    cv2.circle(spr, (w // 4, h // 3), 4, (255, 255, 255), -1)
    cv2.circle(spr, (w // 4, h // 3), 2, (0, 0, 0), -1)
    cv2.line(spr, (w - 12, 6), (w - 4, h - 6), (20, 20, 200), 3)
    blobs = rng.integers(0, 255, (h // 4, w // 4, 3)).astype(np.uint8)
    spr = cv2.addWeighted(spr, 0.75, cv2.resize(blobs, (w, h), interpolation=cv2.INTER_CUBIC), 0.25, 0)
    return spr


def name_tag(text: str = "JeffMage") -> np.ndarray:
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    tag = np.full((th + 8, tw + 10, 3), 25, np.uint8)
    cv2.putText(tag, text, (5, th + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return tag


def paste(img: np.ndarray, spr: np.ndarray, cx: int, cy: int) -> Tuple[int, int, int, int]:
    h, w = spr.shape[:2]
    x0, y0 = cx - w // 2, cy - h // 2
    img[y0:y0 + h, x0:x0 + w] = spr
    return (x0, y0, w, h)


def draw_minimap(img: np.ndarray, box=MINIMAP_BOX, dot=None, dot_r: int = 2) -> None:
    """dot: (fx, fy) fractions of the box, or None for no dot."""
    x, y, w, h = box
    img[y:y + h, x:x + w] = (40, 30, 30)
    cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), (200, 200, 200), 1)
    for k in range(4):   # platform lines on the minimap
        yy = y + 20 + k * 25
        cv2.line(img, (x + 10, yy), (x + w - 10, yy), (120, 120, 120), 1)
    if dot is not None:
        cx = x + dot[0] * w
        cy = y + dot[1] * h
        # draw at sub-pixel accuracy: fill pixels whose centre is within dot_r of (cx, cy)
        ys, xs = np.mgrid[y:y + h, x:x + w]
        m = ((xs + 0.5 - cx) ** 2 + (ys + 0.5 - cy) ** 2) <= dot_r ** 2 + 0.5
        img[y:y + h, x:x + w][m] = DOT_BGR


def draw_bar(img: np.ndarray, box, fill: float, colour) -> None:
    x, y, w, h = box
    img[y:y + h, x:x + w] = (55, 55, 55)                  # empty: dark grey
    fw = int(round(w * fill))
    if fw > 0:
        img[y + 1:y + h - 1, x:x + fw] = colour
        img[y + 1:y + 3, x:x + fw] = np.clip(np.array(colour) + 60, 0, 255)  # highlight
    cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), (200, 200, 200), 1)
    cv2.putText(img, "123/456", (x + w // 2 - 25, y + h - 3), cv2.FONT_HERSHEY_PLAIN, 0.8,
                (255, 255, 255), 1)


def write_png(path: Path, img: np.ndarray) -> Path:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    path.write_bytes(buf.tobytes())
    return path
