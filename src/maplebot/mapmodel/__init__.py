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

    points: Tuple[Tuple[float, float], ...] = ()
    drop_through: bool = False

    def height_at(self, x: float) -> Optional[float]:
        if not self.points:
            return self.y
        for (ax, ay), (bx, by) in zip(self.points, self.points[1:]):
            if ax <= x <= bx:
                return ay + (by - ay) * (x - ax) / (bx - ax)
        return self.points[0][1] if x < self.left else self.points[-1][1]

    def contains_x(self, x: float) -> bool:
        return self.left <= x <= self.right

    def contains_y(self, y: Optional[float]) -> bool:
        if self.y is None:
            return True
        if y is None:
            return False
        return self.y - self.y_above <= y <= self.y + self.y_below

    def contains(self, x: float, y: Optional[float] = None) -> bool:
        height = self.height_at(x)
        return self.contains_x(x) and (height is None or (y is not None and
               height - self.y_above <= y <= height + self.y_below))


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


@dataclass(frozen=True)
class Ladder:
    name: str
    x: float
    top: float
    bottom: float


@dataclass(frozen=True)
class Connection:
    name: str
    source: str
    target: str
    kind: str
    launch_x: float
    land_x: float
    timeout_s: float = 1.5
    ladder: Optional[str] = None
    grab_jump: bool = False


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
        self.connections: Tuple[Connection, ...] = ()
        self.ladders: Tuple[Ladder, ...] = ()
        self.route: Tuple[str, ...] = ()
        self.explicit = False
        self.training: Platform = self.platforms[0]

    @classmethod
    def from_settings(cls, settings: Settings) -> "MapModel":
        """v1: one training platform, full minimap width, at patrol_y (if set)."""
        c = settings.calibration
        if not isinstance(c.movement_profile,dict):
            raise ValueError("Movement profile must be an object")
        left, right = _check_bounds(c.patrol_left, c.patrol_right, "Patrol bounds")
        y, below, above = _check_y(getattr(c, "patrol_y", None),
                                   getattr(c, "patrol_y_tolerance", DEFAULT_Y_BELOW),
                                   getattr(c, "patrol_y_tolerance_above", DEFAULT_Y_ABOVE))
        if not isinstance(c.platforms, list) or not isinstance(c.connections, list) or not isinstance(c.route, list):
            raise ValueError("Platforms, connections and route must be lists")
        if not isinstance(c.ladders,list):
            raise ValueError("Ladders must be a list")
        if not c.platforms and (c.connections or c.route or c.ladders):
            raise ValueError("Connections and route require platform geometry")
        if c.platforms:
            try:
                platforms = []
                for raw in c.platforms:
                    points = tuple((float(x), float(y)) for x, y in raw["points"])
                    if len(points) < 2 or any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in points):
                        raise ValueError("Each platform needs at least two points in 0..1")
                    if any(a[0] >= b[0] for a, b in zip(points, points[1:])):
                        raise ValueError("Platform points must go strictly left to right")
                    platforms.append(Platform(points[0][0], points[-1][0], points[0][1],
                        float(raw.get("tolerance", 0.015)), float(raw.get("tolerance", 0.015)),
                        str(raw["name"]), points, bool(raw.get("drop_through", False))))
                names = {pl.name: pl for pl in platforms}
                if len(names) != len(platforms) or any(not n for n in names):
                    raise ValueError("Platform names must be nonempty and unique")
                model = cls(platforms, left, right, y, below, above)
                ladders = tuple(Ladder(**raw) for raw in c.ladders)
                if len({l.name for l in ladders}) != len(ladders):
                    raise ValueError("Ladder names must be unique")
                for ladder in ladders:
                    if not ladder.name or not (0 <= ladder.x <= 1 and 0 <= ladder.top < ladder.bottom <= 1):
                        raise ValueError("Ladder needs a name, x in 0..1 and top < bottom in 0..1")
                model.ladders = ladders
                ladder_names = {l.name:l for l in ladders}
                edges = []
                for raw in c.connections:
                    e = Connection(**raw)
                    if e.source not in names or e.target not in names or e.source == e.target:
                        raise ValueError("Connection endpoints must name distinct platforms")
                    src, dst = names[e.source], names[e.target]
                    if e.kind not in ("walk", "jump", "drop", "climb") or not e.name:
                        raise ValueError("Connection needs a name and walk/jump/drop kind")
                    if not (src.contains_x(e.launch_x) and dst.contains_x(e.land_x)):
                        raise ValueError("Connection launch/landing must be on its platforms")
                    limit = 30.0 if e.kind == "climb" else 5.0
                    if not 0.25 <= e.timeout_s <= limit:
                        raise ValueError(f"Connection timeout must be 0.25..{limit:g} seconds")
                    if type(e.grab_jump) is not bool:
                        raise ValueError("grab_jump must be true or false")
                    if e.kind == "climb":
                        ladder = ladder_names.get(e.ladder)
                        if ladder is None or abs(e.launch_x-ladder.x) > .005 or abs(e.land_x-ladder.x) > .005:
                            raise ValueError("Climb endpoints must align with a named ladder")
                        start_y, end_y = src.height_at(e.launch_x), dst.height_at(e.land_x)
                        if (min(abs(start_y-ladder.top),abs(start_y-ladder.bottom)) > .04 or
                            min(abs(end_y-ladder.top),abs(end_y-ladder.bottom)) > .04 or
                            abs(start_y-end_y) < .04 or (e.grab_jump and end_y > start_y)):
                            raise ValueError("Climb must connect ladder endpoints; grab+jump is only for ascent")
                    if e.kind != "walk":
                        margin = settings.behavior.edge_margin
                        if not (src.left + margin <= e.launch_x <= src.right - margin and
                                dst.left + margin <= e.land_x <= dst.right - margin):
                            raise ValueError("Jump/drop launch and landing must reserve an edge margin")
                    if e.kind == "drop" and (not src.drop_through or
                        not dst.contains_x(e.launch_x) or abs(e.launch_x - e.land_x) > 0.001 or
                        dst.height_at(e.land_x) <= src.height_at(e.launch_x) + src.y_below + dst.y_above):
                        raise ValueError("Drop requires a drop-through source and a lower landing directly below")
                    if e.kind == "walk" and (max(src.left, dst.left) > min(src.right, dst.right) or
                        abs(e.launch_x - e.land_x) < 0.001 or
                        abs(e.launch_x - e.land_x) > 0.025 or
                        abs(src.height_at(e.launch_x) - dst.height_at(e.land_x)) > 0.025):
                        raise ValueError("Walk connections must join adjacent surfaces without a gap")
                    motion = c.movement_profile
                    if not isinstance(motion,dict):
                        raise ValueError("Movement profile must be an object")
                    if motion.get("verified") is True and e.kind=="jump":
                        for key in ("jump_rise","jump_horizontal"):
                            value = motion.get(key)
                            if value is not None and (not isinstance(value,(float,int)) or not math.isfinite(value) or value<0):
                                raise ValueError("Invalid calibrated movement limit")
                        rise = src.height_at(e.launch_x)-dst.height_at(e.land_x)
                        if motion.get("jump_rise") is not None and rise>motion["jump_rise"]:
                            raise ValueError("Jump exceeds verified rise calibration")
                        if motion.get("jump_horizontal") is not None and abs(e.land_x-e.launch_x)>motion["jump_horizontal"]:
                            raise ValueError("Jump exceeds verified horizontal calibration")
                    edges.append(e)
                if len({e.name for e in edges}) != len(edges):
                    raise ValueError("Connection names must be unique")
                if any(not isinstance(n, str) or n not in names for n in c.route):
                    raise ValueError("Route refers to an unknown platform")
                model.connections, model.route, model.explicit = tuple(edges), tuple(c.route), True
                if model.route:
                    for a, b in zip(model.route, model.route[1:] + model.route[:1]):
                        if a != b and not model.path(a, b):
                            raise ValueError(f"No configured route from {a} to {b}")
                return model
            except (KeyError, TypeError, OverflowError) as exc:
                raise ValueError(f"Invalid platform/connection configuration: {exc}") from exc
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
        return any(pl.contains(x, y) for pl in self.platforms) if self.explicit else self.training.contains(x, y)

    def platform_at(self, x: float, y: Optional[float] = None) -> Optional[Platform]:
        """The first platform containing (x, y), or None. y=None ignores heights."""
        for pl in self.platforms:
            if pl.contains_x(x) and (y is None or pl.contains(x, y)):
                return pl
        return None

    def safe_platform(self, point, margin=0.012) -> Platform:
        if (not isinstance(point, (list, tuple)) or len(point) != 2 or
                any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v)
                    or not 0 <= v <= 1 for v in point)):
            raise ValueError("Safe spot must be two minimap fractions in 0..1")
        if not self.explicit and self.patrol_y is None:
            raise ValueError("Set the training platform height before choosing a safe spot")
        matches = [pl for pl in self.platforms if pl.contains(*point)]
        if len(matches) != 1:
            raise ValueError("Choose a safe spot on exactly one known platform")
        pl = matches[0]
        left, right = (pl.left, pl.right) if self.explicit else (self.patrol_left, self.patrol_right)
        if not left + margin <= point[0] <= right - margin:
            raise ValueError("Choose a safe spot inside the platform's edge margin")
        return pl

    def path(self, source: str, target: str) -> Tuple[Connection, ...]:
        # Prefer the smallest travel budget, rather than the fewest transitions.
        # Ties use insertion order so route selection is reproducible.
        import heapq
        from itertools import count
        serial = count()
        queue = [(0., next(serial), source, ())]
        costs = {source: 0.}
        while queue:
            cost, _, node, path = heapq.heappop(queue)
            if cost > costs[node]:
                continue
            if node == target:
                return path
            for edge in self.connections:
                candidate = cost + edge.timeout_s
                if edge.source == node and candidate < costs.get(edge.target, float('inf')):
                    costs[edge.target] = candidate
                    heapq.heappush(queue, (candidate, next(serial), edge.target, path + (edge,)))
        return ()

    def by_name(self, name: str) -> Platform:
        return next(pl for pl in self.platforms if pl.name == name)

    def __repr__(self) -> str:
        ys = ("" if self.patrol_y is None else
              f", y={self.patrol_y:.3f} -{self.patrol_y_tolerance_above:.3f}"
              f"/+{self.patrol_y_tolerance:.3f}")
        return (f"MapModel(patrol={self.patrol_left:.3f}..{self.patrol_right:.3f}{ys}, "
                f"platforms={len(self.platforms)})")
