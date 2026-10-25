"""Local, opt-in OCR with bounded background work and fresh confirmed readings."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
import math
import re

import cv2
import numpy as np
from .calib import box_inside

log = logging.getLogger(__name__)
REGIONS = {"hp_stock":"hp_stock_box", "mp_stock":"mp_stock_box", "mesos":"mesos_box",
           "notification":"notification_box"}
FULL_PHRASES = ("inventory is full", "inventory full", "not enough space in your inventory",
                "not enough room in your inventory", "please check if your inventory is full")


@dataclass(frozen=True)
class TextLine:
    text: str
    confidence: float


def parse_count(text, maximum=2_000_000_000):
    # Never turn arbitrary letters into digits or accept a partial numeric substring.
    value = str(text).strip()
    if not re.fullmatch(r"(?:\d{1,10}|\d{1,3}(?:,\d{3})+)", value, flags=re.ASCII):
        return None
    result = int(value.replace(",", ""))
    return result if 0 <= result <= maximum else None


class LocalOCR:
    def __init__(self):
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        from rapidocr_onnxruntime import RapidOCR
        self.engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)

    def read(self, crop, multiline=False):
        if crop.size == 0:
            return []
        scale = max(1.0, min(4.0, 48/crop.shape[0]))
        image = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        image = cv2.copyMakeBorder(image, 6,6,6,6,cv2.BORDER_REPLICATE)
        result, _ = self.engine(image, use_det=multiline, use_cls=False)
        return [TextLine(str(row[-2]), float(row[-1])) for row in result or []]


class HUDFilter:
    """Distinct OCR jobs, not repeated game ticks, count as confirmation samples."""
    def __init__(self, settings):
        self.settings = settings
        self.pending = {}
        self.trusted = {}
        self.last_t = -math.inf

    def accept(self, result, t):
        if not math.isfinite(t) or t <= self.last_t:
            return
        self.last_t = t
        for name in REGIONS:
            lines = result.get(name, [])
            lines = [line for line in lines if math.isfinite(line.confidence) and
                     line.confidence >= self.settings.ocr.min_confidence]
            if name == "notification":
                text = " ".join(line.text.lower() for line in lines)
                full = any(phrase in text for phrase in FULL_PHRASES)
                if full:
                    self.trusted["inventory_full"] = (True, t)
                continue
            value = parse_count(lines[0].text) if len(lines)==1 else None
            if value is None:
                self.pending.pop(name, None)
                self.trusted.pop(name, None)
                continue
            previous, n = self.pending.get(name, (None,0))
            n = n+1 if previous == value else 1
            self.pending[name] = value,n
            # Changed readings immediately invalidate the old value while confirmation runs.
            self.trusted.pop(name, None)
            if n >= self.settings.ocr.confirm_reads:
                self.trusted[name] = value,t

    def snapshot(self, now):
        result = {key:value for key,(value,t) in self.trusted.items()
                  if 0 <= now-t <= self.settings.ocr.max_age_s}
        timestamps = [t for _,t in self.trusted.values() if 0 <= now-t <= self.settings.ocr.max_age_s]
        result["hud_t"] = max(timestamps) if timestamps else None
        return result


class HUDReader:
    def __init__(self, settings, backend=None, synchronous=False):
        self.settings, self.backend = settings, backend
        self.synchronous = synchronous
        self.filter = HUDFilter(settings)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hud-ocr")
        self.future = None
        self.last_submit = -math.inf
        self.closed = False
        self.error = ""

    def _read(self, crops, t):
        if self.backend is None:
            self.backend = LocalOCR()
        return {name:self.backend.read(crop, multiline=name=="notification") for name,crop in crops.items()},t

    def poll(self, image, now):
        if self.closed:
            return {}
        if self.future is not None and self.future.done():
            try:
                result,t = self.future.result()
                self.filter.accept(result,t)
                self.error = ""
            except Exception as exc:
                self.error = f"OCR unavailable: {type(exc).__name__}"
                log.warning(self.error, exc_info=True)
            self.future = None
        if self.future is None and now-self.last_submit >= self.settings.ocr.interval_s:
            crops = {}
            for name,field in REGIONS.items():
                box = box_inside(image, getattr(self.settings.calibration,field))
                if box is not None:
                    x,y,w,h = box
                    crops[name] = image[y:y+h,x:x+w].copy()
            self.last_submit = now
            if crops:
                if self.synchronous:
                    try:
                        result,t = self._read(crops,now)
                        self.filter.accept(result,t)
                    except Exception as exc:
                        self.error = f"OCR unavailable: {type(exc).__name__}"
                else:
                    self.future = self.executor.submit(self._read,crops,now)
        return self.filter.snapshot(now)

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=False,cancel_futures=True)
