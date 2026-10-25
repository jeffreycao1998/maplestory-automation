"""
Display-free helpers for the app: everything the GUI does that can be tested without
a screen. No tkinter imports here (tests check that).

- ``View``: maps between canvas pixels (a scaled, maybe zoomed picture) and real
  screenshot pixels, normalises drags from any direction and clamps to the image.
- Settings form: field discovery from the ``Settings`` dataclasses, plain-English labels
  and tooltips, parsing/validation of what the user typed, with friendly errors.
- Wizard: the step list and the check that decides whether "Next" is allowed.
- Status / error formatting for the main window.
"""
from __future__ import annotations

import dataclasses
import json
import math
import re
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from maplebot.settings import Settings

Box = Tuple[int, int, int, int]

# ---------------------------------------------------------------------------
# Canvas <-> image coordinates
# ---------------------------------------------------------------------------

MIN_BOX_PX = 3          # drags smaller than this (in image pixels) count as a click


def fit_scale(img_w: int, img_h: int, max_w: int, max_h: int, allow_upscale: bool = False,
              max_upscale: float = 8.0) -> float:
    """Scale that makes an img_w x img_h picture fit inside max_w x max_h."""
    if img_w <= 0 or img_h <= 0 or max_w <= 0 or max_h <= 0:
        return 1.0
    s = min(max_w / img_w, max_h / img_h)
    if not allow_upscale:
        s = min(s, 1.0)
    return min(s, max_upscale)


def normalize_rect(x0: float, y0: float, x1: float, y1: float) -> Tuple[float, float, float, float]:
    """(left, top, right, bottom) from two corners given in any order."""
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


@dataclass(frozen=True)
class View:
    """A window onto part of the screenshot, drawn on the canvas at ``scale``.

    ``ox, oy, w, h``: the region of the screenshot shown (image pixels).
    ``img_w, img_h``: full screenshot size (for clamping).
    Canvas point (cx, cy) shows image point (ox + cx/scale, oy + cy/scale).
    """
    ox: int
    oy: int
    w: int
    h: int
    scale: float
    img_w: int
    img_h: int

    @classmethod
    def fit(cls, img_w: int, img_h: int, max_w: int, max_h: int,
            region: Optional[Sequence[int]] = None, zoom: bool = False) -> "View":
        """View of ``region`` (default: whole image) scaled to fit max_w x max_h.

        ``zoom`` allows scaling up (used to blow up the small minimap).
        """
        if region:
            r = clamp_box(region, img_w, img_h)
            if r is None:
                region = None
        if region:
            ox, oy, w, h = r
        else:
            ox, oy, w, h = 0, 0, img_w, img_h
        s = fit_scale(w, h, max_w, max_h, allow_upscale=zoom)
        return cls(ox, oy, w, h, s, img_w, img_h)

    @property
    def canvas_size(self) -> Tuple[int, int]:
        return max(1, int(round(self.w * self.scale))), max(1, int(round(self.h * self.scale)))

    def to_image(self, cx: float, cy: float) -> Tuple[int, int]:
        """Canvas point -> image pixel (clamped to the shown region)."""
        x = self.ox + cx / self.scale
        y = self.oy + cy / self.scale
        x = min(max(x, self.ox), self.ox + self.w - 1)
        y = min(max(y, self.oy), self.oy + self.h - 1)
        return int(math.floor(x)), int(math.floor(y))

    def to_canvas(self, x: float, y: float) -> Tuple[float, float]:
        """Image point -> canvas point (not clamped)."""
        return (x - self.ox) * self.scale, (y - self.oy) * self.scale

    def box_to_canvas(self, box: Sequence[int]) -> Tuple[float, float, float, float]:
        """Image box (x, y, w, h) -> canvas rectangle (x0, y0, x1, y1)."""
        x, y, w, h = box
        x0, y0 = self.to_canvas(x, y)
        x1, y1 = self.to_canvas(x + w, y + h)
        return x0, y0, x1, y1

    def drag_to_box(self, cx0: float, cy0: float, cx1: float, cy1: float,
                    min_px: int = MIN_BOX_PX) -> Optional[Box]:
        """A drag on the canvas (any direction) -> image box (x, y, w, h), clamped to
        the shown region. None if the box is smaller than ``min_px`` either way."""
        l, t, r, b = normalize_rect(cx0, cy0, cx1, cy1)
        x0 = self.ox + l / self.scale
        y0 = self.oy + t / self.scale
        x1 = self.ox + r / self.scale
        y1 = self.oy + b / self.scale
        x0 = min(max(x0, self.ox), self.ox + self.w)
        x1 = min(max(x1, self.ox), self.ox + self.w)
        y0 = min(max(y0, self.oy), self.oy + self.h)
        y1 = min(max(y1, self.oy), self.oy + self.h)
        ix0, iy0 = int(math.floor(x0)), int(math.floor(y0))
        ix1, iy1 = int(math.ceil(x1)), int(math.ceil(y1))
        w, h = ix1 - ix0, iy1 - iy0
        if w < min_px or h < min_px:
            return None
        return ix0, iy0, w, h


def clamp_box(box: Optional[Sequence[Any]], img_w: int, img_h: int) -> Optional[Box]:
    """Clip (x, y, w, h) to the image. None if invalid or nothing is left."""
    try:
        if box is None or len(box) != 4:
            return None
        x, y, w, h = (int(round(float(v))) for v in box)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(img_w, x + w), min(img_h, y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def minimap_dot_to_image(box: Sequence[int], pos: Sequence[float], view=None) -> Tuple[int, int]:
    """(fx, fy) fractions of the minimap box -> image pixel."""
    from maplebot.perception.map_reference import map_to_image
    return map_to_image(box,pos,view)


def dot_near_click(box: Sequence[int], pos: Optional[Sequence[float]], click: Sequence[int],
                   tol_px: Optional[float] = None) -> bool:
    """Is the detected dot (fractions of the minimap box) where the user clicked?"""
    if pos is None:
        return False
    px, py = box[0] + pos[0] * box[2], box[1] + pos[1] * box[3]
    tol = tol_px if tol_px is not None else max(4.0, 0.04 * box[2])
    return math.hypot(px - (click[0] + 0.5), py - (click[1] + 0.5)) <= tol


DEFAULT_DOT_HSV = ([20, 150, 180], [35, 255, 255])


def dot_range_candidates(sampled: Tuple[Sequence[int], Sequence[int]],
                         current: Optional[Tuple[Sequence[int], Sequence[int]]] = None):
    """Colour ranges to try after a click on the dot, most specific first.

    The sampled range can come out too wide (a click picks up the dot's dark outline),
    so stricter variants and the known-good default are tried too.
    """
    lo, hi = list(sampled[0]), list(sampled[1])
    strict = [lo[0], max(lo[1], 150), max(lo[2], 180)]
    out = [(lo, hi), (strict, hi), (list(DEFAULT_DOT_HSV[0]), list(DEFAULT_DOT_HSV[1]))]
    if current:
        out.append((list(current[0]), list(current[1])))
    uniq = []
    for c in out:
        if c not in uniq:
            uniq.append(c)
    return uniq


def pick_dot_range(candidates, locate, box: Sequence[int], click: Sequence[int]):
    """First (low, high, pos) whose detected dot is at the click; else (None, None, pos_of_first).

    ``locate(low, high)`` returns the dot position (fractions) or None.
    """
    first_pos = None
    for i, (lo, hi) in enumerate(candidates):
        pos = locate(lo, hi)
        if i == 0:
            first_pos = pos
        if dot_near_click(box, pos, click):
            return lo, hi, pos
    return None, None, first_pos


def preview_size(w: int, h: int, target_w: int = 480) -> Tuple[int, int]:
    """Size to show the live preview at (~target_w wide, aspect kept, never upscaled)."""
    if w <= 0 or h <= 0:
        return target_w, max(1, target_w * 9 // 16)
    s = min(1.0, target_w / w)
    return max(1, int(round(w * s))), max(1, int(round(h * s)))


# ---------------------------------------------------------------------------
# Images / templates (numpy + PIL only)
# ---------------------------------------------------------------------------

def crop(image, box: Sequence[int]):
    """Copy of ``image[y:y+h, x:x+w]`` after clamping; None if the box is outside."""
    ih, iw = image.shape[:2]
    b = clamp_box(box, iw, ih)
    if b is None:
        return None
    x, y, w, h = b
    return image[y:y + h, x:x + w].copy()


def save_crop(image, box: Sequence[int], path: Path) -> Path:
    """Save a crop of a BGR screenshot as PNG (PIL, so non-ASCII paths work on Windows)."""
    from PIL import Image

    c = crop(image, box)
    if c is None:
        raise ValueError("That box is outside the screenshot. Draw it again.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(c[:, :, ::-1].copy()).save(path, format="PNG")
    return path


def next_mob_template_path(templates_dir: Path) -> Path:
    """profile/templates/mob_<n>.png with n one higher than any existing mob_*.png."""
    n = 0
    for p in Path(templates_dir).glob("mob_*.png"):
        m = re.fullmatch(r"mob_(\d+)\.png", p.name)
        if m:
            n = max(n, int(m.group(1)))
    return Path(templates_dir) / f"mob_{n + 1}.png"


def looks_blank(image) -> bool:
    """True if a screenshot is (nearly) one flat colour -- e.g. black because the
    game was minimised or covered by a protected window."""
    import numpy as np

    if image is None or getattr(image, "size", 0) == 0:
        return True
    small = image[::8, ::8]
    return float(np.asarray(small, dtype=np.float32).std()) < 3.0


# ---------------------------------------------------------------------------
# Settings form: field discovery, labels, tooltips
# ---------------------------------------------------------------------------

SECTION_LABELS = {
    "keys": "Keys",
    "skill": "Attack skill",
    "calibration": "Screen setup",
    "detection": "Detection",
    "potions": "Potions",
    "behavior": "Movement",
    "safety": "Safety",
    "retreat": "Safe retreat",
    "play_style": "Play style",
    "ocr": "Visible HUD reading",
    "economy": "Economy",
    "general": "General",
}

# (section, field) -> (label, tooltip). Fields missing here still appear, with a
# label made from the field name, so new settings never get lost.
FIELD_INFO: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("general", "training_only"): ("Training only (pet handles loot)", "Skip manual pickup and drop pursuit, including old saved loot settings. Keep potions, retreat and navigation active."),
    ("skill", "targeting_profile"): ("Skill targeting profile", "magic_claw applies the Classic 300-range cap and conservative platform/height checks. generic uses manually calibrated range and vertical tolerance."),
    ("ocr","enabled"): ("Read visible stock and wallet", "Requires the local OCR extra and tested HUD crops. Unreadable configured stock stops ordinary actions."),
    ("economy","hp_replacement_cost"): ("HP potion replacement cost", "Meso value per potion stock decrease; empty means unknown. This is a valuation, not a recorded purchase."),
    ("economy","mp_replacement_cost"): ("MP potion replacement cost", "Used to estimate net wallet change after valuing consumed stock; empty means unknown."),
    ("calibration", "safe_spot"): ("Safe spot (minimap x, y)", "Choose this by clicking the minimap in the Training area wizard step. Must be on a known platform away from edges."),
    ("retreat", "hp_start"): ("HP potions at run start (estimate)", "Enter your actual stock before each new run. Empty disables HP stock estimation; this is not inventory OCR."),
    ("retreat", "mp_start"): ("MP potions at run start (estimate)", "Enter actual MP stock before each new run. Potion key attempts reduce this estimate, including unsuccessful attempts."),
    ("retreat", "hp_reserve"): ("Retreat at estimated HP potions", "Head to the safe spot when the estimate reaches this count."),
    ("retreat", "mp_reserve"): ("Retreat at estimated MP potions", "Leave enough reserve for travel; this bot does not count your inventory visually."),
    ("retreat", "hp_below"): ("Retreat below HP fraction", "Trigger retreat early; emergency potions and the critical-HP stop still take priority."),
    ("retreat", "mp_below"): ("Retreat below MP fraction", "Low MP triggers retreat even when stock counts are unknown."),
    ("play_style", "enabled"): ("Vary engagement timing", "Adds a brief reaction delay and positive cast-spacing variation. Safety actions stay immediate. Does not guarantee protection from detection."),
    ("safety", "pause_on_dialog"): ("Pause on central dialog candidates", "Conservative screen heuristic; may pause on ordinary menus. Known alert reference pictures always cause a pause."),
    ("keys", "pickup"): ("Pickup key", "Collect items and mesos. Empty disables all pickup input."),
    ("calibration", "platforms"): ("Platforms (JSON)", "Measured minimap polylines. See docs/navigation.md for examples."),
    ("calibration", "connections"): ("Connections (JSON)", "Directed, measured walk/jump/drop links between platforms."),
    ("calibration", "route"): ("Platform route (JSON)", "Repeating list of platform names; empty stays on the training platform."),
    ("keys", "attack"): ("Attack skill key", "The key your attack skill (e.g. Magic Claw) is on."),
    ("keys", "jump"): ("Jump key", "Your jump key. Used to get unstuck."),
    ("keys", "left"): ("Walk left key", "Usually the Left arrow."),
    ("keys", "right"): ("Walk right key", "Usually the Right arrow."),
    ("keys", "hp_potion"): ("HP potion key", "Key your HP potions are on. Leave empty to not use HP potions."),
    ("keys", "mp_potion"): ("MP potion key", "Key your MP potions are on. Leave empty to not use MP potions."),
    ("skill", "hold_s"): ("Hold attack key for (seconds)", "How long the attack key is held down for each cast. 0.1 works for most skills."),
    ("skill", "cooldown_s"): ("Time between casts (seconds)", "Minimum gap between two casts -- the length of the cast animation. Magic Claw: about 0.6-0.7 s. Energy Bolt: about 0.7 s."),
    ("skill", "range_px"): ("Skill reach (pixels)", "Requested horizontal reach in screen pixels. The Magic Claw profile caps this at 300; farther compatible targets are approached first."),
    ("skill", "vertical_tolerance_px"): ("Up/down tolerance (pixels)", "Mobs further above or below you than this are ignored (they're on another platform). Lower it if the bot attacks mobs it can't reach."),
    ("calibration", "window_title"): ("Game window title", "Text in the game window's title bar. 'MapleStory' works for most clients."),
    ("calibration", "minimap_box"): ("Minimap box (x, y, width, height)", "Where the minimap is inside the game window. Easiest to set with the setup wizard."),
    ("calibration", "hp_bar_box"): ("HP bar box (x, y, width, height)", "Where your HP bar is. Leave empty to not read HP. Set it with the setup wizard."),
    ("calibration", "mp_bar_box"): ("MP bar box (x, y, width, height)", "Where your MP bar is. Leave empty to not read MP. Set it with the setup wizard."),
    ("calibration", "patrol_left"): ("Patrol left edge (0-1)", "Left end of the training area, as a fraction of the minimap width (0 = far left, 1 = far right)."),
    ("calibration", "patrol_right"): ("Patrol right edge (0-1)", "Right end of the training area, as a fraction of the minimap width. Must be bigger than the left edge."),
    ("calibration", "patrol_y"): ("Training platform height (0-1)", "Height of your training bridge/platform on the minimap (0 = top, 1 = bottom). Recorded automatically when you set the patrol edges in the wizard. Empty = not set."),
    ("calibration", "patrol_y_tolerance"): ("Platform tolerance below (0-1)", "How far BELOW the training bridge's height (as a fraction of the minimap height) your dot may be before the bot thinks you fell off. Sagging bridges dip in the middle, so this is bigger than the 'above' tolerance. Raise it a little if the bot thinks you fell while you're still on the bridge."),
    ("calibration", "patrol_y_tolerance_above"): ("Platform tolerance above (0-1)", "How far ABOVE the training bridge's height your dot may be before the bot thinks you're on a higher bridge. Keep it small: both ends of a bridge are at the same height. Also used by the setup wizard to warn when your two patrol edges look like different bridges."),
    ("calibration", "player_dot_hsv_low"): ("Your minimap dot colour, low (H, S, V)", "Darkest colour that still counts as your yellow dot. Set by clicking your dot in the wizard."),
    ("calibration", "player_dot_hsv_high"): ("Your minimap dot colour, high (H, S, V)", "Brightest colour that still counts as your yellow dot. Set by clicking your dot in the wizard."),
    ("detection", "mob_threshold"): ("Mob match strictness (0-1)", "How closely something must look like your mob pictures. Lower (e.g. 0.6) finds more mobs but may see things that aren't there; higher is stricter."),
    ("detection", "player_threshold"): ("Name tag match strictness (0-1)", "How closely the name tag must match your saved picture of it."),
    ("detection", "mirror_templates"): ("Also look for mirrored mobs", "Mobs face both ways. Keep this on unless you saved crops facing both ways yourself."),
    ("detection", "downscale"): ("Detection image size (0.2-1)", "Search a smaller copy of the screen for speed. 0.5 = half size. Use 1.0 if small mobs are missed."),
    ("potions", "hp_below"): ("Drink HP potion below (0-1)", "Drink an HP potion when HP falls below this fraction. 0.5 = half HP."),
    ("potions", "mp_below"): ("Drink MP potion below (0-1)", "Drink an MP potion when MP falls below this fraction."),
    ("potions", "cooldown_s"): ("Time between potions (seconds)", "Wait at least this long between two potions."),
    ("potions", "tap_s"): ("Potion key tap length (seconds)", "How long the potion key is pressed. 0.05 is fine."),
    ("behavior", "jump_tap_s"): ("Jump key tap length (seconds)", "How long the jump key is pressed when the bot jumps to get unstuck."),
    ("behavior", "loop_hz"): ("Decisions per second", "How often the bot looks at the screen and decides. 15 is plenty; lower it if your PC is slow."),
    ("behavior", "stuck_s"): ("Stuck after (seconds)", "If you're walking but not moving on the minimap for this long, the bot jumps."),
    ("behavior", "turn_tap_s"): ("Turn tap length (seconds)", "How long an arrow key is tapped to turn around without walking."),
    ("safety", "pause_hotkey"): ("Pause/resume hotkey", "Press this anywhere (even in the game) to pause or resume the bot."),
    ("safety", "stop_hotkey"): ("Stop hotkey", "Press this anywhere to stop the bot immediately."),
    ("safety", "start_delay_s"): ("Start delay (seconds)", "Time after pressing Start to click into the game window."),
    ("safety", "player_lost_s"): ("Pause if your dot is missing for (seconds)", "If the bot can't see your yellow minimap dot this long (map change, death, popup) it pauses."),
    ("safety", "no_mobs_pause_s"): ("Pause if no mobs for (seconds)", "If no mobs are seen this long, the bot pauses -- something is probably wrong."),
    ("safety", "stuck_jumps_limit"): ("Pause after this many stuck-jumps", "If the bot had to jump to get unstuck this many times in one minute, it pauses."),
    ("safety", "hp_critical"): ("Stop if HP below (0-1)", "If HP drops below this even after potions, the bot stops. Needs the HP bar set up."),
    ("safety", "max_runtime_min"): ("Stop after (minutes)", "Stop automatically after this many minutes. 0 = never."),
    ("safety", "save_anomaly_frames"): ("Save pictures of problems", "Save a screenshot to the logs folder whenever the bot pauses or gets stuck. Helps troubleshooting."),
    ("general", "debug_preview"): ("Allow live preview", "Lets the main window show what the bot sees."),
}

# Fields whose input widget / parsing differs from what the type says.
KIND_OVERRIDES = {
    ("keys", "attack"): "key", ("keys", "jump"): "key", ("keys", "left"): "key",
    ("keys", "right"): "key", ("keys", "down"): "key",
    ("keys", "pickup"): "optional_key",
    ("calibration", "platforms"): "json_list",
    ("calibration", "connections"): "json_list",
    ("calibration", "route"): "json_list", ("keys", "hp_potion"): "optional_key",
    ("keys", "mp_potion"): "optional_key",
    ("safety", "pause_hotkey"): "hotkey", ("safety", "stop_hotkey"): "hotkey",
    ("calibration", "minimap_box"): "box", ("calibration", "hp_bar_box"): "box",
    ("calibration", "mp_bar_box"): "box",
    **{("calibration",name):"box" for name in
       ("hp_stock_box","mp_stock_box","mesos_box","notification_box")},
    ("calibration","ladders"):"json_list",
    ("calibration","movement_profile"):"json_object",
    ("keys","up"):"key",
    ("calibration", "player_dot_hsv_low"): "hsv", ("calibration", "player_dot_hsv_high"): "hsv",
}

# (section, field) -> (min, max) inclusive, for numbers.
FIELD_LIMITS: Dict[Tuple[str, str], Tuple[float, float]] = {
    ("skill", "hold_s"): (0.01, 2.0),
    ("skill", "cooldown_s"): (0.05, 10.0),
    ("skill", "range_px"): (10, 2000),
    ("skill", "vertical_tolerance_px"): (0, 1000),
    ("calibration", "patrol_left"): (0.0, 1.0),
    ("calibration", "patrol_right"): (0.0, 1.0),
    ("calibration", "patrol_y"): (0.0, 1.0),
    ("calibration", "patrol_y_tolerance"): (0.005, 0.5),
    ("calibration", "patrol_y_tolerance_above"): (0.005, 0.5),
    ("detection", "mob_threshold"): (0.3, 0.99),
    ("detection", "player_threshold"): (0.3, 0.99),
    ("detection", "downscale"): (0.2, 1.0),
    ("potions", "hp_below"): (0.0, 1.0),
    ("potions", "mp_below"): (0.0, 1.0),
    ("potions", "cooldown_s"): (0.1, 60.0),
    ("potions", "tap_s"): (0.01, 1.0),
    ("behavior", "jump_tap_s"): (0.01, 1.0),
    ("behavior", "loop_hz"): (1.0, 60.0),
    ("behavior", "stuck_s"): (0.5, 30.0),
    ("behavior", "turn_tap_s"): (0.01, 1.0),
    ("safety", "start_delay_s"): (0.0, 30.0),
    ("safety", "player_lost_s"): (0.5, 120.0),
    ("safety", "no_mobs_pause_s"): (5.0, 3600.0),
    ("safety", "stuck_jumps_limit"): (1, 100),
    ("safety", "hp_critical"): (0.0, 1.0),
    ("safety", "max_runtime_min"): (0.0, 1440.0),
}

GENERAL = "general"     # pseudo-section for top-level, non-dataclass Settings fields


@dataclass(frozen=True)
class FieldSpec:
    section: str            # "keys", ... or "general" for top-level fields
    name: str
    label: str
    tooltip: str
    kind: str               # str|int|float|bool|key|optional_key|hotkey|box|hsv|int_list|float_list|optional_int_list
    default: Any

    @property
    def key(self) -> Tuple[str, str]:
        return self.section, self.name


class FieldError(ValueError):
    """A value the user typed that we can't use. ``str(e)`` is shown to the user."""

    def __init__(self, label: str, message: str):
        super().__init__(f"{label}: {message}")
        self.label = label
        self.message = message


def humanize(name: str) -> str:
    """'no_mobs_pause_s' -> 'No mobs pause (seconds)'."""
    words = name.split("_")
    unit = ""
    if words and words[-1] in ("s", "px", "min", "hz"):
        unit = {"s": " (seconds)", "px": " (pixels)", "min": " (minutes)", "hz": " (per second)"}[words.pop()]
    text = " ".join(words).strip() or name
    return text[:1].upper() + text[1:] + unit


def _kind_from_type(tp: Any) -> str:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is typing.Union and type(None) in args:
        inner = [a for a in args if a is not type(None)]
        k = _kind_from_type(inner[0]) if inner else "str"
        return "optional_" + k
    if origin in (list, List, tuple):
        inner = args[0] if args else str
        return "float_list" if inner is float else "int_list" if inner is int else "str_list"
    if tp is bool:
        return "bool"
    if tp is int:
        return "int"
    if tp is float:
        return "float"
    return "str"


def field_kind(section: str, name: str, tp: Any) -> str:
    return KIND_OVERRIDES.get((section, name)) or _kind_from_type(tp)


def _hints(cls: type) -> Dict[str, Any]:
    try:
        return typing.get_type_hints(cls)
    except Exception:          # unresolvable annotation: infer from defaults instead
        return {}


def _type_of_default(value: Any) -> Any:
    if value is None:
        return Optional[str]
    if isinstance(value, list):
        if value and all(isinstance(v, int) and not isinstance(v, bool) for v in value):
            return List[int]
        return List[float] if value and all(isinstance(v, (int, float)) for v in value) else List[str]
    return type(value)


def iter_fields(settings_cls: type = Settings) -> List[FieldSpec]:
    """Every editable setting, grouped by section, in declaration order."""
    out: List[FieldSpec] = []
    top_hints = _hints(settings_cls)
    defaults = settings_cls()
    general: List[FieldSpec] = []
    for sec in dataclasses.fields(settings_cls):
        sec_val = getattr(defaults, sec.name)
        if dataclasses.is_dataclass(sec_val):
            hints = _hints(type(sec_val))
            for f in dataclasses.fields(sec_val):
                label, tip = FIELD_INFO.get((sec.name, f.name), (humanize(f.name), ""))
                dv = getattr(sec_val, f.name)
                tp = hints.get(f.name, _type_of_default(dv))
                out.append(FieldSpec(sec.name, f.name, label, tip,
                                     field_kind(sec.name, f.name, tp), dv))
        else:
            label, tip = FIELD_INFO.get((GENERAL, sec.name), (humanize(sec.name), ""))
            tp = top_hints.get(sec.name, _type_of_default(sec_val))
            general.append(FieldSpec(GENERAL, sec.name, label, tip,
                                     field_kind(GENERAL, sec.name, tp), sec_val))
    return out + general


def sections(specs: Iterable[FieldSpec]) -> List[Tuple[str, str, List[FieldSpec]]]:
    """[(section, label, specs)] in first-seen order."""
    order: Dict[str, List[FieldSpec]] = {}
    for s in specs:
        order.setdefault(s.section, []).append(s)
    return [(sec, SECTION_LABELS.get(sec, humanize(sec)), items) for sec, items in order.items()]


def get_value(settings: Settings, spec: FieldSpec) -> Any:
    if spec.section == GENERAL:
        return getattr(settings, spec.name)
    return getattr(getattr(settings, spec.section), spec.name)


def _fmt_num(v: float) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def format_value(kind: str, value: Any) -> Any:
    """Value as shown in the form: text for entries, bool for checkboxes."""
    if kind in ("json_list","json_object"):
        return json.dumps(value)
    if kind == "bool":
        return bool(value)
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt_num(v) if isinstance(v, (int, float)) else str(v) for v in value)
    if isinstance(value, float):
        return _fmt_num(value)
    return str(value)


_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off", ""}


def _parse_number(text: str, label: str, integer: bool) -> float:
    t = str(text).strip().replace(" ", "")
    if t == "":
        raise FieldError(label, "please enter a number.")
    if "," in t and "." not in t:
        t = t.replace(",", ".")              # "0,65" typed with a comma decimal
    try:
        v = float(t)
    except ValueError:
        raise FieldError(label, f"'{text}' isn't a number.") from None
    if not math.isfinite(v):
        raise FieldError(label, "please enter a normal number.")
    if integer:
        if abs(v - round(v)) > 1e-9:
            raise FieldError(label, "please enter a whole number (no decimals).")
        return int(round(v))
    return v


def _parse_list(text: str, label: str, integer: bool) -> List[float]:
    parts = [p for p in re.split(r"[,\s;]+", str(text).strip().strip("[]()")) if p]
    return [_parse_number(p, label, integer) for p in parts]


def parse_value(spec: FieldSpec, raw: Any) -> Any:
    """Turn what's in the form into a setting value. Raises FieldError (friendly)."""
    kind, label = spec.kind, spec.label
    if kind in ("json_list","json_object"):
        try:
            value = json.loads(str(raw) or "[]")
        except (ValueError, TypeError):
            raise FieldError(label, "enter a JSON list; see docs/navigation.md.") from None
        expected = list if kind=="json_list" else dict
        if not isinstance(value,expected):
            raise FieldError(label, f"must be a JSON {'list' if expected is list else 'object'}.")
        return value
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        t = str(raw).strip().lower()
        if t in _TRUE:
            return True
        if t in _FALSE:
            return False
        raise FieldError(label, "please tick or untick the box.")
    if kind in ("int", "float"):
        v = _parse_number(raw, label, integer=(kind == "int"))
        lim = FIELD_LIMITS.get(spec.key)
        if lim and not (lim[0] <= v <= lim[1]):
            raise FieldError(label, f"must be between {_fmt_num(lim[0])} and {_fmt_num(lim[1])}.")
        if lim is None and v < 0:
            raise FieldError(label, "can't be negative.")
        return v
    if kind in ("key", "optional_key"):
        t = str(raw).strip()
        if t == "" or t.lower() in ("(none)", "none"):
            if kind == "optional_key":
                return ""
            raise FieldError(label, "please pick a key.")
        from maplebot.controls import normalize_key   # lazy: no tkinter, Linux-safe
        try:
            return normalize_key(t)
        except ValueError as e:
            raise FieldError(label, str(e)) from None
    if kind == "hotkey":
        t = str(raw).strip()
        try:
            from maplebot.supervisor.hotkeys import parse_key
        except ImportError:                          # supervisor not built yet
            parse_key = None
        if parse_key is not None:
            try:
                parse_key(t)
            except ValueError as e:
                raise FieldError(label, str(e)) from None
        elif not t:
            raise FieldError(label, "please pick a key such as F11.")
        return t.lower()
    if kind == "box":
        t = str(raw).strip()
        if t == "" or t.lower() == "none":
            return None
        vals = _parse_list(t, label, integer=True)
        if len(vals) != 4:
            raise FieldError(label, "needs 4 whole numbers: x, y, width, height (or leave it empty).")
        if vals[0] < 0 or vals[1] < 0:
            raise FieldError(label, "x and y can't be negative.")
        if vals[2] <= 0 or vals[3] <= 0:
            raise FieldError(label, "width and height must be bigger than 0.")
        return [int(v) for v in vals]
    if kind == "hsv":
        vals = _parse_list(raw, label, integer=True)
        if len(vals) != 3:
            raise FieldError(label, "needs 3 whole numbers: hue, saturation, value.")
        h, s, v = vals
        if not (0 <= h <= 179):
            raise FieldError(label, "hue must be between 0 and 179.")
        if not (0 <= s <= 255 and 0 <= v <= 255):
            raise FieldError(label, "saturation and value must be between 0 and 255.")
        return [int(x) for x in vals]
    if kind in ("int_list", "float_list", "optional_int_list", "optional_float_list"):
        t = str(raw).strip()
        if kind.startswith("optional_") and t == "":
            return None
        return _parse_list(t, label, integer="int" in kind)
    if kind.startswith("optional_"):
        t = str(raw).strip()
        if t == "":
            return None
        inner = dataclasses.replace(spec, kind=kind[len("optional_"):])
        return parse_value(inner, raw)
    return str(raw).strip()


def cross_field_errors(s: Settings) -> List[str]:
    """Problems that involve more than one field."""
    errs = []
    c = s.calibration
    if not c.patrol_left < c.patrol_right:
        errs.append("Patrol left edge must be smaller than the right edge.")
    k = s.keys
    used: Dict[str, str] = {}
    for lbl, val in (("Attack", k.attack), ("Jump", k.jump), ("Walk left", k.left),
                     ("Walk right", k.right), ("Down", k.down), ("Pickup", k.pickup), ("HP potion", k.hp_potion), ("MP potion", k.mp_potion)):
        if not val:
            continue
        if val in used:
            errs.append(f"{lbl} and {used[val]} are both set to the same key ({val}).")
        else:
            used[val] = lbl
    ph, sh = s.safety.pause_hotkey.lower(), s.safety.stop_hotkey.lower()
    if ph and ph == sh:
        errs.append("Pause and Stop hotkeys must be different keys.")
    for hk in (ph, sh):
        if hk and hk in used:
            errs.append(f"The {hk} hotkey is also your {used[hk]} key -- pick another hotkey.")
    errs.extend(s.mechanics_problems())
    return errs


def build_settings(base: Settings, raw_values: Dict[Tuple[str, str], Any],
                   specs: Optional[List[FieldSpec]] = None) -> Tuple[Settings, List[str]]:
    """Apply form values onto a copy of ``base``. Returns (settings, error messages).

    Fields missing from ``raw_values`` keep their value from ``base``.
    """
    specs = specs if specs is not None else iter_fields(type(base))
    errors: List[str] = []
    new = Settings.from_dict(base.to_dict())
    for spec in specs:
        if spec.key not in raw_values:
            continue
        try:
            value = parse_value(spec, raw_values[spec.key])
        except FieldError as e:
            errors.append(str(e))
            continue
        if spec.section == GENERAL:
            setattr(new, spec.name, value)
        else:
            setattr(getattr(new, spec.section), spec.name, value)
    if not errors:
        errors.extend(cross_field_errors(new))
    return new, errors


def defaults_for_section(section: str) -> Dict[Tuple[str, str], Any]:
    """Form values (formatted) of a section's defaults, for 'Reset to defaults'."""
    d = Settings()
    return {sp.key: format_value(sp.kind, get_value(d, sp)) for sp in iter_fields() if sp.section == section}


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WizardStep:
    key: str
    title: str
    instruction: str
    optional: bool = False


WIZARD_STEPS: List[WizardStep] = [
    WizardStep("window", "Game window",
               "Open MapleStory in windowed mode and log in to your character. "
               "Type part of the game window's title (usually 'MapleStory') and click Find window."),
    WizardStep("screenshot", "Screenshot",
               "Click Take screenshot. This window hides for 3 seconds -- click on the game so "
               "it's in front and nothing covers it."),
    WizardStep("minimap", "Minimap",
               "Drag a box around the minimap (the small map in the top-left corner). "
               "Include the whole map but not the title bar above it."),
    WizardStep("dot", "Your dot",
               "Click right on your own yellow dot on the zoomed-in minimap. "
               "A green circle shows where the bot sees you -- check it's on your dot."),
    WizardStep("mobs", "Mobs",
               "Click Download sprites for this map to get its monster poses and common pickup "
               "icons from the configured client build. Drawing boxes is optional: add an example when a pose is missed."),
    WizardStep("player", "Your name tag",
               "Drag a tight box around the name tag under YOUR character -- the bot uses it to "
               "know exactly where you are. Just the name, not other players' tags."),
    WizardStep("bars", "HP and MP bars",
               "Drag a box along your red HP bar (required: the bot stops before your character "
               "dies), then your blue MP bar (recommended for magicians: it lets the bot drink MP "
               "potions). Box just the coloured bar."),
    WizardStep("supplies", "Visible supplies (optional)",
               "Box just the number on each potion shortcut, your meso balance, and optionally the loot notification area. Test the readings before enabling local OCR. You can skip this step.", optional=True),
    WizardStep("patrol", "Training area",
               "Drag between the left and right training limits on the zoomed minimap. "
               "Pick the same bridge you are standing on, a little in from its ends. "
               "The walk-to-edge buttons are also available."),
    WizardStep("keys", "Keys",
               "Click a button, then press that key. Set your attack skill and jump; potion keys "
               "are optional. Also tell the bot how far your skill reaches and how fast it casts."),
    WizardStep("summary", "Done",
               "Check everything looks right, then click Save. You can change any of this later "
               "in Settings or by running the wizard again."),
]

PATROL_MIN_GAP = 0.05


def setup_steps(include_supplies: bool = False):
    """Six pages by default; the OCR supplies page is explicitly optional."""
    screen = WizardStep("screen", "Game and HUD",
                        "Click Detect game and UI, then click into the game during the countdown. "
                        "Check the minimap, your green dot, and the HP/MP readings together. "
                        "Correct any missed region using the dropdown, then click Next.")
    keys = ["mobs", "player"] + (["supplies"] if include_supplies else []) + ["patrol", "keys", "summary"]
    by_key = {step.key: step for step in WIZARD_STEPS}
    return [screen] + [by_key[key] for key in keys]


@dataclass
class WizardContext:
    """What the wizard knows so far (plain data, so the checks are testable)."""
    settings: Settings
    window_box: Optional[Box] = None             # client area found in step 1
    screenshot_size: Optional[Tuple[int, int]] = None   # (w, h) of the current screenshot
    minimap_view: Optional[Tuple[float, float, float, float]] = None
    dot_pos: Optional[Tuple[float, float]] = None       # dot seen with the current colours
    n_mob_templates: int = 0
    has_player_template: bool = False      # profile/templates/player.png exists
    patrol_left_set: Optional[float] = None
    patrol_right_set: Optional[float] = None
    patrol_preset: bool = False     # profile already had patrol edges -> may keep them
    patrol_left_y: Optional[float] = None    # minimap y at the Set-left reading
    patrol_right_y: Optional[float] = None


def step_index(key: str) -> int:
    for i, s in enumerate(WIZARD_STEPS):
        if s.key == key:
            return i
    raise KeyError(key)


def step_label(i: int, map_name: Optional[str] = None, steps=None) -> str:
    steps = WIZARD_STEPS if steps is None else steps
    text = f"Step {i + 1} of {len(steps)}: {steps[i].title}"
    return f"Setting up: {map_name} -- {text}" if map_name else text


def validate_box_on_screen(box: Optional[Sequence[int]], size: Optional[Tuple[int, int]],
                           what: str, min_w: int = 8, min_h: int = 8,
                           max_fraction: float = 0.6) -> Optional[str]:
    """Friendly problem with a box drawn on the screenshot, or None if it's fine."""
    if not box:
        return f"Drag a box around the {what} first."
    x, y, w, h = box
    if w < min_w or h < min_h:
        return f"That box is very small. Drag a bigger box around the {what}."
    if size:
        sw, sh = size
        if x < 0 or y < 0 or x + w > sw or y + h > sh:
            return f"The {what} box goes outside the screenshot. Draw it again."
        if w * h > max_fraction * sw * sh:
            return f"That box covers most of the screen. Drag a box around just the {what}."
    return None


def validate_bar_box(box: Optional[Sequence[int]], size: Optional[Tuple[int, int]],
                     what: str) -> Optional[str]:
    """HP/MP bars are optional, but if drawn they should be wide and short."""
    if not box:
        return None
    p = validate_box_on_screen(box, size, what, min_w=10, min_h=2, max_fraction=0.2)
    if p:
        return p
    if box[2] < 2 * box[3]:
        return f"The {what} box should be wide and short -- drag just along the bar."
    return None


def validate_patrol(left: Optional[float], right: Optional[float],
                    min_gap: float = PATROL_MIN_GAP) -> Optional[str]:
    if left is None and right is None:
        return "Set the left and right ends of your training area."
    if left is None:
        return "Set the left end too."
    if right is None:
        return "Set the right end too."
    if not (0.0 <= left <= 1.0 and 0.0 <= right <= 1.0):
        return "Those positions look wrong. Check the minimap box and try again."
    if left >= right:
        return ("The left end is not left of the right end. Walk to the LEFT end, click "
                "Set left, then walk to the RIGHT end and click Set right.")
    if right - left < min_gap:
        return "The two ends are too close together. Walk further apart and set them again."
    return None


def combine_patrol_y(left_y: Optional[float], right_y: Optional[float],
                     existing: Optional[float] = None) -> Optional[float]:
    """patrol_y = average minimap y of the Set-left / Set-right readings taken this time
    (one reading alone is used as-is); no readings -> keep ``existing``."""
    ys = [v for v in (left_y, right_y) if v is not None]
    if not ys:
        return existing
    return round(sum(ys) / len(ys), 3)


PATROL_Y_TOLERANCE_ABOVE_DEFAULT = 0.03


def tolerance_above(calibration) -> float:
    """calibration.patrol_y_tolerance_above, or its default if settings predate it."""
    v = getattr(calibration, "patrol_y_tolerance_above", None)
    return PATROL_Y_TOLERANCE_ABOVE_DEFAULT if v is None else float(v)


def patrol_band(calibration) -> Optional[Tuple[float, float]]:
    """(top, bottom) minimap-y fractions where the bot counts you as on the training
    platform: patrol_y - tolerance_above .. patrol_y + tolerance (below). None if unset."""
    y = getattr(calibration, "patrol_y", None)
    if y is None:
        return None
    return (max(0.0, y - tolerance_above(calibration)),
            min(1.0, y + float(calibration.patrol_y_tolerance)))


def patrol_y_warning(left_y: Optional[float], right_y: Optional[float], tolerance: float,
                     existing: Optional[float] = None) -> Optional[str]:
    """Warning if the two edge readings look like different bridges/platforms.

    ``tolerance`` should be patrol_y_tolerance_above: both ends of one bridge are at the
    same height, so they should agree closely. With only one reading this time, it is
    compared with the previously saved patrol_y.
    """
    if left_y is not None and right_y is not None:
        a, b = left_y, right_y
    else:
        one = left_y if left_y is not None else right_y
        if one is None or existing is None:
            return None
        a, b = one, existing
    if abs(a - b) > tolerance:
        return ("Those two spots look like different bridges (they're at different heights on "
                "the minimap). Set both edges on the same bridge, a little in from its ends.")
    return None


def validate_keys(keys) -> Optional[str]:
    """Problems with the keys step (attack + jump required, all different)."""
    if not keys.attack:
        return "Set your attack key."
    if not keys.jump:
        return "Set your jump key."
    seen: Dict[str, str] = {}
    for lbl, val in (("attack", keys.attack), ("jump", keys.jump), ("HP potion", keys.hp_potion),
                     ("MP potion", keys.mp_potion), ("walk left", keys.left), ("walk right", keys.right)):
        if not val:
            continue
        if val in seen:
            return f"Your {lbl} key and {seen[val]} key are the same ({val}). Each needs its own key."
        seen[val] = lbl
    return None


def step_problem(key: str, ctx: WizardContext) -> Optional[str]:
    """Why the user can't go past this step yet (None = OK to continue)."""
    c = ctx.settings.calibration
    size = ctx.screenshot_size
    if key == "screen":
        for check in ("window", "screenshot", "minimap", "dot", "bars"):
            problem = step_problem(check, ctx)
            if problem:
                return "Check Game and HUD: " + problem
        return None
    if key == "window":
        if ctx.window_box is None:
            return "Click Find window first. The game must be open and not minimised."
        if ctx.window_box[2] < 200 or ctx.window_box[3] < 150:
            return "That window is tiny. Is it the game? Check the title and try again."
        return None
    if key == "screenshot":
        return None if size else "Click Take screenshot first."
    if key == "minimap":
        return validate_box_on_screen(c.minimap_box, size, "minimap", max_fraction=0.3)
    if key == "dot":
        if ctx.dot_pos is None:
            return ("The bot can't see your dot yet. Click right on your yellow dot on the "
                    "minimap (try again if the green circle isn't on it).")
        return None
    if key == "mobs":
        if ctx.n_mob_templates <= 0:
            return "Download sprites for this map, or optionally draw a box around a monster."
        return None
    if key == "player":
        if not ctx.has_player_template:
            return ("Drag a box around the name tag under your character. It's required: "
                    "without it the bot can't tell exactly where you are and won't attack.")
        return None
    if key == "bars":
        if not c.hp_bar_box:
            return ("Drag a box along your red HP bar. It's required so the bot can stop "
                    "before your character dies.")
        return (validate_bar_box(c.hp_bar_box, size, "HP bar")
                or validate_bar_box(c.mp_bar_box, size, "MP bar"))
    if key == "patrol":
        if ctx.patrol_preset:
            left = ctx.patrol_left_set if ctx.patrol_left_set is not None else c.patrol_left
            right = ctx.patrol_right_set if ctx.patrol_right_set is not None else c.patrol_right
        else:
            left, right = ctx.patrol_left_set, ctx.patrol_right_set
        return validate_patrol(left, right)
    if key == "keys":
        return validate_keys(ctx.settings.keys)
    if key == "summary":
        probs = list(ctx.settings.problems())
        if ctx.n_mob_templates <= 0:
            probs.append("No mob pictures saved; download map sprites or add an example")
        if not ctx.has_player_template:
            probs.append("No name tag picture saved")
        return ("Not finished yet: " + "; ".join(probs) + ".") if probs else None
    return None


def summary_lines(ctx: WizardContext, player_template: Optional[bool] = None) -> List[str]:
    from maplebot.app.keymap import display_name

    s = ctx.settings
    c, k = s.calibration, s.keys
    if player_template is None:
        player_template = ctx.has_player_template

    def box(b):
        return "not set" if not b else f"{b[2]} x {b[3]} at ({b[0]}, {b[1]})"
    return [
        f"Game window: '{c.window_title}'" + (f" ({ctx.window_box[2]} x {ctx.window_box[3]})" if ctx.window_box else ""),
        f"Minimap: {box(c.minimap_box)}",
        f"Mob pictures: {ctx.n_mob_templates}",
        f"Name tag picture: {'yes' if player_template else 'NOT SET (required)'}",
        f"HP bar: {box(c.hp_bar_box) if c.hp_bar_box else 'NOT SET (required)'}",
        f"MP bar: {box(c.mp_bar_box) if c.mp_bar_box else 'not set (recommended for magicians)'}",
        f"Training area: {c.patrol_left:.2f} to {c.patrol_right:.2f} across the minimap"
        + (f", platform height {c.patrol_y:.2f}" if c.patrol_y is not None else ""),
        f"Attack: {display_name(k.attack)}   Jump: {display_name(k.jump)}   "
        f"HP potion: {display_name(k.hp_potion)}   MP potion: {display_name(k.mp_potion)}",
        f"Safe spot: {c.safe_spot if c.safe_spot is not None else 'not set'}",
        f"Potion estimates at run start: HP {s.retreat.hp_start}, MP {s.retreat.mp_start}",
        f"Skill reach: {s.skill.range_px} px   Time between casts: {s.skill.cooldown_s:.2f} s",
        f"Pause/resume: {s.safety.pause_hotkey.upper()}   Stop: {s.safety.stop_hotkey.upper()}",
    ]


# ---------------------------------------------------------------------------
# Main window status / errors
# ---------------------------------------------------------------------------

def format_runtime(seconds: Optional[float]) -> str:
    s = max(0, int(seconds or 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}"


def format_percent(v: Optional[float]) -> str:
    return "--" if v is None else f"{round(v * 100):d}%"


def format_potion_estimates(st) -> str:
    def count(kind):
        visible = getattr(st,kind+"_stock",None)
        estimate = getattr(st,kind+"_pots_estimated",None)
        return str(visible) if visible is not None else "--" if estimate is None else f"~{estimate}"
    return f"H:{count('hp')} M:{count('mp')}"


def format_status(st) -> Dict[str, str]:
    """Status -> label texts for the main window."""
    if not st.running:
        state = "Stopped"
    elif st.paused:
        state = "Paused"
    else:
        state = ("Observe: " if st.observe_only else "") + (st.state or "Running")
    return {
        "state": state,
        "action": ("would " if st.observe_only else "")+(st.action or "--").replace("_", " "),
        "mobs": str(st.n_mobs),
        "map_x": "--" if st.map_x is None else f"{st.map_x:.2f}",
        "hp": format_percent(st.hp),
        "potions": format_potion_estimates(st),
        "wallet_delta": "--" if st.wallet_delta is None else f"{st.wallet_delta:+,}",
        "net_hour": "--" if st.net_per_hour is None else f"{st.net_per_hour:+,.0f}",
        "mp": format_percent(st.mp),
        "attacks": str(st.attacks),
        "runtime": format_runtime(st.runtime_s),
        "fps": f"{st.fps:.1f}",
        "message": st.message or "",
    }


def is_countdown(message: str) -> bool:
    return (message or "").lower().startswith("starting in")


def countdown_text(st) -> str:
    """Big countdown label text ("" when not counting down).

    Uses ``Status.countdown_s``; falls back to the old "Starting in..." message for a
    runner that doesn't set it yet.
    """
    if not getattr(st, "running", False):
        return ""
    cd = getattr(st, "countdown_s", None)
    if cd is not None:
        if cd <= 0:
            return ""
        return f"Starting in {math.ceil(cd - 1e-9)}... click into the game"
    msg = getattr(st, "message", "") or ""
    return msg if is_countdown(msg) else ""


def hotkey_badge(st, settings: Settings) -> str:
    """Persistent warning while the pause/stop hotkeys don't work ("" when fine)."""
    if not getattr(st, "running", False) or getattr(st, "hotkeys_ok", True):
        return ""
    return (f"{settings.safety.pause_hotkey.upper()}/{settings.safety.stop_hotkey.upper()} "
            "not working -- use the buttons")


def start_button_text(settings: Settings) -> str:
    return (f"Start   (pause {settings.safety.pause_hotkey.upper()}, "
            f"stop {settings.safety.stop_hotkey.upper()})")


_LOG_HINT = "Details are in the logs folder (app.log)."


def friendly_error(exc: BaseException) -> Tuple[str, str]:
    """(title, message) for a dialog. Never a stack trace."""
    name = type(exc).__name__
    text = str(exc).strip()
    if name == "MapError":
        return "Map", text or "That can't be done."
    if name == "RunnerError":
        return "Can't start", text or "The bot couldn't start."
    if name == "WindowNotFound":
        return "Game window not found", (text or "Couldn't find the game window.") + \
            "\n\nOpen the game (windowed, not minimised) and check the window title in Settings."
    if name == "HotkeysUnavailable":
        return "Hotkeys unavailable", (text + "\n\nYou can still use the app's buttons.").strip()
    if isinstance(exc, FileNotFoundError):
        return "File missing", f"{text}\n\nTry running the setup wizard again.\n{_LOG_HINT}"
    if name == "BackendError" or isinstance(exc, OSError):
        detail = f"\n\n(Windows said: {text})" if text and len(text) < 200 else ""
        return "Can't press keys", ("Can't press keys in the game -- start the bot with start.bat "
                                    "so it runs as administrator." + detail)
    if isinstance(exc, MemoryError):
        return "Out of memory", f"Your PC ran out of memory. Close other programs and try again.\n{_LOG_HINT}"
    detail = f" ({text})" if text and len(text) < 200 else ""
    return "Something went wrong", f"The bot stopped because of an unexpected problem{detail}.\n\n{_LOG_HINT}"


# ---------------------------------------------------------------------------
# Maps (saved setups per map)
# ---------------------------------------------------------------------------

NOT_SET_UP = " (not set up)"
NEW_MAP_HINT = "e.g. The Tree That Grew II"

# Wizard steps whose values carry over when a map is created from another one.
KEPT_STEPS = ("window", "dot", "player", "bars", "keys")


def map_choices(maps: Sequence[Tuple[str, str]], current_id: str, current_name: str,
                is_set_up) -> List[Tuple[str, str]]:
    """[(map_id, label)] for the map dropdown. ``is_set_up(map_id) -> bool``.

    A brand-new profile has no saved maps yet; the current (default) map is still listed.
    """
    items = list(maps)
    if current_id not in {i for i, _ in items}:
        items.append((current_id, current_name))
    return [(i, n + ("" if is_set_up(i) else NOT_SET_UP)) for i, n in items]


def map_label_to_id(choices: Sequence[Tuple[str, str]], label: str) -> Optional[str]:
    for i, l in choices:
        if l == label:
            return i
    return None


def start_state(problems: Sequence[str], map_set_up: bool, running: bool,
                settings: Settings) -> Tuple[str, bool, str]:
    """(button text, enabled, action) for the Start button. action: "start" | "wizard"."""
    if running:
        return start_button_text(settings), False, "start"
    if not map_set_up:
        return "Run the setup wizard for this map", True, "wizard"
    if problems:
        return start_button_text(settings), False, "start"
    return start_button_text(settings), True, "start"


def kept_steps(settings: Settings, has_player_template: bool, map_set_up: bool,
               kept_from: Optional[str]) -> set:
    """Steps that can be kept as they are because this map was created from another one.

    Only for a map that isn't set up yet. Keys are shared by all maps, so they're always
    "kept" in that case; the others only if the copied value is actually there.
    """
    if map_set_up or not kept_from:
        return set()
    c = settings.calibration
    out = {"keys", "dot"}     # the dot step still checks the colours find you on THIS map
    if c.window_title:
        out.add("window")
    if has_player_template:
        out.add("player")
    if c.hp_bar_box:
        out.add("bars")
    return out


def kept_message(map_name: str) -> str:
    return f"Kept from your other map ({map_name}) -- click Next to keep it, or Redo to do it again."


def wizard_title(map_name: Optional[str]) -> str:
    return f"Setting up: {map_name}" if map_name else "MapleBot setup"


def delete_map_question(name: str) -> str:
    return f"Delete the setup for {name}? Its mob pictures are deleted too."


def clean_map_name(raw: Optional[str]) -> Optional[str]:
    """Trimmed name, or None if the user cancelled / left it empty."""
    if raw is None:
        return None
    name = " ".join(str(raw).split())
    return name or None


def pick_safe_spot(settings, click, view=None):
    """Image pixel click -> validated minimap marker; shared by wizard and tests."""
    from maplebot.mapmodel import MapModel
    box = settings.calibration.minimap_box
    if box is None:
        raise ValueError("Set the minimap box first")
    x,y,w,h = box
    px,py = click
    if not (x <= px < x+w and y <= py < y+h):
        raise ValueError("Click inside the minimap, on a known safe platform")
    point = image_to_map(settings,click,view)
    model = MapModel.from_settings(settings)
    dest=model.safe_platform(point, settings.behavior.edge_margin)
    if settings.calibration.map_reference:
        for source in model.route:
            if source!=dest.name and not model.path(source,dest.name):
                raise ValueError('No measured route from the training platform to this safe spot.')
    return point


def select_patrol_segment(ctx: WizardContext, start, end, player_pos):
    """Set a single-platform patrol from two screenshot points, after all checks.

    Unknown geometry requires the player's dot inside the selected span/height band;
    source spawn dots are never taken as evidence of a walkable connection.
    """
    from maplebot.mapmodel import MapModel
    c = ctx.settings.calibration
    if c.minimap_box is None:
        raise ValueError("Detect the minimap first.")
    if len(c.route)>1:
        raise ValueError("This map uses a multi-platform route. Edit its route in Settings; "
                         "this quick selector is for one platform.")
    x, y, w, h = c.minimap_box
    if w <= 0 or h <= 0:
        raise ValueError("Detect a valid minimap first.")
    def point(value):
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                       or not math.isfinite(v) for v in value)):
            raise ValueError("Drag between two points inside the minimap.")
        px, py = value
        if not (x <= px < x+w and y <= py < y+h):
            raise ValueError("Keep both training limits inside the minimap.")
        return tuple(image_to_map(ctx.settings,value,ctx.minimap_view))
    left, right = sorted((point(start), point(end)))
    problem = validate_patrol(left[0], right[0])
    if problem:
        raise ValueError("Drag a wider span from left to right on one platform.")
    platform = None
    if c.platforms:
        model = MapModel.from_settings(ctx.settings)
        a = model.safe_platform(list(left), ctx.settings.behavior.edge_margin)
        b = model.safe_platform(list(right), ctx.settings.behavior.edge_margin)
        if a.name != b.name:
            raise ValueError("Both limits must be on the same known platform.")
        platform = a.name
    else:
        problem = patrol_y_warning(left[1], right[1], tolerance_above(c))
        if problem:
            raise ValueError(problem)
        if (player_pos is None or len(player_pos) != 2
                or any(not isinstance(v, (float, int)) or isinstance(v, bool)
                       or not math.isfinite(v) or not 0 <= v <= 1 for v in player_pos)):
            raise ValueError("Refresh the minimap so your own dot is visible first.")
        anchor = (left[1]+right[1])/2
        if not (left[0] <= player_pos[0] <= right[0] and
                anchor-tolerance_above(c) <= player_pos[1] <= anchor+c.patrol_y_tolerance):
            raise ValueError("Include your green dot and select the platform you are standing on. "
                             "Move there once and refresh if choosing another platform.")
    # No changes occur until every validation above has passed.
    ctx.patrol_left_set = c.patrol_left = left[0]
    ctx.patrol_right_set = c.patrol_right = right[0]
    ctx.patrol_left_y, ctx.patrol_right_y = left[1], right[1]
    c.patrol_y = (left[1]+right[1])/2
    if platform is not None:
        c.route = [platform]
    return platform


def image_to_map(settings, click, view=None):
    """Screen point -> the configured coordinate basis, refusing missing alignment."""
    c=settings.calibration
    x,y,w,h=c.minimap_box
    if w<=0 or h<=0:
        raise ValueError('Detect a valid minimap first.')
    if c.map_reference and view is None:
        raise ValueError('Map alignment is missing. Refresh the minimap before selecting a point.')
    dx,dy,bx,by=view if view is not None else (0,0,w,h)
    point=[(click[0]+.5-x-dx)/bx,(click[1]+.5-y-dy)/by]
    if not all(math.isfinite(v) and 0<=v<=1 for v in point):
        raise ValueError('Choose a point inside the map.')
    return point
