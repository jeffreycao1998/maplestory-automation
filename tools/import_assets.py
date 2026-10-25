"""Build a map-specific sprite pack from an offline GMS v83 catalog.

python -m tools.import_assets --catalog data/reference-gms83 --map 101010101 \
    --output data/asset-packs/tree-that-grew-2 --fetch

Downloads happen only with --fetch, through git from the supplied catalog checkout.
The live bot never downloads anything. Packs are references, not a trained detector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import xml.etree.ElementTree as ET

import tools  # ensures src is importable when launched as a module


def fetch(catalog, paths):
    # All paths come from the catalog; disallow traversal and git option injection.
    paths = sorted(set(str(p) for p in paths))
    for path in paths:
        parts = PurePosixPath(path).parts
        if not parts or parts[0] not in ("WEB", "wz_files") or ".." in parts or "\\" in path:
            raise ValueError(f"Unsafe catalog path: {path}")
    for start in range(0, len(paths), 100):
        subprocess.run(["git", "-C", str(catalog), "restore", "--source", "HEAD", "--",
                        *paths[start:start+100]], check=True)


def read(catalog, path, download=False):
    target = catalog / path
    if not target.exists() and download:
        fetch(catalog, [path])
    if not target.exists():
        raise ValueError(f"Missing {path}; use --fetch or populate the catalog checkout first")
    return json.loads(target.read_text(encoding="utf-8-sig"))


def extract_map(xml_path):
    """Preserve world coordinates; do not guess their alignment to a screen minimap."""
    if xml_path.stat().st_size > 10_000_000:
        raise ValueError("Map XML is too large")
    text = xml_path.read_text(encoding="utf-8-sig")
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("External entities are not supported")
    root = ET.fromstring(text)
    def values(node):
        return {e.attrib["name"]: int(e.attrib["value"]) for e in node if e.tag == "int"}
    footholds = []
    node = root.find("./imgdir[@name='foothold']")
    if node is not None:
        for layer in node:
            for group in layer:
                for segment in group:
                    row = values(segment)
                    if all(k in row for k in ("x1", "y1", "x2", "y2")):
                        footholds.append({"id": segment.attrib["name"], "layer": layer.attrib["name"],
                                          "group": group.attrib["name"], **row})
    ladders = root.find("./imgdir[@name='ladderRope']")
    mini = root.find("./imgdir[@name='miniMap']")
    return {"coordinate_system": "world_pixels_y_down", "footholds": footholds,
            "ladders": [values(n) for n in ladders] if ladders is not None else [],
            "minimap": values(mini) if mini is not None else {},
            "requires_alignment": True,
            "note": "Verify client version and minimap alignment before enabling navigation."}


def navigation_candidate(data):
    """Join only contiguous footholds on the same layer/group; leave travel links unset."""
    mini = data["minimap"]
    if not all(k in mini for k in ("width", "height", "centerX", "centerY")):
        return None
    if mini["width"] <= 0 or mini["height"] <= 0:
        return None
    segments = [row for row in data["footholds"] if row["x1"] < row["x2"]]
    by_id = {(s["layer"], s["group"], s["id"]): s for s in segments}
    used, platforms = set(), []
    for row in segments:
        key = (row["layer"], row["group"], row["id"])
        if key in used:
            continue
        chain, cursor = [], row
        # Walk backward to a chain start, bounded by visited IDs (WZ graphs may cycle).
        backward = set()
        while cursor["id"] not in backward:
            backward.add(cursor["id"])
            prev = by_id.get((cursor["layer"], cursor["group"], str(cursor.get("prev", 0))))
            if prev is None or (prev["x2"], prev["y2"]) != (cursor["x1"], cursor["y1"]):
                break
            cursor = prev
        start_id = cursor["id"]
        while (cursor["layer"], cursor["group"], cursor["id"]) not in used:
            used.add((cursor["layer"], cursor["group"], cursor["id"]))
            if not chain:
                chain.append((cursor["x1"], cursor["y1"]))
            chain.append((cursor["x2"], cursor["y2"]))
            nxt = by_id.get((cursor["layer"], cursor["group"], str(cursor.get("next", 0))))
            if nxt is None or (cursor["x2"], cursor["y2"]) != (nxt["x1"], nxt["y1"]):
                break
            cursor = nxt
        points = [[(x+mini["centerX"])/mini["width"], (y+mini["centerY"])/mini["height"]] for x, y in chain]
        if len(points) >= 2 and all(0 <= x <= 1 and 0 <= y <= 1 for x, y in points):
            platforms.append({"name": f"foothold-{row['layer']}-{row['group']}-{start_id}",
                              "points": points, "tolerance": 0.015, "drop_through": False})
    ladders = []
    for i,row in enumerate(data.get("ladders",[])):
        x = (row["x"]+mini["centerX"])/mini["width"]
        top,bottom = sorted(((row["y1"]+mini["centerY"])/mini["height"],
                             (row["y2"]+mini["centerY"])/mini["height"]))
        if 0<=x<=1 and 0<=top<bottom<=1:
            ladders.append({"name":f"ladder-{i+1}","x":x,"top":top,"bottom":bottom})
    return {"calibration": {"platforms": platforms, "connections": [], "route": [],"ladders":ladders},
            "requires_alignment": True, "source_minimap": mini,
            "note": "Candidate only. Assumes the calibrated minimap box exactly contains the source map canvas. Verify overlay first; measure jump/drop links separately."}


def build_pack(catalog, map_id, output, download=False, max_items=128):
    catalog, output = Path(catalog), Path(output)
    map_id = str(int(map_id))
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output must be empty; existing templates are never overwritten")
    links = read(catalog, "WEB/Map/map-links.json", download)
    if map_id not in links["maps"]:
        raise ValueError(f"Map {map_id} is not in this catalog")
    info = links["maps"][map_id]
    book = read(catalog, "WEB/Reward/drop-index.json", download)["modes"]["monsterBook"]
    mobs = {str(int(row["id"])): row for row in read(catalog, "WEB/Mob/data.json", download)}
    drops = {str(int(key)): rows for key, rows in book["mobs"].items()}
    selected, wanted_items, missing = [], {}, []
    for mob in info["mobs"]:
        mid = str(int(mob["id"]))
        row = mobs.get(mid)
        if row and row.get("preview"):
            selected.append(("mob", mid, row["name"], row["preview"]))
        else:
            missing.append(f"No mob preview: {mid}")
        for item in drops.get(mid, []):
            if item.get("type") == "item" and item.get("itemTab"):
                wanted_items[str(int(item["itemId"]))] = item
    if len(wanted_items) > max_items:
        raise ValueError(f"Map has {len(wanted_items)} item references; increase --max-items deliberately")
    by_tab = {}
    for item in wanted_items.values():
        tab = item["itemTab"]
        if not tab.isalnum():
            raise ValueError("Invalid item tab in catalog")
        if tab not in by_tab:
            by_tab[tab] = {str(int(row["id"])): row for row in read(catalog, f"WEB/{tab}/data.json", download)}
        row = by_tab[tab].get(str(int(item["itemId"])))
        if row and row.get("preview"):
            selected.append(("item", str(int(row["id"])), row["name"], row["preview"]))
        else:
            missing.append(f"No preview for {item['itemName']} ({item['itemId']})")
    xml_path = f"wz_files/wz/Map.wz/Map/Map{map_id[0]}/{map_id}.img.xml"
    paths = ["WEB/" + row[3] for row in selected]
    if download:
        fetch(catalog, [*paths, xml_path])
    # Validate every local path before writing anything to the pack.
    for path in paths:
        parts = PurePosixPath(path).parts
        if ".." in parts or "\\" in path or Path(path).is_absolute():
            raise ValueError("Invalid sprite path in catalog")
        if not (catalog / path).is_file():
            raise ValueError(f"Missing {path}; use --fetch")
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for kind, item_id, name, path in selected:
        source = catalog / "WEB" / path
        dest = output / f"{kind}_catalog_{item_id}.png"
        shutil.copyfile(source, dest)  # preserve alpha, never paint a black background
        entries.append({"kind": kind, "id": item_id, "name": name, "file": dest.name,
                        "source_path": "WEB/" + path,
                        "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()})
    xml = catalog / xml_path
    if xml.exists():
        map_data = extract_map(xml)
        (output / "map-data.json").write_text(json.dumps(map_data, indent=2), encoding="utf-8")
        candidate = navigation_candidate(map_data)
        if candidate is not None:
            (output / "navigation-candidate.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")
    revision = subprocess.run(["git", "-C", str(catalog), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    manifest = {"map_id": map_id, "map_name": info["mapName"], "source_version": "GMS v83",
                "source": "https://github.com/andrenogrib/gms_v83_wztoweb", "revision": revision,
                "drop_source": "Client Monster Book; reference only, not Classic drop rates",
                "validated_on_classic": False, "entries": entries, "missing": missing}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--map", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--max-items", type=int, default=128)
    args = parser.parse_args(argv)
    try:
        pack = build_pack(args.catalog, args.map, args.output, args.fetch, args.max_items)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Import failed: {exc}\n")
    print(f"Imported {len(pack['entries'])} references for {pack['map_name']}; {len(pack['missing'])} missing previews.")
    print("Review against recorded Classic frames before copying PNGs into your map's templates folder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
