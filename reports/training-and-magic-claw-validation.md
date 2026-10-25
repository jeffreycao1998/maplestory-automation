# Training-only behavior and Magic Claw target eligibility

## Scope

The user reported LOOT_SWEEP repeatedly selecting pickup, then requested training
only because a pet will handle drops. They also clarified that Magic Claw was being
considered against monsters directly below the character on another platform and
asked for hitbox research before casting.

## Research

Primary data inspected: community-extracted Classic World **Release**, client label
**1.0.0**. This is third-party client data, not an official Nexon targeting guarantee.

- [Current skill reference](https://maplestory.quest/classes/magician/skills/magic-claw)
- [Skill record](https://maplestory.quest/api/raw/record?v=Release&key=skill&record=skill%3A2001003)
- [Raw skill node](https://maplestory.quest/api/raw/node?v=Release&path=Skill%2F200.img&node=skill%2F2001003)

The description states a forward range of **300**. Every inspected level has
`mobCount=1`, `attackCount=2`; MP cost varies by rank. The raw skill contains action,
level, hit and effect fields, but no exported `lt/rb` targeting rectangle. Animation
sprite dimensions are not targeting bounds. The source itself identifies missing
search-origin/step/obstruction rules. Legacy/private-server targeting code and user
forum opinions were not used as authoritative Classic World hitbox coordinates.

## Implementation

- Training only defaults true and overrides old saved `loot.enabled=true` without
  requiring setup again. No pickup/pursuit/sweep actions or item matching occur in
  this mode. Keys, saved manual-loot preferences and safety settings remain intact.
- Fixed the fallback scheduler too: optional blind sweeps now run only during an
  established patrol walk, after route/edge/stuck decisions. They always yield the
  following decision. A slow frame can no longer select pickup before starting to walk.
- Magic Claw caps configured reach at 300 and preserves facing/cast-lock checks.
- Cast/approach candidates must have compatible estimated feet/floor heights. With a
  registered map, the current platform's slope determines expected target floor
  height; a closer match to another known platform rejects the target. A tall sprite
  extending up from a lower floor cannot qualify solely because its center is nearby.
- Hysteresis candidates also pass the eligibility check. Generic targeting remains
  available for explicitly calibrated other skills and synthetic fixtures.
- Activity details identify training-only mode and incompatible platform/height
  targets. The preview uses the effective horizontal cap.

## Limits

This is a **conservative eligibility proxy, not a reconstructed exact engine hitbox**.
Player feet offset (35 px), feet tolerance (24 px), sprite-foot estimates and the
1:1 world/screen projection assumption are documented estimates in
`configs/skills/magic-claw.yaml`. Nonstandard world scaling and avatar/name-tag
calibration can require tuning. Cross-platform shots may be rejected even if the game
would permit them. No exact cast duration or point-blank exclusion was invented.
No live game was operated; the user's client still requires live confirmation.

## Verification approach

New regression cases cover an old profile with looting enabled, visible drops during
combat, a below-platform target at the same x, a tall lower-platform sprite, the 300
range cap despite an old 350 setting, facing/cast timing, sloped source geometry,
a nearby lower surface inside the ordinary height tolerance, and slow capture rates.
Generic combat simulations test kill/safety behavior independently of Classic sprite
geometry. Empty-map patrol coverage is now tested directly; random fights are allowed
to remain near productive spawns instead of being required to cover an arbitrary span.

Final full offline suite: **944 passed in 65.07 s**. The original fallback-starvation
regressions failed before the scheduler change and passed afterward. Native gameplay
and the conservative foot-height model have not been validated against live casts.
