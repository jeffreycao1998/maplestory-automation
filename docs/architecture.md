# Architecture & contracts (v1)

Owner: main agent. Subagents read this; they propose changes rather than editing it.
Data types live in `src/maplebot/types.py`; settings in `src/maplebot/settings.py`.
Both are owned by the main agent.

## Tick loop (`src/maplebot/runner.py`, main agent)

```
ScreenCapture.grab() ─► Perceiver.perceive() ─► Supervisor.check() ─► Behavior.decide() ─► Controller.execute()
        Frame                 Perception              Verdict                Decision             key presses
                                   └──────────── RunLogger.log_tick() ◄──────────┘
```
Each tick, at `settings.behavior.loop_hz`:
1. Capture a `Frame` of the game client area.
2. `Perceiver.perceive(frame)` → `Perception`.
3. `Supervisor.check(perception, now)` → `Verdict`. PAUSE/STOP: the runner releases all
   keys and stops acting. Behavior can never override this.
4. `Behavior.decide(perception, now)` → `Decision`.
5. `Controller.execute(decision.action, now)`.
6. `RunLogger.log_tick(...)`; anomalies also save the frame.
7. `Supervisor.note_decision(decision, now)` so watchdogs can see stuck-jumps etc.

## Module interfaces

Signatures are binding. Internals are up to each owner.

### perception (`perception-engineer`)
```python
# maplebot/perception/capture.py
def enable_dpi_awareness() -> None            # no-op off Windows
def find_client_area(window_title: str) -> Box   # raises WindowNotFound
class WindowNotFound(RuntimeError)
class ScreenCapture:
    def __init__(self, window_title: str)    # finds window on init
    def grab(self) -> Frame                   # re-finds window if it moved
    def close(self) -> None

# maplebot/perception/perceiver.py   (re-exported from maplebot.perception)
class Perceiver:
    def __init__(self, settings: Settings, mob_templates: list[Path], player_template: Path | None)
    def perceive(self, frame: Frame) -> Perception
    def update_settings(self, settings: Settings) -> None

# maplebot/perception/calib.py  (helpers the setup wizard uses)
def minimap_player_position(image, minimap_box, hsv_low, hsv_high) -> PointF | None
def hsv_range_from_sample(image, x: int, y: int) -> tuple[list[int], list[int]]   # click on your dot
def bar_fill(image, box: Box) -> float | None           # HP/MP bar fill 0..1
```

### controls (`controls-engineer`)
```python
# maplebot/controls/backends.py
class InputBackend(Protocol):
    def key_down(self, key: str) -> None
    def key_up(self, key: str) -> None
class FakeBackend(InputBackend)               # records (t, key, down) events; .held set
class SendInputBackend(InputBackend)          # Windows, ctypes SendInput with scan codes
def default_backend() -> InputBackend          # SendInput on Windows, else raises
def available_keys() -> list[str]              # key names for the GUI
def normalize_key(name: str) -> str            # aliases -> canonical; ValueError if unknown

# maplebot/controls/controller.py   (re-exported from maplebot.controls)
class Controller:
    def __init__(self, settings: Settings, backend: InputBackend)
    def execute(self, action: Action, now: float) -> None
    def release_all(self) -> None              # idempotent, never raises
    facing: str                                # "left" | "right"
    walking: str | None
    def inhibit(self, reason: str = "") -> None  # block all presses + release; used by pause/stop
    def allow(self) -> None                    # lift the block (worker thread, on resume)
    inhibited: bool
    def close(self) -> None                    # inhibit + release + drop the atexit hook
    def __enter__ / __exit__                   # __exit__ releases all
```

### behavior (`behavior-designer`) and map model (`map-modeler`)
```python
# maplebot/mapmodel/__init__.py
class MapModel:
    @classmethod
    def from_settings(cls, settings: Settings) -> MapModel   # v1: single platform from patrol bounds
    patrol_left: float; patrol_right: float
    def side_of_patrol(self, x: float) -> str  # "left_out" | "inside" | "right_out"

# maplebot/behavior/__init__.py
class Behavior:
    def __init__(self, settings: Settings, map_model: MapModel)
    def decide(self, p: Perception, now: float) -> Decision
    def reset(self) -> None                    # called on resume after pause
    def update_settings(self, settings: Settings) -> None
```
States (v1): `PATROL`, `ENGAGE`, `RECOVER`, `STUCK`, `WAIT` (no usable perception),
`OFF_PLATFORM` (minimap y far from `calibration.patrol_y`; supervisor pauses after
`safety.platform_lost_s`).

Time base: the runner passes wall-clock time live, and `Frame.t` in replays, to
supervisor and behavior alike.

### supervisor (`safety-supervisor`)
```python
# maplebot/supervisor/watchdogs.py   (re-exported from maplebot.supervisor)
class Supervisor:
    def __init__(self, settings: Settings)
    def start(self, now: float) -> None
    def check(self, p: Perception, now: float) -> Verdict
    def note_decision(self, d: Decision, now: float) -> None
    def reset(self, now: float) -> None        # after the user resumes

# maplebot/supervisor/hotkeys.py
class Hotkeys:
    def __init__(self, pause_key: str, stop_key: str, on_pause: Callable, on_stop: Callable)
    def start(self) -> None                    # pynput listener thread; lazy import
    def stop(self) -> None

# maplebot/supervisor/runlog.py
class RunLogger:
    def __init__(self, logs_dir: Path, settings: Settings)   # creates logs/<timestamp>/
    run_dir: Path
    def log_tick(self, p: Perception, d: Decision | None, v: Verdict) -> None
    def save_frame(self, frame: Frame, tag: str) -> Path | None
    def close(self, summary: dict) -> None     # writes summary.json
```

### app (`app-engineer`)
```python
# maplebot/app/gui.py
def main() -> None                             # `python -m maplebot` lands here
```
Uses `BotRunner` (below), `Profile`, `ScreenCapture`, `perception.calib`.

### runner (main agent)
```python
# maplebot/runner.py
class BotRunner:
    def __init__(self, profile: Profile, settings: Settings,
                 backend: InputBackend | None = None,
                 capture: "ScreenCapture | None" = None,
                 on_status: Callable[[Status], None] | None = None,
                 on_preview: Callable[[np.ndarray], None] | None = None,
                 on_decision: Callable[[Decision], None] | None = None,   # replay/tests
                 use_hotkeys: bool = True,
                 clock=time.monotonic, sleep=time.sleep)
    def run(self) -> None        # blocking; call from a worker thread; raises RunnerError (friendly)
    def pause(self, reason: str = "Paused by you") -> None     # any thread; inhibits the controller
    def resume(self) -> None                                  # any thread; worker resets + allows
    def toggle_pause(self) -> None
    def stop(self, reason: str = "Stopped by you") -> None
    paused: bool; status: Status; run_dir: Path | None
```
`on_preview` receives an annotated BGR image (boxes, range, patrol lines) ~5×/second.

## TickLog (`logs/<run>/ticks.jsonl`)
`t, state, action, reason, verdict, (verdict_reason when not OK), player_map, player_screen,
n_mobs, target, hp, mp, flags, confidence` — anomalies (verdict ≠ OK, STUCK, low confidence) also save
`logs/<run>/frames/<t>_<tag>.png`. `summary.json` at the end.

## Platform notes
- The bot runs on Windows. The sandbox is Linux: everything except capture, real input,
  hotkeys and the GUI must import and test on Linux. Windows-only modules are imported
  lazily inside functions.
- The process must be DPI-aware, or capture coordinates are wrong on scaled displays.
- If the game runs elevated, the bot must too (Windows blocks input from a
  non-elevated process into an elevated one). The launcher self-elevates.
