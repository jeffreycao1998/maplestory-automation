"""
Controller: turns one ``Action`` per tick into key down/up events.

It executes, it never decides. Timing that belongs to behaviour (skill cooldown, when
to turn, when to drink) stays in behaviour; the Controller owns only how long a key is
held for each action:

    ATTACK      hold settings.skill.hold_s      (measure: the shortest hold that still
                                                 casts every time in game; 0.08-0.12 s)
    FACE_*      tap  settings.behavior.turn_tap_s (measure: the shortest tap that turns
                                                 the character without moving it)
    JUMP        tap  settings.behavior.jump_tap_s if present, else 0.05 s
    USE_*_POT   tap  settings.potions.tap_s if present, else 0.05 s

Safety guarantees
- Every key this Controller presses is tracked in ``held_keys``. A key is tracked
  *before* the OS call, so even a half-failed press gets a release later.
- Taps use try/finally; if anything inside ``execute`` raises, every key that was not
  held before the call is released before the exception propagates.
- ``release_all()`` is idempotent and never raises; a failed release stays tracked
  and is retried next time.
- Released on ``__exit__``, ``close()`` and interpreter exit (atexit, weakref-safe).
- One RLock serialises all key operations (runner thread executes, the GUI or hotkey
  thread may call release_all at any time).
- ``inhibit()`` (pause/stop) sets a flag and releases everything under that lock;
  ``execute()`` checks the flag inside the same lock acquisition that presses keys, so
  a tick already in flight cannot re-press a key after pause/stop. ``allow()`` undoes it.
  A tap that is mid-hold delays inhibit() by at most its hold time (~skill.hold_s).
"""
from __future__ import annotations

import atexit
import logging
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Set

from maplebot.settings import Settings
from maplebot.types import Action

from .backends import InputBackend, normalize_key

log = logging.getLogger(__name__)

DEFAULT_JUMP_TAP_S = 0.05
DEFAULT_POT_TAP_S = 0.05


@dataclass(frozen=True)
class _Binds:
    attack: str
    jump: str
    left: str
    right: str
    hp_potion: Optional[str]
    mp_potion: Optional[str]


def _resolve_keys(settings: Settings) -> _Binds:
    k = settings.keys
    out: Dict[str, Optional[str]] = {}
    for field in ("attack", "jump", "left", "right"):
        try:
            out[field] = normalize_key(getattr(k, field))
        except ValueError as e:
            raise ValueError(f"{field.replace('_', ' ').capitalize()} key: {e}") from None
    for field in ("hp_potion", "mp_potion"):
        raw = getattr(k, field)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            out[field] = None
            continue
        try:
            out[field] = normalize_key(raw)
        except ValueError as e:
            label = "HP potion" if field == "hp_potion" else "MP potion"
            raise ValueError(f"{label} key: {e}") from None
    if out["left"] == out["right"]:
        raise ValueError("Left and right keys must be different")
    used: Dict[str, str] = {}
    for field, key in out.items():
        if key is None:
            continue
        if key in used:
            log.warning("Key %r is bound to both %s and %s", key, used[key], field)
        used.setdefault(key, field)
    return _Binds(**out)  # type: ignore[arg-type]


def _atexit_release(ref: "weakref.ReferenceType[Controller]") -> None:
    ctrl = ref()
    if ctrl is not None:
        ctrl.release_all()


class Controller:
    def __init__(self, settings: Settings, backend: InputBackend,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._backend = backend
        self._sleep = sleep
        self._lock = threading.RLock()
        self._held: Set[str] = set()
        self.facing: str = "right"        # contract: "left" | "right"
        self._facing_known = False        # first FACE_* always taps (true facing unknown)
        self.walking: Optional[str] = None
        self.last_action: Optional[Action] = None
        self.last_action_t: Optional[float] = None
        self._inhibited = False
        self._inhibit_reason = ""
        self._settings = settings
        self._binds = _resolve_keys(settings)
        self._atexit_cb = lambda ref=weakref.ref(self): _atexit_release(ref)
        atexit.register(self._atexit_cb)
        self._closed = False

    # ---- public -----------------------------------------------------------
    @property
    def held_keys(self) -> frozenset:
        """Keys this Controller believes are down (includes failed releases)."""
        with self._lock:
            return frozenset(self._held)

    @property
    def binds(self) -> _Binds:
        return self._binds

    def update_settings(self, settings: Settings) -> None:
        """Adopt new settings. If keybinds changed, everything is released first.

        Raises ValueError (and keeps the old settings) if a key name is invalid.
        """
        binds = _resolve_keys(settings)
        with self._lock:
            if binds != self._binds:
                self.release_all()
            self._settings = settings
            self._binds = binds

    @property
    def inhibited(self) -> bool:
        """True while pause/stop is in effect: execute() presses nothing."""
        return self._inhibited

    def inhibit(self, reason: str = "") -> None:
        """Block all further key presses and release everything held.

        Safe to call from any thread (runner pause/stop, GUI, hotkeys). Once this
        returns, no key is held and every later ``execute()`` is a no-op until
        ``allow()``. If another thread is mid-tap, this waits on the lock until the tap
        finishes: at most ``max(skill.hold_s, behavior.turn_tap_s, jump/potion tap)``,
        i.e. about ``skill.hold_s`` (0.10 s by default). Idempotent; never raises.
        """
        with self._lock:
            if not self._inhibited:
                log.info("Key input inhibited%s", f": {reason}" if reason else "")
            self._inhibited = True
            self._inhibit_reason = reason
            self.release_all()

    def allow(self) -> None:
        """Re-enable execute() after inhibit() (e.g. when the user resumes)."""
        with self._lock:
            self._inhibited = False
            self._inhibit_reason = ""

    def execute(self, action: Action, now: float) -> None:
        """Perform one Action. A no-op (zero key events) while inhibited.

        The inhibited check and the key operations share one lock acquisition, so a
        concurrent inhibit() either runs fully before (nothing is pressed) or waits
        until this call returns and then releases what it left held (a walk key).
        """
        action = Action(action)
        with self._lock:
            if self._inhibited:
                return
            before = set(self._held)
            try:
                self._dispatch(action)
            except BaseException:
                # Never leave a key down that this call pressed.
                for key in list(self._held - before):
                    self._safe_up(key)
                if self.walking is not None and self._dir_key(self.walking) not in self._held:
                    self.walking = None
                raise
            finally:
                self.last_action = action
                self.last_action_t = now

    def release_all(self) -> None:
        """Release every tracked key. Idempotent; never raises."""
        try:
            with self._lock:
                for key in sorted(self._held):
                    self._safe_up(key)
                self.walking = None
        except BaseException:   # pragma: no cover - last-resort guard
            log.exception("release_all failed unexpectedly")

    def close(self) -> None:
        """Inhibit (release everything, block further presses) and drop the atexit hook."""
        self.inhibit("controller closed")
        if not self._closed:
            self._closed = True
            try:
                atexit.unregister(self._atexit_cb)
            except Exception:   # pragma: no cover
                pass

    def __enter__(self) -> "Controller":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release_all()

    # ---- action handlers ----------------------------------------------------
    def _dispatch(self, action: Action) -> None:
        b = self._binds
        if action is Action.IDLE:
            return
        if action is Action.WALK_LEFT:
            self._walk("left")
        elif action is Action.WALK_RIGHT:
            self._walk("right")
        elif action is Action.STOP_WALK:
            self._stop_walk()
        elif action is Action.FACE_LEFT:
            self._face("left")
        elif action is Action.FACE_RIGHT:
            self._face("right")
        elif action is Action.ATTACK:
            self._stop_walk()
            self._tap(b.attack, self._settings.skill.hold_s)
        elif action is Action.JUMP:
            self._tap(b.jump, self._tap_s(self._settings.behavior, "jump_tap_s",
                                          DEFAULT_JUMP_TAP_S))
        elif action is Action.USE_HP_POT:
            if b.hp_potion:
                self._tap(b.hp_potion, self._pot_tap_s())
        elif action is Action.USE_MP_POT:
            if b.mp_potion:
                self._tap(b.mp_potion, self._pot_tap_s())
        else:   # pragma: no cover - new Action added without a handler
            raise ValueError(f"Controller has no handler for {action!r}")

    def _walk(self, direction: str) -> None:
        if self.walking == direction and self._dir_key(direction) in self._held:
            return
        other = "right" if direction == "left" else "left"
        other_key = self._dir_key(other)
        if other_key in self._held:
            self._up(other_key)
        self.walking = None
        self._down(self._dir_key(direction))
        self.walking = direction
        self.facing = direction
        self._facing_known = True

    def _stop_walk(self) -> None:
        for d in ("left", "right"):
            key = self._dir_key(d)
            if key in self._held:
                self._up(key)
        self.walking = None

    def _face(self, direction: str) -> None:
        self._stop_walk()
        if self._facing_known and self.facing == direction:
            return
        self._tap(self._dir_key(direction), self._settings.behavior.turn_tap_s)
        self.facing = direction
        self._facing_known = True

    # ---- key primitives ------------------------------------------------------
    def _dir_key(self, direction: str) -> str:
        return self._binds.left if direction == "left" else self._binds.right

    def _down(self, key: str) -> None:
        self._held.add(key)          # track first: a half-failed press still gets released
        self._backend.key_down(key)

    def _up(self, key: str) -> None:
        self._backend.key_up(key)
        self._held.discard(key)      # only forget it once the release succeeded

    def _safe_up(self, key: str) -> None:
        try:
            self._up(key)
        except Exception:
            log.exception("Failed to release key %r (will retry on next release_all)", key)

    def _tap(self, key: str, hold_s: float) -> None:
        if key in self._held:
            # Already down (e.g. jump bound to a held key): don't release what we didn't press.
            return
        try:
            self._down(key)
            if hold_s > 0:
                self._sleep(hold_s)
        finally:
            self._up(key)

    @staticmethod
    def _tap_s(section, name: str, default: float) -> float:
        value = getattr(section, name, None)
        return float(value) if value is not None else default

    def _pot_tap_s(self) -> float:
        return self._tap_s(self._settings.potions, "tap_s", DEFAULT_POT_TAP_S)
