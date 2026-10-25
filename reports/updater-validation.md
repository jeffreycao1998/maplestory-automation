# App updater validation — 2026-10-24

- Full offline suite: **829 passed** in 53.75 seconds on macOS / Python 3.12.
- New update coverage: ZIP install with user data preserved, successive revision and
  obsolete-file handling, partial-write rollback, pip failure and launcher repair,
  local-edit protection, corrupted download refusal, safe archive extraction, size
  limits, redirect restrictions, separate-process locking, standalone installer and
  restart handoff, busy GUI guards and failed-helper handling.
- Launcher simulator: dependency installation is excluded while the app or installer
  holds the installation lock, preserving existing startup/elevation/error paths.
- Real public GitHub API smoke check: branch resolved to `38dd6b426f27c7671420f2296f99822e8b824b89`;
  pinned ZIP downloaded successfully (1,472,580 bytes, 145 entries).
- No game process was started or controlled. Native Windows GUI/console restart,
  Windows file locks and UAC still need a Windows smoke test. Backups cover app files;
  pip package changes and power-loss recovery have the limits in `docs/updating.md`.
