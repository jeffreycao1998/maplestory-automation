"""
Map model: where the character may walk, in minimap fractions (0 = left/top, 1 = right/bottom).

Owner: map-modeler.

v1 builds a single training platform from what the user sets in the setup wizard:
  * patrol bounds  `calibration.patrol_left/right`  (minimap x),
  * optional height band (minimap y, which grows DOWNWARD):
        patrol_y - patrol_y_tolerance_above  <=  y  <=  patrol_y + patrol_y_tolerance
    The band is asymmetric on purpose: the wizard takes patrol_y from the two edge
    readings, i.e. the bridge's anchored ends = the TOP of a sagging rope bridge. Walking
    to the middle moves the dot down by the sag (0.063-0.071 measured on The Tree That
    Grew II's minimap), and falling only ever goes down, to the next bridge ~0.117 below.
    So "below" must cover the sag but stop short of the next bridge; "above" only needs
    to cover dot jitter.

The platform's own x extent is not known in v1 (no per-map config yet), so it spans the
whole minimap width and only its height band identifies it; the patrol bounds sit inside
it. This matters on stacked maps (e.g. The Tree That Grew II), where several bridges
share the same x range and only y tells them apart. The class keeps a platform list so
per-map configs (`configs/maps/<map>.yaml`) can add several platforms and ropes later.

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


DEFAULT_Y_BELOW = 0.10     # used only if settings lack the field
DEFAULT_Y_ABOVE = 0.03


@dataclass(frozen=True)
class Platform:
    """A walkable span: x range, and optionally a minimap-y band
    [y - y_above, y + y_below] (minimap y grows downward)."""
    left: float
    right: float
    y: Optional[float] = None      # minimap y fraction; None = height unknown (any y matches)
    y_below: float = DEFAULT_Y_BELOW
    y_above: float = DEFAULT_Y_ABOVE
    name: str = "main"

    def contains_x(self, x: float) -> bool:
        return self.left <= x <= self.right

    def contains_y(self, y: Optional[float]) -> bool:
        if self.y is None:
            return True
        if y is None:
            return False
        return self.y - self.y_above <= y <= self.y + self.y_below

    def contains(self, x: float, y: Optional[float] = None) -> bool:
        return self.contains_x(x) and self.contains_y(y)


def _num(v: object, what: str) -> float:
    try:
        f = float(v)                                 # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number (got {v!r}). "
                         "Re-run the patrol step in setup.") from None
    if math.isnan(f):
        raise ValueError(f"{what} must be a number (got NaN). Re-run the patrol step in setup.")
    return f


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


def _check_y(y: object, below: object, above: object
             ) -> Tuple[Optional[float], float, float]:
    b = _num(below, "Platform height tolerance (below)")
    if not (0.0 < b <= 0.5):
        raise ValueError(
            f"Platform height tolerance (below) must be between 0 and 0.5 (got {b:.3f}).")
    a = _num(above, "Platform height tolerance (above)")
    if not (0.0 <= a <= 0.5):
        raise ValueError(
            f"Platform height tolerance (above) must be between 0 and 0.5 (got {a:.3f}).")
    if y is None:
        return None, b, a
    yf = _num(y, "Platform height")
    if not (0.0 <= yf <= 1.0):
        raise ValueError(f"Platform height must be between 0 and 1 (got {yf:.2f}). "
                         "Re-run the patrol step in setup.")
    return yf, b, a


class MapModel:
    """Patrol zone and platforms of the current map."""

    def __init__(self, platforms: Sequence[Platform], patrol_left: float, patrol_right: float,
                 patrol_y: Optional[float] = None,
                 patrol_y_tolerance: float = DEFAULT_Y_BELOW,
                 patrol_y_tolerance_above: float = DEFAULT_Y_ABOVE):
        self.patrol_left, self.patrol_right = _check_bounds(patrol_left, patrol_right,
                                                            "Patrol bounds")
        # patrol_y_tolerance = allowed distance BELOW patrol_y (as in settings)
        self.patrol_y, self.patrol_y_tolerance, self.patrol_y_tolerance_above = _check_y(
            patrol_y, patrol_y_tolerance, patrol_y_tolerance_above)
        if not platforms:
            raise ValueError("A map needs at least one platform.")
        for pl in platforms:
            _check_bounds(pl.left, pl.right, f"Platform '{pl.name}' edges")
            _check_y(pl.y, pl.y_below, pl.y_above)
        self.platforms: Tuple[Platform, ...] = tuple(platforms)
        # the platform the patrol zone is on (v1: the only one)
        self.training: Platform = self.platforms[0]

    @classmethod
    def from_settings(cls, settings: Settings) -> "MapModel":
        """v1: one training platform, full minimap width, at patrol_y (if set)."""
        c = settings.calibration
        left, right = _check_bounds(c.patrol_left, c.patrol_right, "Patrol bounds")
        y, below, above = _check_y(getattr(c, "patrol_y", None),
                                   getattr(c, "patrol_y_tolerance", DEFAULT_Y_BELOW),
                                   getattr(c, "patrol_y_tolerance_above", DEFAULT_Y_ABOVE))
        return cls([Platform(0.0, 1.0, y=y, y_below=below, y_above=above, name="training")],
                   left, right, y, below, above)

    @property
    def patrol_y_band(self) -> Optional[Tuple[float, float]]:
        """(top, bottom) minimap y accepted as on the training platform, or None."""
        if self.patrol_y is None:
            return None
        return (self.patrol_y - self.patrol_y_tolerance_above,
                self.patrol_y + self.patrol_y_tolerance)

    def side_of_patrol(self, x: float) -> str:
        """'left_out' if x is left of the patrol zone, 'right_out' if right of it,
        else 'inside' (the edges themselves count as inside)."""
        if x < self.patrol_left:
            return LEFT_OUT
        if x > self.patrol_right:
            return RIGHT_OUT
        return INSIDE

    def on_platform(self, x: float, y: Optional[float]) -> bool:
        """True if (x, y) is on the training platform. With no patrol_y set the height is
        unknown and any y counts (v1 behaviour before patrol_y existed); with patrol_y
        set, an unknown y (None) counts as off."""
        return self.training.contains(x, y)

    def platform_at(self, x: float, y: Optional[float] = None) -> Optional[Platform]:
        """The first platform containing (x, y), or None. y=None ignores heights."""
        for pl in self.platforms:
            if pl.contains_x(x) and (y is None or pl.contains_y(y)):
                return pl
        return None

    def __repr__(self) -> str:
        ys = ("" if self.patrol_y is None else
              f", y={self.patrol_y:.3f} -{self.patrol_y_tolerance_above:.3f}"
              f"/+{self.patrol_y_tolerance:.3f}")
        return (f"MapModel(patrol={self.patrol_left:.3f}..{self.patrol_right:.3f}{ys}, "
                f"platforms={len(self.platforms)})")
