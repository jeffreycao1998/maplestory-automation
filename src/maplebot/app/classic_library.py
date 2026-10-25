"""Release-specific Classic World references from a community client-file snapshot.

This is explicitly a third-party extraction, not a Nexon API or live-client check.
It supplies current-edition IDs/PNGs, not server drop tables or validated physics.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
from urllib.parse import quote, urlencode

from .sprite_library import AssetError, LegacySpriteLibrary, _id, _normalized, _relative


class ClassicWorldLibrary(LegacySpriteLibrary):
    def __init__(self, cache, config, cancelled=lambda: False):
        super().__init__(cache, config, cancelled)
        self.base = self.config["classic_base"].rstrip("/")
        if self.base != "https://maplestory.quest":
            raise AssetError("Classic World requires its explicitly configured client-data source.")
        self.snapshot = self.config["classic_snapshot"]
        self.build = self.config["classic_client_build"]
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", self.snapshot + self.build):
            raise AssetError("Invalid Classic client snapshot/build.")
        self.cache = self.cache / f"classic-{self.snapshot}-{self.build}"

    def _api(self, endpoint, **params):
        return self.base + "/api/raw/" + endpoint + "?" + urlencode(dict(v=self.snapshot, **params))

    def _file(self, path):
        return (self.base + "/api/raw/file/" + quote(self.snapshot, safe="") + "/"
                + quote(_relative(path), safe="/"))

    def _index(self, kind):
        result = self._fetch_json(self._file(f"index/{kind}.json"))
        if result.get("category") != kind or not isinstance(result.get("records"), list):
            raise AssetError("Unexpected Classic client index format; no legacy fallback was used.")
        return result["records"]

    def search_maps(self, query):
        query = _normalized(query)
        if len(query) < 2:
            return []
        records = self._index("map")
        rows = [dict(row, mapId=_id(row["id"]), mapName=row["name"], streetName=row.get("street_name", ""))
                for row in records if query in _normalized(str(row["id"]) + row["name"] + row.get("street_name", ""))]
        return sorted(rows, key=lambda row: (query not in {_normalized(row["name"]), row["mapId"]},
                                             row["name"], row["mapId"]))[:100]

    def _record(self, kind, record_id):
        value = self._fetch_json(self._api("record", key=kind, record=f"{kind}:{_id(record_id)}"))
        if _id(value.get("id", "")) != _id(record_id):
            raise AssetError("Classic source returned a different asset ID.")
        return value

    def _animation_frames(self, animation):
        wz = _relative(animation["wz"])
        stem, marker, node = wz.partition(".img/")
        if not marker or not node:
            raise AssetError("Unsupported Classic animation path.")
        result = self._fetch_json(self._api("node", path=stem + ".img", node=node))
        preview = result.get("data", {}).get("$preview", {})
        if preview.get("truncated"):
            raise AssetError("Classic source truncated this animation.")
        frames = preview.get("frames")
        if not frames and preview.get("file") and preview.get("type") == "image":
            frames = [preview]
        if not frames and animation.get("still") and animation.get("file"):
            frames = [{"file": animation["file"]}]
        if not isinstance(frames, list) or not 0 < len(frames) <= self.config["max_frames_per_action"]:
            raise AssetError("No supported frame list or animation exceeds frame limit.")
        return frames

    def _common_items(self, names):
        # The full item index is very large (equipment animations included). Ask
        # for bounded filtered pages, cache them, and accept exact names only.
        def lookup(name):
            rows = []
            for page in range(1, 4):
                value = self._fetch_json(self._api("category", key="item", q=name,
                                                  page=page, page_size=50), 4*1024*1024)
                rows.extend(row for row in value.get("items", []) if row.get("name") == name)
                if page >= value.get("pages", 1):
                    break
            return rows
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lookup, sorted(names)))
        return [row for rows in results for row in rows]

    def build_pack(self, map_id, output: Path, progress=lambda text: None):
        self._check_cancelled()
        map_id = _id(map_id)
        progress("Reading Classic World release map data…")
        info = self._record("map", map_id)
        mids = sorted({_id(row["id"]) for row in info.get("mobs", [])})
        if not mids or len(mids) > self.config["max_monsters"]:
            raise AssetError("Map has no supported monster list or exceeds the monster limit.")
        if output.exists() and any(output.iterdir()):
            raise AssetError("Sprite pack output must be empty.")
        output.mkdir(parents=True, exist_ok=True)
        requests, warnings, knowledge, names = [], [], {}, {}
        for mid in mids:
            self._check_cancelled()
            mob = self._record("mob", mid)
            names[mid] = mob["name"]
            knowledge[mid] = {key: mob.get(key) for key in ("name", "level", "hp", "mp", "exp", "stats", "wz")}
            progress(f"Reading Classic animation poses for {mob['name']}…")
            seen_urls = set()
            for animation in mob.get("animations", []):
                action = animation.get("name", "")
                if not re.fullmatch(r"(?:stand|move|fly|jump|attack[0-9]*|skill[0-9]*)", action):
                    continue
                try:
                    frames = self._animation_frames(animation)
                    if len(requests) + len(frames) > self.config["max_mob_templates"]:
                        raise AssetError("Animation-template limit exceeded.")
                    for n, frame in enumerate(frames):
                        if not frame.get("file"):
                            warnings.append(f"{mob['name']} {action}/{n}: layered frame unsupported.")
                            continue
                        url = self._file(frame["file"])
                        if url not in seen_urls:
                            requests.append(("mob", mid, mob["name"], action, n, url))
                            seen_urls.add(url)
                except Exception as e:
                    self._check_cancelled()
                    warnings.append(f"{mob['name']} {action}: {e}")
            if not any(row[1] == mid for row in requests):
                icon = mob.get("icon", {}).get("file")
                if not icon:
                    raise AssetError(f"No Classic sprites for {mob['name']}; no v83 fallback was used.")
                requests.append(("mob", mid, mob["name"], "preview", 0, self._file(icon)))
                warnings.append(f"{mob['name']}: Classic icon only; animation poses unavailable.")
        # The client cannot supply server drop tables. Use a clearly identified
        # common-pickup reference set from this same client; never join v83 drops.
        progress("Reading current Classic common pickup icons…")
        try:
            wanted = set(self.config.get("classic_common_items", []))
            if len(wanted) > self.config["max_items"]:
                raise AssetError("Common item list exceeds configured limit.")
            found, item_ids = set(), set()
            for item in self._common_items(wanted):
                if item.get("name") in wanted and item.get("icon", {}).get("file"):
                    if len(item_ids) >= self.config["max_items"]:
                        break
                    iid = _id(item["id"])
                    if iid in item_ids:
                        continue
                    requests.append(("item", iid, item["name"], "icon", 0, self._file(item["icon"]["file"])))
                    found.add(item["name"])
                    item_ids.add(iid)
            warnings += [f"No current Classic item image: {name}" for name in sorted(wanted - found)]
        except Exception as e:
            self._check_cancelled()
            warnings.append(f"Common pickup icons unavailable: {e}")
        entries, failures = self._download_sprites(requests, output, progress)
        missing = set(mids) - {row["id"] for row in entries if row["kind"] == "mob"}
        if missing:
            raise AssetError("No usable Classic sprites for: " + ", ".join(names[mid] for mid in missing))
        manifest = {"schema": 1, "map_id": map_id, "map_name": info["name"],
                    "source_game": "MapleStory Classic World", "source_snapshot": self.snapshot,
                    "source_client_build": self.build, "source": self.base + "/raw",
                    "source_kind": "community_client_extraction", "validated_on_classic": False,
                    "map_reference": {"bounds": info.get("bounds"), "minimap": info.get("minimap"),
                                      "mob_stats": knowledge, "requires_alignment": True},
                    "drop_source": "Common current-client pickup icons; not a server drop table",
                    "entries": entries, "warnings": warnings + failures}
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        # Reference only: do not activate movement geometry or replace calibrated timing.
        (output / "classic-map-reference.json").write_text(json.dumps({
            "map_id": map_id, "bounds": info.get("bounds"), "minimap": info.get("minimap"),
            "mob_stats": knowledge, "requires_alignment": True}, indent=2), encoding="utf-8")
        return manifest
