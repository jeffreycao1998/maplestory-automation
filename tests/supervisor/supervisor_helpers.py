from __future__ import annotations

import dataclasses

from maplebot.settings import Settings
from maplebot.types import Action, Decision, Flag, Mob, Perception


MOB = Mob(x=400, y=300, w=40, h=30, score=0.9, template="mob_snail.png")


def make_p(t: float = 0.0, *, player=(0.5, 0.5), mobs=(MOB,), hp=0.9, mp=0.8,
           flags=frozenset(), confidence=1.0) -> Perception:
    return Perception(t=t, screen_size=(800, 600), player_map=player,
                      player_screen=(400, 320) if player is not None else None,
                      player_screen_estimated=False, mobs=tuple(mobs), hp=hp, mp=mp,
                      flags=frozenset(flags), confidence=confidence)


def stuck_jump() -> Decision:
    return Decision(Action.JUMP, "STUCK", reason="minimap x not moving")


def make_settings(**safety) -> Settings:
    s = Settings()
    s.safety = dataclasses.replace(s.safety, player_lost_s=3.0, no_mobs_pause_s=180.0,
                                   stuck_jumps_limit=6, hp_critical=0.15,
                                   max_runtime_min=120.0)
    if safety:
        s.safety = dataclasses.replace(s.safety, **safety)
    return s
