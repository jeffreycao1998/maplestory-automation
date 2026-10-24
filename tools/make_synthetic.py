"""
Write a small synthetic labelled dataset (a demo for tools.eval_perception).

    python -m tools.make_synthetic                      # 20 frames -> data/labeled/synthetic/
    python -m tools.make_synthetic --n 10 --out /tmp/syn --seed 100

Writes ``<out>/000001.png`` + ``000001.json`` (data/README.md label format, plus
``hp_bar_box``/``mp_bar_box`` so the eval can calibrate from the labels) and
``<out>/templates/mob_synthetic-0.png`` + ``player.png``. Evaluate with:

    python -m tools.eval_perception --data <out>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import tools  # noqa: F401
from tools import REPO_ROOT
from tools._common import rel, write_json, write_png

MAX_DEFAULT_FRAMES = 20
LABEL_KEYS = ("map", "minimap_box", "player_map", "player_screen", "mobs", "hp", "mp", "flags",
              "hp_bar_box", "mp_bar_box", "seed")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.make_synthetic", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=MAX_DEFAULT_FRAMES, help="number of frames (default 20)")
    p.add_argument("--out", default=str(REPO_ROOT / "data" / "labeled" / "synthetic"))
    p.add_argument("--seed", type=int, default=0, help="first seed (default 0)")
    p.add_argument("--size", default="1024x768", help="frame size WxH (default 1024x768)")
    return p


def make_dataset(out: Path, n: int, seed: int = 0, size=(1024, 768)) -> List[Path]:
    from maplebot.replay import synthetic as S
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    S.write_templates(out / "templates", mob_kinds=(0,))
    written = []
    for i in range(n):
        img, truth = S.random_scene(seed + i, size=size)
        label = {k: truth.get(k) for k in LABEL_KEYS}
        label["mobs"] = [{"box": m["box"], "kind": m["kind"], "visible": m["visible"]}
                         for m in truth["mobs"]]
        stem = f"{i + 1:06d}"
        write_png(out / f"{stem}.png", img, compression=9)
        write_json(out / f"{stem}.json", label)
        written.append(out / f"{stem}.png")
    return written


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        size = tuple(int(v) for v in args.size.lower().split("x"))
        assert len(size) == 2
    except Exception:
        print("--size must look like 1024x768")
        return 2
    if args.n < 0:
        print("--n must be >= 0")
        return 2
    files = make_dataset(Path(args.out), args.n, args.seed, size)  # type: ignore[arg-type]
    total = sum(f.stat().st_size for f in files)
    print(f"Wrote {len(files)} frames ({total / 1e6:.1f} MB) + templates to {rel(Path(args.out))}")
    print(f"Evaluate with: python -m tools.eval_perception --data {rel(Path(args.out))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
