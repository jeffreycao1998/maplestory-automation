# MapleStory Classic training automation

Screen-reading training bot for a **magician** in MapleStory Classic. It looks at pixels
and presses keys — no memory reading, no packet editing, no client modification.
Start: one map, one ranged skill. Grow from there.

## Environment (fill these in — every agent relies on them)
| Setting | Value |
|---|---|
| OS | Windows (bot runs on the user's PC; Claude's sandbox is Linux) |
| Server | _TBD (official Classic or private)_ |
| Game resolution / mode | 1366×768 windowed — never moved or resized during a run |
| Class / skill | Magician (level ~13), Magic Claw. MP is the bottleneck — MP potions matter |
| Keybinds | attack: _TBD_, jump: _TBD_, move: arrow keys |
| First map | Victoria Road: The Tree That Grew II — vertical map of sagging rope bridges with a central rope; Slimes + Stumps; green background. Real frame: `data/labeled/tree-that-grew-2/` |

## How work is organised
You (the main agent) are the **orchestrator**. You own the plan, the shared contracts
and the integration. Specialist subagents in `.claude/agents/` each own one part of the
system and only edit files inside their area.

| Part | Subagent | Owns |
|---|---|---|
| Perception (capture, minimap, mobs, player, HP/MP, popups) | `perception-engineer` | `src/maplebot/perception/` |
| Map model (platforms, ropes, patrol zones) | `map-modeler` | `src/maplebot/mapmodel/`, `configs/maps/` |
| Decision logic (state machine) | `behavior-designer` | `src/maplebot/behavior/` |
| Input / actions | `controls-engineer` | `src/maplebot/controls/` |
| Watchdogs, kill switch, run logging | `safety-supervisor` | `src/maplebot/supervisor/` |
| Recorder, replay, datasets, metrics, tests | `test-harness-engineer` | `tools/`, `tests/`, `src/maplebot/replay/` |
| Launcher, setup wizard, control window | `app-engineer` | `src/maplebot/app/`, `start.bat`, `docs/user-guide.md` |
| Tick loop, shared types/settings, integration | main agent | `src/maplebot/runner.py`, `types.py`, `settings.py` |
| Post-run log analysis (read-only) | `run-analyst` | writes only `reports/` |
| Pre-merge review (read-only) | `code-reviewer` | nothing |

### Orchestration rules
1. **Contracts first.** `docs/architecture.md` defines the data passed between parts
   (`Frame`, `Perception`, `MapModel`, `Action`, `TickLog`). Only you change it. If a
   subagent needs a contract change, it stops and proposes it; you decide, update the
   doc, then re-brief affected agents.
2. **Brief subagents fully.** They start cold. Every brief includes: the goal, the
   relevant contract section, which files they may touch, the dataset/test to measure
   against, and what "done" means.
3. **One owner per file.** Never give two agents overlapping write areas in parallel.
   Parallelise only across different parts (e.g. perception + controls).
4. **Measured, not vibes.** A change is done when its tests pass and its metric
   (see `docs/roadmap.md`) holds or improves. Agents report numbers, not opinions.
5. **Review before merge.** Run `code-reviewer` on any change touching controls,
   supervisor, or contracts.
6. **Follow the roadmap phase order.** Don't build phase N+1 features before phase N's
   exit criteria are met.

## Hard rules (all agents)
- Never run anything against the live game. Only the user does that. Everything Claude
  runs uses recorded frames and the replay harness.
- Every code path that presses a key must guarantee release on exit, crash or pause.
- The bot must stop itself, not keep guessing, when it's unsure (lost player, death,
  popup, disconnect, low detection confidence).
- No anti-cheat evasion, memory reading, injection, or client modification.
- Keep `config` values in YAML under `configs/`, not hard-coded.

## Commands
- Tests: `pytest`
- Perception metrics on the dataset: `python -m tools.eval_perception` (to be built)
- Replay a recording: `python -m tools.replay data/recordings/<name>` (to be built)
