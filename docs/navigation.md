# Smarter movement and looting

The bot still reads the screen and uses normal keys. All new timing and geometry is
configurable; there is no universal physics calibration. Test changes on recordings,
then supervise the first live run yourself. Windows input has not been live-tested
as part of this change.

## Existing setups

Existing profiles load without migration. Pickup defaults to **Z**; change it in the
wizard's Keys step or Settings → Keys. Set it empty, or turn off Loot → Enabled, to
disable looting. An enabled pickup key must be different from attack, jump, movement,
and potion keys. No item pictures are required for periodic pickup taps during patrol.

The bot reserves a small margin before patrol/platform edges and brakes using recent
minimap speed. Nearby mobs add knockback clearance. An observed HP drop stops walking
briefly to let knockback settle; potions retain priority. These are precautions, not a
guarantee against every hit. Tune `edge_margin`, `reaction_s`, `knockback_margin` and
`knockback_wait_s` for your client and capture rate.

`skill.animation_lock_s` defaults to 0.65 seconds for the existing magician setup.
Both decisions and controls prevent movement/jumping until the lock finishes. Measure
it for your skill. A failed key action now releases the existing walk as well as the
keys it just pressed. Pause/Stop still release all tracked keys.

## Platforms, ramps and sagging bridges

In Settings → Screen setup, Platforms, Connections and Platform route accept JSON.
They are stored per map in `profile/maps/<map-id>/map.yaml`. Empty Platforms preserves
the original one-bridge setup. Each polyline must run strictly left to right. Its local
height is interpolated, so walking up a ramp does not look like falling off a bridge.
Coordinates are fractions **inside the calibrated minimap box**, with Y growing down.
The minimap preview draws configured surfaces in blue.

Example Platforms (illustrative geometry, not a configuration for a real map):

```json
[
  {"name":"ground", "points":[[0.1,0.8],[0.9,0.8]], "tolerance":0.015},
  {"name":"bridge", "points":[[0.2,0.5],[0.5,0.56],[0.8,0.5]], "tolerance":0.015, "drop_through":true}
]
```

Example Connections:

```json
[
  {"name":"up", "source":"ground", "target":"bridge", "kind":"jump", "launch_x":0.5, "land_x":0.5, "timeout_s":1.5},
  {"name":"down", "source":"bridge", "target":"ground", "kind":"drop", "launch_x":0.5, "land_x":0.5, "timeout_s":1.5}
]
```

Example repeating route: `["bridge", "ground"]`.

Connections are **directed** and must be measured for your character. A jump can be
straight up or left/right; the bot approaches its launch point, jumps once, steers
only toward its landing coordinate and requires stable minimap observations on the
destination. It stops and pauses on a missed landing or timeout. A drop is allowed only
from a platform explicitly marked `drop_through`, with a lower destination directly
below: stop walking, hold Down, tap Jump, release Jump, release Down. Walk connections
must join touching/overlapping surfaces at nearly equal heights. Jump/drop endpoints
must reserve the edge margin. Unknown gaps are never inferred as reachable.

Without a route, the first listed platform is the training destination; recovery from
another known platform needs a configured path back. An unknown surface triggers the
existing platform watchdog. During an intentional jump/drop only the platform timer
gets a bounded exemption; HP, missing-player and confidence checks remain active.
Ropes/ladders can now use explicit climb connections; see [reliability.md](reliability.md).

## Drops

Optional `item_*.png` references enable movement toward same-floor drops. Import a
catalog pack as described in [assets.md](assets.md), or choose Drop / mesos in the
wizard's Mobs step to save a screenshot reference. Only monsters count toward the
wizard's required mob pictures. Yellow boxes in the live preview mark detected items.

Nearby visible drops can be picked up between casts, so combat cannot monopolize the
bot forever. More distant drops are pursued when no mob needs engagement. It never
plans a platform transition just to chase a drop, and edge braking wins over loot.
`loot.max_attempts` and `loot.pursuit_s` bound failed pickups/chases; the location is
ignored for `loot.ignore_s` before trying again. This handles things like other players'
protected drops or an inventory that cannot take an item. Pickup input is not proof
that an item entered the inventory. Floor matching is a screen-space heuristic;
`floor_tolerance_px`, `player_feet_offset_px` and `range_px` need tuning.

## Automatic screen setup

The minimap and bars steps have Auto-detect buttons. These propose closed Classic UI
frames and show the result for review before Save. HP/MP detection requires aligned red
and blue framed bars; it does not mistake the filled portion for the bar's full width.
An obscured frame, empty bar, different UI or ambiguous result may require manual setup.
Your own name tag still needs one reference to distinguish you from other players.

## Offline checks

```text
python -m pytest -q
python -m tools.replay --synthetic 300 --stop-on-pause --quiet
```

Synthetic sequences cover ramps, jump/drop ordering, landing success/failure, potion
priority, edge braking, damage recovery, bounded looting, key release and UI proposals.
They do not validate live Classic movement timing or real-world detector accuracy.

## Click a safe spot and retreat when supplies run low

In the setup wizard's **Training area** step, click **Choose safe spot**, then click
on a platform in the minimap. The white marker is where the character should stop.
The marker must be on a known surface, inside the edge margin; a one-bridge setup
needs its platform height calibrated first. Choose a place you have checked is safe:
the bot cannot establish that a location is free of mob spawns.

In **Settings → Safe retreat**:

- Set **HP/MP potions at run start** to your actual stock if you want reserve triggers.
  Empty leaves the count unknown. The status window shows estimates, reduced by potion
  action attempts. Unless the new visible HUD OCR is enabled and fresh, these are estimates: failed key attempts can make the estimate
  too low, and manual potion use can make it too high. Looted/refilled potions are not
  automatically added to attempt estimates. Fresh HUD OCR can correct visible stock; see [reliability.md](reliability.md). Re-enter accurate starting stock if using estimates.
- Set HP/MP reserves (default 10) high enough to cover travel. Low-resource triggers
  also work without counts: HP below 25% or MP below 8%, provided a marker is set.
- The bot latches retreat, stops attacking/looting, finishes any flight already in
  progress, follows measured platform links, then walks to the marker. Emergency
  potions, spell locks, edge braking and critical-HP stops keep priority.
- After stable arrival it releases keys and pauses for a manual refill. Pause/resume
  does not replenish estimates or cancel retreat. Stop, refill, update the starting
  stock and start a new run. There is no automatic shopping or inventory management.
- Unknown surfaces, missing routes, missed landings or the retreat deadline (default
  45 seconds) stop and pause. It does not invent a jump or rope route. In a one-bridge
  setup the safe spot must be on that same calibrated bridge. Connections are still
  required for travel between platforms.

## Screen interruptions and optional play timing

Live input checks game focus at each action and before every key-down. A focus change
releases held keys and pauses; it never automatically brings the game forward.
Resume uses the configured start-delay countdown so there is time to click back into
the game. F11 can resume directly from the game if hotkeys are working.

**Pause on central dialog candidates** detects some large, bright neutral panels and
may pause on ordinary menus too. It is a conservative screen heuristic, not a general
verification-prompt detector. In the wizard's Mobs step, choose **Stop alert** and save
a distinctive picture of a known verification/disconnect dialog to add a stronger
reference. Alert pictures live under `profile/alerts/`, shared by all maps. A match
always pauses for you to handle manually; no prompt is answered or dismissed. Missing
minimap, low confidence and critical-HP watchdogs remain active during retreat.

**Settings → Play style → Vary engagement timing** optionally adds a short reaction
delay before a fight and positive variation between casts. It is off by default.
It does not delay potions/retreat/edge corrections or shorten the cast lock. Its seed
makes replay reproducible. These settings and interruption guards do not hide automation
or guarantee protection against anti-cheat detection or account penalties. No game hooks,
process hiding, prompt bypasses or anti-cheat manipulation are implemented.

For OCR-based stock, inventory-full notification reading and the independent movement
input lease, see [the reliability guide](reliability.md).


## Map and spawn-zone picker

On **Training area**, use **Map and spawn zones…** after downloading the current map's
sprite pack. Refresh the screenshot while standing on a platform first. The picker
requires a strong match between the visible minimap and that map's downloaded image,
and exactly one known safe platform under your own dot. A position on a rope, an
ambiguous dot or another map's image cannot initialize the plan.

Orange markers show source spawn locations. Click a marker or its platform to choose
a training destination. The picker refuses a destination if the current platform has
no supported path to it. Selecting a spawn does not chase an invisible monster; the
bot travels to the selected training platform, then uses current screen detections.

Applying the plan changes to full-map coordinates, imports platforms and ladders, and
clears the previous safe spot and viewport route/measurements. This remains an
in-memory wizard edit until **Save**. Choose a safe spot again in the new coordinates.
When the minimap scrolls, the bot verifies the current crop against the source image.
If registration fails it reports a missing position and stops movement. Overlays and
retreat destinations use the same transform.

Jump and rope routes need measured character capabilities. After aligning and saving
the map, manually record walking, three complete directional jumps and climbing:

```powershell
.venv\Scripts\python.exe -m tools.record --name movement --fps 15 --seconds 90 --keys
.venv\Scripts\python.exe -m tools.calibrate_motion data/recordings/movement --profile profile --output reports/movement.json
```

Use the recorder's `--help` for capture options and stopping controls. Review the
recording and report, reopen the picker, check the review box and import the JSON.
The report must have no unresolved warnings and must match the map image, build and
full-map coordinate basis. Movement limits are never borrowed from a modern bot.
Only then are supported jump/climb links proposed. Inspect them in **Observe only**;
a proposed route still needs live validation by you. Landing timeouts, loss of rope
alignment and missing paths stop traversal rather than improvising another jump.

Kerning City Construction Site contains many platforms and 14 ropes/ladders in the
current source data. Some rope bases require a jump to grab them. The importer and
picker expose these prerequisites, but bottom-to-top travel is not yet live verified.
[Research and measured limits](../reports/open-source-navigation-review.md).
