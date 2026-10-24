# Roadmap

Each phase has exit criteria. Don't start the next phase until they're met.

## Phase 0 — Infrastructure
Lead: `test-harness-engineer` (main agent finalises contracts)
- [ ] Fill in the Environment table in `CLAUDE.md`
- [ ] Recorder: user plays manually, saves frames (+ optional keypresses) to `data/recordings/`
- [ ] Label format + labelling helper (`data/README.md`)
- [ ] 50–200 labelled frames from the first map in `data/labeled/`
- [ ] `tools.eval_perception` prints metrics; `tools.replay` runs the pipeline on a recording
- **Exit:** `pytest` runs green on an empty pipeline; eval tool runs on the dataset

## Phase 1 — Perception on one map
Lead: `perception-engineer`
- [ ] Minimap player position
- [ ] Mob detection
- [ ] Player on-screen position
- [ ] HP/MP bars, death/popup flags
- **Exit:** mob recall ≥ 90%, ≤ 1 false positive per 10 frames, minimap x error ≤ 1%

## Phase 2 — One-platform loop
Leads: `map-modeler`, `behavior-designer`, `controls-engineer`, `safety-supervisor`
- [ ] Map config for the first map
- [ ] PATROL / ENGAGE state machine, tested on replays
- [ ] Controls with guaranteed key release
- [ ] Kill switch, pause, watchdogs, tick logging
- **Exit:** replay tests pass; user runs a supervised 15-minute session without intervention

## Phase 3 — Robustness
Leads: `run-analyst` drives; specialists fix
- [ ] Potions / RECOVER state
- [ ] Death, disconnect, popup → stop cleanly
- [ ] Fix top failure modes from run reports
- **Exit:** 60-minute supervised session; all stops were intentional

## Phase 4 — Expansion
- [ ] Multiple platforms, ropes
- [ ] Second map
- [ ] More skills / buffs
