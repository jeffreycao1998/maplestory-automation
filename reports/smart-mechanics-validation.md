# Smart mechanics validation — 2026-10-24

Scope: offline tests and screenshots only; no game client was launched and no live
keys were sent. FakeBackend was used for all input tests and replay.

- Final whole repository suite: 718 tests passed (49.5 seconds).
- Smart mechanics tests include sloped/sagging platforms, invalid geometry, successful
  up-jump/down-jump sequences, bounded failed landings, rising-through-platform rejection,
  missing-player watchdog precedence, predictive edge braking, damage settling,
  spell animation locks, pickup attempts, floor/edge loot exclusions, key-release failures,
  imported alpha sprites, reference budgets and offline asset selection.
- Synthetic closed-loop replay: 300 frames (19.9 simulated seconds), 300 OK verdicts,
  31 pickup actions, 9 casts, 2 simulated kills, zero keys held on exit. This simulator
  is not a validation of live platform physics or actual item acquisition.
- Auto-setup proposed the exact minimap, HP and MP boxes in the existing labelled real
  screenshot. Blank frames and unframed partial fills produced no proposals.
- Public GMS v83 pack: 2 mob + 52 item references; 6 missing previews. Original map XML
  yielded 306 footholds, 4 ladders and 27 candidate platform polylines. All downloaded
  assets remain in ignored local folders, with hashes and upstream revision in manifest.
- Catalog-only mob evaluation on the existing single labelled screenshot: 1/6 mobs
  found, 100% precision, no false positives, 16.7% recall. This FAILS the existing 90%
  recall criterion and is not an activated replacement for screenshot references.
  One frame is not a general accuracy benchmark. There are no item labels in that
  frame, so no item recall claim is possible. No neural detector was trained.

Remaining live validation: Classic movement/cast/knockback timing, down-jump support on
particular platforms, map/minimap alignment, item icon rendering, pickup success and
multiple animation poses. Ladder traversal is not implemented. See docs/navigation.md
and docs/assets.md for setup and limits.

## Safe-spot retreat and interruption follow-up

- Added a click-to-mark refuge on the calibrated minimap, per-map storage, bounded
  directed routing, stable arrival and a manual refill pause. Test sequences include
  the real runner's pause/log/key-release integration using fake capture and input.
- Reserve estimates are initialized from explicit run-start counts and reduced by
  potion action attempts. They and retreat intent survive pause/resume. HP/MP resource
  thresholds also trigger retreat, but no inventory OCR was implemented.
- Tested potion/critical-HP priority, unknown/unreachable platforms, diversion after a
  flight already in progress, cast locks, retreat deadlines despite repeated potion
  requests, and no automatic return to combat after arrival.
- Tested foreground identity/client-box checks and failure to verify focus. A focus
  loss mid-drop blocks Jump, releases Down and inhibits later input until manual resume.
  The runner uses a foreground guard on both owned and injected captures when supplied.
- Tested conservative central modal candidates and saved alert references causing
  immediate sticky pauses. The existing real screenshot did not flag as a modal.
  This is not proof of comprehensive verification-prompt or disconnect detection.
- Tested optional, seeded engagement timing variation for reproducibility and unchanged
  survival priority. It remains off by default and does not establish anti-cheat evasion.
- Tested the GUI-resume countdown with an injected fake live capture: input resumes only
  after focus is returned during the countdown. No live game input was used.

The follow-up adds 26 tests. Final complete suite: 744 passed in 52.49 seconds.
All earlier limitations about Classic live validation and catalog detector recall remain.
