# Data

```
data/
  recordings/<name>/      # raw recordings from tools/record.py (git-ignored, large)
    frames/000123.png
    meta.json             # window size, fps, map, resolution, date
    keys.jsonl            # optional: {"t": 12.34, "key": "ctrl", "down": true}
  labeled/<map>/          # small hand-labelled set, committed
    000123.png
    000123.json
```

## Label format (`<frame>.json`)
```json
{
  "map": "henesys-hunting-ground-1",
  "minimap_box": [10, 30, 200, 120],
  "player_map": [0.42, 0.61],
  "player_screen": [512, 430],
  "mobs": [
    {"box": [600, 400, 48, 40], "kind": "green-mushroom"}
  ],
  "hp": 0.85,
  "mp": 0.40,
  "flags": []
}
```
- Pixel boxes are `[x, y, width, height]` relative to the game window.
- Minimap positions are 0–1 fractions of the minimap box.
- Use `null` for anything not visible in the frame.

## What to label
Aim for variety, not volume: mobs facing both ways, overlapping mobs, mobs at the
screen edge, you mid-cast, damage numbers on screen, empty screens, and any death,
popup or loading screen.
