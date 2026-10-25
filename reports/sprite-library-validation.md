# Automatic map sprite setup validation — 2026-10-24

The former implementation only exposed a Git/CLI catalog importer and still required
manual templates in a fresh wizard. Setup now downloads and activates map-specific
animation references without drawing boxes or copying files manually.

- Full offline suite: **858 passed** in 53.99 s (macOS,
  Python 3.12); 29 new sprite-library tests cover map search, frame enumeration,
  alpha preservation, deduplication, death/hurt exclusion, optional item failures,
  explicit preview fallback, cancellation, checksums, cache/HTTP limits and host
  restrictions, immutable-bank publication, interruption safety and per-map Profile
  loading that satisfies the wizard's mob step without manual crops.
- Final app regression checks after clearing stale screenshot overlays: **231 passed**;
  the settings-without-PyYAML subprocess check also passed.
- Real asset acquisition: v83 map `101010101`, 14 distinct monster poses and 52 item
  icons; 6 missing item previews reported. No source images are committed.
- `python -m tools.eval_perception --data data/labeled/tree-that-grew-2 --templates
  data/asset-packs/tree-that-grew-2-animated`: **3/6** labeled mobs found, **0 false
  positives**, default per-tick reference budget 8, 256.1 ms/frame in this run.
- Full-bank setup preview: **4/6**, **0 false positives** (all 14 templates searched).
  The remaining labels are partly above the screen or behind an attack effect.
- One frame is not a held-out dataset. Recall remains below the 90% phase target;
  no trained universal detector or live Classic accuracy is claimed. Native Windows
  wizard layout/download/cancel behavior still needs a Windows smoke test. The bot
  was never run against the game.

References: pinned catalog `58c0c763b2eb4a868e5aeb198e6ead3313f229df`, and public
`https://maplestory.io/api/GMS/83/mob/<id>/render/<action>/<frame>` PNGs. Cached assets
record source URLs and SHA-256 hashes. Game art remains owned by Nexon.
