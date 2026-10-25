# Reliability expansion

Implement and verify offline; no live game is launched by the agent.

1. Visible stock/wallet OCR with confidence, fresh-sample confirmation, bounded background
   work, unreadable-data stops, inventory-full handling, and setup regions.
2. Observe-only live pipeline with physical input disabled and a route/decision preview.
3. Directed ladder/rope traversal with explicit geometry, guaranteed key release and
   progress/landing deadlines. Recording-driven movement measurements remain proposals
   until the user validates them on their character.
4. Real-frame multi-pose reference extraction, labelled dataset review/export and evaluation
   on separately supplied recordings. Do not substitute synthetic accuracy for Classic accuracy.
5. Wallet change, valued potion stock decrease and net estimates in status/run reports.
   Do not infer mesos gained from pickup presses or call an unconfirmed disappearance a kill.

Existing uncommitted improvements stay in place. All calibration-dependent behavior is
opt-in; unknown or stale observations must not silently become inventory facts.

Implementation and offline verification completed for all five workflows. Final full
suite: 786 passing tests. Real screenshot OCR reads 15/19; a reference-bank smoke check
finds 4/6 mobs in-sample with zero false positives and remains unactivated. See
reports/reliability-expansion-validation.md. Live validation and independent Classic
recordings remain required inputs, not inferred successes.
