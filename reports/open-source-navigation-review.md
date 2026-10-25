# Public bot review and offline validation — 2026-10-24

Changes use independent implementations of general techniques from public source.
No external executable, model weights or client hooks were installed. No live game
was operated. The input watchdog and stop-on-uncertainty behavior remain enabled.

## Sources inspected

| Project / reviewed revision | Relevant source | Findings |
|---|---|---|
| [Auto Maple](https://github.com/tanjeffreyz/auto-maple/tree/5d88510e582784d6992ef72e8664e7df88d392ff) | `src/routine/layout.py`, `src/modules/capture.py` | Persistent layouts, shortest paths and minimap anchors. Teleport assumptions do not fit this character. No license found in reviewed tree; no source copied. |
| [maple-bot-](https://github.com/MapleStory-Archive/maple-bot-/tree/6cd0df2ff2dee6597392fb180c1eeeb6477420b1) | `backend/src/pathing.rs`, `backend/src/minimap.rs` | Weighted platform graph, capability checks and repeated minimap verification. MIT. Modern double-jump/grapple presets and bundled models were not imported. |
| [MS-ML-Bot](https://github.com/TinForge/MS-ML-Bot/tree/9a3e148b328916d6eff25145b76a49f27135c84e) | `process/detection.py`, `process/bot.py` | Detection association over time and character movement profiles. Unlicense. YOLO weights were not loaded or assumed compatible with Classic. |

## Implemented

- Fresh minimap registration against a checksummed current-client map image. Multiple
  separated texture patches must agree. Missing or ambiguous registration returns an
  unknown position, never a stale coordinate. Cached alignment is freshly checked.
- **Training area → Map and spawn zones…** previews source geometry and selects a
  reachable training destination. Full-map coordinates are opt in; changing basis
  clears old viewport routes, safe spots and incompatible motion measurements before
  wizard Save. Existing viewport setups remain supported.
- Footholds become polylines; ladder endpoints and spawn foothold IDs produce reference
  geometry. A spawn is neither a live monster sighting nor proof of reachability.
  Jump/climb links require reviewed character measurements. Unsupported endpoints are
  discarded; existing bounded navigation confirms landings and stops on failure.
- Route search minimizes summed connection time budgets rather than hop count. This
  cost is not a learned estimate of actual travel time.
- Bounded local sprite searches retain recent sightings across the global animation
  pose cycle. Every result requires a current-pixel match; vanished mobs are not
  extrapolated. Other poses of the same species are checked locally.
- Drag training limits on one platform; known ramps can have different endpoint
  heights. A drag never invents a jump or joins different platforms.

## Recorded screenshot measurements

Dataset: `data/labeled/tree-that-grew-2/000001.png`, 1366×768, six labeled monsters.
Assets: community Classic World Release / client label 1.0.0, 18 monster poses.
This repeatedly processes **one static frame**, not live video or a held-out map set.

| Check | Result |
|---|---|
| Six ticks, tracking cleared each tick, global budget 8 | 1, 2, 2, 2, 2, 2 detections |
| Six ticks, fresh local tracking, same global budget | 1, 3, 4, 4, 4, 4 detections |
| Detection centers outside every labeled mob box | 0 in both runs |
| Matcher time with tracking, six observations | 226–245 ms on this development machine |
| Cold map registration | 243 ms, 30 agreeing patches, mean score 0.9943 |
| Fresh cached-alignment verification | 10 ms, 33 agreeing patches |
| Transform inside 166×112 minimap ROI | offset (33, -16), basis (99.1875, 167.1875) pixels per full-map fraction |

The source image is 99×167 while the visible ROI is 166×112. It is cropped and scrolled;
viewport routes cannot consume full-map spawn coordinates directly. The clipped top
monster and the hit-effect-occluded monster remain missed. This is not a general
trained Classic detector; moving detections and Windows loop speed need user testing.

## Actual map imports

The real pack builder downloaded the reference, installed it in a temporary profile,
verified the hash and resolved it through `Profile.map_reference_descriptor`. Tree
That Grew II supplied 14 platform polylines, 4 ladders/ropes, 10 spawn zones, 18 monster
poses and 30 common pickup icons. Two expected icon names remain unavailable:
Dark Stump Leaf and Steel Ore.

Kerning City Construction Site (10003010) has 477 source foothold segments, imported
as 48 positive-direction platform polylines, plus 14 ladders/ropes. Some rope bases
require a jump to grab them. **Bottom-to-top travel has not been live validated.**
A source graph does not prove the character can execute every proposed edge.

## Verification

- Full offline suite: **914 passed in 63.70 s**.
- Registration checks cover scrolling, stale/blank/wrong/repeated imagery, source
  scale, missing descriptors and changed coordinate metadata.
- Integration checks cover bank checksum/build identity, real-shaped API import,
  coordinate-switch invalidation, unreachable goals/safe spots, full-map clicks,
  weighted routes and current-pixel verification of animated/disappeared mobs.
- Existing input-release, spell-lock, landing, critical-HP and watchdog tests passed.
- Native Windows UI, actual jump/climb execution and bottom-to-top travel remain
  unverified. Observe-mode inspection does not prove a physical traversal succeeds.
