# Reliability expansion validation — 2026-10-24

Final full test suite: **786 passed in 53.95 seconds** (42 new expansion tests).
All game actions used fake backends. The game was not launched or controlled.

Implemented workflows:

- Local optional RapidOCR reads configured potion counts, meso balance and inventory-full
  notifications. Confidence filtering, distinct-sample confirmation, invalidation, freshness,
  one-job background limits, unreadable-stock guards and reserve correction are covered.
  New setup includes a tested HUD-region step and saves reference view dimensions.
- Observe-only mode ignores even an explicitly supplied physical backend. It labels
  proposed actions, enables preview and draws configured route/ladder geometry. Tests
  confirm the supplied backend receives no key-down or key-up calls.
- Ladder/rope ascent, descent and optional jump-to-grab use explicit geometry and directed
  connections. Tests cover progress, stable exit, invalid alignment, vertical corridor loss,
  stalls, low-confidence release and idempotent held-key transitions.
- Manual recording timestamps feed movement measurements. Candidate walk/jump/climb
  measurements do not silently change live settings. Verified limits reject overlong jumps.
  Key recording filters new presses to configured controls while the game is foreground.
- A recording review UI saves labels only after explicit review. Multi-pose reference
  extraction separates complete recordings, rejects duplicate pixel images across the split,
  evaluates supported mob/drop categories and never silently activates a bank.
- Economy status/run summaries distinguish observed wallet change, visible stock decreases,
  explicit replacement values and unknown net estimates. Pickup presses are not income.
- A live movement lease releases keys independently of the main loop after a stall.
  Context exit closes the watcher; a closed controller cannot be resumed. View-size changes
  stop old coordinate use. Existing focus, prompt, critical-HP and retreat guards remain.

Real pixel checks:

- The existing 1366x768 screenshot contains visible shortcut counts **15** and **19**.
  Local OCR read both exactly above 0.90 confidence using tight, manually checked crops.
  The crop metadata is in data/labeled/tree-that-grew-2/hud-regions.json. Potion type is
  deliberately not inferred from these shortcut icons.
- A reviewed reference-bank smoke check on that same screenshot found **4/6** labelled
  mobs with **0 false positives**, compared with the earlier catalog-only 1/6. This is
  in-sample, not an independent accuracy claim. The bank's reference gate correctly fails.
- Item recall is unknown because the existing real screenshot has no item annotations.
  No universal neural detector was trained, and no held-out real Classic recording exists
  in the supplied dataset. Synthetic held-out recordings test the pipeline, not live accuracy.

Remaining user validation:

- Configure and check HUD crops on the actual client; supply two independent manual
  recordings to learn/evaluate real multi-pose references. Unknown readings stay unknown.
- Verify ladder exits, movement candidates, map alignment, buffs and actual cast timing.
  The automated tests do not establish live Windows input behavior or native GUI layout.
- Review potion replacement prices and wallet interpretation. Inventory sorting, shops,
  arbitrary unseen-drop recognition and anti-cheat bypasses are not implemented.

Use docs/reliability.md for setup, commands, data requirements and interpretation.
