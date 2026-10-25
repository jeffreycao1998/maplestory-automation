"""Map-based, cached sprite acquisition; only setup downloads, never the bot loop.

The catalog supplies map spawns/drop IDs. The render API supplies actual animation
frames, excluding hurt/death poses. No screenshot or profile data is uploaded.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import uuid
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

from maplebot.settings import REPO_ROOT


class AssetError(RuntimeError):
    pass


HOSTS = {"raw.githubusercontent.com", "maplestory.io", "maplestory.quest"}
SOURCE_CONFIG = REPO_ROOT / "configs/sprite-sources.yaml"


def _url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username
            or parsed.password or parsed.port not in {None, 443}):
        raise AssetError("Sprite downloads must use the configured HTTPS sprite services.")


class _Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _relative(path):
    if (not isinstance(path, str) or path.startswith("/") or "\\" in path or ":" in path
            or any(p in {"", ".", ".."} for p in path.split("/"))):
        raise AssetError("Invalid sprite path in catalog.")
    return PurePosixPath(path).as_posix()


def _id(value):
    value = str(value)
    if not re.fullmatch(r"[0-9]{1,10}", value):
        raise AssetError("Invalid game asset ID.")
    return str(int(value))


def _json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _normalized(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())


class LegacySpriteLibrary:
    def __init__(self, cache: Path, config: Path | None = None, cancelled=lambda: False):
        self.cache = Path(cache)
        self.config = _json(config or REPO_ROOT / "configs/sprite-sources.yaml")
        repo, revision = self.config["catalog_repository"], self.config["catalog_revision"]
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise AssetError("Invalid sprite catalog configuration.")
        self.base = f"https://raw.githubusercontent.com/{repo}/{revision}/WEB/"
        self.api = self.config["sprite_api"].rstrip("/")
        _url(self.api)
        for field, ceiling in [("max_monsters", 48), ("max_frames_per_action", 128),
                               ("max_mob_templates", 512), ("max_items", 256)]:
            if type(self.config.get(field)) is not int or not 1 <= self.config[field] <= ceiling:
                raise AssetError(f"Invalid {field} in sprite source configuration.")
        self.cancelled = cancelled

    def _check_cancelled(self):
        if self.cancelled():
            raise AssetError("Sprite download cancelled.")

    def _fetch(self, url, limit=40 * 1024 * 1024):
        self._check_cancelled()
        _url(url)
        self.cache.mkdir(parents=True, exist_ok=True)
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".asset")
        if path.is_file():
            if path.stat().st_size > limit:
                raise AssetError("Cached sprite asset exceeds size limit.")
            return path
        fd, name = tempfile.mkstemp(prefix="download-", dir=self.cache)
        temp = Path(name)
        try:
            request = Request(url, headers={"User-Agent": "MapleBot-asset-import"})
            with os.fdopen(fd, "wb") as out, build_opener(_Redirect()).open(request, timeout=20) as response:
                _url(response.geturl())
                total = 0
                while chunk := response.read(128 * 1024):
                    self._check_cancelled()
                    total += len(chunk)
                    if total > limit:
                        raise AssetError("Sprite asset exceeds download size limit.")
                    out.write(chunk)
            os.replace(temp, path)
            return path
        finally:
            temp.unlink(missing_ok=True)

    def _catalog(self, path):
        return self._fetch_json(self.base + quote(_relative(path), safe="/"))

    def _fetch_json(self, url, limit=40 * 1024 * 1024):
        path = self._fetch(url, limit)
        try:
            return _json(path)
        except (ValueError, OSError) as e:
            path.unlink(missing_ok=True)       # a bad response must not poison later retries
            raise AssetError(f"Sprite service returned invalid JSON: {e}") from e

    def search_maps(self, query):
        query = _normalized(query)
        if len(query) < 2:
            return []
        maps = self._catalog("Map/map-links.json")["maps"]
        found = [dict(row, mapId=_id(mid)) for mid, row in maps.items()
                 if query in _normalized(str(mid) + row["streetName"] + row["mapName"])]
        return sorted(found, key=lambda row: (query not in {_normalized(row["mapName"]), row["mapId"]},
                                               row["mapName"], row["mapId"]))[:100]

    def _png(self, source, target):
        self._check_cancelled()
        with Image.open(source) as image:
            if image.width * image.height > 4_000_000 or image.width < 4 or image.height < 4:
                raise AssetError("Unsupported sprite dimensions.")
            rgba = image.convert("RGBA")
            if rgba.getchannel("A").getbbox() is None:
                raise AssetError("Sprite is fully transparent.")
            rgba.save(target, format="PNG")
        return hashlib.sha256(target.read_bytes()).hexdigest()

    def _download_sprites(self, requests, output, progress):
        entries, failures = [], []
        seen = set()
        # Three concurrent requests, cached thereafter. All URLs are public asset IDs.
        def acquire(request):
            kind, mid, name, action, frame, url = request
            return request, self._fetch(url, 4 * 1024 * 1024)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(acquire, request) for request in requests]
            for n, (request, future) in enumerate(zip(requests, futures), 1):
                self._check_cancelled()
                kind, mid, name, action, frame, url = request
                progress(f"Downloading sprites {n}/{len(requests)}: {name}…")
                target = output / f"{kind}_library_{mid}_{action}_{frame}.png"
                try:
                    _, source = future.result()
                    digest = self._png(source, target)
                    if (kind, mid, digest) in seen:
                        target.unlink()
                        continue
                    seen.add((kind, mid, digest))
                    entries.append({"kind": kind, "id": mid, "name": name, "action": action,
                                    "frame": frame, "file": target.name, "sha256": digest, "url": url})
                except Exception as e:
                    self._check_cancelled()
                    target.unlink(missing_ok=True)
                    failures.append(f"{name} {action}/{frame}: {e}")
        return entries, failures

    def build_pack(self, map_id, output: Path, progress=lambda text: None):
        map_id = _id(map_id)
        self._check_cancelled()
        info = self._catalog("Map/map-links.json")["maps"].get(map_id)
        if info is None:
            raise AssetError("Map is not present in the configured game catalog.")
        mobs = {_id(row["id"]): row for row in info["mobs"]}
        if not mobs:
            raise AssetError("This map has no catalogued monsters.")
        if len(mobs) > self.config["max_monsters"]:
            raise AssetError("This map exceeds the configured monster limit.")
        if output.exists() and any(output.iterdir()):
            raise AssetError("Sprite pack output must be empty.")
        output.mkdir(parents=True, exist_ok=True)
        entries, warnings, failures = [], [], []
        requests = []
        for mid, mob in mobs.items():
            progress(f"Reading animation poses for {mob['name']}…")
            try:
                metadata = self._fetch_json(f"{self.api}/mob/{mid}", 2 * 1024 * 1024)
                actions = metadata.get("framebooks", {})
                poses = []
                for action, count in actions.items():
                    if not re.fullmatch(r"(?:stand|move|fly|jump|attack[0-9]*|skill[0-9]*)", action):
                        continue
                    if type(count) is not int or not 0 < count <= self.config["max_frames_per_action"]:
                        warnings.append(f"{mob['name']} {action}: invalid or excessive frame count; skipped.")
                        continue
                    if len(requests) + len(poses) + count > self.config["max_mob_templates"]:
                        raise AssetError("Animation-template limit exceeded.")
                    poses.extend((action, i) for i in range(count))
                if not poses:
                    raise AssetError("No supported standing/movement animations.")
                for action, frame in poses:
                    requests.append(("mob", mid, mob["name"], action, frame,
                                     f"{self.api}/mob/{mid}/render/{action}/{frame}"))
            except Exception as e:
                self._check_cancelled()
                warnings.append(f"{mob['name']}: animation source unavailable ({e}); preview only.")
                if mob.get("preview"):
                    requests.append(("mob", mid, mob["name"], "preview", 0,
                                     self.base + quote(_relative(mob["preview"]), safe="/")))
        if len(requests) > self.config["max_mob_templates"]:
            raise AssetError("This map exceeds the configured animation-template limit.")
        progress("Reading this map's item drops (first download may take a minute)…")
        try:
            drops = self._catalog("Reward/drop-index.json")["modes"]["monsterBook"]["mobs"]
            drops = {_id(mid): rows for mid, rows in drops.items()}
            items = {_id(row["itemId"]): row for mid in mobs for row in drops.get(mid, [])
                     if row.get("type") == "item" and row.get("itemTab")}
            if len(items) > self.config["max_items"]:
                raise AssetError("This map exceeds the configured item-reference limit.")
            tabs = {}
            for iid, item in items.items():
                tab = item["itemTab"]
                if not isinstance(tab, str) or not tab.isalnum():
                    raise AssetError("Invalid item tab in catalog.")
                if tab not in tabs:
                    tabs[tab] = {_id(r["id"]): r for r in self._catalog(f"{tab}/data.json")}
                row = tabs[tab].get(iid)
                if row and row.get("preview"):
                    requests.append(("item", iid, row["name"], "icon", 0,
                                     self.base + quote(_relative(row["preview"]), safe="/")))
                else:
                    warnings.append(f"No item image: {item.get('itemName', iid)} ({iid}).")
        except Exception as e:
            self._check_cancelled()
            warnings.append(f"Drop catalog unavailable: {e}")
        entries, failures = self._download_sprites(requests, output, progress)
        missing_mobs = set(mobs) - {row["id"] for row in entries if row["kind"] == "mob"}
        if missing_mobs:
            raise AssetError("No usable sprites for: " + ", ".join(mobs[mid]["name"] for mid in missing_mobs)
                             + ". Existing templates were kept. " + "; ".join(failures[:2]))
        manifest = {"schema": 1, "map_id": map_id, "map_name": info["mapName"],
                    "catalog_repository": self.config["catalog_repository"],
                    "catalog_revision": self.config["catalog_revision"], "sprite_api": self.api,
                    "validated_on_classic": False, "entries": entries, "warnings": warnings + failures}
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest


def SpriteLibrary(cache: Path, config: Path | None = None, cancelled=lambda: False):
    """Choose the explicitly configured edition. Never silently fall back to v83."""
    config = config or SOURCE_CONFIG
    value = _json(config)
    provider = value.get("provider", "classic-world")
    if provider == "classic-world":
        from .classic_library import ClassicWorldLibrary
        return ClassicWorldLibrary(cache, config, cancelled)
    if provider == "legacy-v83":
        return LegacySpriteLibrary(cache, config, cancelled)
    raise AssetError("Unknown sprite provider; choose the matching client edition.")


def compatible_manifest(manifest, config=None):
    value = _json(config or SOURCE_CONFIG)
    if value.get("provider", "classic-world") == "classic-world":
        return (manifest.get("source_game") == "MapleStory Classic World"
                and manifest.get("source_client_build") == value.get("classic_client_build")
                and manifest.get("source_snapshot") == value.get("classic_snapshot"))
    return (value.get("provider") == "legacy-v83"
            and manifest.get("catalog_repository") == value.get("catalog_repository")
            and manifest.get("catalog_revision") == value.get("catalog_revision"))


def source_caption(config=None):
    value = _json(config or SOURCE_CONFIG)
    if value.get("provider", "classic-world") == "classic-world":
        return (f"Classic World {value['classic_client_build']} · {value['classic_snapshot']} snapshot\n"
                "Community client-file extraction, not an official Nexon API. Check Observe only before keys.")
    return "Legacy GMS v83 references — not the current Classic World client."


def install_pack(pack: Path, templates: Path):
    """Publish an immutable bank through an atomic pointer; keep all manual crops.

    Interrupted copying leaves the old bank active. Old banks remain available for
    recovery; only the bank named in sprite-library.json is loaded by Profile.
    """
    manifest = _json(pack / "manifest.json")
    entries = manifest["entries"]
    references=manifest.get('reference_files',[])
    if len(references)>1 or any(row.get('file')!='reference_minimap.png' for row in references):
        raise AssetError('Invalid map reference files')
    for row in entries+references:
        name = row["file"]
        if name!='reference_minimap.png' and not re.fullmatch(r"(?:mob|item)_library_[0-9]+_[a-z0-9]+_[0-9]+\.png", name):
            raise AssetError("Invalid sprite pack filename.")
        if hashlib.sha256((pack / name).read_bytes()).hexdigest() != row["sha256"]:
            raise AssetError("Sprite pack checksum mismatch.")
    templates.mkdir(parents=True, exist_ok=True)
    bank_id = uuid.uuid4().hex
    bank = templates / ".sprite-banks" / bank_id
    bank.mkdir(parents=True)
    fd, name = tempfile.mkstemp(prefix="sprite-library-", suffix=".pending", dir=templates)
    temp = Path(name)
    try:
        # Close the temporary file before replace: Windows won't rename an open file.
        with os.fdopen(fd, "w", encoding="utf-8") as pointer:
            for row in entries+references:
                shutil.copyfile(pack / row["file"], bank / row["file"])
            manifest = dict(manifest, bank=bank_id)
            json.dump(manifest, pointer, indent=2)
        os.replace(temp, templates / "sprite-library.json")
    except BaseException:
        shutil.rmtree(bank, ignore_errors=True)
        raise
    finally:
        temp.unlink(missing_ok=True)
    return manifest
