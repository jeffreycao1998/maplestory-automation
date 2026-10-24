"""Display-free app logic: coordinates, form parsing, wizard validation, status text."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from maplebot.app import logic as L
from maplebot.settings import Keys, Settings
from maplebot.types import Status


# ---------------------------------------------------------------- coordinates

def test_fit_scale_downscales_and_keeps_aspect():
    assert L.fit_scale(1366, 768, 960, 540) == pytest.approx(min(960 / 1366, 540 / 768))
    assert L.fit_scale(400, 300, 960, 540) == 1.0                      # no upscale by default
    assert L.fit_scale(100, 50, 960, 540, allow_upscale=True) == pytest.approx(8.0)  # capped
    assert L.fit_scale(0, 10, 100, 100) == 1.0


@pytest.mark.parametrize("corners", [
    (10, 20, 110, 70),    # top-left -> bottom-right
    (110, 70, 10, 20),    # bottom-right -> top-left
    (110, 20, 10, 70),    # top-right -> bottom-left
    (10, 70, 110, 20),    # bottom-left -> top-right
])
def test_drag_any_direction_gives_same_box(corners):
    v = L.View.fit(1366, 768, 1366, 768)          # scale 1
    assert v.drag_to_box(*corners) == (10, 20, 100, 50)


@pytest.mark.parametrize("corners", [(0, 0, 200, 100), (200, 100, 0, 0), (200, 0, 0, 100), (0, 100, 200, 0)])
def test_drag_scaled_maps_to_real_pixels(corners):
    v = L.View.fit(1366, 768, 683, 384)            # exactly half size
    assert v.scale == pytest.approx(0.5)
    assert v.drag_to_box(*corners) == (0, 0, 400, 200)


def test_drag_clamped_to_image_bounds():
    v = L.View.fit(1366, 768, 683, 384)
    box = v.drag_to_box(-50, -50, 10000, 10000)
    assert box == (0, 0, 1366, 768)
    x, y, w, h = v.drag_to_box(600, 300, 900, 600)
    assert x + w <= 1366 and y + h <= 768


def test_tiny_drag_is_a_click():
    v = L.View.fit(1366, 768, 1366, 768)
    assert v.drag_to_box(10, 10, 11, 11) is None
    assert v.drag_to_box(10, 10, 50, 11) is None


def test_round_trip_canvas_image_with_region_zoom():
    mm = (8, 30, 160, 120)
    v = L.View.fit(1366, 768, 900, 480, region=mm, zoom=True)
    assert v.scale == pytest.approx(min(900 / 160, 480 / 120))
    for px, py in [(8, 30), (50, 77), (167, 149)]:
        cx, cy = v.to_canvas(px + 0.5, py + 0.5)
        assert v.to_image(cx, cy) == (px, py)
    # drag the whole zoomed canvas -> exactly the region
    cw, ch = v.canvas_size
    assert v.drag_to_box(0, 0, cw, ch) == mm
    assert v.drag_to_box(cw, ch, 0, 0) == mm


def test_to_image_clamps_outside_points():
    v = L.View.fit(1366, 768, 683, 384)
    assert v.to_image(-10, -10) == (0, 0)
    assert v.to_image(5000, 5000) == (1365, 767)


def test_box_to_canvas_inverse_of_drag():
    v = L.View.fit(1920, 1080, 960, 540)
    box = (300, 200, 120, 80)
    assert v.drag_to_box(*v.box_to_canvas(box)) == box


def test_region_partly_outside_image_is_clamped():
    v = L.View.fit(800, 600, 500, 500, region=(-6, -6, 100, 100), zoom=True)
    assert (v.ox, v.oy, v.w, v.h) == (0, 0, 94, 94)


def test_clamp_box():
    assert L.clamp_box((10, 10, 50, 50), 40, 40) == (10, 10, 30, 30)
    assert L.clamp_box((-5, -5, 10, 10), 40, 40) == (0, 0, 5, 5)
    assert L.clamp_box((50, 50, 10, 10), 40, 40) is None
    assert L.clamp_box((0, 0, 0, 10), 40, 40) is None
    assert L.clamp_box(None, 40, 40) is None
    assert L.clamp_box(("a", 0, 1, 1), 40, 40) is None


def test_normalize_rect():
    assert L.normalize_rect(5, 9, 1, 2) == (1, 2, 5, 9)


def test_minimap_dot_to_image_and_preview_size():
    assert L.minimap_dot_to_image((10, 20, 100, 50), (0.5, 0.5)) == (60, 45)
    assert L.preview_size(1366, 768) == (480, 270)
    assert L.preview_size(320, 240) == (320, 240)


# ---------------------------------------------------------------- images

def test_crop_and_save_crop(tmp_path):
    img = np.zeros((100, 200, 3), np.uint8)
    img[10:20, 30:50] = (0, 0, 255)                # red in BGR
    c = L.crop(img, (30, 10, 20, 10))
    assert c.shape == (10, 20, 3) and (c == (0, 0, 255)).all()
    p = L.save_crop(img, (30, 10, 20, 10), tmp_path / "t" / "mob_1.png")
    from PIL import Image
    with Image.open(p) as im:
        assert im.size == (20, 10)
        assert im.convert("RGB").getpixel((0, 0)) == (255, 0, 0)   # saved as RGB correctly
    with pytest.raises(ValueError):
        L.save_crop(img, (500, 500, 10, 10), tmp_path / "x.png")


def test_next_mob_template_path(tmp_path):
    assert L.next_mob_template_path(tmp_path).name == "mob_1.png"
    (tmp_path / "mob_1.png").write_bytes(b"")
    (tmp_path / "mob_7.png").write_bytes(b"")
    (tmp_path / "mob_x.png").write_bytes(b"")
    assert L.next_mob_template_path(tmp_path).name == "mob_8.png"


def test_looks_blank():
    assert L.looks_blank(np.zeros((100, 100, 3), np.uint8))
    rng = np.random.default_rng(0)
    assert not L.looks_blank(rng.integers(0, 255, (100, 100, 3), dtype=np.uint8))
    assert L.looks_blank(None)


# ---------------------------------------------------------------- settings form

def test_every_settings_field_appears_in_form():
    specs = L.iter_fields()
    keys = {s.key for s in specs}
    d = Settings()
    import dataclasses
    for sec in dataclasses.fields(Settings):
        val = getattr(d, sec.name)
        if dataclasses.is_dataclass(val):
            for f in dataclasses.fields(val):
                assert (sec.name, f.name) in keys
        else:
            assert (L.GENERAL, sec.name) in keys


def test_known_fields_have_labels_and_tooltips():
    for s in L.iter_fields():
        assert s.label
        if s.key in L.FIELD_INFO:
            assert s.tooltip


def test_unknown_field_gets_humanized_label():
    import dataclasses as dc

    @dc.dataclass
    class Extra:
        brand_new_delay_s: float = 1.0

    @dc.dataclass
    class S2:
        extra: Extra = dc.field(default_factory=Extra)
        some_flag: bool = False

    specs = L.iter_fields(S2)
    assert specs[0].label == "Brand new delay (seconds)" and specs[0].kind == "float"
    assert specs[1].section == L.GENERAL and specs[1].kind == "bool"


def test_kinds():
    k = {s.key: s.kind for s in L.iter_fields()}
    assert k[("keys", "attack")] == "key"
    assert k[("keys", "hp_potion")] == "optional_key"
    assert k[("calibration", "minimap_box")] == "box"
    assert k[("calibration", "player_dot_hsv_low")] == "hsv"
    assert k[("skill", "range_px")] == "int"
    assert k[("skill", "cooldown_s")] == "float"
    assert k[("detection", "mirror_templates")] == "bool"
    assert k[("safety", "pause_hotkey")] == "hotkey"


def test_humanize():
    assert L.humanize("no_mobs_pause_s") == "No mobs pause (seconds)"
    assert L.humanize("range_px") == "Range (pixels)"
    assert L.humanize("debug_preview") == "Debug preview"


def spec(section, name):
    return next(s for s in L.iter_fields() if s.key == (section, name))


def test_parse_int_float():
    assert L.parse_value(spec("skill", "range_px"), " 400 ") == 400
    assert L.parse_value(spec("skill", "range_px"), "400.0") == 400
    assert L.parse_value(spec("skill", "cooldown_s"), "0.7") == pytest.approx(0.7)
    assert L.parse_value(spec("skill", "cooldown_s"), "0,7") == pytest.approx(0.7)   # comma decimal
    with pytest.raises(L.FieldError, match="whole number"):
        L.parse_value(spec("skill", "range_px"), "12.5")
    with pytest.raises(L.FieldError, match="isn't a number"):
        L.parse_value(spec("skill", "cooldown_s"), "fast")
    with pytest.raises(L.FieldError, match="enter a number"):
        L.parse_value(spec("skill", "cooldown_s"), "")
    with pytest.raises(L.FieldError, match="between"):
        L.parse_value(spec("detection", "mob_threshold"), "1.5")
    with pytest.raises(L.FieldError):
        L.parse_value(spec("skill", "cooldown_s"), "nan")


def test_parse_error_names_the_field():
    with pytest.raises(L.FieldError) as e:
        L.parse_value(spec("skill", "cooldown_s"), "abc")
    assert str(e.value).startswith("Time between casts")


def test_parse_bool():
    s = spec("detection", "mirror_templates")
    assert L.parse_value(s, True) is True
    assert L.parse_value(s, "yes") is True
    assert L.parse_value(s, "0") is False
    with pytest.raises(L.FieldError):
        L.parse_value(s, "maybe")


def test_parse_box():
    s = spec("calibration", "minimap_box")
    assert L.parse_value(s, "10, 20, 160, 120") == [10, 20, 160, 120]
    assert L.parse_value(s, "[10 20 160 120]") == [10, 20, 160, 120]
    assert L.parse_value(s, "") is None
    with pytest.raises(L.FieldError, match="4 whole numbers"):
        L.parse_value(s, "1, 2, 3")
    with pytest.raises(L.FieldError, match="bigger than 0"):
        L.parse_value(s, "1, 2, 0, 5")
    with pytest.raises(L.FieldError, match="negative"):
        L.parse_value(s, "-1, 2, 3, 5")


def test_parse_hsv():
    s = spec("calibration", "player_dot_hsv_low")
    assert L.parse_value(s, "20,150,180") == [20, 150, 180]
    with pytest.raises(L.FieldError, match="hue"):
        L.parse_value(s, "200, 1, 1")
    with pytest.raises(L.FieldError, match="3 whole numbers"):
        L.parse_value(s, "1, 2")


def test_parse_lists_generic():
    s = L.FieldSpec("x", "y", "Things", "", "float_list", [])
    assert L.parse_value(s, "1, 2.5; 3") == [1.0, 2.5, 3.0]
    s2 = L.FieldSpec("x", "y", "Maybe", "", "optional_int_list", None)
    assert L.parse_value(s2, "") is None
    assert L.parse_value(s2, "1 2") == [1, 2]


def test_parse_keys():
    s = spec("keys", "attack")
    assert L.parse_value(s, "Ctrl") == "ctrl"
    assert L.parse_value(s, "Page Up") == "pageup"
    with pytest.raises(L.FieldError, match="pick a key"):
        L.parse_value(s, "")
    with pytest.raises(L.FieldError):
        L.parse_value(s, "notakey")
    opt = spec("keys", "hp_potion")
    assert L.parse_value(opt, "") == ""
    assert L.parse_value(opt, "(none)") == ""
    assert L.parse_value(opt, "delete") == "delete"


def test_parse_hotkey():
    s = spec("safety", "pause_hotkey")
    assert L.parse_value(s, "F11") == "f11"
    with pytest.raises(L.FieldError):
        L.parse_value(s, "")


def test_format_value():
    assert L.format_value("float", 0.65) == "0.65"
    assert L.format_value("float", 3.0) == "3"
    assert L.format_value("box", None) == ""
    assert L.format_value("box", [1, 2, 3, 4]) == "1, 2, 3, 4"
    assert L.format_value("bool", 1) is True
    assert L.format_value("int", 350) == "350"


def test_format_parse_round_trip_for_defaults():
    d = Settings()
    raw = {s.key: L.format_value(s.kind, L.get_value(d, s)) for s in L.iter_fields()}
    new, errors = L.build_settings(d, raw)
    assert errors == []
    assert new.to_dict() == d.to_dict()


def test_build_settings_applies_and_does_not_mutate_base():
    base = Settings()
    new, errors = L.build_settings(base, {("skill", "range_px"): "420",
                                          ("calibration", "minimap_box"): "5,6,150,100",
                                          (L.GENERAL, "debug_preview"): False})
    assert errors == []
    assert new.skill.range_px == 420 and new.calibration.minimap_box == [5, 6, 150, 100]
    assert new.debug_preview is False
    assert base.skill.range_px == 350 and base.calibration.minimap_box is None


def test_build_settings_collects_all_errors():
    _, errors = L.build_settings(Settings(), {("skill", "range_px"): "x",
                                              ("skill", "cooldown_s"): "y"})
    assert len(errors) == 2


def test_cross_field_errors():
    s = Settings()
    s.calibration.patrol_left, s.calibration.patrol_right = 0.8, 0.2
    s.keys.jump = "ctrl"
    s.safety.stop_hotkey = "f11"
    errs = L.cross_field_errors(s)
    assert any("Patrol" in e for e in errs)
    assert any("same key" in e for e in errs)
    assert any("Pause and Stop" in e for e in errs)
    s2 = Settings()
    s2.keys.hp_potion = "f11"
    assert any("hotkey" in e for e in L.cross_field_errors(s2))
    assert L.cross_field_errors(Settings()) == []


def test_defaults_for_section():
    d = L.defaults_for_section("skill")
    assert d[("skill", "range_px")] == "350"


def test_sections_order_and_labels():
    secs = L.sections(L.iter_fields())
    assert [s[0] for s in secs][:2] == ["keys", "skill"]
    assert secs[0][1] == "Keys" and secs[-1][0] == L.GENERAL


# ---------------------------------------------------------------- wizard

def ctx(**kw):
    c = L.WizardContext(settings=Settings())
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_wizard_steps_shape():
    keys = [s.key for s in L.WIZARD_STEPS]
    assert keys == ["window", "screenshot", "minimap", "dot", "mobs", "player", "bars",
                    "patrol", "keys", "summary"]
    assert not [s.key for s in L.WIZARD_STEPS if s.optional]   # name tag + HP bar required
    for s in L.WIZARD_STEPS:
        assert 10 < len(s.instruction) < 320
    assert L.step_label(0) == "Step 1 of 10: Game window"
    assert L.step_index("patrol") == 7


def test_step_window():
    assert L.step_problem("window", ctx())
    assert L.step_problem("window", ctx(window_box=(0, 0, 100, 80)))
    assert L.step_problem("window", ctx(window_box=(0, 0, 1366, 768))) is None


def test_step_screenshot():
    assert L.step_problem("screenshot", ctx())
    assert L.step_problem("screenshot", ctx(screenshot_size=(1366, 768))) is None


def test_step_minimap():
    c = ctx(screenshot_size=(1366, 768))
    assert "first" in L.step_problem("minimap", c)
    c.settings.calibration.minimap_box = [5, 5, 4, 4]
    assert "small" in L.step_problem("minimap", c)
    c.settings.calibration.minimap_box = [0, 0, 1300, 700]
    assert "most of the screen" in L.step_problem("minimap", c)
    c.settings.calibration.minimap_box = [1300, 0, 200, 100]
    assert "outside" in L.step_problem("minimap", c)
    c.settings.calibration.minimap_box = [8, 30, 160, 120]
    assert L.step_problem("minimap", c) is None


def test_step_dot_and_mobs():
    assert L.step_problem("dot", ctx())
    assert L.step_problem("dot", ctx(dot_pos=(0.4, 0.5))) is None
    assert L.step_problem("mobs", ctx())
    assert L.step_problem("mobs", ctx(n_mob_templates=2)) is None
    assert "required" in L.step_problem("player", ctx())
    assert L.step_problem("player", ctx(has_player_template=True)) is None


def test_step_bars():
    c = ctx(screenshot_size=(1366, 768))
    assert "required" in L.step_problem("bars", c)                  # HP bar is required
    c.settings.calibration.mp_bar_box = [612, 751, 103, 14]
    assert "HP bar" in L.step_problem("bars", c)                    # MP alone isn't enough
    c.settings.calibration.mp_bar_box = None
    c.settings.calibration.hp_bar_box = [504, 751, 103, 14]
    assert L.step_problem("bars", c) is None                        # MP stays optional
    c.settings.calibration.hp_bar_box = [500, 600, 20, 60]          # tall, not a bar
    assert "wide and short" in L.step_problem("bars", c)
    c.settings.calibration.hp_bar_box = [500, 740, 140, 12]
    assert L.step_problem("bars", c) is None
    c.settings.calibration.mp_bar_box = [500, 740, 900, 300]
    assert L.step_problem("bars", c)


def test_validate_patrol():
    assert L.validate_patrol(None, None)
    assert "right end" in L.validate_patrol(0.2, None)
    assert "left end" in L.validate_patrol(None, 0.8)
    assert "not left of" in L.validate_patrol(0.8, 0.2)
    assert "not left of" in L.validate_patrol(0.5, 0.5)
    assert "too close" in L.validate_patrol(0.50, 0.53)
    assert L.validate_patrol(0.50, 0.55) is None
    assert L.validate_patrol(0.2, 0.8) is None
    assert L.validate_patrol(-0.1, 0.8)


def test_step_patrol_requires_fresh_edges_unless_preset():
    c = ctx()                                          # defaults 0.3 / 0.7 don't count
    assert L.step_problem("patrol", c)
    c.patrol_left_set = 0.25
    assert "right end" in L.step_problem("patrol", c)
    c.patrol_right_set = 0.65
    assert L.step_problem("patrol", c) is None
    c2 = ctx(patrol_preset=True)                       # already-set-up profile may keep them
    assert L.step_problem("patrol", c2) is None
    c2.patrol_right_set = 0.1
    assert L.step_problem("patrol", c2)


def test_validate_keys():
    assert L.validate_keys(Keys()) is None
    assert "attack" in L.validate_keys(Keys(attack=""))
    assert "jump" in L.validate_keys(Keys(jump=""))
    assert "same" in L.validate_keys(Keys(attack="ctrl", jump="ctrl"))
    assert "same" in L.validate_keys(Keys(hp_potion="left"))
    assert L.validate_keys(Keys(hp_potion="delete", mp_potion="end")) is None


def test_step_summary():
    c = ctx(n_mob_templates=1, has_player_template=True)
    assert "Minimap" in L.step_problem("summary", c)
    c.settings.calibration.minimap_box = [8, 30, 160, 120]
    assert "HP bar" in L.step_problem("summary", c)
    c.settings.calibration.hp_bar_box = [504, 751, 103, 14]
    assert L.step_problem("summary", c) is None
    c.n_mob_templates = 0
    assert "mob pictures" in L.step_problem("summary", c)
    c.n_mob_templates, c.has_player_template = 1, False
    assert "name tag" in L.step_problem("summary", c)


def test_summary_lines():
    c = ctx(window_box=(0, 0, 1366, 768), n_mob_templates=2)
    c.settings.calibration.minimap_box = [8, 30, 160, 120]
    c.settings.keys.mp_potion = "delete"
    text = "\n".join(L.summary_lines(c, player_template=False))
    assert "1366 x 768" in text and "160 x 120 at (8, 30)" in text
    assert "Mob pictures: 2" in text and "Delete" in text and "F11" in text


# ---------------------------------------------------------------- status / errors

def test_format_status():
    st = Status(running=True, state="ENGAGE", action="attack", n_mobs=3, map_x=0.4567,
                hp=0.83, mp=None, fps=14.96, runtime_s=3725, attacks=12, message="Running")
    f = L.format_status(st)
    assert f["state"] == "ENGAGE" and f["map_x"] == "0.46" and f["hp"] == "83%"
    assert f["mp"] == "--" and f["runtime"] == "1:02:05" and f["fps"] == "15.0"
    assert L.format_status(Status())["state"] == "Stopped"
    assert L.format_status(Status(running=True, paused=True))["state"] == "Paused"
    assert L.format_status(Status(running=True, action="walk_left"))["action"] == "walk left"


def test_countdown_and_start_text():
    assert L.is_countdown("Starting in 3… click into the game")
    assert not L.is_countdown("Running")
    assert not L.is_countdown(None)
    assert "F11" in L.start_button_text(Settings()) and "F12" in L.start_button_text(Settings())


def test_friendly_error_never_traceback():
    class RunnerError(RuntimeError):
        pass

    class WindowNotFound(RuntimeError):
        pass

    class HotkeysUnavailable(RuntimeError):
        pass

    t, m = L.friendly_error(RunnerError("No mob pictures saved yet."))
    assert t == "Can't start" and m == "No mob pictures saved yet."
    t, m = L.friendly_error(WindowNotFound("Couldn't find 'MapleStory'"))
    assert "windowed" in m
    t, m = L.friendly_error(HotkeysUnavailable("pynput missing"))
    assert "buttons" in m
    t, m = L.friendly_error(PermissionError("denied"))
    assert "start.bat" in m
    t, m = L.friendly_error(OSError(5, "SendInput failed: access denied"))
    assert t == "Can't press keys" and "administrator" in m and "start.bat" in m
    t, m = L.friendly_error(FileNotFoundError("mob_1.png"))
    assert t == "File missing"                                      # not the key-press dialog
    t, m = L.friendly_error(ZeroDivisionError("division by zero"))
    assert "Traceback" not in m and "logs" in m


# ---------------------------------------------------------------- imports

def test_logic_and_keymap_do_not_import_tkinter():
    code = ("import sys; import maplebot.app.logic, maplebot.app.keymap; "
            "from maplebot.app import logic; logic.iter_fields(); "
            "logic.parse_value(logic.iter_fields()[0], 'ctrl'); "
            "sys.exit(1 if 'tkinter' in sys.modules else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**__import__("os").environ,
                            "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")})
    assert r.returncode == 0, r.stderr


def test_all_app_modules_compile():
    import pathlib
    import py_compile
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "maplebot"
    files = list((root / "app").glob("*.py")) + [root / "__main__.py"]
    assert {f.name for f in files} >= {"gui.py", "wizard.py", "settings_form.py", "logic.py",
                                       "keymap.py", "widgets.py", "__main__.py"}
    for f in files:
        py_compile.compile(str(f), doraise=True)


# ---------------------------------------------------------------- dot click (real frame)

REAL = __import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "labeled" / "tree-that-grew-2" / "000001.png"


def test_dot_near_click():
    box = (10, 10, 100, 50)
    assert L.dot_near_click(box, (0.5, 0.5), (59, 34))
    assert not L.dot_near_click(box, (0.9, 0.5), (59, 34))
    assert not L.dot_near_click(box, None, (59, 34))


def test_pick_dot_range_falls_back_to_stricter_candidate():
    box = (0, 0, 100, 100)
    cands = [([1, 1, 1], [9, 9, 9]), ([2, 2, 2], [9, 9, 9])]
    where = {1: (0.9, 0.9), 2: (0.2, 0.3)}
    lo, hi, pos = L.pick_dot_range(cands, lambda lo, hi: where[lo[0]], box, (20, 30))
    assert lo == [2, 2, 2] and pos == (0.2, 0.3)
    lo, hi, pos = L.pick_dot_range(cands, lambda lo, hi: (0.9, 0.9), box, (20, 30))
    assert lo is None and pos == (0.9, 0.9)


@pytest.mark.skipif(not REAL.exists(), reason="labelled frame missing")
@pytest.mark.parametrize("click,ok", [((98, 130), True), ((97, 129), True), ((99, 131), True),
                                      ((76, 124), False)])   # last = orange party-member dot
def test_wizard_dot_click_on_real_frame(click, ok):
    import cv2
    from maplebot.perception.calib import hsv_range_from_sample, minimap_player_position
    img = cv2.imread(str(REAL))
    box = [15, 81, 166, 112]                                  # labelled minimap box
    cands = L.dot_range_candidates(hsv_range_from_sample(img, *click))
    lo, hi, pos = L.pick_dot_range(cands, lambda a, b: minimap_player_position(img, box, a, b), box, click)
    if ok:
        assert lo is not None
        assert pos == pytest.approx((0.506, 0.446), abs=0.02)   # labelled player_map
    else:
        assert lo is None


# ---------------------------------------------------------------- review follow-ups

def test_patrol_y_fields_in_form_with_tooltips():
    specs = {sp.key: sp for sp in L.iter_fields()}
    for k in (("calibration", "patrol_y"), ("calibration", "patrol_y_tolerance")):
        assert specs[k].tooltip
    assert specs[("calibration", "patrol_y")].kind == "optional_float"
    sp = specs[("calibration", "patrol_y")]
    assert L.parse_value(sp, "") is None
    assert L.parse_value(sp, "0.45") == pytest.approx(0.45)
    with pytest.raises(L.FieldError):
        L.parse_value(sp, "1.5")
    with pytest.raises(L.FieldError):
        L.parse_value(specs[("calibration", "patrol_y_tolerance")], "0")


def test_combine_patrol_y():
    assert L.combine_patrol_y(0.40, 0.44) == pytest.approx(0.42)
    assert L.combine_patrol_y(0.40, None) == pytest.approx(0.40)
    assert L.combine_patrol_y(None, None, existing=0.3) == 0.3
    assert L.combine_patrol_y(None, None) is None


def test_patrol_y_warning_different_bridges():
    assert L.patrol_y_warning(0.44, 0.46, 0.04) is None
    w = L.patrol_y_warning(0.20, 0.45, 0.04)
    assert w and "different bridges" in w
    assert L.patrol_y_warning(0.45, None, 0.04) is None                 # nothing to compare yet
    assert "different bridges" in L.patrol_y_warning(0.70, None, 0.04, existing=0.45)
    assert L.patrol_y_warning(None, None, 0.04, existing=0.45) is None


def test_patrol_instruction_mentions_one_bridge():
    st = L.WIZARD_STEPS[L.step_index("patrol")]
    assert "same bridge" in st.instruction and "in from its ends" in st.instruction


def test_summary_mentions_hp_required_and_patrol_y():
    c = ctx()
    c.settings.calibration.patrol_y = 0.446
    text = "\n".join(L.summary_lines(c))
    assert "required" in text and "recommended for magicians" in text and "0.45" in text
    assert "Name tag picture: NOT SET (required)" in text
    c.has_player_template = True
    assert "Name tag picture: yes" in "\n".join(L.summary_lines(c))


def test_countdown_text_uses_countdown_s():
    St = SimpleNamespace
    assert L.countdown_text(St(running=True, countdown_s=2.4, message="")) == \
        "Starting in 3... click into the game"
    assert L.countdown_text(St(running=True, countdown_s=0.0, message="Starting in 1")) == ""
    assert L.countdown_text(St(running=False, countdown_s=2.0, message="")) == ""
    # countdown_s None -> no countdown even if a message happens to start that way
    assert L.countdown_text(St(running=True, countdown_s=None, message="Running")) == ""
    # older runner without the field: falls back to the message
    assert L.countdown_text(St(running=True, message="Starting in 2… click")) == "Starting in 2… click"
    assert L.countdown_text(Status(running=True)) == ""


def test_status_message_shown_verbatim():
    msg = "Hotkeys unavailable: run as admin. Use the app's buttons to pause/stop."
    assert L.format_status(Status(running=True, message=msg))["message"] == msg


def test_pid_file_written_and_removed(tmp_path):
    code = ("import sys, pathlib; sys.modules['tkinter'] = None\n"
            "from maplebot.app.gui import write_pid_file\n"
            "p = write_pid_file(pathlib.Path(sys.argv[1]))\n"
            "import os; assert p.read_text().strip() == str(os.getpid())\n"
            "print('written')\n")
    root = __import__("pathlib").Path(__file__).resolve().parents[2] / "src"
    r = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True,
                       env={**__import__("os").environ, "PYTHONPATH": str(root)})
    assert r.returncode == 0 and "written" in r.stdout, r.stderr
    assert not (tmp_path / "app.pid").exists()                 # removed at exit


def test_start_bat_robust_quoting():
    bat = (__import__("pathlib").Path(__file__).resolve().parents[2] / "start.bat").read_bytes()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")   # CRLF only
    text = bat.decode("ascii")
    assert "$env:MAPLEBOT_SELF" in text and "'%~f0'" not in text       # path not in PS source
    assert "app.pid" in text and 'imagename eq pythonw.exe' not in text
    # no parenthesised blocks that a ')' in the folder path could break
    import re
    assert not re.search(r"^\s*if [^\r\n]*\($", text, re.M)


# ---------------------------------------------------------------- re-review round 2

def test_name_tag_step_text():
    st = L.WIZARD_STEPS[L.step_index("player")]
    assert not st.optional and "YOUR character" in st.instruction
    assert "exactly where you are" in st.instruction


def test_tolerance_above_field_and_band():
    from maplebot.settings import Calibration
    c = Calibration()
    assert L.tolerance_above(c) == pytest.approx(getattr(c, "patrol_y_tolerance_above", 0.03))
    assert L.patrol_band(c) is None
    c.patrol_y, c.patrol_y_tolerance = 0.45, 0.10
    c.patrol_y_tolerance_above = 0.03
    assert L.patrol_band(c) == pytest.approx((0.42, 0.55))      # asymmetric: small above, big below
    c.patrol_y = 0.98
    assert L.patrol_band(c)[1] == 1.0
    assert ("calibration", "patrol_y_tolerance_above") in L.FIELD_INFO
    assert ("calibration", "patrol_y_tolerance_above") in L.FIELD_LIMITS


def test_tolerance_above_in_form_when_settings_has_it():
    import dataclasses
    from maplebot.settings import Calibration
    names = {f.name for f in dataclasses.fields(Calibration)}
    if "patrol_y_tolerance_above" not in names:
        pytest.skip("settings.py doesn't have patrol_y_tolerance_above yet")
    sp = next(x for x in L.iter_fields() if x.key == ("calibration", "patrol_y_tolerance_above"))
    assert "ABOVE" in sp.tooltip and sp.kind == "float"


def test_different_bridges_uses_tolerance_above():
    from maplebot.settings import Calibration
    c = Calibration()
    c.patrol_y_tolerance = 0.10
    c.patrol_y_tolerance_above = 0.03
    tol = L.tolerance_above(c)
    # 0.05 apart: inside the generous "below" tolerance, but two ends of one bridge
    # should match closely, so it's flagged.
    assert L.patrol_y_warning(0.40, 0.45, tol)
    assert L.patrol_y_warning(0.40, 0.42, tol) is None


def test_hotkey_badge():
    s = Settings()
    assert L.hotkey_badge(SimpleNamespace(running=True, hotkeys_ok=False), s) == \
        "F11/F12 not working -- use the buttons"
    assert L.hotkey_badge(SimpleNamespace(running=True, hotkeys_ok=True), s) == ""
    assert L.hotkey_badge(SimpleNamespace(running=False, hotkeys_ok=False), s) == ""
    assert L.hotkey_badge(Status(running=True), s) == ""          # default hotkeys_ok


def _run_py(code, *args):
    import os
    root = __import__("pathlib").Path(__file__).resolve().parents[2] / "src"
    return subprocess.Popen([sys.executable, "-c", code, *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONPATH": str(root)})


def test_single_instance_lock(tmp_path):
    holder = _run_py("import sys, pathlib; sys.modules['tkinter'] = None\n"
                     "from maplebot.app.gui import acquire_single_instance\n"
                     "f = acquire_single_instance(pathlib.Path(sys.argv[1]))\n"
                     "print('locked', flush=True); sys.stdin.readline()\n", str(tmp_path))
    try:
        assert holder.stdout.readline().strip() == "locked"
        second = _run_py("import sys, pathlib; sys.modules['tkinter'] = None\n"
                         "from maplebot.app.gui import acquire_single_instance, AlreadyRunning\n"
                         "try:\n    acquire_single_instance(pathlib.Path(sys.argv[1]))\n"
                         "except AlreadyRunning as e:\n    print('blocked:', e)\n", str(tmp_path))
        out, err = second.communicate(timeout=30)
        assert "blocked: The bot is already open." in out, err
    finally:
        holder.communicate("\n", timeout=30)
    # lock released when the holder exits -> a new copy can start
    third = _run_py("import sys, pathlib; sys.modules['tkinter'] = None\n"
                    "from maplebot.app.gui import acquire_single_instance\n"
                    "acquire_single_instance(pathlib.Path(sys.argv[1])); print('ok')\n", str(tmp_path))
    out, err = third.communicate(timeout=30)
    assert out.strip() == "ok", err


def test_lock_errors_only_busy_means_already_open(tmp_path, monkeypatch):
    import errno
    import types
    from maplebot.app import gui
    for code in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
        assert gui.lock_held_by_other(OSError(code, "busy"))
    for code in (errno.ENOLCK, errno.EINVAL, errno.EIO):
        assert not gui.lock_held_by_other(OSError(code, "other"))
    assert 36 in gui._busy_errnos("win32")
    if errno.EDEADLK != 36:
        assert 36 not in gui._busy_errnos("linux")     # ENAMETOOLONG on Linux, not "busy"

    # a non-busy failure propagates as plain OSError (main() then carries on unlocked)
    fake = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4,
                                 flock=lambda *a: (_ for _ in ()).throw(OSError(errno.ENOLCK, "no locks")))
    monkeypatch.setitem(sys.modules, "fcntl", fake)
    monkeypatch.setattr(gui.sys, "platform", "linux")
    with pytest.raises(OSError) as ei:
        gui.acquire_single_instance(tmp_path)
    assert not isinstance(ei.value, gui.AlreadyRunning)
    fake.flock = lambda *a: (_ for _ in ()).throw(OSError(errno.EAGAIN, "busy"))
    with pytest.raises(gui.AlreadyRunning):
        gui.acquire_single_instance(tmp_path)
