# Visible supplies, observation, routes and learning

All features are implemented with screen pixels, normal keys and local files. Start
with **Observe only**. Windows game timing and new map alignment still require your
own supervised validation. This repository contains one real labelled screenshot, not
a representative Classic recording dataset.

## Install and read visible potion counts

Activate the repository's Python environment, then install the optional local OCR runtime:

```text
python -m pip install -e ".[ocr]"
```

The pinned [RapidOCR runtime](https://rapidai.github.io/RapidOCRDocs/v1.4.4/install_usage/api/RapidOCR/)
contains local models. The app performs inference locally and disables ONNX telemetry;
it does not upload screenshots. If the optional dependency is missing, enabling OCR
produces a setup error rather than silently pretending stock is known.

In the setup wizard, **Visible supplies** is an optional step after HP/MP bars. Draw a
tight box around the complete number on each potion shortcut, excluding its icon/key
label. Also box the meso balance (open the relevant panel if needed) and optionally the
notification area where inventory-full messages appear. Click **Test OCR**, compare the
readings with the screenshot, then enable OCR. Keep those regions visible during a run.
The test does not establish correctness for every font/scale or future screenshot.

Numbers need two separate high-confidence OCR samples. Blank, ambiguous, low-confidence
and stale reads are unknown, not zero. Changed counts invalidate the previous count
until confirmed. HUD work uses one background job so OCR does not hold game keys or
block the decision loop. Replays use synchronous OCR for repeatable frame-time results.

Fresh visible stock corrects reserve estimates; a cached sample cannot repeatedly undo
a potion attempt. `~` on a potion count in the status window means an estimate. If a
configured stock crop becomes unreadable, ordinary movement/combat stops and the bot
pauses after the configured timeout. A retreat already in progress can continue on known
geometry with its existing deadline; HP, focus, prompt and missing-player checks still win.

Inventory-full detection recognizes supported English phrases in the configured
notification area. It retreats to a configured safe spot or stops for manual clearing.
It never assumes an item entered inventory merely because Pickup was pressed. Ordinary
inventory menus may also trigger the conservative central-dialog pause. No inventory
sorting, shop purchases or verification-dialog interaction is automated.

## Live Observe mode

The main window has **Observe only — show decisions without keys**. It turns on preview
and runs capture, perception, decisions and watchdogs with a fake input backend, even if
a caller supplied a physical backend. It never calls the supplied backend. Proposed
platform connections are drawn as arrows on the minimap; ladders and the safe marker
are shown too. States/actions are labelled as observations/proposals in the UI and logs.

Observe mode still follows the actual screenshots: it cannot simulate how the game
would have moved after an unexecuted proposal. A stationary character may therefore
produce a proposed stuck recovery. Move manually to exercise routes. No observed pickup
proposal is recorded as an actual pickup attempt or income.

## Ladders and ropes

Use Settings → Screen setup to add `ladders` and directed `climb` connections. Example:

```json
[{"name":"central-rope","x":0.5,"top":0.4,"bottom":0.7}]
```

```json
[
  {"name":"climb-up","source":"lower","target":"upper","kind":"climb","launch_x":0.5,"land_x":0.5,"ladder":"central-rope","grab_jump":true,"timeout_s":8},
  {"name":"climb-down","source":"upper","target":"lower","kind":"climb","launch_x":0.5,"land_x":0.5,"ladder":"central-rope","timeout_s":8}
]
```

Both named platforms must exist and meet the ladder endpoints. `grab_jump` is optional
and only allowed for ascent. The bot aligns horizontally, holds Up/Down, optionally
jumps while holding Up, verifies progress and alignment, stops at the destination,
and requires stable arrival. A stall, drift off the rope, missed exit or deadline pauses.
All stops release vertical as well as horizontal keys. Configure the Up key in Settings
if it differs from the default arrow. Different clients/rope geometries need validation.
Imported map candidates now include ladder geometry but do not silently create routes.

## Measure your character from manual play

Record several separate walks, at least three complete jumps returning to the same
height, some directional jumps, casts followed by attempted movement, and climbs:

```text
python -m tools.record --name movement-check --fps 15 --seconds 120 --keys
python -m tools.calibrate_motion data/recordings/movement-check --profile profile --output reports/movement-check.json
```

The recorder logs configured control presses only while the game is foreground; it
still records corresponding releases. Calibration uses the recorded capture timestamps,
not an assumed uniform FPS. Incomplete/non-monotonic timestamps are rejected unless you
explicitly reconstruct a replay with an FPS override.

The report contains observed walk/climb speed, conservative jump rise/reach estimates,
complete jump samples and warnings. Cast-to-movement delays include human delay and
cannot by themselves prove an animation lock. Measurements are **candidates**, with
`verified:false`; they are not silently applied. Copy a reviewed candidate object into
`calibration.movement_profile` in Settings if useful. Mark it verified only after checking
it for this character, speed/jump buffs and minimap calibration. Verified rise/reach limits
reject configured jumps beyond those limits. Unmeasured values remain unknown.

## Learn a multi-pose reference bank from real recordings

Capture at least two separate recordings, with varied poses, locations and backgrounds:

```text
python -m tools.record --name training-a --fps 15 --seconds 120
python -m tools.record --name validation-b --fps 15 --seconds 120
python -m tools.label_recording data/recordings/training-a --output data/labeled/my-map --every 15
python -m tools.label_recording data/recordings/validation-b --output data/labeled/my-map --every 15
python -m tools.learn_references --data data/labeled/my-map --output data/reference-banks/my-map --profile profile
```

The review app proposes detections, lets you add/delete mob/drop boxes and visibility
labels, and writes labels only when you click **Save reviewed frame**. Check missed
objects as well as wrong boxes. It never sends game input. Capture your own name tag
once with the regular setup wizard; it remains the player reference.

Learning extracts diverse, visible poses from training recordings. Entire recordings
are held out for evaluation; duplicate pixel images crossing the split are rejected.
The report gives separate mob/item recall, precision and false positives per frame.
A reference gate requires at least ten unique held-out frames, 90% recall and at most
0.1 false positives/frame for supported labelled categories. This small gate is a
screening threshold, not proof of general Classic accuracy. Unlabelled categories have
unknown recall. `--in-sample` is only an explicitly optimistic smoke check.

This is a multi-pose reference bank, **not a trained universal neural detector**. It
cannot promise recognition of unseen sprites. It does not automatically replace your
current map's templates. Inspect `manifest.json`, review held-out misses/false positives,
then copy appropriate `mob_*.png` / `item_*.png` into that map's templates and check them
in Observe mode. Keep a backup and evaluate the resulting complete template set before
live use. Live template work is bounded per tick; evaluation searches the complete bank.

## Economy and profit estimates

With meso/stock regions visible, the status window shows wallet change and a net-per-hour
estimate. Set HP/MP replacement costs under Settings → Economy. Missing stock, wallet
reads or prices yield `--` where a trustworthy estimate is unavailable.

The formula is **observed wallet change − valued visible potion-stock decreases**.
This is an estimate of replacement consumption, not a transaction ledger. Manual shop
purchases, moved stacks, loot, sales and other spending can affect the readings. A bought
potion can already reduce wallet balance, so do not interpret the valuation as realized
profit or assume purchases are distinguished automatically. Run summaries preserve the
basis, stock decreases, last known wallet change, and physical pickup attempts; there
is no invented confirmed-kill or successful-pickup counter.

## Additional stop protections

New setup saves the reference view dimensions. A resized game view pauses instead of
reusing HUD/minimap coordinates blindly. Existing profiles without a saved reference
size should rerun screenshot setup to enable this check.

Live movement has an independent input lease (default 0.5 seconds). If the main loop
stalls while a movement key is held, the controller releases keys and stays inhibited
until manual resume. This complements normal edge braking and the foreground guard;
it does not guarantee a character can never fall or be hit. Start/Resume countdowns
and prompt/critical-HP stops remain active. No anti-cheat bypass or detection guarantee
is provided by any of these features.
