"""Per-map setups: shared settings + one folder per map."""
import pytest

from maplebot.settings import MapError, Profile, Settings, dump_yaml_or_json


def _setup(profile, minimap=(10, 10, 100, 60), mob="mob_1.png"):
    s = profile.load()
    s.calibration.minimap_box = list(minimap)
    s.calibration.hp_bar_box = [1, 2, 3, 4]
    s.calibration.patrol_y = 0.4
    profile.save(s)
    profile.templates_dir.mkdir(parents=True, exist_ok=True)
    (profile.templates_dir / mob).write_bytes(b"png")
    (profile.templates_dir / "player.png").write_bytes(b"tag")
    return s


def test_fresh_profile_has_one_default_map(tmp_path):
    p = Profile(tmp_path)
    s = p.load()
    assert s.calibration.minimap_box is None
    p.save(s)
    assert [n for _, n in p.maps()] == ["My first map"]
    assert p.templates_dir == tmp_path / "maps" / p.current_map / "templates"


def test_shared_settings_survive_switching_maps(tmp_path):
    p = Profile(tmp_path)
    s = _setup(p)
    s.keys.attack = "z"
    p.save(s)
    first = p.current_map

    p.create_map("Pig Beach")
    s2 = p.load()
    assert s2.keys.attack == "z"                       # shared
    assert s2.calibration.minimap_box is None          # per map: must redo
    assert s2.calibration.patrol_y is None
    assert s2.calibration.hp_bar_box == [1, 2, 3, 4]   # copied: UI doesn't move
    assert p.player_template() is not None             # name tag copied
    assert p.mob_templates() == []                     # mobs differ per map
    assert not p.map_is_set_up()

    s2.calibration.minimap_box = [5, 5, 50, 50]
    s2.keys.jump = "x"
    p.save(s2)
    p.switch_map(first)
    s3 = p.load()
    assert s3.calibration.minimap_box == [10, 10, 100, 60]
    assert s3.keys.jump == "x"                         # shared change carries over
    assert [m.name for m in p.mob_templates()] == ["mob_1.png"]
    assert p.map_is_set_up()


def test_map_names_rename_delete(tmp_path):
    p = Profile(tmp_path)
    _setup(p)
    a = p.current_map
    b = p.create_map("The Tree That Grew II")
    assert b == "the-tree-that-grew-ii"
    with pytest.raises(MapError, match="already"):
        p.create_map("the tree that grew ii")
    with pytest.raises(MapError):
        p.create_map("   ")
    p.rename_map(b, "Ellinia trees")
    assert dict(p.maps())[b] == "Ellinia trees"
    p.delete_map(b)                                    # deleting current -> falls back
    assert p.current_map == a
    with pytest.raises(MapError, match="only map"):
        p.delete_map(a)


def test_old_single_map_profile_is_migrated(tmp_path):
    old = Settings()
    old.keys.attack = "q"
    old.calibration.minimap_box = [7, 7, 70, 70]
    (tmp_path / "settings.yaml").write_text(dump_yaml_or_json(old.to_dict()))
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "mob_1.png").write_bytes(b"png")

    p = Profile(tmp_path)
    s = p.load()
    assert s.keys.attack == "q"
    assert s.calibration.minimap_box == [7, 7, 70, 70]
    assert [m.name for m in p.mob_templates()] == ["mob_1.png"]
    assert not (tmp_path / "templates").exists()
    assert len(p.maps()) == 1
    s2 = Profile(tmp_path).load()                      # idempotent
    assert s2.calibration.minimap_box == [7, 7, 70, 70]
