"""Small helpers shared by the tools (not a public API)."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from tools import REPO_ROOT


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def write_png(path: Path, image, compression: int = 3) -> None:
    """cv2.imwrite that also works with non-ASCII Windows paths."""
    import cv2
    ok, buf = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, int(compression)])
    if not ok:
        raise IOError(f"could not encode {path}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))


def load_profile(profile_dir: Optional[str]):
    from maplebot.settings import Profile
    return Profile(Path(profile_dir)) if profile_dir else Profile.default()


def fmt_pct(x: Optional[float], nd: int = 1) -> str:
    return "n/a" if x is None else f"{100.0 * x:.{nd}f}%"


def fmt(x: Optional[float], nd: int = 2, unit: str = "") -> str:
    return "n/a" if x is None else f"{x:.{nd}f}{unit}"


def rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)
