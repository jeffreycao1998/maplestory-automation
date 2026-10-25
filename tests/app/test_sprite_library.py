"""No network/game required: catalog -> animation frames -> active Profile -> wizard."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from maplebot.app import sprite_library as S
from maplebot.app import logic
from maplebot.settings import Profile, Settings


def png(path, color):
    image = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
    for y in range(2, 10):
        for x in range(2, 10):
            image.putpixel((x, y), (color, x * 20, y * 20, 255))
    image.save(path)
    return path


@pytest.fixture
def library(tmp_path, monkeypatch):
    lib = S.SpriteLibrary(tmp_path / "cache")
    catalog = {
        "Map/map-links.json": {"maps": {"101010101": {"mapId": "101010101",
            "mapName": "The Tree That Grew II", "streetName": "Victoria Road",
            "mobs": [{"id": "0210100", "name": "Slime", "preview": "Mob/images/slime.png"}]},
            "104010001": {"mapId": "104010001", "mapName": "The Beach", "streetName": "Victoria Road", "mobs": []}}},
        "Reward/drop-index.json": {"modes": {"monsterBook": {"mobs": {"0210100": [
            {"type": "item", "itemId": "2000000", "itemTab": "Consume"},
            {"type": "item", "itemId": "2000001", "itemTab": "Consume", "itemName": "Missing"}]}}}},
        "Consume/data.json": [{"id": "2000000", "name": "Potion", "preview": "Consume/images/potion.png"}]
    }
    for path, data in catalog.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data))
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps({"framebooks": {"stand": 1, "move": 2, "hit1": 3, "die1": 4}}))
    stand = png(tmp_path / "stand.png", 10)
    move0 = png(tmp_path / "move0.png", 30)
    move1 = png(tmp_path / "move1.png", 50)
    potion = png(tmp_path / "potion.png", 70)
    responses = {f"{lib.api}/mob/210100": meta,
                 f"{lib.api}/mob/210100/render/stand/0": stand,
                 f"{lib.api}/mob/210100/render/move/0": move0,
                 f"{lib.api}/mob/210100/render/move/1": move1,
                 lib.base + "Mob/images/slime.png": stand,
                 lib.base + "Consume/images/potion.png": potion,
                 **{lib.base + path: tmp_path / path for path in catalog}}
    lib.requests = []
    lib.responses = responses
    def fetch(url, limit=40 * 1024 * 1024):
        lib._check_cancelled()
        lib.requests.append(url)
        if url not in responses:
            raise S.AssetError("simulated unavailable asset")
        return responses[url]
    monkeypatch.setattr(lib, "_fetch", fetch)
    return lib


def test_search_name_and_id_from_global_map_index(library):
    assert library.search_maps("the tree that grew ii")[0]["mapId"] == "101010101"
    assert library.search_maps("101010101")[0]["mapName"] == "The Tree That Grew II"
    assert len(library.search_maps("Victoria Road")) == 2
    assert library.search_maps("x") == []
    assert library.search_maps("unlisted map") == []


def test_downloads_every_supported_pose_and_drops_without_boxes(library, tmp_path):
    messages = []
    output = tmp_path / "pack"
    manifest = library.build_pack("101010101", output, messages.append)
    mobs = [r for r in manifest["entries"] if r["kind"] == "mob"]
    assert {(r["action"], r["frame"]) for r in mobs} == {("stand", 0), ("move", 0), ("move", 1)}
    assert sum(r["kind"] == "item" for r in manifest["entries"]) == 1
    assert not any("/hit1/" in url or "/die1/" in url for url in library.requests)
    assert "No item image: Missing" in manifest["warnings"][0]
    assert manifest["validated_on_classic"] is False
    for row in manifest["entries"]:
        assert hashlib.sha256((output / row["file"]).read_bytes()).hexdigest() == row["sha256"]
        with Image.open(output / row["file"]) as im:
            assert im.mode == "RGBA" and im.getextrema()[-1] == (0, 255)
    assert messages


def test_profile_and_wizard_accept_downloaded_bank_without_manual_mob_crops(library, tmp_path):
    pack = tmp_path / "pack"
    library.build_pack("101010101", pack)
    profile = Profile(tmp_path / "profile")
    profile.ensure()
    assert profile.mob_templates() == []
    S.install_pack(pack, profile.templates_dir)
    assert len(profile.mob_templates()) == 3
    assert len(profile.item_templates()) == 1
    assert list(profile.templates_dir.glob("mob_*.png")) == []
    context = logic.WizardContext(settings=Settings(), n_mob_templates=len(profile.mob_templates()))
    assert logic.step_problem("mobs", context) is None


def test_switching_active_pack_keeps_manual_examples_and_only_loads_new_bank(library, tmp_path):
    pack = tmp_path / "pack"
    library.build_pack("101010101", pack)
    profile = Profile(tmp_path / "profile")
    profile.ensure()
    manual = png(profile.templates_dir / "mob_001.png", 100)
    manual_bytes = manual.read_bytes()
    S.install_pack(pack, profile.templates_dir)
    previous = profile.mob_templates()
    manifest = json.loads((pack / "manifest.json").read_text())
    manifest["entries"] = [r for r in manifest["entries"] if r["kind"] == "mob"][:1]
    (pack / "manifest.json").write_text(json.dumps(manifest))
    S.install_pack(pack, profile.templates_dir)
    assert len(profile.mob_templates()) == 2
    assert profile.item_templates() == []
    assert manual.read_bytes() == manual_bytes
    assert all(p.exists() for p in previous)       # old bank available for recovery, inactive


def test_interrupted_bank_publication_keeps_previous_bank_active(library, tmp_path, monkeypatch):
    pack = tmp_path / "pack"
    library.build_pack("101010101", pack)
    profile = Profile(tmp_path / "profile")
    profile.ensure()
    S.install_pack(pack, profile.templates_dir)
    previous = profile.mob_templates()
    old_record = (profile.templates_dir / "sprite-library.json").read_bytes()
    real_replace = S.os.replace
    def fail(source, target):
        if target == profile.templates_dir / "sprite-library.json":
            raise OSError("simulated disk error")
        real_replace(source, target)
    monkeypatch.setattr(S.os, "replace", fail)
    with pytest.raises(OSError, match="disk error"):
        S.install_pack(pack, profile.templates_dir)
    assert profile.mob_templates() == previous
    assert (profile.templates_dir / "sprite-library.json").read_bytes() == old_record
    assert len(list((profile.templates_dir / ".sprite-banks").iterdir())) == 1


def test_corrupted_pack_is_rejected_before_changing_profile(library, tmp_path):
    pack = tmp_path / "pack"
    manifest = library.build_pack("101010101", pack)
    (pack / manifest["entries"][0]["file"]).write_bytes(b"corrupt")
    profile = Profile(tmp_path / "profile")
    profile.ensure()
    with pytest.raises(S.AssetError, match="checksum"):
        S.install_pack(pack, profile.templates_dir)
    assert not (profile.templates_dir / "sprite-library.json").exists()


def test_missing_frames_are_reported_and_total_failure_is_not_installed(library, tmp_path):
    del library.responses[f"{library.api}/mob/210100/render/move/1"]
    manifest = library.build_pack("101010101", tmp_path / "partial")
    assert len(manifest["entries"]) == 3
    assert any("Slime move/1" in warning for warning in manifest["warnings"])
    del library.responses[f"{library.api}/mob/210100/render/move/0"]
    del library.responses[f"{library.api}/mob/210100/render/stand/0"]
    with pytest.raises(S.AssetError, match="No usable sprites for: Slime"):
        library.build_pack("101010101", tmp_path / "failed")


def test_animation_service_failure_explicitly_reports_preview_fallback(library, tmp_path):
    del library.responses[f"{library.api}/mob/210100"]
    manifest = library.build_pack("101010101", tmp_path / "pack")
    mobs = [r for r in manifest["entries"] if r["kind"] == "mob"]
    assert len(mobs) == 1 and mobs[0]["action"] == "preview"
    assert any("preview only" in warning for warning in manifest["warnings"])


def test_identical_frames_are_deduplicated(library, tmp_path):
    library.responses[f"{library.api}/mob/210100/render/move/0"] = library.responses[f"{library.api}/mob/210100/render/stand/0"]
    manifest = library.build_pack("101010101", tmp_path / "pack")
    assert sum(r["kind"] == "mob" for r in manifest["entries"]) == 2


def test_bad_map_id_empty_maps_and_output_overwrite_are_rejected(library, tmp_path):
    with pytest.raises(S.AssetError, match="ID"):
        library.build_pack("../../escape", tmp_path / "pack")
    with pytest.raises(S.AssetError, match="no catalogued monsters"):
        library.build_pack("104010001", tmp_path / "empty")
    library.build_pack("101010101", tmp_path / "pack")
    with pytest.raises(S.AssetError, match="output must be empty"):
        library.build_pack("101010101", tmp_path / "pack")


def test_cancel_stops_before_fetch_or_install(library, tmp_path):
    library.cancelled = lambda: True
    with pytest.raises(S.AssetError, match="cancelled"):
        library.build_pack("101010101", tmp_path / "pack")
    assert library.requests == []


@pytest.mark.parametrize("path", ["../outside.png", "/outside.png", "Mob\\outside.png", "Mob/C:foo", "Mob//foo"])
def test_catalog_paths_cannot_escape_asset_source(path):
    with pytest.raises(S.AssetError, match="sprite path"):
        S._relative(path)


@pytest.mark.parametrize("url", ["http://maplestory.io/a", "https://evil.com/a",
    "https://maplestory.io.evil.com/a", "https://user@maplestory.io/a"])
def test_sprite_fetch_and_redirect_require_trusted_https(url):
    with pytest.raises(S.AssetError, match="HTTPS"):
        S._url(url)
    with pytest.raises(S.AssetError, match="HTTPS"):
        S._Redirect().redirect_request(None, None, 302, "", {}, url)


@pytest.mark.parametrize("record", [{"bank": "../../outside", "entries": []}, [], {"bank": "x"}, "broken JSON"])
def test_bad_library_pointer_never_loads_outside_profile(tmp_path, record):
    profile = Profile(tmp_path / "profile")
    profile.ensure()
    (profile.templates_dir / "sprite-library.json").write_text(json.dumps(record))
    assert profile.mob_templates() == []


def test_downloaded_bank_is_map_specific(library, tmp_path):
    pack = tmp_path / "pack"
    library.build_pack("101010101", pack)
    profile = Profile(tmp_path / "profile")
    profile.ensure()
    S.install_pack(pack, profile.templates_dir)
    assert len(profile.mob_templates()) == 3
    profile.create_map("Pig Beach")
    assert profile.mob_templates() == []


def test_wizard_instructions_offer_download_as_primary_flow():
    step = next(s for s in logic.WIZARD_STEPS if s.key == "mobs")
    assert "Download sprites" in step.instruction
    assert "optional" in step.instruction


def test_http_asset_cache_downloads_once_and_uses_service_user_agent(tmp_path, monkeypatch):
    calls = []
    class Response(io.BytesIO):
        def geturl(self):
            return "https://maplestory.io/a"
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            return Response(b"asset data")
    monkeypatch.setattr(S, "build_opener", lambda redirect: Opener())
    lib = S.SpriteLibrary(tmp_path / "cache")
    first = lib._fetch("https://maplestory.io/a")
    second = lib._fetch("https://maplestory.io/a")
    assert first == second and first.read_bytes() == b"asset data"
    assert len(calls) == 1
    assert calls[0].get_header("User-agent") == "MapleBot-asset-import"
    assert not list(lib.cache.glob("download-*"))


def test_oversized_download_leaves_no_cached_partial_asset(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        def geturl(self):
            return "https://maplestory.io/a"
    class Opener:
        def open(self, request, timeout):
            return Response(b"too much data")
    monkeypatch.setattr(S, "build_opener", lambda redirect: Opener())
    lib = S.SpriteLibrary(tmp_path / "cache")
    with pytest.raises(S.AssetError, match="size limit"):
        lib._fetch("https://maplestory.io/a", limit=3)
    assert list(lib.cache.iterdir()) == []


def test_invalid_json_is_not_cached_forever(tmp_path, monkeypatch):
    bad = tmp_path / "bad.asset"
    bad.write_text("temporary HTML error")
    lib = S.SpriteLibrary(tmp_path / "cache")
    monkeypatch.setattr(lib, "_fetch", lambda url, limit: bad)
    with pytest.raises(S.AssetError, match="invalid JSON"):
        lib._fetch_json("https://maplestory.io/a")
    assert not bad.exists()
