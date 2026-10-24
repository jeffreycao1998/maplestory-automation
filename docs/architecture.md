# Architecture & contracts

Owner: main agent. Subagents read this; they propose changes rather than editing it.

## Tick loop

```
capture ──► perception ──► behavior ──► controls
   │            │              ▲            │
   │            ▼              │            ▼
   │        supervisor ◄───────┴──── tick log (JSONL + anomaly frames)
   └── map model (static per map) feeds perception + behavior
```

Each tick (target 15–20 per second):
1. **Capture** grabs the game window → `Frame`.
2. **Perception** turns the `Frame` into a `Perception`.
3. **Supervisor** checks `Perception` + history; may force `PAUSE`/`STOP`.
4. **Behavior** picks an `Action` from `Perception` + `MapModel` + its own state.
5. **Controls** executes the `Action`.
6. A `TickLog` line is written; odd ticks also save the frame.

Perception and behavior are **pure functions of their inputs** (plus behavior's own
state), so both run identically on live capture and on recorded frames.

## Contracts (v0 — expect revisions)

### Frame
- `image`: BGR array of the game window's client area
- `t`: timestamp (seconds)
- `window`: (left, top, width, height) on screen

### Perception
- `player_map`: (x, y) as 0–1 fractions of the minimap, or null
- `player_screen`: (x, y) pixels, or null
- `mobs`: list of `{x, y, w, h, score, template}` in screen pixels
- `hp`, `mp`: 0–1 fractions, or null
- `flags`: set of `dead`, `popup`, `disconnected`, `minimap_missing`
- `confidence`: 0–1 overall

### MapModel (from `configs/maps/<map>.yaml`)
- `name`, `minimap_box`
- `platforms`: list of `{id, x_start, x_end, y}` in minimap fractions
- `ropes`: list of `{x, y_top, y_bottom}`
- `patrol`: `{platform_id, x_left, x_right}`

### Action
One of: `ATTACK`, `FACE_LEFT`, `FACE_RIGHT`, `WALK_LEFT`, `WALK_RIGHT`, `STOP_WALK`,
`JUMP`, `USE_HP_POT`, `USE_MP_POT`, `IDLE`, `PAUSE`, `STOP`.
Controls owns how an action maps to key presses and timing.

### TickLog (one JSON line per tick, `logs/<run>/ticks.jsonl`)
`t, state, action, player_map, player_screen, n_mobs, target, hp, mp, flags,
confidence, anomaly` — anomalies also save `logs/<run>/frames/<t>.png`.

## Behavior states (v0)
`PATROL → ENGAGE → (RECOVER) → PATROL`, with `STUCK`, `PAUSED`, `STOPPED` reachable
from any state. The supervisor can force `PAUSED`/`STOPPED`; behavior cannot override it.
