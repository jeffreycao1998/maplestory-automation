# maplestory-automation

Screen-reading training automation for a magician in Global MapleStory Classic World.
It looks at the game window and presses keys — nothing else.

**Status:** screen perception, replay tests, supervised controls and configurable navigation.
New movement/loot features, safe-spot retreat, prompt/focus stops and optional play timing
are tested offline; live Classic timing still needs validation.
See [OCR, Observe mode, ladders, learning and economy](docs/reliability.md),
[movement and looting setup](docs/navigation.md) and [bulk sprite/map import](docs/assets.md).
ZIP installations can [update from the app](docs/updating.md) after installing a copy
with the **Check for updates** button.
Setup has six pages: one detection/review page for the game window, minimap, player dot
and HP/MP bars, then monsters, your name tag, training area, keys and review. Potion/meso
OCR adds an optional seventh page. HP/MP bar detection does not need the OCR package.
Monster downloads now target a community Classic World 1.0.0 release snapshot, not
legacy GMS v83. Manual mob boxes are optional; covered mobs can still be missed.
The historical phase checklist is in [`docs/roadmap.md`](docs/roadmap.md).

## Layout
```
CLAUDE.md               # project context + orchestration rules for the main agent
.claude/agents/         # specialist subagents, one per part of the system
docs/architecture.md    # data contracts between parts
docs/roadmap.md         # phases and exit criteria
configs/                # keybinds, timings, thresholds, per-map configs
src/maplebot/
  perception/           # screenshot -> what's on screen
  mapmodel/             # platforms, ropes, patrol zones
  behavior/             # state machine: what to do next
  controls/             # actions -> key presses, guaranteed release
  supervisor/           # kill switch, watchdogs, run logs
  replay/               # run the pipeline on recordings
tools/                  # recorder, labeller, eval, replay CLIs
tests/                  # pytest, all runnable without the game
data/                   # recordings (ignored) + labelled frames
logs/                   # run logs (ignored)
reports/                # run analyses
```

## Working with Claude
Open this repo in Claude Code. The main agent reads `CLAUDE.md`, plans the work,
and delegates to the subagents in `.claude/agents/`:

| Agent | Does |
|---|---|
| `perception-engineer` | detection accuracy, measured on the labelled dataset |
| `map-modeler` | per-map platform and patrol configs |
| `behavior-designer` | the decision state machine |
| `controls-engineer` | key mapping, timing, guaranteed key release |
| `safety-supervisor` | kill switch, watchdogs, run logging |
| `test-harness-engineer` | recorder, labeller, eval, replay, test fixtures |
| `run-analyst` | ranks failures from real run logs (read-only) |
| `code-reviewer` | pre-merge review (read-only) |

Claude never runs the bot against the live game — only you do.

## Before relying on it
Botting breaks Nexon's terms of service and accounts on official servers do get banned.
