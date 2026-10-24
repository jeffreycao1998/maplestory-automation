"""Developer tools: recorder, replay runner, perception eval, synthetic data.

Run from the repo root as ``python -m tools.<name> --help``. Owner: test-harness-engineer.
Importing this package puts ``src/`` on ``sys.path`` so ``maplebot`` imports without an
editable install.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    try:
        import maplebot  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(_SRC))
