"""Conservative Classic UI proposals, used only in the setup wizard.

A coloured fill alone cannot reveal a bar's full capacity. Require a closed neutral
frame and an aligned red/blue pair; return no proposal when evidence is ambiguous.
"""
from __future__ import annotations

import cv2
import numpy as np

from .calib import as_bgr, minimap_player_position


def suggest_regions(image):
    img = as_bgr(image)
    if img is None:
        return {}
    height, width = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    neutral = cv2.inRange(hsv, (0, 0, 170), (179, 80, 255))
    contours, _ = cv2.findContours(neutral, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = set()
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if cv2.contourArea(contour) / max(1, w * h) > 0.90:
            boxes.add((x, y, w, h))
    out = {}
    maps = []
    for x, y, w, h in boxes:
        if (x < width * 0.2 and y < height * 0.3 and 80 <= w < width * 0.4 and
                60 <= h < height * 0.5 and 0.5 < w / h < 3):
            inner = (x + 3, y + 5, w - 6, h - 11)
            if minimap_player_position(img, inner, [20, 150, 180], [35, 255, 255]) is not None:
                maps.append(inner)
    # Nested outlines can describe the same map: choose the smallest enclosed area.
    if maps:
        maps.sort(key=lambda b: b[2] * b[3])
        chosen = maps[0]
        if all(abs(b[0] - chosen[0]) < 10 and abs(b[1] - chosen[1]) < 10 for b in maps):
            out["minimap_box"] = list(chosen)
    bars = {"hp": [], "mp": []}
    for x, y, w, h in boxes:
        if y < height * 0.8 or not (50 <= w <= width * 0.3 and 6 <= h <= 26 and w / h > 4):
            continue
        box = (x + 2, y + 1, w - 4, h - 2)
        roi = hsv[y + 1:y + h - 1, x + 2:x + w - 2]
        saturated = (roi[:, :, 1] > 100) & (roi[:, :, 2] > 100)
        hue = roi[:, :, 0]
        red = np.count_nonzero(saturated & ((hue < 12) | (hue > 168)))
        blue = np.count_nonzero(saturated & (hue > 90) & (hue < 130))
        if red >= 4 and red > blue * 2:
            bars["hp"].append(box)
        if blue >= 4 and blue > red * 2:
            bars["mp"].append(box)
    pairs = [(hp, mp) for hp in bars["hp"] for mp in bars["mp"]
             if hp[0] < mp[0] and abs(hp[1] - mp[1]) <= 2 and abs(hp[2] - mp[2]) <= 5
             and abs(hp[3] - mp[3]) <= 2 and 0 < mp[0] - (hp[0] + hp[2]) < 30]
    if len(pairs) == 1:
        out["hp_bar_box"], out["mp_bar_box"] = map(list, pairs[0])
    return out
