"""
Behaviour state machine: one Action per tick from Perception + MapModel.

Owner: behavior-designer.

Pure and deterministic: no I/O, no sleeps, no clock reads. The only notion of time is
the `now` passed to `decide()`. The machine keeps its own belief of the character's
facing/walking, updated from the actions it emits with the same semantics as the
Controller:

    WALK_LEFT/RIGHT  -> facing = dir, walking = dir
    STOP_WALK        -> walking = None
    FACE_LEFT/RIGHT  -> walking = None, facing = dir
    ATTACK           -> walking = None
    everything else  -> unchanged

Facing starts UNKNOWN (None), also after reset(): the character may be facing either
way when the bot starts or resumes, so the first engagement always sends FACE_* before
the first ATTACK, and the first WALK_* also makes the facing known.

Rules, in priority order (first match wins):

    WAIT     confidence <= 0.5 or LOW_CONFIDENCE flag    -> STOP_WALK if walking else IDLE
    RECOVER  HP / MP below threshold, key set, pot cooldown passed -> USE_HP_POT / USE_MP_POT
    WAIT     no minimap position                          -> STOP_WALK if walking else IDLE
    ENGAGE   mob in skill range on screen (or seen < 0.6 s ago)  -> STOP_WALK / FACE_* / ATTACK / IDLE
             (won't turn toward a mob behind while one ahead was seen < 0.3 s ago)
    OFF_PLATFORM  patrol_y set and minimap y outside patrol_y +- tolerance
             -> IDLE (never starts a walk); a walk already under way is stopped
                (STOP_WALK) once y has been off for OFF_PLATFORM_DEBOUNCE_S
    PATROL   edges, approach visible mob, keep direction  -> WALK_* on change, else
    STUCK    still walking the same way, minimap x flat for stuck_s -> JUMP, else IDLE

Confidence 0.5 is NOT trusted (inclusive threshold). The Perceiver scores a frame 0.5
exactly when the minimap dot is missing, so this means the bot never engages or walks
without a minimap position. An estimated player position (player_screen_estimated) is
never used for ENGAGE or for walking toward mobs; patrolling by the minimap still works.
Engaging a mob in range while OFF_PLATFORM is allowed; only walking is blocked (the
supervisor pauses after safety.platform_lost_s).

Approach hysteresis: after the patrol turns around at an edge, mobs beyond that edge
are not approached until the opposite edge is reached or a mob comes into range
(otherwise a mob just past the edge would make the patrol bounce at the edge).
Flicker hysteresis: a mob seen ahead (targeting or approaching) within TURN_HOLD_S
keeps us facing / walking that way even if this frame missed it.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from maplebot.mapmodel import MapModel
from maplebot.settings import Settings
from maplebot.types import Action, Decision, Flag, Mob, Perception

# Tuning constants (behaviour internals, not user settings).
MIN_CONFIDENCE = 0.5        # below this perception is not trusted
DEAD_ZONE_PX = 12           # |dx| within this keeps the current facing
LINGER_S = 0.6              # stay in ENGAGE this long after the last in-range sighting
STUCK_EPS = 0.004           # minimap x must move more than this to count as moving
TARGET_SWITCH_MARGIN_PX = 40  # a mob behind us must be this much nearer to make us turn
TURN_HOLD_S = 0.3           # don't turn away from a mob seen ahead within this long
OFF_PLATFORM_DEBOUNCE_S = 0.2  # y out of band this long before we stop (1-px dot jitter)

LEFT, RIGHT = "left", "right"

_WALK = {LEFT: Action.WALK_LEFT, RIGHT: Action.WALK_RIGHT}
_FACE = {LEFT: Action.FACE_LEFT, RIGHT: Action.FACE_RIGHT}


def _pct(v: float) -> str:
    return f"{round(v * 100)}%"


def _side(dx: float) -> str:
    return RIGHT if dx > 0 else LEFT


class Behavior:
    """Rule-based decision maker. See module docstring for the rules."""

    def __init__(self, settings: Settings, map_model: MapModel):
        self.settings = settings
        self.map = map_model
        self.reset()

    # ------------------------------------------------------------------ public API
    def reset(self) -> None:
        """Forget all beliefs and timers (called on resume after a pause)."""
        self.facing: Optional[str] = None      # unknown until we turn or walk
        self.walking: Optional[str] = None
        self._patrol_dir: str = RIGHT          # direction to resume patrolling in
        self._edge_block: Optional[str] = None  # don't approach mobs toward this edge
        self._last_attack: float = -math.inf
        self._last_hp_pot: float = -math.inf
        self._last_mp_pot: float = -math.inf
        self._last_in_range: float = -math.inf
        self._ahead: Optional[Tuple[Mob, float]] = None   # (mob, t) last in-range mob ahead
        self._approach_seen: float = -math.inf   # last time a mob was seen ahead while walking
        self._stuck_ref: Optional[Tuple[float, float]] = None   # (x, t) last movement
        self._off_since: Optional[float] = None  # first tick y was off the training band

    def update_settings(self, settings: Settings) -> None:
        """Take new settings. Rebuilds the map model if the patrol bounds changed
        (raises ValueError, leaving everything unchanged, if they are invalid)."""
        c = settings.calibration
        if (c.patrol_left, c.patrol_right) != (self.map.patrol_left, self.map.patrol_right):
            self.map = MapModel.from_settings(settings)
        self.settings = settings

    def decide(self, p: Perception, now: float) -> Decision:
        x = self._map_x(p)
        self._track_movement(x, now)
        d = (self._rule_wait_confidence(p)
             or self._rule_recover(p, now)
             or self._rule_wait_minimap(x)
             or self._rule_engage(p, now)
             or self._rule_off_platform(p, now)
             or self._rule_patrol(p, x, now))  # type: ignore[arg-type]
        self._apply(d.action, x, now)
        return d

    # ------------------------------------------------------------------ rules
    def _rule_wait_confidence(self, p: Perception) -> Optional[Decision]:
        if p.confidence <= MIN_CONFIDENCE or Flag.LOW_CONFIDENCE in p.flags:
            return self._wait(f"low confidence ({p.confidence:.2f})")
        return None

    def _rule_recover(self, p: Perception, now: float) -> Optional[Decision]:
        pots = self.settings.potions
        keys = self.settings.keys
        cd = pots.cooldown_s
        if (p.hp is not None and p.hp < pots.hp_below and keys.hp_potion
                and now - self._last_hp_pot >= cd):
            return Decision(Action.USE_HP_POT, "RECOVER",
                            reason=f"HP {_pct(p.hp)} < {_pct(pots.hp_below)}")
        if (p.mp is not None and p.mp < pots.mp_below and keys.mp_potion
                and now - self._last_mp_pot >= cd):
            return Decision(Action.USE_MP_POT, "RECOVER",
                            reason=f"MP {_pct(p.mp)} < {_pct(pots.mp_below)}")
        return None

    def _rule_engage(self, p: Perception, now: float) -> Optional[Decision]:
        target = self._pick_target(p, now)
        if target is None:
            if now - self._last_in_range < LINGER_S:
                if self.walking:
                    return Decision(Action.STOP_WALK, "ENGAGE", reason="lingering after mob")
                left = LINGER_S - (now - self._last_in_range)
                return Decision(Action.IDLE, "ENGAGE",
                                reason=f"lingering {left:.1f}s after last mob")
            return None

        self._last_in_range = now
        self._edge_block = None          # fresh start for patrol once this fight ends
        dx = target.x - self._player_xy(p)[0]    # type: ignore[index]
        where = f"mob {abs(dx)}px {_side(dx)}" if abs(dx) > DEAD_ZONE_PX else "mob on top of us"
        if self.walking:
            return Decision(Action.STOP_WALK, "ENGAGE", target, f"stop: {where}")
        if abs(dx) > DEAD_ZONE_PX:
            need = _side(dx)
        elif self.facing is not None:
            need = self.facing                       # dead zone: keep facing
        else:
            need = RIGHT if dx >= 0 else LEFT        # dead zone, facing unknown: pick one
        if need != self.facing:
            if self.facing is None:
                where += ", facing unknown"
            self._ahead = (target, now)      # it will be ahead once we've turned
            return Decision(_FACE[need], "ENGAGE", target, f"turn {need}: {where}")
        if target not in p.mobs:
            # Only remembered (flicker hold): keep facing it, but don't cast blind --
            # it may have died or walked past us.
            return Decision(Action.IDLE, "ENGAGE", target, f"holding facing, {where} (last seen)")
        since = now - self._last_attack
        cd = self.settings.skill.cooldown_s
        if since >= cd:
            return Decision(Action.ATTACK, "ENGAGE", target, f"attack {where}")
        return Decision(Action.IDLE, "ENGAGE", target,
                        f"cooldown {cd - since:.2f}s, {where}")

    def _held_ahead(self, now: float) -> Optional[Mob]:
        if self._ahead is not None and now - self._ahead[1] < TURN_HOLD_S:
            return self._ahead[0]
        return None

    def _rule_wait_minimap(self, x: Optional[float]) -> Optional[Decision]:
        if x is None:
            return self._wait("no minimap position")
        return None

    def _rule_off_platform(self, p: Perception, now: float) -> Optional[Decision]:
        m = self.map
        if m.patrol_y is None:
            return None
        x, y = p.player_map                         # type: ignore[misc]
        if m.on_platform(x, y):
            self._off_since = None
            return None
        if self._off_since is None:
            self._off_since = now
        why = f"not on the training bridge (y {y:.2f} vs {m.patrol_y:.2f})"
        if not self.walking:
            return Decision(Action.IDLE, "OFF_PLATFORM", reason=why)   # never start a walk
        if now - self._off_since < OFF_PLATFORM_DEBOUNCE_S:
            return None                  # maybe 1-px dot jitter: keep the walk going a moment
        return Decision(Action.STOP_WALK, "OFF_PLATFORM", reason=why)

    def _rule_patrol(self, p: Perception, x: float, now: float) -> Decision:
        want, reason, target = self._patrol_choice(p, x, now)
        if self.walking != want:
            return Decision(_WALK[want], "PATROL", target, reason)
        # Still walking the same way: STUCK check (a turn-around above takes priority,
        # so pressing into an edge never triggers a jump).
        if (self._stuck_ref is not None
                and now - self._stuck_ref[1] >= self.settings.behavior.stuck_s):
            return Decision(Action.JUMP, "STUCK", target,
                            f"not moving for {now - self._stuck_ref[1]:.1f}s, jump")
        return Decision(Action.IDLE, "PATROL", target, f"walking {want}: {reason}")

    # ------------------------------------------------------------------ helpers
    def _wait(self, why: str) -> Decision:
        if self.walking:
            return Decision(Action.STOP_WALK, "WAIT", reason=why)
        return Decision(Action.IDLE, "WAIT", reason=why)

    @staticmethod
    def _map_x(p: Perception) -> Optional[float]:
        if p.player_map is None or Flag.MINIMAP_MISSING in p.flags:
            return None
        return float(p.player_map[0])

    @staticmethod
    def _player_xy(p: Perception) -> Optional[Tuple[int, int]]:
        """Player screen position usable for aiming/approaching: None if not found or
        only estimated (an estimate is the screen centre, not where the character is)."""
        if p.player_screen is None or p.player_screen_estimated:
            return None
        return p.player_screen

    def _same_row(self, p: Perception) -> List[Mob]:
        pos = self._player_xy(p)
        if pos is None:
            return []
        py = pos[1]
        tol = self.settings.skill.vertical_tolerance_px
        return [m for m in p.mobs if abs(m.y - py) <= tol]

    def _pick_target(self, p: Perception, now: float) -> Optional[Mob]:
        """Nearest in-range mob by |dx|, with hysteresis: the nearest mob on the side we
        face (or in the dead zone, or seen there < TURN_HOLD_S ago) is kept unless one
        behind us is TARGET_SWITCH_MARGIN_PX nearer, so two mobs at similar distances
        or a flickering detection don't make us flip back and forth."""
        pos = self._player_xy(p)
        if pos is None:
            return None
        px = pos[0]
        rng = self.settings.skill.range_px
        in_range = [m for m in self._same_row(p) if abs(m.x - px) <= rng]
        if not in_range:
            return None
        key = lambda m: (abs(m.x - px), m.x)        # noqa: E731 - deterministic ties
        nearest = min(in_range, key=key)
        ahead = [m for m in in_range
                 if abs(m.x - px) <= DEAD_ZONE_PX or _side(m.x - px) == self.facing]
        # The nearest mob ahead may just have flickered out of detection: keep it as a
        # candidate for TURN_HOLD_S so one missed frame doesn't spin us around.
        held = self._held_ahead(now)
        if ahead:
            best_ahead = min(ahead, key=key)
            if (held is not None and abs(held.x - px)
                    < abs(best_ahead.x - px) - TARGET_SWITCH_MARGIN_PX):
                best_ahead = held          # a clearly nearer one ahead just flickered out
            else:
                self._ahead = (best_ahead, now)
        elif held is not None:
            best_ahead = held
        else:
            return nearest
        if abs(nearest.x - px) < abs(best_ahead.x - px) - TARGET_SWITCH_MARGIN_PX:
            self._ahead = None           # deliberate switch: no hold
            return nearest
        return best_ahead

    def _patrol_choice(self, p: Perception, x: float, now: float) -> Tuple[str, str, Optional[Mob]]:
        """(direction to walk, reason, mob being approached or None)."""
        if x <= self.map.patrol_left:
            self._edge_block = LEFT
            return RIGHT, "reached left edge", None
        if x >= self.map.patrol_right:
            self._edge_block = RIGHT
            return LEFT, "reached right edge", None
        mob = self._approach_target(p, now)
        if mob is not None:
            dx = mob.x - self._player_xy(p)[0]      # type: ignore[index]
            toward = _side(dx)
            if dx != 0 and toward != self._edge_block:
                return toward, f"approach mob {abs(dx)}px {toward}", mob
        if self.walking:
            return self.walking, "patrolling", None
        return self._patrol_dir, f"start patrol {self._patrol_dir}", None

    def _approach_target(self, p: Perception, now: float) -> Optional[Mob]:
        """Nearest visible same-row mob, with the same hysteresis as targeting: keep
        walking toward mobs ahead unless one behind is TARGET_SWITCH_MARGIN_PX nearer,
        and don't turn back while a mob ahead was seen < TURN_HOLD_S ago (flicker)."""
        mobs = self._same_row(p)                    # empty if no usable player position
        pos = self._player_xy(p)
        px = pos[0] if pos is not None else 0
        key = lambda m: (abs(m.x - px), m.x)        # noqa: E731
        ahead = [m for m in mobs if self.walking and _side(m.x - px) == self.walking]
        if ahead:
            best_ahead = min(ahead, key=key)
            self._approach_seen = now
        else:
            best_ahead = None
        if not mobs:
            return None
        nearest = min(mobs, key=key)
        if not self.walking:
            return nearest
        if best_ahead is None:
            if now - self._approach_seen < TURN_HOLD_S:
                return None                  # keep walking: the mob ahead flickered out
            return nearest
        if abs(nearest.x - px) < abs(best_ahead.x - px) - TARGET_SWITCH_MARGIN_PX:
            return nearest
        return best_ahead

    def _track_movement(self, x: Optional[float], now: float) -> None:
        if not self.walking:
            self._stuck_ref = None
            return
        if x is None:
            return
        if self._stuck_ref is None or abs(x - self._stuck_ref[0]) > STUCK_EPS:
            self._stuck_ref = (x, now)

    def _apply(self, action: Action, x: Optional[float], now: float) -> None:
        """Update beliefs exactly like the Controller does, plus timers."""
        if action in (Action.WALK_LEFT, Action.WALK_RIGHT):
            d = LEFT if action is Action.WALK_LEFT else RIGHT
            if self.walking != d:
                self._stuck_ref = (x, now) if x is not None else None
            self.facing = self.walking = self._patrol_dir = d
        elif action is Action.STOP_WALK:
            self.walking = None
            self._stuck_ref = None
        elif action in (Action.FACE_LEFT, Action.FACE_RIGHT):
            self.walking = None
            self._stuck_ref = None
            self.facing = LEFT if action is Action.FACE_LEFT else RIGHT
        elif action is Action.ATTACK:
            self.walking = None
            self._stuck_ref = None
            self._last_attack = now
        elif action is Action.JUMP:
            self._stuck_ref = (x, now) if x is not None else None
        elif action is Action.USE_HP_POT:
            self._last_hp_pot = now
        elif action is Action.USE_MP_POT:
            self._last_mp_pot = now
