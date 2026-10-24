"""
User settings and the profile folder.

Owner: main agent. One Settings object is passed to every part.
Everything the user sets up lives in the profile folder (git-ignored):

    profile/
      settings.yaml        # everything below, written by the setup wizard / settings tab
      templates/
        mob_*.png          # crops of mobs (any number)
        player.png         # optional crop of your name tag
    logs/<run>/            # run logs (repo root, git-ignored)
"""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Keys:
    attack: str = "ctrl"
    jump: str = "alt"
    left: str = "left"
    right: str = "right"
    hp_potion: str = ""        # "" = not used
    mp_potion: str = ""


@dataclass
class Skill:
    hold_s: float = 0.10       # how long to hold the attack key
    cooldown_s: float = 0.65   # minimum gap between casts (cast animation length)
    range_px: int = 350        # horizontal reach on screen
    vertical_tolerance_px: int = 60   # ignore mobs further above/below than this


@dataclass
class Calibration:
    window_title: str = "MapleStory"
    minimap_box: Optional[List[int]] = None   # [x, y, w, h] in client pixels
    hp_bar_box: Optional[List[int]] = None
    mp_bar_box: Optional[List[int]] = None
    patrol_left: float = 0.30                 # minimap x fraction
    patrol_right: float = 0.70
    patrol_y: Optional[float] = None          # minimap y of the bridge ENDS (set with the edges)
    # On-platform band is [patrol_y - above, patrol_y + below]. Bridges sag downward
    # (~0.06-0.07 of minimap height on The Tree That Grew II) and you only ever fall down;
    # the next bridge down is ~0.117 below the ends.
    patrol_y_tolerance: float = 0.10          # how far BELOW patrol_y still counts as on the bridge
    patrol_y_tolerance_above: float = 0.03    # how far ABOVE patrol_y still counts as on the bridge
    player_dot_hsv_low: List[int] = field(default_factory=lambda: [20, 150, 180])
    player_dot_hsv_high: List[int] = field(default_factory=lambda: [35, 255, 255])


@dataclass
class Detection:
    mob_threshold: float = 0.70
    player_threshold: float = 0.75
    mirror_templates: bool = True
    downscale: float = 0.5     # match on a smaller image for speed (1.0 = full size)
    player_tag_offset_px: int = 63   # how far above your name tag your body centre is


@dataclass
class Potions:
    hp_below: float = 0.50
    mp_below: float = 0.30
    cooldown_s: float = 1.5
    tap_s: float = 0.05


@dataclass
class Behavior:
    loop_hz: float = 15.0
    stuck_s: float = 2.5       # walking but minimap x not changing -> jump
    turn_tap_s: float = 0.05
    jump_tap_s: float = 0.05


@dataclass
class Safety:
    pause_hotkey: str = "f11"
    stop_hotkey: str = "f12"
    start_delay_s: float = 3.0         # time to click into the game after pressing Start
    player_lost_s: float = 3.0         # minimap dot missing this long -> pause
    no_mobs_pause_s: float = 180.0     # no mobs seen this long -> pause
    stuck_jumps_limit: int = 6         # this many stuck-jumps within 60 s -> pause
    hp_critical: float = 0.15          # HP below this (after potions) -> stop
    hp_unreadable_pause_s: float = 10.0  # HP bar unreadable this long -> pause (can't detect death)
    platform_lost_s: float = 2.0       # off the training platform (minimap y) this long -> pause
    max_runtime_min: float = 120.0     # 0 = unlimited
    save_anomaly_frames: bool = True


@dataclass
class Settings:
    keys: Keys = field(default_factory=Keys)
    skill: Skill = field(default_factory=Skill)
    calibration: Calibration = field(default_factory=Calibration)
    detection: Detection = field(default_factory=Detection)
    potions: Potions = field(default_factory=Potions)
    behavior: Behavior = field(default_factory=Behavior)
    safety: Safety = field(default_factory=Safety)
    debug_preview: bool = True

    # ---- helpers --------------------------------------------------------
    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Settings":
        s = cls()
        for section in dataclasses.fields(cls):
            value = (data or {}).get(section.name)
            if value is None:
                continue
            current = getattr(s, section.name)
            if dataclasses.is_dataclass(current):
                known = {f.name for f in dataclasses.fields(current)}
                setattr(s, section.name,
                        dataclasses.replace(current, **{k: v for k, v in value.items() if k in known}))
            else:
                setattr(s, section.name, value)
        return s

    def problems(self) -> List[str]:
        """Things that must be set before the bot can start (shown in the GUI)."""
        out = []
        if not self.calibration.minimap_box:
            out.append("Minimap not calibrated")
        if not (0.0 <= self.calibration.patrol_left < self.calibration.patrol_right <= 1.0):
            out.append("Patrol left edge must be left of the right edge")
        if not self.calibration.hp_bar_box:
            out.append("HP bar not calibrated (needed so the bot stops before your character dies)")
        if not self.keys.attack:
            out.append("Attack key not set")
        try:
            from .supervisor.hotkeys import parse_key
            for label, key in (("Pause", self.safety.pause_hotkey), ("Stop", self.safety.stop_hotkey)):
                parse_key(key)
            if parse_key(self.safety.pause_hotkey) == parse_key(self.safety.stop_hotkey):
                out.append("Pause and Stop hotkeys must be different")
        except ImportError:
            pass
        except ValueError as e:
            out.append(f"Hotkey problem: {e}")
        try:
            from .controls import normalize_key
        except Exception:      # controls unavailable: skip key validation
            return out
        for name in ("attack", "jump", "left", "right", "hp_potion", "mp_potion"):
            value = getattr(self.keys, name)
            if not value:
                continue
            try:
                normalize_key(value)
            except ValueError:
                out.append(f"{name.replace('_', ' ').capitalize()} key '{value}' isn't a key the bot knows")
        return out


@dataclass
class Profile:
    root: Path

    @classmethod
    def default(cls) -> "Profile":
        return cls(Path(os.environ.get("MAPLEBOT_PROFILE", REPO_ROOT / "profile")))

    @property
    def settings_path(self) -> Path:
        return self.root / "settings.yaml"

    @property
    def templates_dir(self) -> Path:
        return self.root / "templates"

    @property
    def logs_dir(self) -> Path:
        return Path(os.environ.get("MAPLEBOT_LOGS", REPO_ROOT / "logs"))

    def mob_templates(self) -> List[Path]:
        return sorted(self.templates_dir.glob("mob_*.png"))

    def player_template(self) -> Optional[Path]:
        p = self.templates_dir / "player.png"
        return p if p.exists() else None

    def ensure(self) -> None:
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> Settings:
        if self.settings_path.exists():
            with open(self.settings_path, "r", encoding="utf-8") as f:
                return Settings.from_dict(yaml.safe_load(f))
        return Settings()

    def save(self, settings: Settings) -> None:
        self.ensure()
        tmp = self.settings_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(settings.to_dict(), f, sort_keys=False)
        os.replace(tmp, self.settings_path)
