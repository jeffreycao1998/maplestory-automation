"""Latched, bounded travel to a user-selected refuge; no inventory OCR claims."""
from __future__ import annotations

from dataclasses import replace
from maplebot.types import Action, Decision


class RetreatState:
    def __init__(self, settings):
        self.settings = settings
        self.last_hud_t = -float("inf")
        self.hp_remaining = settings.retreat.hp_start
        self.mp_remaining = settings.retreat.mp_start
        self.reason = ""
        self.started = None
        self.arrived = False
        self.settled_since = None
        self.last_position = None

    def observe(self, p, now):
        config = self.settings.retreat
        if p.hud_t is not None and p.hud_t > self.last_hud_t:
            self.last_hud_t = p.hud_t
            for kind in ("hp","mp"):
                visible = getattr(p,kind+"_stock")
                if visible is not None:
                    setattr(self,kind+"_remaining",visible)
        if not config.enabled or self.reason or self.settings.calibration.safe_spot is None:
            return
        for kind, count, reserve in (("HP", self.hp_remaining, config.hp_reserve),
                                     ("MP", self.mp_remaining, config.mp_reserve)):
            if count is not None and count <= reserve:
                self.reason = f"Estimated {kind} potions {count} at/below reserve {reserve}"
                break
        if not self.reason and p.inventory_full:
            self.reason = "Inventory is full"
        if not self.reason:
            if p.hp is not None and p.hp < config.hp_below:
                self.reason = f"HP below retreat threshold ({p.hp:.0%})"
            elif p.mp is not None and p.mp < config.mp_below:
                self.reason = f"MP below retreat threshold ({p.mp:.0%})"
        if self.reason:
            self.started = now

    def can_use(self, kind):
        remaining = getattr(self, kind + "_remaining")
        return remaining is None or remaining > 0

    def note_action(self, action):
        kind = {Action.USE_HP_POT: "hp", Action.USE_MP_POT: "mp"}.get(action)
        if kind is not None:
            attr = kind + "_remaining"
            count = getattr(self, attr)
            if count is not None:
                setattr(self, attr, max(0, count - 1))

    def decide(self, p, now, model, navigation):
        if not self.reason:
            return None
        config = self.settings.retreat
        def decision(state, reason, action=Action.STOP_WALK):
            return Decision(action, state, reason=reason, retreat_reason=self.reason)
        if self.arrived:
            return decision("RETREAT_ARRIVED", "At safe spot; refill potions and update starting stock before restarting")
        if now - self.started >= config.timeout_s:
            return decision("RETREAT_FAILED", "Safe-spot retreat exceeded its deadline")
        point = self.settings.calibration.safe_spot
        try:
            dest = model.safe_platform(point, self.settings.behavior.edge_margin)
        except ValueError as exc:
            return decision("RETREAT_FAILED", str(exc))
        if p.player_map is None:
            return decision("RETREAT_WAIT", "Waiting for a valid minimap position")
        x, y = p.player_map
        here = model.platform_at(x, y)
        if here is None or not model.on_platform(x,y):
            return decision("RETREAT_FAILED", "Player is not on a known platform; cannot plan a safe retreat")
        if here.name != dest.name:
            if not model.explicit:
                return decision("RETREAT_FAILED", "No measured platform route to the safe spot")
            result = navigation.decide(p, now, goal=dest.name)
            if result is None:
                return decision("RETREAT_FAILED", "No measured path to the safe spot")
            return replace(result, retreat_reason=self.reason, reason=f"Retreat: {result.reason}")
        self.last_position, previous = p.player_map, self.last_position
        if abs(x - point[0]) <= config.arrival_tolerance:
            stable = previous is not None and abs(x-previous[0]) <= .004 and abs(y-previous[1]) <= .008
            if stable:
                if self.settled_since is None:
                    self.settled_since = now
                if now-self.settled_since >= config.settle_s:
                    self.arrived = True
                    return decision("RETREAT_ARRIVED", "Safe spot reached; manual refill required")
            else:
                self.settled_since = None
            return decision("RETREAT_SETTLE", "Braking and confirming arrival at safe spot")
        self.settled_since = None
        return decision("RETREAT_WALK", "Walking to safe-spot marker",
                        Action.WALK_RIGHT if point[0] > x else Action.WALK_LEFT)
