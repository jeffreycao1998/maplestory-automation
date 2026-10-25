"""
Replay a recording through the whole pipeline with fake input (no game, no keys pressed).

    python -m tools.replay data/recordings/henesys1 [--profile profile] [--fps 15]
    python -m tools.replay --synthetic 300            # closed loop on a generated world

perception -> supervisor -> behavior -> controls(FakeBackend), one tick per frame, the
frame's timestamp as ``now``. A PAUSE verdict releases keys and is logged; replay then
calls ``behavior.reset()`` / ``supervisor.reset()`` and carries on (``--stop-on-pause``
ends the run instead), so one bad stretch doesn't hide the rest of the recording. A STOP
verdict ends the run.

Prints per-state tick counts, action counts and verdicts; writes a RunLogger run log
(ticks.jsonl, anomaly frames, summary.json) to logs/replay_<name>/<timestamp>/.

Settings and templates come from the profile (``--profile``). For a synthetic run they
come from the generator. A recording made by tools.record has no calibration of its own,
so set up the profile with the app's setup wizard first.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional

import tools  # noqa: F401
from tools import REPO_ROOT
from tools._common import load_profile, rel


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.replay", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("recording", nargs="?", help="recording folder (with frames/) or a folder of PNGs")
    p.add_argument("--synthetic", type=int, default=None, metavar="N",
                   help="instead of a recording, run N ticks on a generated world (closed loop)")
    p.add_argument("--seed", type=int, default=0, help="synthetic world seed")
    p.add_argument("--profile", default=None, help="profile folder (default ./profile)")
    p.add_argument("--fps", type=float, default=None,
                   help="frame rate for timestamps (default: meta.json fps, else 15)")
    p.add_argument("--logs", default=None, help="logs root (default logs/)")
    p.add_argument("--stop-on-pause", action="store_true", help="end the run at the first PAUSE")
    p.add_argument("--max-frames", type=int, default=None, help="only replay the first N frames")
    p.add_argument("--quiet", action="store_true", help="don't print the action timeline")
    return p


def run_pipeline(capture, settings, mob_templates, player_template, logs_dir: Path,
                 stop_on_pause: bool = False, max_frames: Optional[int] = None,
                 on_decision=None, timeline: Optional[list] = None, item_templates=None, alert_templates=None) -> dict:
    """Drive the pipeline over ``capture`` until it ends. Returns the result summary."""
    from maplebot.behavior import Behavior
    from maplebot.controls import Controller, FakeBackend
    from maplebot.mapmodel import MapModel
    from maplebot.perception import Perceiver
    from maplebot.replay.folder_capture import EndOfRecording
    from maplebot.supervisor import RunLogger, Supervisor
    from maplebot.types import Action, Flag, VerdictKind

    perceiver = Perceiver(settings, list(mob_templates), player_template, item_templates, alert_templates, hud_sync=True)
    behavior = Behavior(settings, MapModel.from_settings(settings))
    supervisor = Supervisor(settings)
    # The frames' clock drives everything; key taps must not really sleep.
    backend = FakeBackend()
    controller = Controller(settings, backend, sleep=lambda s: None)
    logger = RunLogger(Path(logs_dir), settings)

    states: Counter = Counter()
    actions: Counter = Counter()
    verdicts: Counter = Counter()
    reasons: Counter = Counter()
    frames = 0
    stop_reason = "recording ended"
    first_t = last_t = None
    try:
        with controller:
            started = False
            while max_frames is None or frames < max_frames:
                try:
                    frame = capture.grab()
                except (EndOfRecording, StopIteration):
                    break
                frames += 1
                now = float(frame.t)
                first_t = now if first_t is None else first_t
                last_t = now
                if not started:
                    supervisor.start(now)
                    started = True
                p = perceiver.perceive(frame)
                v = supervisor.check(p, now)
                verdicts[v.kind.value] += 1
                if v.kind is not VerdictKind.OK:
                    controller.release_all()
                    reasons[f"{v.kind.value}: {v.reason}"] += 1
                    logger.log_tick(p, None, v)
                    logger.save_frame(frame, v.kind.value)
                    if timeline is not None:
                        timeline.append((now, v.kind.value.upper(), "", v.reason))
                    if v.kind is VerdictKind.STOP:
                        stop_reason = v.reason
                        break
                    if stop_on_pause:
                        stop_reason = f"paused: {v.reason}"
                        break
                    # carry on as if the user pressed Resume
                    behavior.reset()
                    supervisor.reset(now)
                    continue
                d = behavior.decide(p, now)
                controller.execute(d.action, now)
                supervisor.note_decision(d, now)
                logger.log_tick(p, d, v)
                if d.state == "STUCK" or Flag.LOW_CONFIDENCE in p.flags:
                    logger.save_frame(frame, "stuck" if d.state == "STUCK" else "lowconf")
                states[d.state] += 1
                actions[d.action.value] += 1
                if on_decision is not None:
                    on_decision(d)
                if timeline is not None and d.action is not Action.IDLE:
                    timeline.append((now, d.state, d.action.value, d.reason))
    finally:
        controller.release_all()
        perceiver.close()
        held = sorted(backend.held)
        result = {
            "frames": frames, "stop_reason": stop_reason,
            "state_ticks": dict(states), "action_counts": dict(actions),
            "verdict_ticks": dict(verdicts), "verdict_reasons": dict(reasons),
            "key_events": len(backend.events), "keys_held_at_end": held,
            "span_s": round((last_t - first_t), 3) if first_t is not None else 0.0,
        }
        logger.close({"replay": True, **result})
        result["run_dir"] = str(logger.run_dir)
    return result


def print_result(name: str, r: dict, timeline: Optional[list] = None) -> None:
    def table(title, counts):
        total = sum(counts.values()) or 1
        print(f"{title}:")
        if not counts:
            print("  (none)")
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<12} {v:>6}  {100 * v / total:5.1f}%")

    print(f"== replay {name}: {r['frames']} frames, {r['span_s']:.1f} s ==")
    table("states", r["state_ticks"])
    table("actions", r["action_counts"])
    table("verdicts", r["verdict_ticks"])
    if r["verdict_reasons"]:
        print("verdict reasons:")
        for k, v in r["verdict_reasons"].items():
            print(f"  {v:>5} x {k}")
    print(f"key events: {r['key_events']}   keys held at end: {r['keys_held_at_end'] or 'none'}")
    print(f"stop reason: {r['stop_reason']}")
    if timeline:
        print("timeline (non-idle actions, first 25):")
        for t, state, action, reason in timeline[:25]:
            print(f"  {t:8.2f}  {state:<8} {action:<11} {reason}")
    print(f"run log: {rel(Path(r['run_dir']))}")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logs_root = Path(args.logs) if args.logs else REPO_ROOT / "logs"
    timeline: Optional[list] = None if args.quiet else []

    if args.synthetic is not None:
        import tempfile
        from maplebot.replay import synthetic as S
        from maplebot.replay.folder_capture import SequenceCapture
        seq = S.SceneSequence(seed=args.seed)
        settings = seq.settings()
        fps = args.fps or settings.behavior.loop_hz
        cap = SequenceCapture(seq, fps=fps, max_frames=max(0, args.synthetic))
        name = f"synthetic{args.seed}"
        with tempfile.TemporaryDirectory(prefix="maplebot-replay-") as td:
            paths = S.write_templates(Path(td))
            r = run_pipeline(cap, settings, [p for p in paths if p.name.startswith("mob_")],
                             Path(td) / "player.png", logs_root / f"replay_{name}",
                             args.stop_on_pause, args.max_frames,
                             on_decision=lambda d: cap.on_action(d.action), timeline=timeline)
        print_result(name, r, timeline)
        print(f"sim: kills {seq.kills}, casts {seq.casts}, hits {seq.hits}, "
              f"map x {seq.map_x():.3f}")
        return 0

    if not args.recording:
        print("Give a recording folder, e.g. python -m tools.replay data/recordings/henesys1 "
              "(or --synthetic 300)")
        return 2
    from maplebot.replay.folder_capture import FolderCapture
    rec = Path(args.recording)
    try:
        cap = FolderCapture(rec, fps=args.fps)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    if len(cap) == 0:
        print(f"ERROR: no PNG frames in {rel(rec)} (or {rel(rec / 'frames')})")
        return 1
    profile = load_profile(args.profile)
    settings = profile.load()
    problems = settings.problems()
    if problems:
        print("WARNING: profile isn't fully set up: " + "; ".join(problems))
    mob_t = profile.mob_templates()
    if not mob_t:
        print(f"WARNING: no mob templates in {rel(profile.templates_dir)}; no mobs will be seen.")
    name = rec.name
    try:
        r = run_pipeline(cap, settings, mob_t, profile.player_template(),
                         logs_root / f"replay_{name}", args.stop_on_pause, args.max_frames,
                         timeline=timeline, item_templates=profile.item_templates(),
                         alert_templates=profile.alert_templates())
    except ValueError as e:          # e.g. invalid patrol bounds / key names
        print(f"ERROR: settings problem: {e}")
        return 1
    print_result(name, r, timeline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
