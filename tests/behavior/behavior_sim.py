"""
Tiny 1-D training-map simulator that drives Behavior tick by tick.

World: one platform `world_w` px wide. The camera follows the player, so the player is
always at screen x = PLAYER_X and a mob's screen x is PLAYER_X + (mob_wx - player_wx).
Only mobs within half a screen width are visible. Minimap x = player_wx / world_w.

Minimap y models a sagging rope bridge (The Tree That Grew II): anchors at minimap x
`bridge` = (left, right) at height `bridge_y`, sagging by `sag` (minimap fraction) to the
middle in a parabola, quantised to whole minimap pixels (`minimap_h` px tall) like the
real dot. Defaults follow the real minimap (112 px): sag 0.063-0.071, next bridge 0.117
lower. `patrol_y` = how calibration.patrol_y is set: "end" (wizard: dot y at the patrol
edges), "mid" (middle of the walked y range) or None. `fall_at` = t: the character
drops to the bridge below (+`bridge_gap`) and stays there. The character's true facing at start is `start_facing` (random by seed if
None): the bot doesn't know it. `y_jitter` = chance per frame the dot reads 1 px off.

Controller semantics are mirrored exactly (WALK_* sets facing+walking, STOP_WALK clears
walking, FACE_* stops and turns, ATTACK stops). A cast hits the nearest mob in front of
the player within the skill range; a mob dies after `hits_to_kill` hits and a new one
spawns after `respawn_s` somewhere on the platform (possibly outside the patrol zone).

Run directly for a summary:  python tests/behavior/behavior_sim.py
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from maplebot.behavior import Behavior
from maplebot.mapmodel import MapModel
from maplebot.settings import Settings
from maplebot.types import Action, Mob, Perception

PLAYER_X, PLAYER_Y = 400, 400
SCREEN_W = 800


@dataclass
class SimMob:
    wx: float
    hits: int = 0
    vx: float = 0.0


@dataclass
class SimResult:
    kills: int = 0
    attacks: int = 0
    duration_s: float = 0.0
    min_map_x: float = 1.0
    max_map_x: float = 0.0
    max_overshoot: float = 0.0           # beyond patrol bounds, minimap fraction
    facing_changes: List[float] = field(default_factory=list)
    max_facing_changes_per_s: int = 0
    jumps: int = 0
    wrong_way_casts: int = 0             # cast facing away from every in-range mob (> dead zone)
    max_off_streak_s: float = 0.0        # longest run of OFF_PLATFORM decisions
    min_map_y: float = 1.0
    max_map_y: float = 0.0
    walk_ticks_after_fall: int = 0       # ticks spent walking > debounce after a fall
    first_off_platform_t: Optional[float] = None
    fall_detect_delay_s: Optional[float] = None   # fall -> first OFF_PLATFORM decision
    states_after_fall: dict = field(default_factory=dict)   # from 0.5 s after the fall
    states: dict = field(default_factory=dict)
    actions: dict = field(default_factory=dict)

    @property
    def kills_per_min(self) -> float:
        return self.kills * 60.0 / self.duration_s if self.duration_s else 0.0


def simulate(seed: int = 1, duration_s: float = 60.0, hz: float = 15.0,
             world_w: float = 4000.0, speed_px_s: float = 125.0,
             n_mobs: int = 4, hits_to_kill: int = 3, respawn_s: float = 4.0,
             miss_rate: float = 0.1, mob_speed: float = 20.0,
             obstacle: Optional[Tuple[float, float]] = None,
             settings: Optional[Settings] = None,
             start_facing: Optional[str] = None,
             bridge: Tuple[float, float] = (0.28, 0.72), bridge_y: float = 0.40,
             sag: float = 0.07, minimap_h: int = 112, bridge_gap: float = 0.117,
             patrol_y: Optional[str] = "end",
             fall_at: Optional[float] = None, y_jitter: float = 0.0) -> SimResult:
    """`obstacle` = (start_wx, end_wx): walking stalls there until a JUMP carries the
    player over it. `miss_rate` = chance per frame that a visible mob is not detected."""
    rng = random.Random(seed)
    s = settings or Settings()
    bl, br = bridge
    bc, bh = (bl + br) / 2, (br - bl) / 2

    def bridge_y_at(mx: float) -> float:
        u = min(1.0, abs(mx - bc) / bh)
        return bridge_y + sag * (1 - u * u)

    if patrol_y == "end":
        # what the setup wizard stores: the dot y at the patrol edges, quantised like the
        # real dot. With the default bridge the edges sit near the anchored ends, i.e.
        # the TOP of the sag; walking to the middle moves the dot down by ~sag.
        ys = [bridge_y_at(e) for e in (s.calibration.patrol_left, s.calibration.patrol_right)]
        s.calibration.patrol_y = round(min(ys) * minimap_h) / minimap_h
    elif patrol_y == "mid":
        # alternative: middle of the y range seen walking the zone
        ys = [bridge_y_at(s.calibration.patrol_left + i / 200 *
                          (s.calibration.patrol_right - s.calibration.patrol_left))
              for i in range(201)]
        s.calibration.patrol_y = (min(ys) + max(ys)) / 2
    else:
        s.calibration.patrol_y = None
    b = Behavior(s, MapModel.from_settings(s))
    left_wx, right_wx = s.calibration.patrol_left * world_w, s.calibration.patrol_right * world_w

    def spawn() -> SimMob:
        return SimMob(wx=rng.uniform(0.2 * world_w, 0.8 * world_w),
                      vx=rng.choice([-1, 1]) * mob_speed)

    px = (left_wx + right_wx) / 2
    facing = start_facing or rng.choice(["left", "right"])
    walking = None
    fallen = False
    off_start: Optional[float] = None
    mobs: List[SimMob] = [spawn() for _ in range(n_mobs)]
    respawns: List[float] = []
    jump_until = -1.0
    res = SimResult(duration_s=duration_s)
    dt = 1.0 / hz
    t = 0.0
    prev_facing = facing

    while t < duration_s:
        # ---- world update
        for m in mobs:
            m.wx += m.vx * dt
            if not (0.2 * world_w <= m.wx <= 0.8 * world_w) or rng.random() < 0.01:
                m.vx = -m.vx
        while respawns and respawns[0] <= t:
            respawns.pop(0)
            mobs.append(spawn())

        # ---- perception
        visible = []
        for m in mobs:
            sx = PLAYER_X + (m.wx - px)
            if 0 <= sx < SCREEN_W and rng.random() >= miss_rate:
                visible.append(Mob(int(round(sx)), PLAYER_Y + rng.randint(-5, 5), 40, 40,
                                   0.9, "mob_sim.png"))
        if fall_at is not None and t >= fall_at:
            fallen = True
        my = bridge_y_at(px / world_w) + (bridge_gap if fallen else 0.0)
        my = round(my * minimap_h) / minimap_h            # whole minimap pixels
        if y_jitter and rng.random() < y_jitter:          # dot detection off by 1 px
            my += rng.choice([-1, 1]) / minimap_h
        res.min_map_y, res.max_map_y = min(res.min_map_y, my), max(res.max_map_y, my)
        p = Perception(t=t, screen_size=(SCREEN_W, 600), player_map=(px / world_w, my),
                       player_screen=(PLAYER_X, PLAYER_Y), player_screen_estimated=False,
                       mobs=tuple(visible), hp=1.0, mp=1.0)

        d = b.decide(p, t)
        res.states[d.state] = res.states.get(d.state, 0) + 1
        res.actions[d.action.value] = res.actions.get(d.action.value, 0) + 1

        # ---- controller
        a = d.action
        if a in (Action.WALK_LEFT, Action.WALK_RIGHT):
            facing = walking = "left" if a is Action.WALK_LEFT else "right"
        elif a is Action.STOP_WALK:
            walking = None
        elif a in (Action.FACE_LEFT, Action.FACE_RIGHT):
            walking = None
            facing = "left" if a is Action.FACE_LEFT else "right"
        elif a is Action.JUMP:
            res.jumps += 1
            jump_until = t + 0.6
        elif a is Action.ATTACK:
            walking = None
            res.attacks += 1
            sign = 1 if facing == "right" else -1
            # wrong way = every mob in range is clearly behind (beyond the 12 px dead
            # zone + rounding); a mob in the dead zone counts as a valid target
            in_range = [m for m in mobs if abs(m.wx - px) <= s.skill.range_px + 1]  # + rounding
            if in_range and all((m.wx - px) * sign < -13.5 for m in in_range):
                res.wrong_way_casts += 1
            hittable = [m for m in mobs
                        if 0 <= (m.wx - px) * sign <= s.skill.range_px]
            if hittable:
                m = min(hittable, key=lambda m: abs(m.wx - px))
                m.hits += 1
                if m.hits >= hits_to_kill:
                    mobs.remove(m)
                    res.kills += 1
                    respawns.append(t + respawn_s)
                    respawns.sort()

        if b.facing is not None:
            assert facing == b.facing, "facing belief drifted from the character"
        assert walking == b.walking, "walking belief drifted from controller"
        if d.state == "OFF_PLATFORM":
            if res.first_off_platform_t is None:
                res.first_off_platform_t = t
            if off_start is None:
                off_start = t
            res.max_off_streak_s = max(res.max_off_streak_s, t - off_start + dt)
        else:
            off_start = None
        if fallen and d.state == "OFF_PLATFORM" and res.fall_detect_delay_s is None:
            res.fall_detect_delay_s = t - fall_at          # type: ignore[operator]
        if fallen and t - fall_at > 0.5:                  # type: ignore[operator]
            res.states_after_fall[d.state] = res.states_after_fall.get(d.state, 0) + 1
            if walking:
                res.walk_ticks_after_fall += 1
        if facing != prev_facing:
            res.facing_changes.append(t)
            prev_facing = facing

        # ---- physics
        if walking:
            step = speed_px_s * dt * (1 if walking == "right" else -1)
            new = px + step
            blocked = (obstacle is not None and t >= jump_until
                       and obstacle[0] <= new <= obstacle[1])
            if not blocked:
                px = min(max(new, 0.0), world_w)

        mx = px / world_w
        res.min_map_x = min(res.min_map_x, mx)
        res.max_map_x = max(res.max_map_x, mx)
        res.max_overshoot = max(res.max_overshoot,
                                s.calibration.patrol_left - mx, mx - s.calibration.patrol_right)
        t += dt

    # worst 1-second window of facing changes
    fc = res.facing_changes
    res.max_facing_changes_per_s = max(
        (sum(1 for u in fc if s0 <= u < s0 + 1.0) for s0 in fc), default=0)
    return res


if __name__ == "__main__":
    for seed in range(1, 6):
        r = simulate(seed=seed)
        print(f"seed {seed}: kills/min {r.kills_per_min:.1f}  attacks {r.attacks}  "
              f"overshoot {r.max_overshoot:.4f}  map_x {r.min_map_x:.3f}..{r.max_map_x:.3f}  "
              f"facing changes {len(r.facing_changes)} (max {r.max_facing_changes_per_s}/s)  "
              f"jumps {r.jumps}  wrong-way casts {r.wrong_way_casts}  "
              f"y {r.min_map_y:.3f}..{r.max_map_y:.3f}  states {r.states}")
    r = simulate(seed=1, obstacle=(2300, 2340))
    print(f"obstacle: kills/min {r.kills_per_min:.1f} jumps {r.jumps} "
          f"map_x {r.min_map_x:.3f}..{r.max_map_x:.3f}")
