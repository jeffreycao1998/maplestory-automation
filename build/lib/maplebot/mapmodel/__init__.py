"""
Map model: where the character may walk, in minimap x fractions (0 = left, 1 = right).

Owner: map-modeler.

v1 has a single platform built from the patrol bounds the user sets in the setup
wizard (`settings.calibration.patrol_left/right`). The class keeps a list of platforms
so per-map configs (`configs/maps/<map>.yaml`) with several platforms and ropes can be
added later without changing callers.

Pure data + arithmetic: no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from maplebot.settings import Settings

__all__ = ["MapModel", "Platform"]

LEFT_OUT = "left_out"
INSIDE = "inside"
RIGHT_OUT = "right_out"


@dataclass(frozen=True)
class Platform:
    """A walkable horizontal span, in minimap x fractions."""
    left: float
    right: float
    y: Optional[float] = None      # minimap y fraction; None = unknown (v1)
    name: str = "main"

    def contains(self, x: float) -> bool:
        return self.left <= x <= self.right


def _check_bounds(left: object, right: object, what: str) -> Tuple[float, float]:
    try:
        lo, hi = float(left), float(right)          # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(
            f"{what} must be numbers between 0 and 1 (got left={left!r}, right={right!r}). "
            "Re-run the patrol step in setup.") from None
    if math.isnan(lo) or math.isnan(hi) or not (0.0 <= lo < hi <= 1.0):
        raise ValueError(
            f"{what} are invalid: the left edge ({lo:.2f}) must be smaller than the right "
            f"edge ({hi:.2f}), and both must be between 0 and 1. "
            "Re-run the patrol step in setup.")
    return lo, hi


class MapModel:
    """Patrol zone and platforms of the current map."""

    def __init__(self, platforms: Sequence[Platform], patrol_left: float, patrol_right: float):
        self.patrol_left, self.patrol_right = _check_bounds(patrol_left, patrol_right,
                                                            "Patrol bounds")
        if not platforms:
            raise ValueError("A map needs at least one platform.")
        for pl in platforms:
            _check_bounds(pl.left, pl.right, f"Platform '{pl.name}' edges")
        self.platforms: Tuple[Platform, ...] = tuple(platforms)

    @classmethod
    def from_settings(cls, settings: Settings) -> "MapModel":
        """v1: one platform spanning the patrol bounds from calibration."""
        c = settings.calibration
        left, right = _check_bounds(c.patrol_left, c.patrol_right, "Patrol bounds")
        return cls([Platform(left, right)], left, right)

    def side_of_patrol(self, x: float) -> str:
        """'left_out' if x is left of the patrol zone, 'right_out' if right of it,
        else 'inside' (the edges themselves count as inside)."""
        if x < self.patrol_left:
            return LEFT_OUT
        if x > self.patrol_right:
            return RIGHT_OUT
        return INSIDE

    def platform_at(self, x: float) -> Optional[Platform]:
        """The platform whose x span contains x (v1: ignores y), or None."""
        for pl in self.platforms:
            if pl.contains(x):
                return pl
        return None

    def __repr__(self) -> str:
        return (f"MapModel(patrol={self.patrol_left:.3f}..{self.patrol_right:.3f}, "
                f"platforms={len(self.platforms)})")
