# Updating MapleBot

For a downloaded ZIP installation:

1. Stop the bot and save/close Settings.
2. Click **Check for updates**. If code is available, confirm **Update MapleBot?**
3. Wait for download verification and restart. A console shows installation progress.

There is no Git installation, GitHub browsing or manual ZIP extraction after the first
installation containing the update button. A push is available through the button;
maintainers do not have to publish a separate GitHub Release. The app checks the branch
in `configs/updater.yaml`, currently `main`. Changes pushed only to a feature branch
do not appear as app updates until they reach `main`.

The first update of a ZIP copy records its installed commit. Before that, the dialog
says **original ZIP** and may reinstall the same code once. Subsequent checks compare
the installed revision with GitHub. Network errors leave the current app open.
This version supports public repositories; it never asks for or stores a GitHub token.
Developer Git checkouts use `git pull --ff-only` instead; ZIP replacement refuses them.

## What gets replaced

Only app-owned source, tools, tests, docs, shipped configs and selected root files are
updated. `profile/`, `logs/`, all `data/`, `.venv/` and `configs/local*` stay in place.
Normal dependency installation can update packages inside the existing environment.
Local sprite packs remain local; code updates do not download a new asset bank.

Code backups are stored in `.updates/backups/`. `.maplebot-update.json` records the
commit and hashes of managed files. Later updates remove obsolete files only when
the prior manifest owns them. Files of unknown ownership in an original ZIP are kept.
Editing/deleting a previously managed app file blocks an update so changes are not
silently discarded. User settings belong under `profile/` or `configs/local*`.

## Failure and recovery

The app downloads a commit-pinned GitHub ZIP over HTTPS, rejects unsafe paths, links,
case-colliding names and oversized archives, compiles Python sources, and verifies
staged file hashes before writing installed code. The separate helper waits for the
parent process to exit, takes the same OS lock as app startup and dependency installation,
and backs up every affected file before replacement. No updates run alongside the bot.

File/dependency installation errors trigger restoration of previous app files. A pip
failure can leave changed packages in the environment; the launcher stamp is removed
so `start.bat` reinstalls the old project's requirements. Package versions and live GUI
startup are not transactionally rolled back. A successful install does not prove the
gameplay changes work on your client.

An OS crash or power loss can interrupt replacement. Keep the backup, close MapleBot,
and restore it from a terminal in the app folder (replace the backup name):

```bat
".venv\Scripts\python.exe" -m maplebot.app.updater --restore "." ".updates\backups\20261024-120000-example"
```

Then open `start.bat`. The helper's log is `.updates/update.log`; downloaded/staged
files are under `.updates/pending-*`. Backups are retained until you remove them.
Do not remove this folder during an update. If a file cannot be restored, the error
identifies the backup; it contains `files/` and `journal.json` for manual recovery.

The downloader uses GitHub's [commit API](https://docs.github.com/en/rest/commits/commits)
and [source archive API](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip).
Authentication comes from HTTPS to GitHub, not a separate signed-release system.
