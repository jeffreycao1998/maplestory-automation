"""Map picker / per-map wizard logic, including against the real Profile."""
from __future__ import annotations

import pytest

from maplebot.app import logic as L
from maplebot.settings import MapError, Profile, Settings


def _set_up(profile, name_tag=True):
    s = profile.load()
    s.calibration.minimap_box = [15, 81, 166, 112]
    s.calibration.hp_bar_box = [504, 751, 103, 14]
    s.calibration.player_dot_hsv_low = [22, 140, 204]
    s.calibration.player_dot_hsv_high = [31, 255, 255]
    profile.save(s)
    profile.templates_dir.mkdir(parents=True, exist_ok=True)
    (profile.templates_dir / "mob_1.png").write_bytes(b"png")
    if name_tag:
        (profile.templates_dir / "player.png").write_bytes(b"tag")
    return s


def test_map_choices_marks_not_set_up_and_lists_fresh_default():
    ch = L.map_choices([("a", "Pig Beach"), ("b", "Tree")], "b", "Tree", lambda i: i == "a")
    assert ch == [("a", "Pig Beach"), ("b", "Tree (not set up)")]
    # brand-new profile: nothing saved yet, the current map still shows
    assert L.map_choices([], "my-first-map", "My first map", lambda i: False) == \
        [("my-first-map", "My first map (not set up)")]
    assert L.map_label_to_id(ch, "Tree (not set up)") == "b"
    assert L.map_label_to_id(ch, "nope") is None


def test_start_state():
    s = Settings()
    assert L.start_state([], False, False, s) == ("Run the setup wizard for this map", True, "wizard")
    t, en, act = L.start_state([], True, False, s)
    assert en and act == "start" and "F11" in t
    assert L.start_state(["HP bar not calibrated"], True, False, s)[1] is False
    assert L.start_state([], True, True, s)[1] is False          # running


def test_step_label_and_title_mention_map():
    assert L.step_label(2, "Pig Beach") == "Setting up: Pig Beach -- Step 3 of 11: Minimap"
    assert L.step_label(2) == "Step 3 of 11: Minimap"
    assert L.wizard_title("Pig Beach") == "Setting up: Pig Beach"


def test_delete_question_and_names():
    assert L.delete_map_question("Pig Beach") == \
        "Delete the setup for Pig Beach? Its mob pictures are deleted too."
    assert L.clean_map_name("  Pig   Beach ") == "Pig Beach"
    assert L.clean_map_name("   ") is None and L.clean_map_name(None) is None


def test_map_error_is_friendly():
    t, m = L.friendly_error(MapError("You already have a map called 'Pig Beach'."))
    assert t == "Map" and m == "You already have a map called 'Pig Beach'."


def test_kept_steps_rules():
    s = Settings()
    assert L.kept_steps(s, True, map_set_up=True, kept_from="Tree") == set()
    assert L.kept_steps(s, True, map_set_up=False, kept_from=None) == set()
    assert L.kept_steps(s, False, False, "Tree") == {"keys", "dot", "window"}
    s.calibration.hp_bar_box = [1, 2, 30, 4]
    assert L.kept_steps(s, True, False, "Tree") == {"keys", "dot", "window", "player", "bars"}
    assert "Tree" in L.kept_message("Tree") and "Redo" in L.kept_message("Tree")


def test_new_map_from_real_profile_keeps_only_shared_parts(tmp_path):
    p = Profile(tmp_path)
    _set_up(p)
    old_name = p.current_map_name()
    assert p.map_is_set_up()
    p.create_map("Pig Beach")
    s = p.load()
    kept = L.kept_steps(s, p.player_template() is not None, p.map_is_set_up(), old_name)
    assert kept == {"window", "dot", "player", "bars", "keys"}
    # what the user still has to do on the new map
    ctx = L.WizardContext(settings=s, screenshot_size=(1366, 768),
                          n_mob_templates=len(p.mob_templates()),
                          has_player_template=p.player_template() is not None)
    assert L.step_problem("minimap", ctx)                  # minimap box: redo
    assert L.step_problem("mobs", ctx)                     # mobs: redo
    assert L.step_problem("patrol", ctx)                   # training area: redo
    assert L.step_problem("player", ctx) is None           # name tag kept
    assert L.step_problem("bars", ctx) is None             # HP bar kept
    assert L.step_problem("keys", ctx) is None
    choices = L.map_choices(p.maps(), p.current_map, p.current_map_name(), p.map_is_set_up)
    assert [l for _, l in choices] == ["My first map", "Pig Beach (not set up)"]


def test_patrol_preset_is_per_map(tmp_path):
    """Wizard: preset = this map was set up before (not 'any settings file exists')."""
    p = Profile(tmp_path)
    _set_up(p)
    assert p.map_is_set_up() and p.load().calibration.minimap_box
    p.create_map("Pig Beach")
    assert not p.map_is_set_up()           # -> wizard requires fresh patrol edges here
    first = [i for i, n in p.maps() if n == "My first map"][0]
    p.switch_map(first)
    assert p.map_is_set_up()               # -> the old map keeps its edges


def test_wizard_save_through_profile_doesnt_touch_other_map(tmp_path):
    p = Profile(tmp_path)
    _set_up(p)
    first = p.current_map
    p.create_map("Pig Beach")
    s = p.load()
    s.calibration.minimap_box = [10, 10, 150, 100]
    s.calibration.patrol_left, s.calibration.patrol_right = 0.2, 0.8
    p.save(s)
    p.switch_map(first)
    back = p.load()
    assert back.calibration.minimap_box == [15, 81, 166, 112]
    assert back.calibration.patrol_left == 0.30
    assert [x.name for x in p.mob_templates()] == ["mob_1.png"]


def test_map_errors_from_profile(tmp_path):
    p = Profile(tmp_path)
    _set_up(p)
    with pytest.raises(MapError):
        p.delete_map(p.current_map)          # only map
    p.create_map("Pig Beach")
    with pytest.raises(MapError) as e:
        p.create_map("pig beach")
    assert L.friendly_error(e.value)[0] == "Map"
