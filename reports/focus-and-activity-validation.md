# Focus recovery and visible activity — 2026-10-24

The reported symptom was no movement or attacks. The user supplied the active pause
reason: "Game window lost focus or could not be verified; manual resume required".
This identifies an input-guard pause, not evidence of a failed monster detector.

## Changes

- Start and explicit Resume attempt ordinary OS window activation before the existing
  countdown. `ScreenCapture.activate()` verifies actual foreground identity after the
  request; a successful API return alone is insufficient. Failed activation leaves
  time for the user to click the game. Observe only never activates it.
- Unexpected focus loss still releases keys, pauses, and requires explicit Resume.
  Its status now includes a recovery instruction and target-window diagnostics.
  There is no automatic reactivation loop and no bypass of the foreground guard.
- The app's topmost preference applies while stopped, so it cannot stay over the
  game during capture. The label makes this behavior explicit.
- The main status displays the decision reason. The Activity log retains detection
  evidence, requested bindings, lifecycle changes and pause/error details, including
  transient statuses that the ordinary display coalesces. History is bounded and
  repeated details throttled by `configs/activity.yaml`.
- Copy diagnostics includes recent history, map, window filter, configured keys,
  readiness problems and log paths. It copies locally; nothing is uploaded.
- Readable activity is emitted through existing logging to `logs/app.log`. Detailed
  per-frame JSON remains in each run's `ticks.jsonl`.
- A decision completing concurrently with Pause/Stop no longer overwrites the visible
  pause/stop reason with a generic Running update.

## Offline verification

- Full suite: **932 passed in 65.46 s**.
- Final focused checks after the recovery-text/copy-diagnostics adjustments:
  **8 passed in 0.62 s**.
- Cases cover verified/refused activation, no reactivation after focus loss, activation
  on explicit Resume, no activation/input in Observe only, released held keys, history
  bounds/throttling, retained transient pauses, topmost restoration, disk-log output
  and copied diagnostics.

Windows API calls were simulated; no live game was operated. Native Windows window
activation and the user's installed client remain to be confirmed on their laptop.
An "input requested" entry is not proof that the game accepted an input.
