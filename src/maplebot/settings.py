"""
User settings and the profile folder.

Owner: main agent. One Settings object is passed to every part.
Everything the user sets up lives in the profile folder (git-ignored):

    profile/
      settings.yaml        # SHARED by all maps: keys, skill, potions, behaviour, safety,
                           # detection, plus `current_map`
      maps/<map-id>/
        map.yaml           # PER MAP: the `calibration` section (minimap box, bars,
                           # training area, dot colour) and a display `name`
        templates/
          mob_*.png        # crops of this map's mobs (any number)
          player.png       # crop of your name tag
    logs/<run>/            # run logs (repo root, git-ignored)

`Profile.load()` returns one merged Settings for the current map, and `Profile.save()`
splits it back. `templates_dir`, `mob_templates()` and `player_template()` always refer
to the current map, so callers don't need to know about maps at all.

An old single-map profile (profile/settings.yaml with calibration + profile/templates/)
is migrated automatically into maps/<id>/ the first time it's loaded.
"""
from __future__ import annotations

import dataclasses
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

try:                      # PyYAML is optional: JSON is valid YAML, so files stay readable either way
    import yaml
except ImportError:       # e.g. no PyYAML wheel yet for a brand-new Python version
    yaml = None
import json
import math

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Keys:
    attack: str = "ctrl"
    jump: str = "alt"
    left: str = "left"
    right: str = "right"
    up: str = "up"
    down: str = "down"
    pickup: str = "z"
    hp_potion: str = ""        # "" = not used
    mp_potion: str = ""


@dataclass
class Skill:
    hold_s: float = 0.10       # how long to hold the attack key
    cooldown_s: float = 0.65   # minimum gap between casts (cast animation length)
    animation_lock_s: float = 0.65  # movement/jump lock after casting; tune for the skill
    range_px: int = 350        # horizontal reach on screen
    vertical_tolerance_px: int = 60   # ignore mobs further above/below than this


@dataclass
class Calibration:
    window_title: str = "MapleStory"
    reference_size: Optional[List[int]] = None
    minimap_box: Optional[List[int]] = None   # [x, y, w, h] in client pixels
    hp_bar_box: Optional[List[int]] = None
    mp_bar_box: Optional[List[int]] = None
    patrol_left: float = 0.30                 # minimap x fraction
    patrol_right: float = 0.70
    patrol_y: Optional[float] = None          # minimap y of the bridge ENDS (set with the edges)
    # On-platform band is [patrol_y - above, patrol_y + below]. Bridges sag downward
    # (~0.06-0.07 of minimap height on The Tree That Grew II) and you only ever fall down;
    # the next bridge down is ~0.117 below the ends.
    patrol_y_tolerance: float = 0.095         # how far BELOW patrol_y still counts as on the bridge
    patrol_y_tolerance_above: float = 0.03    # how far ABOVE patrol_y still counts as on the bridge
    platforms: List[dict] = field(default_factory=list)
    connections: List[dict] = field(default_factory=list)
    route: List[str] = field(default_factory=list)
    safe_spot: Optional[List[float]] = None
    ladders: List[dict] = field(default_factory=list)
    movement_profile: dict = field(default_factory=dict)
    hp_stock_box: Optional[List[int]] = None
    mp_stock_box: Optional[List[int]] = None
    mesos_box: Optional[List[int]] = None
    notification_box: Optional[List[int]] = None
    player_dot_hsv_low: List[int] = field(default_factory=lambda: [20, 150, 180])
    player_dot_hsv_high: List[int] = field(default_factory=lambda: [35, 255, 255])


@dataclass
class Detection:
    sprite_templates_per_tick: int = 8  # round-robin budget for imported references
    item_threshold: float = 0.82
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
    edge_margin: float = 0.012
    reaction_s: float = 0.18
    knockback_margin: float = 0.015
    knockback_wait_s: float = 0.45
    drop_setup_s: float = 0.06
    landing_settle_s: float = 0.15
    waypoint_tolerance: float = 0.012
    loop_hz: float = 15.0
    stuck_s: float = 2.5       # walking but minimap x not changing -> jump
    turn_tap_s: float = 0.05
    jump_tap_s: float = 0.05


@dataclass
class Loot:
    enabled: bool = True
    pickup_interval_s: float = 0.45
    tap_s: float = 0.05
    range_px: int = 45
    floor_tolerance_px: int = 45
    player_feet_offset_px: int = 25
    pursuit_s: float = 5.0
    max_attempts: int = 5
    ignore_s: float = 20.0


@dataclass
class Retreat:
    enabled: bool = True
    hp_start: Optional[int] = None   # manually supplied stock; None = do not estimate
    mp_start: Optional[int] = None
    hp_reserve: int = 10
    mp_reserve: int = 10
    hp_below: float = 0.25
    mp_below: float = 0.08
    timeout_s: float = 45.0
    arrival_tolerance: float = 0.012
    settle_s: float = 0.4


@dataclass
class OCR:
    enabled: bool = False
    min_confidence: float = 0.90
    interval_s: float = 0.5
    max_age_s: float = 2.0
    confirm_reads: int = 2
    unreadable_pause_s: float = 5.0


@dataclass
class Economy:
    hp_replacement_cost: Optional[int] = None
    mp_replacement_cost: Optional[int] = None


@dataclass
class PlayStyle:
    enabled: bool = False
    reaction_min_s: float = 0.08
    reaction_max_s: float = 0.20
    cast_extra_max_s: float = 0.15
    seed: int = 0


@dataclass
class Safety:
    input_timeout_s: float = 0.5
    pause_on_dialog: bool = True
    alert_threshold: float = 0.90
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
    loot: Loot = field(default_factory=Loot)
    retreat: Retreat = field(default_factory=Retreat)
    play_style: PlayStyle = field(default_factory=PlayStyle)
    ocr: OCR = field(default_factory=OCR)
    economy: Economy = field(default_factory=Economy)
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
        out = self.mechanics_problems()
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
        for name in ("attack", "jump", "left", "right", "up", "down", "pickup", "hp_potion", "mp_potion"):
            value = getattr(self.keys, name)
            if not value:
                continue
            try:
                normalize_key(value)
            except ValueError:
                out.append(f"{name.replace('_', ' ').capitalize()} key '{value}' isn't a key the bot knows")
        return out


    def mechanics_problems(self) -> List[str]:
        """Validate advanced settings at start and in the form, including direct file edits."""
        out = []
        limits = {
            "ocr.min_confidence": (0.7, 0.999), "ocr.interval_s": (0.1, 5),
            "ocr.max_age_s": (0.5, 10), "ocr.confirm_reads": (2, 5),
            "ocr.unreadable_pause_s": (1, 60),
            "retreat.hp_reserve": (0, 100000), "retreat.mp_reserve": (0, 100000),
            "retreat.hp_below": (0, 1), "retreat.mp_below": (0, 1),
            "retreat.timeout_s": (1, 300), "retreat.arrival_tolerance": (0.002, 0.03),
            "retreat.settle_s": (0.1, 2), "play_style.reaction_min_s": (0, 1),
            "play_style.reaction_max_s": (0, 1), "play_style.cast_extra_max_s": (0, 1),
            "safety.alert_threshold": (0.7, 0.99),
            "safety.input_timeout_s": (0.2, 5),
            "skill.animation_lock_s": (0, 5), "behavior.edge_margin": (0, 0.2),
            "behavior.reaction_s": (0, 1), "behavior.knockback_margin": (0, 0.2),
            "behavior.knockback_wait_s": (0, 2), "behavior.drop_setup_s": (0.01, 0.3),
            "behavior.landing_settle_s": (0.05, 0.5), "behavior.waypoint_tolerance": (0.001, 0.03),
            "loot.pickup_interval_s": (0.1, 10), "loot.tap_s": (0.01, 0.2),
            "loot.range_px": (1, 150), "loot.floor_tolerance_px": (1, 150),
            "loot.player_feet_offset_px": (0, 150), "loot.pursuit_s": (0.5, 20),
            "loot.max_attempts": (1, 30), "loot.ignore_s": (1, 300),
            "detection.item_threshold": (0.5, 0.99),
            "detection.sprite_templates_per_tick": (1, 128),
        }
        for path, (low, high) in limits.items():
            section, key = path.split(".")
            value = getattr(getattr(self, section), key)
            if (not isinstance(value, (float, int)) or isinstance(value, bool) or
                    not math.isfinite(value) or not low <= value <= high):
                out.append(f"{path} must be between {low} and {high}")
        size = self.calibration.reference_size
        if size is not None and (not isinstance(size,list) or len(size)!=2 or
                any(type(v) is not int or v<=0 for v in size)):
            out.append("Reference size must be two positive pixel dimensions")
        if type(self.ocr.confirm_reads) is not int:
            out.append("OCR confirmation reads must be a whole number")
        for name in ("hp_replacement_cost", "mp_replacement_cost"):
            value = getattr(self.economy, name)
            if value is not None and (type(value) is not int or not 0 <= value <= 100000000):
                out.append(f"{name} must be a whole nonnegative meso amount or empty")
        if self.ocr.enabled:
            import importlib.util
            if importlib.util.find_spec("rapidocr_onnxruntime") is None:
                out.append('Local OCR is missing: install with python -m pip install -e ".[ocr]"')
            if not any(getattr(self.calibration, name) for name in
                       ("hp_stock_box", "mp_stock_box", "mesos_box", "notification_box")):
                out.append("Set at least one OCR HUD region before enabling OCR")
        for name in ("hp_stock_box", "mp_stock_box", "mesos_box", "notification_box"):
            box = getattr(self.calibration, name)
            if box is not None and (not isinstance(box, list) or len(box) != 4 or
                    any(type(v) is not int for v in box) or min(box[:2]) < 0 or
                    not 4 <= box[2] <= 1000 or not 6 <= box[3] <= 300):
                out.append(f"{name} needs x,y,width,height with a small nonempty HUD crop")
        for name in ("hp_start", "mp_start", "hp_reserve", "mp_reserve"):
            value = getattr(self.retreat, name)
            if value is not None and (type(value) is not int or not 0 <= value <= 100000):
                out.append(f"retreat.{name} must be a whole count in 0..100000 or empty for starting stock")
        if type(self.play_style.seed) is not int or not 0 <= self.play_style.seed <= 2147483647:
            out.append("Play style seed must be a whole number in 0..2147483647")
        if (isinstance(self.play_style.reaction_min_s, (int, float)) and
                isinstance(self.play_style.reaction_max_s, (int, float)) and
                self.play_style.reaction_min_s > self.play_style.reaction_max_s):
            out.append("Minimum reaction delay must not exceed maximum reaction delay")
        if not isinstance(self.detection.sprite_templates_per_tick, int):
            out.append("detection.sprite_templates_per_tick must be a whole number")
        if not isinstance(self.loot.max_attempts, int):
            out.append("loot.max_attempts must be a whole number")
        try:
            from .controls import normalize_key
            binds = [(name, normalize_key(getattr(self.keys, name)))
                     for name in ("attack", "jump", "left", "right", "up", "down", "pickup", "hp_potion", "mp_potion")
                     if getattr(self.keys, name)]
            for name in ("up", "down", "pickup"):
                if name == "pickup" and not self.loot.enabled:
                    continue
                value = next((v for n, v in binds if n == name), None)
                if value and any(v == value and n != name for n, v in binds):
                    out.append(f"{name.capitalize()} needs a separate key")
        except ValueError as exc:
            out.append(str(exc))
        try:
            from .mapmodel import MapModel
            model = MapModel.from_settings(self)
            if self.calibration.safe_spot is not None:
                model.safe_platform(self.calibration.safe_spot, self.behavior.edge_margin)
        except (ValueError, TypeError) as exc:
            out.append(str(exc))
        return out


MAP_SECTIONS = ("calibration",)          # Settings sections stored per map
DEFAULT_MAP_NAME = "My first map"


def map_id_for(name: str) -> str:
    """Folder-safe id for a map name ("The Tree That Grew II" -> "the-tree-that-grew-ii")."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:60] or "map"


class MapError(ValueError):
    """A map operation the user asked for can't be done; message is user-facing."""


@dataclass
class Profile:
    root: Path

    @classmethod
    def default(cls) -> "Profile":
        return cls(Path(os.environ.get("MAPLEBOT_PROFILE", REPO_ROOT / "profile")))

    # ---- paths -----------------------------------------------------------
    @property
    def shared_settings_path(self) -> Path:
        return self.root / "settings.yaml"

    @property
    def maps_dir(self) -> Path:
        return self.root / "maps"

    def map_dir(self, map_id: Optional[str] = None) -> Path:
        return self.maps_dir / (map_id or self.current_map)

    @property
    def settings_path(self) -> Path:
        """The current map's file (exists once that map has been set up and saved)."""
        return self.map_dir() / "map.yaml"

    @property
    def templates_dir(self) -> Path:
        return self.map_dir() / "templates"

    @property
    def logs_dir(self) -> Path:
        return Path(os.environ.get("MAPLEBOT_LOGS", REPO_ROOT / "logs"))

    def mob_templates(self) -> List[Path]:
        return sorted(self.templates_dir.glob("mob_*.png")) + self.library_templates("mob")

    def library_templates(self, kind: str) -> List[Path]:
        """Only the atomically published bank is active; old banks/manual crops coexist."""
        import re
        record = self.templates_dir / "sprite-library.json"
        if not record.is_file():
            return []
        try:
            value = json.loads(record.read_text(encoding="utf-8"))
            bank_id = value["bank"]
            if not re.fullmatch(r"[0-9a-f]{32}", bank_id):
                return []
            bank = self.templates_dir / ".sprite-banks" / bank_id
            return [bank / row["file"] for row in value["entries"]
                    if row.get("kind") == kind and re.fullmatch(
                        rf"{kind}_library_[0-9]+_[a-z0-9]+_[0-9]+\.png", row["file"])
                    and (bank / row["file"]).is_file()]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return []                         # needs a fresh download; never follow bad paths

    @property
    def alerts_dir(self) -> Path:
        return self.root / "alerts"

    def alert_templates(self) -> List[Path]:
        return sorted(self.alerts_dir.glob("alert_*.png"))

    def item_templates(self) -> List[Path]:
        return sorted(self.templates_dir.glob("item_*.png")) + self.library_templates("item")

    def player_template(self) -> Optional[Path]:
        p = self.templates_dir / "player.png"
        return p if p.exists() else None

    # ---- maps --------------------------------------------------------------
    def _read(self, path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = load_yaml_or_json(f.read())
        return data if isinstance(data, dict) else {}

    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(dump_yaml_or_json(data))
        os.replace(tmp, path)

    def _migrate(self) -> None:
        """Move an old single-map profile into maps/<id>/ (idempotent)."""
        old_templates = self.root / "templates"
        shared = self._read(self.shared_settings_path)
        has_old_cal = any(k in shared for k in MAP_SECTIONS)
        if self.maps_dir.exists() and not has_old_cal and not old_templates.exists():
            return
        if not has_old_cal and not old_templates.exists():
            return
        map_id = shared.get("current_map") or map_id_for(DEFAULT_MAP_NAME)
        dest = self.maps_dir / map_id
        dest.mkdir(parents=True, exist_ok=True)
        map_data = self._read(dest / "map.yaml")
        map_data.setdefault("name", DEFAULT_MAP_NAME)
        for k in MAP_SECTIONS:
            if k in shared:
                map_data.setdefault(k, shared.pop(k))
        self._write(dest / "map.yaml", map_data)
        if old_templates.exists():
            (dest / "templates").mkdir(parents=True, exist_ok=True)
            for f in old_templates.iterdir():
                target = dest / "templates" / f.name
                if not target.exists():
                    shutil.move(str(f), str(target))
            try:
                old_templates.rmdir()
            except OSError:
                pass
        shared["current_map"] = map_id
        self._write(self.shared_settings_path, shared)

    def maps(self) -> List[Tuple[str, str]]:
        """All saved maps as (map_id, display name), sorted by name. A brand-new profile
        lists its default map even before anything has been saved."""
        self._migrate()
        out = []
        if not self.maps_dir.is_dir() or not any(d.is_dir() for d in self.maps_dir.iterdir()):
            return [(map_id_for(DEFAULT_MAP_NAME), DEFAULT_MAP_NAME)]
        if self.maps_dir.exists():
            for d in self.maps_dir.iterdir():
                if d.is_dir():
                    name = self._read(d / "map.yaml").get("name") or d.name
                    out.append((d.name, str(name)))
        return sorted(out, key=lambda m: m[1].lower())

    @property
    def current_map(self) -> str:
        self._migrate()
        cur = self._read(self.shared_settings_path).get("current_map")
        if cur and (self.maps_dir / cur).is_dir():
            return cur
        existing = self.maps_dir.is_dir() and sorted(d.name for d in self.maps_dir.iterdir() if d.is_dir())
        return existing[0] if existing else map_id_for(DEFAULT_MAP_NAME)

    def current_map_name(self) -> str:
        return dict(self.maps()).get(self.current_map, DEFAULT_MAP_NAME)

    def map_is_set_up(self, map_id: Optional[str] = None) -> bool:
        d = self.map_dir(map_id)
        cal = self._read(d / "map.yaml").get("calibration") or {}
        return bool(cal.get("minimap_box")) and any((d / "templates").glob("mob_*.png"))

    def switch_map(self, map_id: str) -> None:
        if not (self.maps_dir / map_id).is_dir():
            raise MapError(f"There's no saved map called '{map_id}'.")
        shared = self._read(self.shared_settings_path)
        shared["current_map"] = map_id
        self._write(self.shared_settings_path, shared)

    def create_map(self, name: str, copy_from: Optional[str] = None) -> str:
        """Create a map and make it current. Copies from `copy_from` (default: the
        current map, if any) the things that don't change between maps: the game
        window title, HP/MP bar boxes, minimap dot colour and your name-tag picture.
        Map-specific things (minimap box, mob pictures, training area) start empty."""
        name = (name or "").strip()
        if not name:
            raise MapError("Give the map a name, e.g. 'The Tree That Grew II'.")
        if not self.maps_dir.is_dir() or not any(d.is_dir() for d in self.maps_dir.iterdir()):
            self.ensure()            # materialise the default map so it can be copied from
        map_id = map_id_for(name)
        existing = [n for _, n in self.maps() if n.lower() == name.lower()]
        if existing:
            raise MapError(f"You already have a map called '{existing[0]}'.")
        base, i = map_id, 2
        while (self.maps_dir / map_id).exists():
            map_id, i = f"{base}-{i}", i + 1
        src_id = copy_from if copy_from is not None else (
            self.current_map if (self.maps_dir / self.current_map).is_dir() else None)
        cal = Calibration()
        if src_id and (self.maps_dir / src_id).is_dir():
            src_cal = self._read(self.maps_dir / src_id / "map.yaml").get("calibration") or {}
            for k in ("window_title", "reference_size", "hp_bar_box", "mp_bar_box", "hp_stock_box", "mp_stock_box",
                      "mesos_box", "notification_box",
                      "player_dot_hsv_low", "player_dot_hsv_high"):
                if src_cal.get(k) is not None:
                    setattr(cal, k, src_cal[k])
        dest = self.maps_dir / map_id
        (dest / "templates").mkdir(parents=True, exist_ok=True)
        if src_id:
            tag = self.maps_dir / src_id / "templates" / "player.png"
            if tag.exists():
                shutil.copy2(tag, dest / "templates" / "player.png")
        self._write(dest / "map.yaml", {"name": name, "calibration": dataclasses.asdict(cal)})
        self.switch_map(map_id)
        return map_id

    def rename_map(self, map_id: str, new_name: str) -> None:
        new_name = (new_name or "").strip()
        if not new_name:
            raise MapError("The map name can't be empty.")
        if any(n.lower() == new_name.lower() and i != map_id for i, n in self.maps()):
            raise MapError(f"You already have a map called '{new_name}'.")
        path = self.maps_dir / map_id / "map.yaml"
        if not path.parent.is_dir():
            raise MapError(f"There's no saved map called '{map_id}'.")
        data = self._read(path)
        data["name"] = new_name
        self._write(path, data)

    def delete_map(self, map_id: str) -> None:
        """Delete a map's setup. You can't delete the only map."""
        ids = [i for i, _ in self.maps() if (self.maps_dir / i).is_dir()]
        if map_id not in ids:
            raise MapError(f"There's no saved map called '{map_id}'.")
        if len(ids) == 1:
            raise MapError("You can't delete your only map. Create another one first.")
        was_current = map_id == self.current_map
        shutil.rmtree(self.maps_dir / map_id)
        if was_current:
            self.switch_map([i for i in ids if i != map_id][0])

    # ---- settings ------------------------------------------------------------
    def ensure(self) -> None:
        self._migrate()
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        if not self.settings_path.exists():
            self._write(self.settings_path, {"name": DEFAULT_MAP_NAME})
        shared = self._read(self.shared_settings_path)
        if shared.get("current_map") != self.current_map:
            shared["current_map"] = self.current_map
            self._write(self.shared_settings_path, shared)

    def load(self) -> Settings:
        self._migrate()
        shared = self._read(self.shared_settings_path)
        merged = {k: v for k, v in shared.items() if k not in MAP_SECTIONS and k != "current_map"}
        map_data = self._read(self.settings_path)
        for k in MAP_SECTIONS:
            if k in map_data:
                merged[k] = map_data[k]
        return Settings.from_dict(merged or None)

    def save(self, settings: Settings) -> None:
        self.ensure()
        data = settings.to_dict()
        shared = {k: v for k, v in data.items() if k not in MAP_SECTIONS}
        shared["current_map"] = self.current_map
        self._write(self.shared_settings_path, shared)
        map_data = self._read(self.settings_path)
        map_data.setdefault("name", self.current_map_name())
        for k in MAP_SECTIONS:
            map_data[k] = data[k]
        self._write(self.settings_path, map_data)


def load_yaml_or_json(text: str):
    if not text.strip():
        return None
    if yaml is not None:
        return yaml.safe_load(text)
    return json.loads(text)


def dump_yaml_or_json(data) -> str:
    if yaml is not None:
        return yaml.safe_dump(data, sort_keys=False)
    return json.dumps(data, indent=2) + "\n"
