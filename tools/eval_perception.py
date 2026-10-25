"""
Perception metrics on labelled frames (or on generated synthetic scenes).

    python -m tools.eval_perception                       # data/labeled/ (synthetic if empty)
    python -m tools.eval_perception --data data/labeled/tree-that-grew-2
    python -m tools.eval_perception --synthetic 50        # 50 generated scenes, generator sprites

Labelled data is ``<folder>/<frame>.png`` + ``<frame>.json`` (format in data/README.md),
searched recursively. Templates, first found wins:
  ``--templates DIR``  >  ``<folder>/templates/``  >  the profile's templates
  (``--crop-templates`` instead cuts one template per mob kind out of the labels
  themselves: handy for a first look, but optimistic -- the eval then tests on its
  training crops).
Calibration (minimap / HP / MP boxes) comes from each label when it has them
(``minimap_box``, ``hp_bar_box``, ``mp_bar_box``), else from the profile; pass
``--profile-calibration`` to always use the profile.

Metrics (stable names; other agents compare them run to run):
  mob_recall, mob_precision, mob_fp_per_frame -- a detection matches a labelled mob if its
      centre is within half the labelled box (|dx| <= w/2 and |dy| <= h/2), one-to-one,
      nearest first
  mob_recall_visible -- recall over labelled mobs with "visible" >= 0.7 (synthetic labels
      carry the drawn visible fraction; hand labels don't, so this is n/a for them)
  minimap_x_err / minimap_y_err -- |pred - truth| as % of the minimap box (mean, max),
      over frames where both exist; minimap_missed = labelled dot not found
  player_screen_err_px -- Euclidean px (mean, max); "estimated" = no name-tag match
  hp_abs_err / mp_abs_err -- mean, max absolute fill error
  ms_per_frame -- mean and p95 wall time of Perceiver.perceive
A per-frame CSV goes to reports/eval_<timestamp>.csv. Exit code is 0 unless the
arguments are wrong.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import tools  # noqa: F401
from tools import REPO_ROOT
from tools._common import fmt, load_profile, rel, timestamp, write_json

# Phase 1 exit criteria (docs/roadmap.md)
TARGET_RECALL = 0.90
TARGET_FP_PER_FRAME = 0.10
TARGET_MINIMAP_X_ERR = 0.01
VISIBLE_MIN = 0.7      # "mostly visible" mobs for mob_recall_visible (synthetic labels only)

CSV_FIELDS = [
    "dataset", "frame", "n_truth", "n_pred", "tp", "fp", "fn",
    "truth_map_x", "truth_map_y", "pred_map_x", "pred_map_y", "map_x_err_pct", "map_y_err_pct",
    "truth_screen_x", "truth_screen_y", "pred_screen_x", "pred_screen_y", "screen_err_px",
    "screen_estimated", "truth_hp", "pred_hp", "hp_err", "truth_mp", "pred_mp", "mp_err",
    "confidence", "flags", "ms", "missed_boxes", "fp_boxes",
]


# ======================================================================================
# matching + metrics (pure; tested directly)
# ======================================================================================
def match_mobs(truth_boxes: Sequence[Sequence[int]], preds: Sequence[Tuple[int, int]]
               ) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
    """One-to-one greedy match, nearest pairs first.

    ``truth_boxes``: [x, y, w, h]; ``preds``: (cx, cy) centres.
    A pair is eligible if the pred centre is within half the truth box on both axes.
    Returns (matches [(truth_i, pred_j)], unmatched truth indices, unmatched pred indices).
    """
    pairs = []
    for i, (x, y, w, h) in enumerate(truth_boxes):
        tcx, tcy = x + w / 2.0, y + h / 2.0
        for j, (px, py) in enumerate(preds):
            dx, dy = abs(px - tcx), abs(py - tcy)
            if dx <= w / 2.0 and dy <= h / 2.0:
                pairs.append((math.hypot(dx, dy), i, j))
    pairs.sort()
    used_t, used_p, matches = set(), set(), []
    for _, i, j in pairs:
        if i in used_t or j in used_p:
            continue
        used_t.add(i)
        used_p.add(j)
        matches.append((i, j))
    fn = [i for i in range(len(truth_boxes)) if i not in used_t]
    fp = [j for j in range(len(preds)) if j not in used_p]
    return matches, fn, fp


def _stats(xs: List[float]) -> Tuple[Optional[float], Optional[float]]:
    return (sum(xs) / len(xs), max(xs)) if xs else (None, None)


def _p95(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(math.ceil(0.95 * len(s))) - 1)]


@dataclass
class Totals:
    frames: int = 0
    frames_with_mob_eval: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    vis_truth: int = 0          # labelled mobs with "visible" >= VISIBLE_MIN (synthetic)
    vis_tp: int = 0
    map_x: List[float] = field(default_factory=list)
    map_y: List[float] = field(default_factory=list)
    minimap_labelled: int = 0
    minimap_missed: int = 0
    minimap_false: int = 0
    screen: List[float] = field(default_factory=list)
    screen_estimated: int = 0
    hp: List[float] = field(default_factory=list)
    mp: List[float] = field(default_factory=list)
    hp_unread: int = 0
    mp_unread: int = 0
    ms: List[float] = field(default_factory=list)

    def summary(self) -> dict:
        n_truth = self.tp + self.fn
        n_pred = self.tp + self.fp
        mob_eval = self.frames_with_mob_eval > 0
        mx, mxx = _stats(self.map_x)
        my, myx = _stats(self.map_y)
        se, sex = _stats(self.screen)
        he, hex_ = _stats(self.hp)
        me, mex = _stats(self.mp)
        ms, _ = _stats(self.ms)
        return {
            "frames": self.frames,
            "mob_frames": self.frames_with_mob_eval,
            "mob_truth": n_truth, "mob_pred": n_pred, "mob_tp": self.tp,
            "mob_recall": (self.tp / n_truth) if (mob_eval and n_truth) else None,
            "mob_precision": (self.tp / n_pred) if (mob_eval and n_pred) else (1.0 if mob_eval and n_truth == 0 else None),
            "mob_fp_per_frame": (self.fp / self.frames_with_mob_eval) if mob_eval else None,
            "mob_recall_visible": (self.vis_tp / self.vis_truth) if (mob_eval and self.vis_truth) else None,
            "mob_visible_truth": self.vis_truth,
            "minimap_frames": len(self.map_x), "minimap_labelled": self.minimap_labelled,
            "minimap_missed": self.minimap_missed, "minimap_false": self.minimap_false,
            "minimap_x_err_mean": mx, "minimap_x_err_max": mxx,
            "minimap_y_err_mean": my, "minimap_y_err_max": myx,
            "player_screen_frames": len(self.screen), "player_screen_estimated": self.screen_estimated,
            "player_screen_err_px_mean": se, "player_screen_err_px_max": sex,
            "hp_abs_err_mean": he, "hp_abs_err_max": hex_, "hp_unread": self.hp_unread,
            "mp_abs_err_mean": me, "mp_abs_err_max": mex, "mp_unread": self.mp_unread,
            "ms_per_frame_mean": ms, "ms_per_frame_p95": _p95(self.ms),
        }


def score_frame(truth: dict, p, ms: float, mob_eval: bool, totals: Totals,
                dataset: str = "", frame: str = "") -> dict:
    """Compare one Perception with its label, add to ``totals``, return the CSV row."""
    row: Dict[str, object] = {k: "" for k in CSV_FIELDS}
    row.update(dataset=dataset, frame=frame, ms=round(ms, 2),
               confidence=round(float(p.confidence), 3),
               flags="|".join(sorted(str(getattr(f, "value", f)) for f in p.flags)))
    totals.frames += 1
    totals.ms.append(ms)

    boxes = [m["box"] for m in (truth.get("mobs") or []) if m and m.get("box")]
    preds = [(m.x, m.y) for m in p.mobs]
    row.update(n_truth=len(boxes), n_pred=len(preds))
    if mob_eval:
        matches, fn, fp = match_mobs(boxes, preds)
        totals.frames_with_mob_eval += 1
        totals.tp += len(matches)
        totals.fn += len(fn)
        totals.fp += len(fp)
        matched = {i for i, _ in matches}
        mobs_l = [m for m in (truth.get("mobs") or []) if m and m.get("box")]
        for i, m in enumerate(mobs_l):
            if m.get("visible") is not None and float(m["visible"]) >= VISIBLE_MIN:
                totals.vis_truth += 1
                totals.vis_tp += int(i in matched)
        row.update(tp=len(matches), fp=len(fp), fn=len(fn),
                   missed_boxes=json.dumps([list(boxes[i]) for i in fn]),
                   fp_boxes=json.dumps([[m.x - m.w // 2, m.y - m.h // 2, m.w, m.h]
                                        for j, m in enumerate(p.mobs) if j in fp]))

    tm = truth.get("player_map")
    predicted_map=p.player_map
    # Historical labels use visible-viewport fractions. Keep their metric basis
    # explicit when the running detector uses registered full-map coordinates.
    view=getattr(p,'minimap_view',None)
    box=truth.get('minimap_box')
    if predicted_map is not None and view is not None and box and not truth.get('map_reference'):
        dx,dy,bx,by=view
        predicted_map=((dx+predicted_map[0]*bx)/box[2],(dy+predicted_map[1]*by)/box[3])
    if tm is not None:
        totals.minimap_labelled += 1
        row.update(truth_map_x=tm[0], truth_map_y=tm[1])
    if p.player_map is not None:
        row.update(pred_map_x=round(predicted_map[0], 5), pred_map_y=round(predicted_map[1], 5))
    if tm is not None and p.player_map is not None:
        ex, ey = abs(predicted_map[0] - tm[0]), abs(predicted_map[1] - tm[1])
        totals.map_x.append(ex)
        totals.map_y.append(ey)
        row.update(map_x_err_pct=round(100 * ex, 3), map_y_err_pct=round(100 * ey, 3))
    elif tm is not None:
        totals.minimap_missed += 1
    elif p.player_map is not None and "player_map" in truth:
        totals.minimap_false += 1

    ts = truth.get("player_screen")
    if ts is not None:
        row.update(truth_screen_x=ts[0], truth_screen_y=ts[1])
    if p.player_screen is not None:
        row.update(pred_screen_x=p.player_screen[0], pred_screen_y=p.player_screen[1],
                   screen_estimated=int(bool(p.player_screen_estimated)))
    if ts is not None and p.player_screen is not None:
        e = math.hypot(p.player_screen[0] - ts[0], p.player_screen[1] - ts[1])
        totals.screen.append(e)
        totals.screen_estimated += int(bool(p.player_screen_estimated))
        row["screen_err_px"] = round(e, 1)

    for key, lst, unread in (("hp", totals.hp, "hp_unread"), ("mp", totals.mp, "mp_unread")):
        tv, pv = truth.get(key), getattr(p, key)
        if tv is not None:
            row[f"truth_{key}"] = tv
            if pv is None:
                setattr(totals, unread, getattr(totals, unread) + 1)
        if pv is not None:
            row[f"pred_{key}"] = round(pv, 4)
        if tv is not None and pv is not None:
            e = abs(pv - tv)
            lst.append(e)
            row[f"{key}_err"] = round(e, 4)
    return row


# ======================================================================================
# datasets
# ======================================================================================
@dataclass
class LabelledFrame:
    image_path: Path
    label: dict


def find_labelled(root: Path) -> Dict[Path, List[LabelledFrame]]:
    """{folder: [frames]} for every <name>.json with a sibling <name>.png under root."""
    root = Path(root)
    out: Dict[Path, List[LabelledFrame]] = {}
    if not root.exists():
        return out
    for js in sorted(root.rglob("*.json")):
        if "templates" in js.parts or js.name in ("meta.json", "summary.json"):
            continue
        png = js.with_suffix(".png")
        if not png.is_file():
            continue
        try:
            label = json.loads(js.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  skipping {rel(js)}: bad JSON ({e})")
            continue
        if not isinstance(label, dict):
            continue
        out.setdefault(js.parent, []).append(LabelledFrame(png, label))
    return out


def crop_templates(frames: List[LabelledFrame], out_dir: Path) -> List[Path]:
    """One template per mob kind: the first fully-visible labelled box of that kind."""
    from maplebot.replay.folder_capture import read_image
    from tools._common import write_png
    out_dir.mkdir(parents=True, exist_ok=True)
    seen: Dict[str, Path] = {}
    for f in frames:
        img = None
        for m in f.label.get("mobs") or []:
            kind = str(m.get("kind") or "mob")
            if kind in seen or "note" in m:      # notes mark partial / covered mobs
                continue
            img = read_image(f.image_path) if img is None else img
            if img is None:
                break
            x, y, w, h = (int(v) for v in m["box"])
            if x < 0 or y < 0 or x + w > img.shape[1] or y + h > img.shape[0] or w < 8 or h < 8:
                continue
            p = out_dir / f"mob_{kind}.png"
            write_png(p, img[y:y + h, x:x + w])
            seen[kind] = p
    return sorted(seen.values())


def _settings_for_label(base, label: dict, use_label_boxes: bool):
    from maplebot.settings import Settings
    s = Settings.from_dict(base.to_dict())
    if use_label_boxes:
        for key, attr in (("minimap_box", "minimap_box"), ("hp_bar_box", "hp_bar_box"),
                          ("mp_bar_box", "mp_bar_box")):
            if label.get(key) is not None:
                setattr(s.calibration, attr, [int(v) for v in label[key]])
    return s


# ======================================================================================
# runs
# ======================================================================================
def eval_synthetic(n: int, seed: int = 0, size=(1024, 768), base_settings=None,
                   rows: Optional[list] = None) -> Totals:
    from maplebot.perception import Perceiver
    from maplebot.replay import synthetic as S
    from maplebot.types import Frame

    totals = Totals()
    with tempfile.TemporaryDirectory(prefix="maplebot-eval-") as td:
        paths = S.write_templates(Path(td), mob_kinds=(0,))
        mob_t = [p for p in paths if p.name.startswith("mob_")]
        player_t = Path(td) / "player.png"
        perceiver = None
        for i in range(n):
            img, truth = S.random_scene(seed + i, size=size)
            s = S.settings_for_scene(truth, base_settings)
            if perceiver is None:
                perceiver = Perceiver(s, mob_t, player_t)
            else:
                perceiver.update_settings(s)
            frame = Frame(image=img, t=float(i), window=(0, 0, size[0], size[1]))
            t0 = time.perf_counter()
            p = perceiver.perceive(frame)
            ms = (time.perf_counter() - t0) * 1000
            row = score_frame(truth, p, ms, True, totals, "synthetic", f"seed{seed + i}")
            if rows is not None:
                rows.append(row)
    return totals


def eval_labelled(root: Path, profile, templates: Optional[Path], crop: bool,
                  use_label_boxes: bool, rows: Optional[list] = None,
                  base_settings=None) -> Tuple[Totals, List[str]]:
    from maplebot.perception import Perceiver
    from maplebot.replay.folder_capture import read_image
    from maplebot.types import Frame

    notes: List[str] = []
    totals = Totals()
    base = base_settings or profile.load()
    groups = find_labelled(root)
    for folder, frames in groups.items():
        tmp = None
        if templates is not None:
            mob_t = sorted(Path(templates).glob("mob_*.png"))
            pt = Path(templates) / "player.png"
            src = rel(templates)
        elif crop:
            tmp = tempfile.TemporaryDirectory(prefix="maplebot-crops-")
            mob_t = crop_templates(frames, Path(tmp.name))
            pt = None
            src = "cropped from labels (optimistic)"
        elif (folder / "templates").is_dir():
            mob_t = sorted((folder / "templates").glob("mob_*.png"))
            pt = folder / "templates" / "player.png"
            src = rel(folder / "templates")
        else:
            mob_t = profile.mob_templates()
            pt = profile.player_template()
            src = f"profile {rel(profile.templates_dir)}"
        pt = pt if pt is not None and Path(pt).is_file() else None
        mob_eval = bool(mob_t)
        notes.append(f"{rel(folder)}: {len(frames)} frames, {len(mob_t)} mob templates "
                     f"({src}), player template: {'yes' if pt else 'no'}")
        if not mob_eval:
            notes.append(f"  no mob templates -> mob metrics skipped for {rel(folder)} "
                         "(save templates in the setup wizard, add <folder>/templates/, "
                         "or pass --crop-templates)")
        perceiver = None
        for lf in frames:
            img = read_image(lf.image_path)
            if img is None:
                notes.append(f"  unreadable image {rel(lf.image_path)}")
                continue
            s = _settings_for_label(base, lf.label, use_label_boxes)
            if perceiver is None:
                perceiver = Perceiver(s, mob_t, pt, map_reference=profile.map_reference_descriptor(s))
            else:
                perceiver.update_settings(s)
            frame = Frame(image=img, t=0.0, window=(0, 0, img.shape[1], img.shape[0]))
            t0 = time.perf_counter()
            p = perceiver.perceive(frame)
            ms = (time.perf_counter() - t0) * 1000
            row = score_frame(lf.label, p, ms, mob_eval, totals, rel(folder), lf.image_path.name)
            if rows is not None:
                rows.append(row)
        if tmp is not None:
            tmp.cleanup()
    return totals, notes


# ======================================================================================
# output
# ======================================================================================
def _pct(x: Optional[float], nd: int = 2) -> str:
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def _check(ok: Optional[bool]) -> str:
    return "  n/a" if ok is None else ("  PASS" if ok else "  FAIL")


def format_summary(title: str, s: dict) -> str:
    rec, fp, mx = s["mob_recall"], s["mob_fp_per_frame"], s["minimap_x_err_mean"]
    lines = [
        f"== {title}: {s['frames']} frames ==",
        f"mobs          recall {_pct(rec, 1):>7}   precision {_pct(s['mob_precision'], 1):>7}   "
        f"FP/frame {fmt(fp, 3):>6}   ({s['mob_tp']}/{s['mob_truth']} found, "
        f"{s['mob_pred']} detections, {s['mob_frames']} frames with templates)",
        *([f"              recall on mobs >= {int(VISIBLE_MIN * 100)}% visible "
           f"{_pct(s['mob_recall_visible'], 1)} ({s['mob_visible_truth']} mobs; the rest are "
           "occluded by the player / other mobs)"] if s.get("mob_visible_truth") else []),
        f"minimap       x err mean {_pct(mx, 3)} max {_pct(s['minimap_x_err_max'], 3)}   "
        f"y err mean {_pct(s['minimap_y_err_mean'], 3)} max {_pct(s['minimap_y_err_max'], 3)}   "
        f"(missed {s['minimap_missed']}/{s['minimap_labelled']}, false {s['minimap_false']})",
        f"player screen err mean {fmt(s['player_screen_err_px_mean'], 1, ' px')} "
        f"max {fmt(s['player_screen_err_px_max'], 1, ' px')}   "
        f"(estimated {s['player_screen_estimated']}/{s['player_screen_frames']})",
        f"HP/MP         HP err mean {fmt(s['hp_abs_err_mean'], 3)} max {fmt(s['hp_abs_err_max'], 3)}"
        f"   MP err mean {fmt(s['mp_abs_err_mean'], 3)} max {fmt(s['mp_abs_err_max'], 3)}"
        f"   (unread HP {s['hp_unread']}, MP {s['mp_unread']})",
        f"speed         {fmt(s['ms_per_frame_mean'], 1, ' ms')}/frame mean, "
        f"p95 {fmt(s['ms_per_frame_p95'], 1, ' ms')}",
        "phase-1 exit  recall>=90%" + _check(None if rec is None else rec >= TARGET_RECALL)
        + "   FP/frame<=0.1" + _check(None if fp is None else fp <= TARGET_FP_PER_FRAME)
        + "   minimap x<=1%" + _check(None if mx is None else (mx <= TARGET_MINIMAP_X_ERR
                                                         and s["minimap_missed"] == 0)),
    ]
    return "\n".join(lines)


def write_csv(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.eval_perception", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default=str(REPO_ROOT / "data" / "labeled"),
                   help="labelled folder, searched recursively (default data/labeled)")
    p.add_argument("--synthetic", type=int, default=None, metavar="N",
                   help="evaluate N generated scenes instead of labelled data")
    p.add_argument("--seed", type=int, default=0, help="first synthetic seed (default 0)")
    p.add_argument("--size", default="1024x768", help="synthetic frame size WxH")
    p.add_argument("--templates", default=None, help="folder with mob_*.png / player.png")
    p.add_argument("--crop-templates", action="store_true",
                   help="cut templates out of the labelled mobs (optimistic)")
    p.add_argument("--profile", default=None, help="profile folder (default ./profile)")
    p.add_argument("--profile-calibration", action="store_true",
                   help="use the profile's minimap/HP/MP boxes even if labels have them")
    p.add_argument("--reports", default=str(REPO_ROOT / "reports"),
                   help="where to write eval_<timestamp>.csv (default reports/)")
    p.add_argument("--json", default=None, help="also write the summary as JSON here")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sw, sh = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        print("--size must look like 1024x768")
        return 2
    profile = load_profile(args.profile)
    rows: List[dict] = []
    summaries = {}

    labelled = find_labelled(Path(args.data)) if args.synthetic is None else {}
    if args.synthetic is None and not labelled:
        print(f"No labelled frames under {rel(Path(args.data))} -> using 20 synthetic scenes.")
        args.synthetic = 20

    if args.synthetic is not None:
        n = max(0, int(args.synthetic))
        totals = eval_synthetic(n, args.seed, (sw, sh), rows=rows)
        summaries["synthetic"] = totals.summary()
        print(format_summary(f"synthetic (seeds {args.seed}..{args.seed + n - 1}, {sw}x{sh})",
                             summaries["synthetic"]))
    else:
        totals, notes = eval_labelled(Path(args.data), profile,
                                      Path(args.templates) if args.templates else None,
                                      args.crop_templates, not args.profile_calibration, rows=rows)
        for line in notes:
            print(line)
        summaries["labelled"] = totals.summary()
        print(format_summary(f"labelled {rel(Path(args.data))}", summaries["labelled"]))

    csv_path = Path(args.reports) / f"eval_{timestamp()}.csv"
    write_csv(csv_path, rows)
    print(f"per-frame CSV: {rel(csv_path)}")
    if args.json:
        write_json(Path(args.json), summaries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
