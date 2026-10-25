"""`python -m maplebot` opens the app, excluding concurrent code installation."""
from pathlib import Path

from maplebot.app.updater import UpdateError, installation_lock


def run():
    root = Path(__file__).resolve().parents[2]
    try:
        with installation_lock(root):
            # Don't import the rest of the app until replacement is excluded.
            from maplebot.app.gui import main
            main()
    except UpdateError as e:
        import sys
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(e), "MapleBot", 0x40)
        else:
            print(str(e), file=sys.stderr)
        raise SystemExit(3) from e

if __name__ == "__main__":
    run()
