"""Hand-built Perception objects and a ready-made Behavior for behaviour tests."""
from __future__ import annotations

from maplebot.behavior import Behavior
from maplebot.mapmodel import MapModel
from maplebot.settings import Settings
from maplebot.types import Mob, Perception

PLAYER_Y = 400
PLAYER_X = 400


def mob(x, y=PLAYER_Y, score=0.9):
    return Mob(x=int(x), y=int(y), w=40, h=40, score=score, template="mob_test.png")


def perc(t=0.0, map_x=0.5, mobs=(), player=(PLAYER_X, PLAYER_Y), hp=1.0, mp=1.0,
         confidence=1.0, flags=frozenset(), map_y=0.5, estimated=False):
    """Build a Perception. `mobs` may be Mob objects or plain x positions (same row)."""
    ms = tuple(m if isinstance(m, Mob) else mob(m) for m in mobs)
    return Perception(
        t=t,
        screen_size=(800, 600),
        player_map=None if map_x is None else (map_x, map_y),
        player_screen=player,
        player_screen_estimated=estimated,
        mobs=ms,
        hp=hp,
        mp=mp,
        flags=frozenset(flags),
        confidence=confidence,
    )


def make_settings(**over):
    s = Settings()
    s.keys.hp_potion = over.pop("hp_key", "")
    s.keys.mp_potion = over.pop("mp_key", "")
    for k, v in over.items():
        section, name = k.split("__")
        setattr(getattr(s, section), name, v)
    return s


def make_behavior(facing=None, **over):
    """A fresh Behavior. `facing` presets the facing belief (default: unknown, as at
    start-up); tests about targeting logic pass facing="right" to skip the first turn."""
    s = make_settings(**over)
    b = Behavior(s, MapModel.from_settings(s))
    b.facing = facing
    return b
