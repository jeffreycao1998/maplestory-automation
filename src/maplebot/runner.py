"""
The tick loop: capture -> perceive -> supervise -> decide -> act -> log.

Owner: main agent. See docs/architecture.md.
BotRunner.run() blocks; the app calls it from a worker thread and talks to it with
pause() / resume() / toggle_pause() / stop(), which are safe to call from any thread
(GUI thread, hotkey listener thread).

Key-safety model: pause() and stop() put the Controller into an *inhibited* state under
its own lock. While inhibited, Controller.execute() presses nothing, so a tick that was
already in flight when the user hit F11/F12 can't re-press a key. Only the worker thread
lifts the inhibit, when it processes a resume.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import cv2
import numpy as np

from .settings import Profile, Settings
from .types import Action, Decision, Flag, Frame, Perception, Status, VerdictKind

log = logging.getLogger(__name__)

PREVIEW_INTERVAL_S = 0.2
PREVIEW_MAX_WIDTH = 640


class RunnerError(RuntimeError):
    """A problem the user can fix; the message is shown as-is in the app."""


class BotRunner:
    def __init__(self, profile: Profile, settings: Settings,
                 backend=None, capture=None,
                 on_status: Optional[Callable[[Status], None]] = None,
                 on_preview: Optional[Callable[[np.ndarray], None]] = None,
                 on_decision: Optional[Callable[[Decision], None]] = None,
                 use_hotkeys: bool = True,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.profile = profile
        self.settings = settings
        self._backend = backend
        self._capture = capture
        self._on_status = on_status
        self._on_preview = on_preview
        self._on_decision = on_decision
        self._use_hotkeys = use_hotkeys
        self._clock = clock
        self._sleep = sleep

        self._lock = threading.RLock()       # guards the flags below, status and _controller
        self._stop = threading.Event()
        self._paused = False                 # intended state (what the user / supervisor asked for)
        self._resume_pending = False         # worker must reset behaviour/supervisor and un-inhibit
        self._controller = None
        self._stop_reason = "Stopped by you"
        self._hotkey_warning = ""
        self.status = Status()
        self.run_dir = None

    # ---- control (any thread) -------------------------------------------
    def pause(self, reason: str = "Paused by you") -> None:
        with self._lock:
            self._paused = True
            self._resume_pending = False
            c = self._controller
        if c is not None:
            c.inhibit(reason)
        self._set_status(paused=True, message=reason)

    def resume(self) -> None:
        with self._lock:
            if not self._paused or self._stop.is_set():
                return
            self._paused = False
            self._resume_pending = True
        self._set_status(paused=False, message=self._running_message())

    def toggle_pause(self) -> None:
        with self._lock:
            paused = self._paused
        if paused:
            self.resume()
        else:
            self.pause()

    def stop(self, reason: str = "Stopped by you") -> None:
        with self._lock:
            if not self._stop.is_set():
                self._stop_reason = reason
            self._stop.set()
            c = self._controller
        if c is not None:
            c.inhibit(reason)
        self._set_status(message=reason)

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    # ---- main loop (worker thread) ----------------------------------------
    def run(self) -> None:
        from .behavior import Behavior
        from .controls import Controller, default_backend
        from .mapmodel import MapModel
        from .perception import Perceiver, enable_dpi_awareness
        from .supervisor import Hotkeys, HotkeysUnavailable, RunLogger, Supervisor

        s = self.settings
        problems = s.problems()
        if problems:
            raise RunnerError("Setup isn't finished: " + "; ".join(problems) + ". Open the setup wizard.")
        mob_templates = self.profile.mob_templates()
        if not mob_templates:
            raise RunnerError("No mob pictures saved yet. Open the setup wizard and box a mob.")
        try:
            map_model = MapModel.from_settings(s)
        except ValueError as e:
            raise RunnerError(str(e)) from e

        enable_dpi_awareness()
        self._set_status(running=True, paused=False, message="Starting…", hotkeys_ok=True)

        capture = self._capture
        own_capture = capture is None
        hotkeys = None
        controller = None
        logger = None
        ticks = 0
        attacks = 0
        t_start: Optional[float] = None
        stop_reason = None

        try:
            # Hotkeys first, so F12 works during the countdown too.
            if self._use_hotkeys:
                try:
                    hotkeys = Hotkeys(s.safety.pause_hotkey, s.safety.stop_hotkey,
                                      on_pause=self.toggle_pause, on_stop=self.stop)
                    hotkeys.start()
                except (HotkeysUnavailable, ValueError) as e:
                    hotkeys = None
                    log.warning("hotkeys unavailable: %s", e)
                    self._hotkey_warning = f"{s.safety.pause_hotkey.upper()}/{s.safety.stop_hotkey.upper()} " \
                                           f"hotkeys aren't working ({e}) — use the app's Pause/Stop buttons."
                    self._set_status(hotkeys_ok=False, message=self._hotkey_warning)

            # Countdown so the user can click into the game.
            end = self._clock() + max(0.0, s.safety.start_delay_s)
            while not self._stop.is_set() and self._clock() < end:
                left = max(0.0, end - self._clock())
                self._set_status(countdown_s=left,
                                 message=f"Starting in {left:.0f}… click into the game")
                self._sleep(0.1)
            self._set_status(countdown_s=None)
            if self._stop.is_set():
                stop_reason = self._stop_reason
                return

            if own_capture:
                from .perception import ScreenCapture, WindowNotFound
                try:
                    capture = ScreenCapture(s.calibration.window_title)
                except WindowNotFound as e:
                    raise RunnerError(str(e)) from e

            backend = self._backend
            if backend is None:
                try:
                    backend = default_backend()
                except RuntimeError as e:
                    raise RunnerError(str(e)) from e

            perceiver = Perceiver(s, mob_templates, self.profile.player_template())
            behavior = Behavior(s, map_model)
            supervisor = Supervisor(s)
            controller = Controller(s, backend)
            try:
                logger = RunLogger(self.profile.logs_dir, s)
            except OSError as e:
                raise RunnerError(f"Can't write run logs to {self.profile.logs_dir}: {e}") from e
            self.run_dir = logger.run_dir

            with self._lock:
                self._controller = controller
                already_paused = self._paused
                if self._stop.is_set():
                    controller.inhibit(self._stop_reason)
            if already_paused:          # user pressed pause during the countdown
                controller.inhibit("Paused by you")

            last_preview = 0.0
            fps = 0.0
            period = 1.0 / max(1.0, s.behavior.loop_hz)
            self._set_status(message=self._running_message())

            while not self._stop.is_set():
                tick_t0 = self._clock()

                with self._lock:
                    resume_now = self._resume_pending
                    self._resume_pending = False
                    paused = self._paused
                if resume_now and not paused:
                    behavior.reset()
                    supervisor.reset(tick_t0 if own_capture else (t_start or 0.0))
                    controller.allow()

                if paused:
                    if t_start is not None and own_capture:
                        self._set_status(runtime_s=tick_t0 - t_start)
                    self._sleep(0.1)
                    continue

                frame = self._grab(capture)
                if frame is None:          # recording ended (replay / tests)
                    stop_reason = "Recording ended"
                    break
                # One time base for everything: wall clock live, frame time in replays.
                now = self._clock() if own_capture else frame.t
                if t_start is None:
                    t_start = now
                    supervisor.start(now)
                elif resume_now and not own_capture:
                    supervisor.reset(now)

                p = perceiver.perceive(frame)
                verdict = supervisor.check(p, now)

                if verdict.kind is not VerdictKind.OK:
                    controller.inhibit(verdict.reason)
                    logger.log_tick(p, None, verdict)
                    logger.save_frame(frame, verdict.kind.value)
                    if verdict.kind is VerdictKind.STOP:
                        stop_reason = verdict.reason
                        break
                    self.pause(verdict.reason)
                    continue

                d = behavior.decide(p, now)
                controller.execute(d.action, now)   # no-op if pause/stop raced in
                supervisor.note_decision(d, now)
                logger.log_tick(p, d, verdict)
                if d.state == "STUCK":
                    logger.save_frame(frame, "stuck")
                elif Flag.LOW_CONFIDENCE in p.flags:
                    logger.save_frame(frame, "lowconf")
                if self._on_decision:
                    self._on_decision(d)

                ticks += 1
                if d.action is Action.ATTACK:
                    attacks += 1

                elapsed = self._clock() - tick_t0
                inst = 1.0 / max(elapsed, period, 1e-3)
                fps = inst if fps == 0 else fps * 0.9 + inst * 0.1
                self._set_status(state=d.state, action=d.action.value, n_mobs=len(p.mobs),
                                 map_x=p.player_map[0] if p.player_map else None,
                                 hp=p.hp, mp=p.mp, fps=fps, attacks=attacks,
                                 runtime_s=now - t_start)

                if (self._on_preview and s.debug_preview
                        and (tick_t0 - last_preview) >= PREVIEW_INTERVAL_S):
                    last_preview = tick_t0
                    try:
                        self._on_preview(annotate(frame, p, d, s))
                    except Exception:       # preview must never kill the bot
                        log.exception("preview failed")

                if own_capture:
                    remaining = period - (self._clock() - tick_t0)
                    if remaining > 0:
                        self._sleep(remaining)

            if stop_reason is None:
                stop_reason = self._stop_reason
        except BaseException as e:
            if stop_reason is None:
                stop_reason = str(e) if isinstance(e, RunnerError) else f"Stopped by an error: {e}"
            raise
        finally:
            if controller is not None:
                controller.inhibit("run ended")
                controller.release_all()
                try:
                    controller.close()
                except Exception:
                    log.exception("controller close failed")
            if hotkeys is not None:
                hotkeys.stop()
            if logger is not None:
                runtime = 0.0
                if t_start is not None:
                    runtime = (self._clock() - t_start) if own_capture else max(0.0, self.status.runtime_s)
                logger.close({"stop_reason": stop_reason, "ticks": ticks, "attacks": attacks,
                              "runtime_s": round(runtime, 1)})
            if own_capture and capture is not None:
                try:
                    capture.close()
                except Exception:
                    log.exception("capture close failed")
            with self._lock:
                self._controller = None
            self._set_status(running=False, paused=False, countdown_s=None,
                             message=stop_reason or self._stop_reason)

    # ---- helpers ------------------------------------------------------------
    def _running_message(self) -> str:
        return "Running" + (f" — {self._hotkey_warning}" if self._hotkey_warning else "")

    def _grab(self, capture) -> Optional[Frame]:
        try:
            return capture.grab()
        except StopIteration:        # FolderCapture/SequenceCapture EndOfRecording
            return None

    def _set_status(self, **changes) -> None:
        with self._lock:
            for k, v in changes.items():
                setattr(self.status, k, v)
            snapshot = Status(**vars(self.status))
        if self._on_status:
            try:
                self._on_status(snapshot)
            except Exception:
                log.exception("status callback failed")


# ---- preview drawing -----------------------------------------------------
def annotate(frame: Frame, p: Perception, d: Optional[Decision], s: Settings) -> np.ndarray:
    """What the bot sees: you + attack range (green; orange if your position is a guess),
    mobs (orange), target (red), minimap box (cyan), patrol edges (magenta)."""
    img = frame.image.copy()
    if p.player_screen:
        px, py = p.player_screen
        colour = (0, 200, 255) if p.player_screen_estimated else (0, 255, 0)
        cv2.circle(img, (px, py), 8, colour, 2)
        r, v = s.skill.range_px, s.skill.vertical_tolerance_px
        cv2.rectangle(img, (px - r, py - v), (px + r, py + v), colour, 1)
    target = d.target if d else None
    for m in p.mobs:
        is_target = target is not None and (m.x, m.y) == (target.x, target.y)
        cv2.rectangle(img, (m.x - m.w // 2, m.y - m.h // 2), (m.x + m.w // 2, m.y + m.h // 2),
                      (0, 0, 255) if is_target else (0, 165, 255), 2)
    mm = s.calibration.minimap_box
    if mm:
        x, y, w, h = mm
        cv2.rectangle(img, (x, y), (x + w, y + h), (255, 255, 0), 1)
        for frac in (s.calibration.patrol_left, s.calibration.patrol_right):
            lx = x + int(frac * w)
            cv2.line(img, (lx, y), (lx, y + h), (255, 0, 255), 1)
        if s.calibration.patrol_y is not None:
            ly = y + int(s.calibration.patrol_y * h)
            cv2.line(img, (x + int(s.calibration.patrol_left * w), ly),
                     (x + int(s.calibration.patrol_right * w), ly), (255, 0, 255), 1)
        if p.player_map:
            cv2.circle(img, (x + int(p.player_map[0] * w), y + int(p.player_map[1] * h)), 4, (0, 255, 0), 1)
    label = f"{d.state if d else '-'} | {d.action.value if d else '-'} | mobs {len(p.mobs)}"
    if d and d.reason:
        label += f" | {d.reason}"
    cv2.putText(img, label, (10, img.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
    cv2.putText(img, label, (10, img.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    if img.shape[1] > PREVIEW_MAX_WIDTH:
        scale = PREVIEW_MAX_WIDTH / img.shape[1]
        img = cv2.resize(img, (PREVIEW_MAX_WIDTH, int(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    return img
