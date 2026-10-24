"""Decision logic: picks one Action per tick. Owner: behavior-designer."""
from maplebot.behavior.machine import (
    Behavior,
    DEAD_ZONE_PX,
    LINGER_S,
    MIN_CONFIDENCE,
    STUCK_EPS,
    TARGET_SWITCH_MARGIN_PX,
    TURN_HOLD_S,
)

__all__ = ["Behavior", "DEAD_ZONE_PX", "LINGER_S", "MIN_CONFIDENCE", "STUCK_EPS",
           "TARGET_SWITCH_MARGIN_PX", "TURN_HOLD_S"]
