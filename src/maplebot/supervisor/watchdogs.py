"""
Watchdogs that force the bot to PAUSE or STOP.

The Supervisor reads only `Perception`, `Decision` history (for stuck-jumps), the
map model built from settings, and the clock, so it can be driven by hand-built
sequences and replays. All thresholds come from
`settings.safety`. Every verdict carries a specific, friendly reason that the user
reads in the app.

Rules:
- Once a PAUSE or STOP is returned it is *sticky*: every later `check()` returns it
  again until `reset()` (user resumed) or `start()` (new run).
- STOP outranks PAUSE: if both are due on the same check, STOP is returned, and a
  STOP is never downgraded to PAUSE. (A latched PAUSE can only become STOP if
  `check()` is still being called; the runner does not call it while paused, so in
  practice a pause stays a pause until the user resumes or stops.)
- If the supervisor itself fails, it returns STOP (default to stopping).
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Deque, Optional

from maplebot.mapmodel import MapModel
from maplebot.settings import Settings
from maplebot.types import Action, Decision, Flag, Perception, Verdict, VerdictKind

log = logging.getLogger(__name__)

# Defaults for values that may later become Settings.safety fields; read through
# getattr so promoting them to settings needs no code change here.
DEFAULT_STUCK_WINDOW_S = 60.0     # stuck-jumps are counted over this sliding window
DEFAULT_HP_CRITICAL_HOLD_S = 1.0  # HP must stay critical this long before STOP

_OK = Verdict(VerdictKind.OK)
_RANK = {VerdictKind.OK: 0, VerdictKind.PAUSE: 1, VerdictKind.STOP: 2}


def _fmt_duration(seconds: float) -> str:
    """3 -> '3 s', 90 -> '1 min 30 s', 180 -> '3 min', 7200 -> '2 h'."""
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        m, rem = divmod(s, 60)
        return f"{m} min" if rem == 0 else f"{m} min {rem} s"
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"{h} h" if m == 0 else f"{h} h {m} min"


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _has_low_confidence(flags) -> bool:
    try:
        return Flag.LOW_CONFIDENCE in flags or Flag.LOW_CONFIDENCE.value in flags
    except TypeError:
        return False


class _Since:
    """Tracks since when a condition has been continuously true."""

    __slots__ = ("since",)

    def __init__(self) -> None:
        self.since: Optional[float] = None

    def update(self, active: bool, now: float) -> float:
        """Returns how long the condition has held (0 if not active)."""
        if not active:
            self.since = None
            return 0.0
        if self.since is None:
            self.since = now
        return now - self.since

    def held(self, now: float) -> float:
        return 0.0 if self.since is None else now - self.since

    def clear(self) -> None:
        self.since = None


class Supervisor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._start_t: Optional[float] = None
        self._latched: Verdict = _OK
        self._player_lost = _Since()
        self._no_mobs = _Since()
        self._hp_low = _Since()
        self._low_conf = _Since()
        self._hp_unreadable = _Since()
        self._off_platform = _Since()     # player_map y outside the training platform band
        self._map: Optional[MapModel] = None
        self._map_error: str = ""
        self._build_map()
        self._stuck_jumps: Deque[float] = deque()

    # ---- lifecycle ------------------------------------------------------
    def start(self, now: float) -> None:
        """New run: clears everything, including the run clock."""
        self._start_t = now
        self.reset(now)

    def reset(self, now: float) -> None:
        """After the user resumes: clears the latched verdict and all condition timers.

        The run clock (for max runtime) keeps going.
        """
        if self._start_t is None:
            self._start_t = now
        self._latched = _OK
        for timer in (self._player_lost, self._no_mobs, self._hp_low, self._low_conf,
                      self._hp_unreadable, self._off_platform):
            timer.clear()
        self._stuck_jumps.clear()

    def update_settings(self, settings: Settings) -> None:
        """New thresholds apply from the next check; running timers are kept."""
        self.settings = settings
        self._build_map()

    def _build_map(self) -> None:
        """The platform watchdog is off when calibration.patrol_y is None."""
        self._map, self._map_error = None, ""
        if getattr(self.settings.calibration, "patrol_y", None) is None:
            return
        try:
            self._map = MapModel.from_settings(self.settings)
        except Exception as e:  # invalid patrol setup: refuse to run blind (see _check)
            log.warning("Supervisor couldn't build the map model: %s", e)
            self._map_error = str(e) or type(e).__name__

    @property
    def stuck_window_s(self) -> float:
        return float(getattr(self.settings.safety, "stuck_window_s", DEFAULT_STUCK_WINDOW_S))

    @property
    def hp_critical_hold_s(self) -> float:
        return float(getattr(self.settings.safety, "hp_critical_hold_s",
                             DEFAULT_HP_CRITICAL_HOLD_S))

    def elapsed(self, now: float) -> float:
        return 0.0 if self._start_t is None else max(0.0, now - self._start_t)

    @property
    def verdict(self) -> Verdict:
        """The currently latched verdict (OK if none)."""
        return self._latched

    # ---- inputs ---------------------------------------------------------
    def note_decision(self, d: Decision, now: float) -> None:
        try:
            if d is None:
                return
            state = str(d.state).upper()
            if state == "STUCK" and d.action == Action.JUMP:
                self._stuck_jumps.append(now)
        except Exception:  # never let bookkeeping crash the loop
            log.exception("Supervisor.note_decision failed")

    def check(self, p: Perception, now: float) -> Verdict:
        try:
            return self._check(p, now)
        except Exception as e:
            log.exception("Supervisor.check failed")
            v = Verdict(VerdictKind.STOP,
                        f"The safety checker hit an internal error ({type(e).__name__}: {e}) "
                        "— stopping to be safe. Please send the run log to get this fixed.")
            self._latched = v
            return v

    # ---- internals ------------------------------------------------------
    def _check(self, p: Perception, now: float) -> Verdict:
        if self._start_t is None:
            self.start(now)
        s = self.settings.safety

        # Update every timer each tick, even when latched, so a STOP condition
        # that appears while paused can still upgrade the verdict.
        lost_for = self._player_lost.update(p.player_map is None, now)
        no_mobs_for = self._no_mobs.update(len(p.mobs or ()) == 0, now)
        low_conf_for = self._low_conf.update(_has_low_confidence(p.flags or ()), now)

        # HP: a readable value at/above the threshold clears the timer; an unreadable
        # (None) tick neither clears nor confirms it, so a flickery bar can't hide low HP.
        hp = p.hp
        if hp is not None:
            hp_low_for = self._hp_low.update(hp < s.hp_critical, now)
        else:
            hp_low_for = 0.0
        hp_unreadable_for = self._hp_unreadable.update(hp is None, now)
        # Platform: computed from the minimap dot, independent of behaviour state.
        # Off → timer runs; on → timer clears; unknown position → neither (player-lost
        # covers a missing dot).
        off_platform_for = 0.0
        if self._map is not None and p.player_map is not None:
            x, y = p.player_map
            off_platform_for = self._off_platform.update(
                not self._map.on_platform(float(x), float(y)), now)
        elif self._map is not None:
            off_platform_for = self._off_platform.held(now)
        hold_s = self.hp_critical_hold_s
        window_s = self.stuck_window_s

        cutoff = now - window_s
        while self._stuck_jumps and self._stuck_jumps[0] < cutoff:
            self._stuck_jumps.popleft()
        n_stuck = len(self._stuck_jumps)

        found: list[Verdict] = []

        # -- STOP conditions
        if hp is not None and hp < s.hp_critical and hp_low_for >= hold_s:
            found.append(Verdict(
                VerdictKind.STOP,
                f"HP has been below {_pct(s.hp_critical)} for over "
                f"{_fmt_duration(hold_s)} (now {_pct(hp)}) — stopping so your "
                "character doesn't die. Check your HP potions and potion key, and that the "
                "HP bar is calibrated correctly."))
        if s.max_runtime_min and s.max_runtime_min > 0:
            limit_s = s.max_runtime_min * 60.0
            if self.elapsed(now) >= limit_s:
                found.append(Verdict(
                    VerdictKind.STOP,
                    f"Reached your maximum run time of {_fmt_duration(limit_s)} — stopping "
                    "as planned. You can change this limit in the safety settings."))

        # -- PAUSE conditions
        if hp_unreadable_for >= s.hp_unreadable_pause_s:
            found.append(Verdict(
                VerdictKind.PAUSE,
                f"Can't read your HP bar for {_fmt_duration(s.hp_unreadable_pause_s)} — the "
                "bot can't tell if your character is in danger. Check nothing covers the HP "
                "bar, or redo that setup step."))
        if self._map_error:
            found.append(Verdict(
                VerdictKind.PAUSE,
                f"The training platform settings can't be used ({self._map_error}) — the "
                "bot can't tell if your character fell off. Redo the patrol setup step."))
        if self._off_platform.since is not None and off_platform_for >= s.platform_lost_s:
            found.append(Verdict(
                VerdictKind.PAUSE,
                "Your character isn't on the training platform any more — it may have "
                "fallen to another bridge. Move it back and resume."))
        if lost_for >= s.player_lost_s:
            found.append(Verdict(
                VerdictKind.PAUSE,
                f"Can't see your character on the minimap for {_fmt_duration(s.player_lost_s)} "
                "— the map may have changed, a window may be covering the game, or the "
                "minimap is closed."))
        if low_conf_for >= s.player_lost_s:
            found.append(Verdict(
                VerdictKind.PAUSE,
                f"Detection has been unsure for {_fmt_duration(s.player_lost_s)} — the game "
                "view may be covered, dimmed by a dialog, or showing something the bot "
                "doesn't recognise. Pausing until you check."))
        if s.stuck_jumps_limit > 0 and n_stuck >= s.stuck_jumps_limit:
            found.append(Verdict(
                VerdictKind.PAUSE,
                f"Your character got stuck {n_stuck} times in the last "
                f"{_fmt_duration(window_s)} and jumping didn't free it — it may be "
                "caught on a rope, ladder or wall. Move it back into the patrol area and "
                "resume."))
        if no_mobs_for >= s.no_mobs_pause_s:
            found.append(Verdict(
                VerdictKind.PAUSE,
                f"No monsters seen for {_fmt_duration(s.no_mobs_pause_s)} — you may be on a "
                "different map, the monsters may look different from your saved pictures, "
                "or something is covering the game."))

        for v in found:  # first found of the highest rank wins
            if _RANK[v.kind] > _RANK[self._latched.kind]:
                self._latched = v
        return self._latched
