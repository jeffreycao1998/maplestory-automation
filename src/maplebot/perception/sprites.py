"""Alpha-masked reference sprites; transparent pixels never become black scenery."""
from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np

from maplebot.types import Mob


class SpriteMatcher:
    def __init__(self):
        self.templates = []
        self._cursor = 0

    def add(self, path: Path) -> bool:
        """True for an RGBA sprite (including a rejected/empty RGBA file)."""
        try:
            image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        except (OSError, ValueError):
            return False
        if image is None or image.ndim != 3 or image.shape[2] != 4 or np.all(image[:, :, 3] == 255):
            return False
        mask = (image[:, :, 3] >= 240).astype(np.uint8) * 255
        points = cv2.findNonZero(mask)
        if points is None or np.count_nonzero(mask) < 20:
            return True
        x, y, w, h = cv2.boundingRect(points)
        bgr, mask = image[y:y+h, x:x+w, :3].copy(), mask[y:y+h, x:x+w].copy()
        if min(w, h) >= 4 and float(bgr[mask > 0].std()) > 5:
            self.templates.append((path.name, bgr, mask))
        return True

    def find(self, image, threshold, exclude=(), mirror=True, region=None, budget=None):
        ox, oy, rw, rh = region if region is not None else (0, 0, image.shape[1], image.shape[0])
        roi = image[oy:oy+rh, ox:ox+rw]
        if roi.size == 0:
            return []
        detections = []
        small = cv2.resize(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), None, fx=0.5, fy=0.5,
                           interpolation=cv2.INTER_AREA)

        templates = self.templates
        if templates and budget is not None:
            n = min(len(templates), max(1, int(budget)))
            templates = [self.templates[(self._cursor+i) % len(self.templates)] for i in range(n)]
            self._cursor = (self._cursor+n) % len(self.templates)
        for name, bgr, mask in templates:
            h, w = bgr.shape[:2]
            if h > roi.shape[0] or w > roi.shape[1]:
                continue
            for flip in range(2 if mirror else 1):
                template = cv2.flip(bgr, 1) if flip else bgr
                alpha = cv2.flip(mask, 1) if flip else mask
                # Cheap proposals ignore transparency; only a few full-size colour checks
                # use the alpha mask. Full-screen masked FFTs are too slow for a control loop.
                gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
                coarse = cv2.resize(gray, (max(2, w//2), max(2, h//2)), interpolation=cv2.INTER_AREA)
                if min(small.shape) < 2 or coarse.shape[0] > small.shape[0] or coarse.shape[1] > small.shape[1]:
                    continue
                scores = cv2.matchTemplate(small, coarse, cv2.TM_CCOEFF_NORMED)
                np.nan_to_num(scores, copy=False, nan=-1, posinf=-1, neginf=-1)
                for _ in range(12):
                    _, proposal, _, (sx, sy) = cv2.minMaxLoc(scores)
                    if proposal < 0.30:
                        break
                    cv2.rectangle(scores, (sx-w//4, sy-h//4), (sx+w//4, sy+h//4), -1, -1)
                    gx, gy = int(sx*roi.shape[1]/small.shape[1]), int(sy*roi.shape[0]/small.shape[0])
                    x0, y0 = max(0, gx-4), max(0, gy-4)
                    x1, y1 = min(roi.shape[1], gx+w+4), min(roi.shape[0], gy+h+4)
                    patch = roi[y0:y1, x0:x1]
                    if patch.shape[0] < h or patch.shape[1] < w:
                        continue
                    fine = cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED, mask=alpha)
                    np.nan_to_num(fine, copy=False, nan=-1, posinf=-1, neginf=-1)
                    _, score, _, (dx, dy) = cv2.minMaxLoc(fine)
                    if score < threshold:
                        continue
                    cx, cy = ox+x0+dx+w//2, oy+y0+dy+h//2
                    if not any(bx <= cx < bx+bw and by <= cy < by+bh for bx, by, bw, bh in exclude):
                        detections.append(Mob(cx, cy, w, h, float(min(1, score)), name))
        kept = []
        for m in sorted(detections, key=lambda m: -m.score):
            if not any(abs(m.x-n.x) < (m.w+n.w)/3 and abs(m.y-n.y) < (m.h+n.h)/3 for n in kept):
                kept.append(m)
            if len(kept) >= 40:
                break
        return kept
