"""
Shared data types passed between parts of the bot.

Owner: main agent. Subagents use these as-is and propose changes rather than editing.
Everything here is plain data -- no I/O, no OS calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

import numpy as np

Box = Tuple[int, int, int, int]          # (x, y, width, height), pixels, game-client coords
PointF = Tuple[float, float]             # (x, y) as 0..1 fractions
PointPx = Tuple[int, int]                # (x, y) pixels, game-client coords


@dataclass(frozen=True)
class Frame:
    """One screenshot of the game's client area (no title bar / borders)."""
    image: np.ndarray                    # BGR, uint8, shape (h, w, 3)
    t: float                             # time.monotonic() when captured
    window: Box                          # client area position on the screen


@dataclass(frozen=True)
class Mob:
    x: int                               # centre, pixels
    y: int
    w: int
    h: int
    score: float                         # template match score 0..1
    template: str                        # template file name it matched


@dataclass(frozen=True)
class Item:
    x: int
    y: int
    w: int
    h: int
    score: float
    template: str


class Flag(str, Enum):
    MINIMAP_MISSING = "minimap_missing"  # minimap box doesn't look like a minimap / dot absent
    PLAYER_OFFSCREEN = "player_offscreen"  # player template configured but not found
    UNEXPECTED_DIALOG = "unexpected_dialog"
    ALERT_MATCH = "alert_match"
    INVENTORY_FULL = "inventory_full"
    STOCK_UNREADABLE = "stock_unreadable"
    VIEW_CHANGED = "view_changed"
    LOW_CONFIDENCE = "low_confidence"


@dataclass(frozen=True)
class Perception:
    t: float
    screen_size: Tuple[int, int]         # (w, h) of the frame
    player_map: Optional[PointF]         # yellow dot, full-map fractions when registered, else viewport; None if unknown
    player_screen: Optional[PointPx]     # character position on screen
    player_screen_estimated: bool        # True if player_screen is a guess (no name-tag match)
    mobs: Tuple[Mob, ...]                # excludes anything inside minimap / HP / MP boxes
    hp: Optional[float]                  # 0..1, None if HP bar not calibrated / unreadable
    mp: Optional[float]
    flags: frozenset = field(default_factory=frozenset)   # set of Flag
    confidence: float = 1.0              # 0..1
    items: Tuple[Item, ...] = ()
    hp_stock: Optional[int] = None
    mp_stock: Optional[int] = None
    mesos: Optional[int] = None
    inventory_full: Optional[bool] = None
    hud_t: Optional[float] = None
    minimap_view: Optional[Tuple[float, float, float, float]] = None  # offset px + pixels per full-map fraction


class Action(str, Enum):
    IDLE = "idle"                        # do nothing this tick (keep current walk state)
    ATTACK = "attack"                    # cast the skill once
    FACE_LEFT = "face_left"              # turn without walking
    FACE_RIGHT = "face_right"
    WALK_LEFT = "walk_left"              # start/continue walking (held until STOP_WALK or other walk)
    WALK_RIGHT = "walk_right"
    STOP_WALK = "stop_walk"
    JUMP = "jump"
    JUMP_UP = "jump_up"                # stop horizontal travel before jumping
    JUMP_LEFT = "jump_left"
    JUMP_RIGHT = "jump_right"
    DROP_DOWN = "drop_down"
    CLIMB_UP = "climb_up"
    CLIMB_DOWN = "climb_down"
    GRAB_UP = "grab_up"
    PICKUP = "pickup"
    USE_HP_POT = "use_hp_pot"
    USE_MP_POT = "use_mp_pot"


@dataclass(frozen=True)
class Decision:
    action: Action
    state: str                           # behaviour state name, e.g. "PATROL", "ENGAGE"
    target: Optional[Mob] = None
    reason: str = ""
    transition: Optional[str] = None
    retreat_reason: str = ""
    route: Tuple[str, ...] = ()


class VerdictKind(str, Enum):
    OK = "ok"
    PAUSE = "pause"                      # stop acting, release keys, wait for the user
    STOP = "stop"                        # end the run


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    reason: str = ""


@dataclass
class Status:
    """What the GUI shows. Produced by the runner every tick."""
    running: bool = False
    paused: bool = False
    state: str = "IDLE"
    action: str = ""
    n_mobs: int = 0
    map_x: Optional[float] = None
    hp: Optional[float] = None
    mp: Optional[float] = None
    fps: float = 0.0
    message: str = ""                    # last pause/stop reason or error
    runtime_s: float = 0.0
    attacks: int = 0
    countdown_s: Optional[float] = None  # seconds left before the bot starts acting, else None
    hp_pots_estimated: Optional[int] = None
    mp_pots_estimated: Optional[int] = None
    hp_stock: Optional[int] = None
    mp_stock: Optional[int] = None
    observe_only: bool = False
    wallet_delta: Optional[int] = None
    net_per_hour: Optional[float] = None
    hotkeys_ok: bool = True              # False if F11/F12 aren't working (use the app's buttons)
