# Shorter setup and correct Classic World source — 2026-10-24

## Scope and source correction

The user identified the target as the newly released official Global MapleStory
Classic World. Legacy GMS v83 was an unverified and unsuitable default catalog.
The new default is the community extraction at `https://maplestory.quest/raw`, labeled
Release / Grand Launch 1.0.0 / 21 October 2026. It is not a Nexon API or independent
verification of the user's installed build.

Confirmed differences: The Tree That Grew II uses map ID `10002061`, rather than
`101010101`; Slime uses ID `7`, rather than `210100`. The current map's client spawn
list includes Slime, Stump and Dark Stump, rather than the old two-species list.
Legacy imported banks are retained but inactive under the current Classic provider.
Source build/snapshot metadata and PNG checksums are retained in the active bank.
No old server drop tables are joined to current monster IDs. Common current-client
pickup icons are labeled as references, not verified drop coverage or rates.

## Verification

- Full offline suite: **880 passed** in 56.00 s, macOS / Python 3.12.
- New tests cover the six-page default, opt-in seventh OCR page, exact recorded
  HP/MP/minimap calibration and automatic yellow-dot sampling with orange-party-dot
  rejection, ambiguity/blank abstention, preservation of manual edits, stale automatic
  region removal, and resize invalidation before the first capture.
- Current-source tests cover release-specific IDs, animation frame parsing, death
  exclusion, metadata checks, old/wrong-build bank rejection, trusted file paths,
  unsupported/truncated data and no silent v83 fallback. Catalog species references
  are interleaved within the unchanged per-tick budget.
- Real public acquisition: **18 distinct mob poses and 30 common pickup icons**;
  `Dark Stump Leaf` and `Steel Ore` were not found. Filtered requests avoid the
  oversized full item index. Files remain local and ignored by Git.
- One labeled screenshot, default 8-template tick budget: **1/6 mobs found**, zero
  false positives, about **263 ms/frame** in this run. Full-bank setup preview:
  **4/6 found**, zero false positives. Recognition changes as the reference budget
  cycles; these are not representative live metrics. The 90% recall target still fails.
- HP fill differs from the frame's label by 0.001; MP by 0.011. Minimap player error
  is 0.002% horizontally and 0.043% vertically. This is one frame, not broad UI validation.

The new six-page wizard groups automatic UI calibration into a single review page;
region corrections remain available there. It does not infer custom keybindings,
choose a safe training platform, or guess which party member is the user's character.
The optional OCR dependency/version issue is separate from HP/MP bar detection.
No game was launched/controlled, and the native Windows GUI layout has not been smoke
tested. Current release data does not validate jump/cast timings, safe routes, server
drop rates or handling of clipped/occluded monsters.
