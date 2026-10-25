# Importing game knowledge without drawing every sprite

The reference importer selects a map's mobs and their Client Monster Book item drops
from the public [GMS v83 WZ catalog](https://github.com/andrenogrib/gms_v83_wztoweb).
It copies transparent sprite PNGs and records IDs, names, source paths, SHA-256 hashes,
and the catalog commit in `manifest.json`. This is reference matching, not training a
neural detector. Original game art belongs to Nexon; downloaded packs stay local and
are ignored by this repository.

## Build a map pack

From the repository root (Windows: use `python`; a virtual environment is recommended):

```text
git clone --depth 1 --filter=blob:none --no-checkout https://github.com/andrenogrib/gms_v83_wztoweb.git data/reference-gms83
python -m tools.import_assets --catalog data/reference-gms83 --map 101010101 --output data/asset-packs/tree-that-grew-2 --fetch
```

`--fetch` downloads just the required index, PNG and map XML paths with Git. Without it,
the importer is offline and fails with a missing-path message. Existing output folders
must be empty; existing templates are never overwritten. Missing previews are listed
in the manifest, and `--max-items` bounds the reference pack size. The source's map index
is `WEB/Map/map-links.json`; use a matching map ID when building another pack.

Copy reviewed `mob_*.png` and `item_*.png` from the pack into your current map's
`profile/maps/<map-id>/templates/`, alongside `player.png`. Existing screenshot crops
can remain. The wizard recognizes imported mob references, and the live runner and
recording replay load item references automatically. No downloads happen in the live loop.

The first local Tree That Grew II import produced 54 references (2 mobs, 52 items),
with 6 unavailable previews. This does not establish complete Classic drop coverage:
Monster Book references can differ from your server and omit mesos/other drops.
Periodic pickup still works for unrecognized items you walk over.

## Compatibility and validation

A catalog pose, inventory icon or v83 map is not guaranteed to match Classic. Animation,
semi-transparency, dropped-item rotation, spell effects and scaling can change pixels.
Imported alpha sprites use a masked colour check, excluding UI regions; item sprites
are searched near the character's feet to limit cost and floor confusion. They retain
a strict acceptance threshold. `detection.sprite_templates_per_tick` bounds reference
work (default 8, round-robin); an item can be missed on a tick where its reference is
not searched. The bot only chases currently visible detections. A single catalog slime pose missed the slime animations
in the repository's screenshot during initial validation. Do not lower thresholds until
background scenery starts matching just to force recall.

Use a short recording from the actual map to evaluate a pack before relying on it.
For a general detector that recognizes unseen drops, a future training dataset should
combine multiple animation poses composited onto varied game backgrounds with real
annotated recordings. Hold out entire recordings/maps for evaluation. Synthetic sprite
training alone is not evidence of live accuracy. The current implementation does not
claim a trained universal mob/drop detector.

## Map/platform knowledge

The pack also contains `map-data.json`: original world-coordinate footholds, their
layer/group/connectivity, ladder/rope definitions and minimap metadata. The importer
joins contiguous, rightward footholds into `navigation-candidate.json` polylines.
Walls/reversed segments are excluded, jump/drop links stay empty, and all platforms
start with `drop_through: false` because traversal must be verified separately.

These are **candidates**. The coordinate conversion assumes the calibrated minimap box
exactly contains that source map's canvas; Classic may have another layout, scale,
padding or crop. Compare the overlay at several known player positions before copying
candidate geometry into calibration. The importer never silently activates it. Measure
connections and set drop-through surfaces using [navigation.md](navigation.md).

The full game client is not needed for this catalog path. A future local WZ extractor
could use the user's matching client for more animation frames; this change only reads
the downloaded offline XML/PNG data and does not run or modify the game client.

Reproduce the initial catalog-only mob check (one frame, optimistic sample size):

```text
python -m tools.eval_perception --data data/labeled/tree-that-grew-2 --templates data/asset-packs/tree-that-grew-2
```

Initial result: 1/6 labelled mobs found, 0 false positives, 16.7% recall. The pack
fails the project's 90% recall criterion. It has not been activated as a replacement
for screenshot templates. Item recall cannot be measured from this dataset because
it has no item labels. Keep these limitations separate from passing control tests.

For reviewed real-recording pose banks and held-out evaluation, see
[reliability.md](reliability.md). Imported map candidates now preserve normalized ladder
geometry; climb routes still require explicit connections and alignment checks.
