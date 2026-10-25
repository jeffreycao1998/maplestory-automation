"""
Main window: Start / Pause / Stop, live status, optional preview, and the buttons that
open the setup wizard, settings and logs folder.

The bot runs in a daemon worker thread (``BotRunner.run``). Its callbacks only put
data into a queue / latest-preview slot; the Tk thread polls them every 100 ms. Tk is
never touched from the worker thread.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

from maplebot.settings import Profile, Settings, REPO_ROOT

from . import logic

log = logging.getLogger("maplebot.app")

POLL_MS = 100
PREVIEW_W = 480


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(logs_dir: Path) -> Path:
    """Log to logs/app.log. If start.bat already redirected stderr there, use that stream
    (two writers on one file would garble it); otherwise open the file ourselves."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "app.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redirected = sys.stderr is not None and not _isatty(sys.stderr)
    handler: logging.Handler
    if redirected:
        handler = logging.StreamHandler(sys.stderr)
    else:
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2,
                                                       encoding="utf-8")
        if sys.stderr is not None:
            console = logging.StreamHandler(sys.stderr)
            console.setFormatter(fmt)
            root.addHandler(console)
    handler.setFormatter(fmt)
    root.addHandler(handler)
    return path


def _isatty(stream) -> bool:
    try:
        return stream.isatty()
    except Exception:
        return False


def is_admin() -> Optional[bool]:
    """True/False on Windows, None elsewhere."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))          # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root, profile: Profile, settings: Settings):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.root = root
        self.profile = profile
        self.settings = settings
        self.runner = None
        self.thread: Optional[threading.Thread] = None
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self._preview_lock = threading.Lock()
        self._preview_img = None
        self._preview_photo = None
        self._closing = False
        self._updating = False
        self._preview_on = False        # plain mirror of preview_var, safe to read from the worker
        self.wizard = None
        self.settings_win = None
        self.activity_win = None
        from maplebot.diagnostics import ActivityJournal
        self.activity = ActivityJournal()
        self.activity.append('Ready. Click Start for game input, or Observe only to inspect decisions without keys.')

        root.title("MapleBot")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        style = ttk.Style(root)
        try:
            style.configure("Big.TButton", font=("Segoe UI", 13, "bold"), padding=(10, 10))
            style.configure("Mid.TButton", font=("Segoe UI", 11), padding=(8, 8))
            style.configure("Countdown.TLabel", font=("Segoe UI", 15, "bold"), foreground="#b06000")
            style.configure("Problem.TLabel", foreground="#c00000")
        except tk.TclError:
            pass

        outer = ttk.Frame(root, padding=10)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")
        self.ontop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Keep on top when stopped", variable=self.ontop_var,
                        command=self._apply_ontop).pack(side="right")
        self.admin_lbl = ttk.Label(top, style="Problem.TLabel", wraplength=300, justify="left")
        self.admin_lbl.pack(side="left")
        if is_admin() is False:
            self.admin_lbl.config(text="Not running as admin: the game may ignore the bot's keys. "
                                       "Close this and double-click start.bat.")

        # ---- map picker: one saved setup per map
        mrow = ttk.Frame(outer)
        mrow.pack(fill="x", pady=(8, 0))
        ttk.Label(mrow, text="Map:").pack(side="left")
        self.map_var = tk.StringVar()
        self.map_combo = ttk.Combobox(mrow, textvariable=self.map_var, state="readonly", width=28)
        self.map_combo.pack(side="left", padx=(6, 6))
        self.map_combo.bind("<<ComboboxSelected>>", lambda _e: self._map_selected())
        self.new_map_btn = ttk.Button(mrow, text="New map…", command=self.new_map)
        self.new_map_btn.pack(side="left")
        self.rename_map_btn = ttk.Button(mrow, text="Rename…", command=self.rename_map)
        self.rename_map_btn.pack(side="left", padx=3)
        self.delete_map_btn = ttk.Button(mrow, text="Delete", command=self.delete_map)
        self.delete_map_btn.pack(side="left")
        self._map_choices = []

        self.start_btn = ttk.Button(outer, text=logic.start_button_text(settings),
                                    style="Big.TButton", command=self.start)
        self.start_btn.pack(fill="x", pady=(8, 4))
        self.observe_btn = ttk.Button(outer,text="Observe only — show decisions without keys",
                                      command=lambda:self.start(observe_only=True))
        self.observe_btn.pack(fill="x",pady=(0,4))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        self.pause_btn = ttk.Button(row, text="Pause", style="Mid.TButton", command=self.pause_resume)
        self.pause_btn.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.stop_btn = ttk.Button(row, text="Stop", style="Mid.TButton", command=self.stop)
        self.stop_btn.pack(side="left", fill="x", expand=True, padx=(3, 0))

        self.countdown_lbl = ttk.Label(outer, style="Countdown.TLabel")
        self.countdown_lbl.pack(pady=(6, 0))
        # Persistent while the runner reports hotkeys_ok=False (packed only then).
        self.hotkey_badge = tk.Label(outer, text="", background="#c00000", foreground="white",
                                     font=("Segoe UI", 10, "bold"), padx=8, pady=4)
        self._badge_shown = False
        self.problems_lbl = ttk.Label(outer, style="Problem.TLabel", wraplength=PREVIEW_W, justify="left")
        self.problems_lbl.pack(fill="x")

        grid = ttk.LabelFrame(outer, text="Status", padding=8)
        grid.pack(fill="x", pady=6)
        self.vals = {}
        fields = [("state", "State"), ("action", "Doing"), ("mobs", "Mobs seen"),
                  ("map_x", "Map position"), ("hp", "HP"), ("mp", "MP"),
                  ("potions", "Potions"), ("wallet_delta", "Wallet Δ"), ("net_hour", "Net est./hr"),
                  ("attacks", "Attacks"), ("runtime", "Running for"), ("fps", "Checks/sec")]
        for i, (key, label) in enumerate(fields):
            r, c = divmod(i, 3)
            ttk.Label(grid, text=label + ":", foreground="#555555").grid(row=r * 2, column=c, sticky="w", padx=6)
            v = ttk.Label(grid, text="--", font=("Segoe UI", 11, "bold"), width=12)
            v.grid(row=r * 2 + 1, column=c, sticky="w", padx=6, pady=(0, 4))
            self.vals[key] = v
        self.msg_lbl = ttk.Label(grid, text="", wraplength=PREVIEW_W - 20, justify="left")
        self.msg_lbl.grid(row=2*((len(fields)+2)//3), column=0, columnspan=3, sticky="w", padx=6, pady=(4, 0))

        prow = ttk.Frame(outer)
        prow.pack(fill="x")
        self.preview_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(prow, text="Preview (what the bot sees)", variable=self.preview_var,
                        command=self._preview_toggled).pack(side="left")
        self.legend = ttk.Label(outer, foreground="#555555", wraplength=PREVIEW_W, justify="left",
                                text="Green box = you and your skill reach (amber = position guessed), "
                                     "orange = mobs, red = the mob being attacked, cyan = minimap, "
                                     "pink lines = patrol edges.")
        self.preview_lbl = ttk.Label(outer)

        brow = ttk.Frame(outer)
        brow.pack(fill="x", pady=(8, 0), side="bottom")
        self.wizard_btn = ttk.Button(brow, text="Setup wizard", command=self.open_wizard)
        self.wizard_btn.pack(side="left", expand=True, fill="x", padx=(0, 3))
        self.settings_btn = ttk.Button(brow, text="Settings", command=self.open_settings)
        self.settings_btn.pack(side="left", expand=True, fill="x", padx=3)
        ttk.Button(brow, text="Activity log", command=self.open_activity).pack(
            side="left", expand=True, fill="x", padx=(3, 0))

        urow = ttk.Frame(outer)
        urow.pack(fill="x", pady=(6, 0), side="bottom")
        self.update_btn = ttk.Button(urow, text="Check for updates", command=self.check_for_updates)
        self.update_btn.pack(side="left")
        self.update_lbl = ttk.Label(urow, text="", wraplength=300)
        self.update_lbl.pack(side="left", padx=(8, 0))

        self._apply_ontop()
        self._refresh_maps()
        self._refresh_buttons()
        self._refresh_problems()
        root.after(POLL_MS, self._poll)
        if self._problems():
            root.after(300, self.open_wizard)

    # ---- helpers -----------------------------------------------------------
    def _apply_ontop(self):
        try:
            self.root.attributes("-topmost", bool(self.ontop_var.get()) and not self.running)
        except self.tk.TclError:
            pass

    def _problems(self):
        probs = list(self.settings.problems())
        try:
            if not self.profile.mob_templates():
                probs.append("No mob pictures saved")
            if self.profile.player_template() is None:
                probs.append("No name tag picture saved")
        except Exception:
            pass
        return probs

    def _map_set_up(self, map_id=None) -> bool:
        try:
            return bool(self.profile.map_is_set_up(map_id))
        except Exception:
            log.warning("map_is_set_up failed", exc_info=True)
            return False

    def _refresh_maps(self):
        """Fill the Map dropdown from the profile."""
        try:
            cur = self.profile.current_map
            choices = logic.map_choices(self.profile.maps(), cur, self.profile.current_map_name(),
                                        self._map_set_up)
        except Exception as e:
            log.exception("listing maps failed")
            self._show_error(e)
            return
        self._map_choices = choices
        self.map_combo.config(values=[label for _, label in choices])
        self.map_var.set(next((label for i, label in choices if i == cur), ""))

    def _map_selected(self):
        map_id = logic.map_label_to_id(self._map_choices, self.map_var.get())
        if map_id is None or map_id == self.profile.current_map:
            return
        if self.running or self._updating:
            self._refresh_maps()            # snap back: can't switch while the bot runs
            return
        self._switch_to(map_id)

    def _switch_to(self, map_id: str):
        from tkinter import messagebox
        self._close_side_windows()
        try:
            self.profile.switch_map(map_id)
            self.settings = self.profile.load()
        except Exception as e:
            title, text = logic.friendly_error(e)
            messagebox.showerror(title, text, parent=self.root)
        self._refresh_maps()
        self._refresh_problems()

    def _close_side_windows(self):
        """The settings window edits the current map; close it before the map changes."""
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.destroy()
            except Exception:
                pass
            self.settings_win = None

    def _ask_map_name(self, title: str, prompt: str, initial: str = ""):
        from .widgets import ask_text
        return logic.clean_map_name(ask_text(self.root, title, prompt, initial=initial,
                                             hint=logic.NEW_MAP_HINT))

    def new_map(self):
        if self.running or self._updating:
            return
        from tkinter import messagebox
        name = self._ask_map_name("New map", "Name of the map you want to train on:")
        if not name:
            return
        try:
            had_setup = self._map_set_up()
            old_name = self.profile.current_map_name()
            had_old = any(i == self.profile.current_map for i, _ in self.profile.maps())
            self._close_side_windows()
            self.profile.create_map(name)
            self.settings = self.profile.load()
        except Exception as e:
            title, text = logic.friendly_error(e)
            messagebox.showerror(title, text, parent=self.root)
            return
        self._refresh_maps()
        self._refresh_problems()
        self.open_wizard(kept_from=old_name if (had_old and had_setup) else None)

    def rename_map(self):
        if self.running or self._updating:
            return
        from tkinter import messagebox
        cur_name = self.profile.current_map_name()
        name = self._ask_map_name("Rename map", "New name for this map:", initial=cur_name)
        if not name or name == cur_name:
            return
        try:
            self.profile.rename_map(self.profile.current_map, name)
        except Exception as e:
            title, text = logic.friendly_error(e)
            messagebox.showerror(title, text, parent=self.root)
            return
        self._refresh_maps()

    def delete_map(self):
        if self.running or self._updating:
            return
        from tkinter import messagebox
        name = self.profile.current_map_name()
        if not messagebox.askyesno("Delete map?", logic.delete_map_question(name), parent=self.root):
            return
        try:
            self._close_side_windows()
            self.profile.delete_map(self.profile.current_map)
            self.settings = self.profile.load()
        except Exception as e:
            title, text = logic.friendly_error(e)
            messagebox.showerror(title, text, parent=self.root)
            return
        self._refresh_maps()
        self._refresh_problems()

    def _refresh_problems(self):
        probs = self._problems()
        if probs and not self.running:
            self.problems_lbl.config(text="Finish setup first: " + "; ".join(probs) +
                                          ". Click Setup wizard.")
        else:
            self.problems_lbl.config(text="")
        self._refresh_buttons()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _refresh_buttons(self):
        running = self.running
        busy = running or self._updating
        text, enabled, self._start_action = logic.start_state(
            self._problems(), self._map_set_up(), running, self.settings)
        self.start_btn.state(["!disabled"] if enabled and not busy else ["disabled"])
        self.observe_btn.state(["!disabled"] if enabled and not busy else ["disabled"])
        self.start_btn.config(text=text)
        self.map_combo.config(state="disabled" if busy else "readonly")
        for b in (self.new_map_btn, self.rename_map_btn, self.delete_map_btn):
            b.state(["disabled"] if busy else ["!disabled"])
        self.pause_btn.state(["!disabled"] if running else ["disabled"])
        self.stop_btn.state(["!disabled"] if running else ["disabled"])
        for b in (self.wizard_btn, self.settings_btn):
            b.state(["disabled"] if busy else ["!disabled"])
        self.update_btn.state(["disabled"] if busy else ["!disabled"])
        paused = bool(self.runner and self.runner.status.paused)
        self.pause_btn.config(text="Resume" if paused else "Pause")

    def _settings_saved(self, s: Settings):
        self.settings = s
        self._refresh_problems()

    # ---- buttons -------------------------------------------------------------
    _start_action = "start"

    def start(self, observe_only=False):
        if self.running or self._updating:
            return
        if self._start_action == "wizard":
            self.open_wizard()
            return
        probs = self._problems()
        if probs:
            from tkinter import messagebox
            messagebox.showwarning("Setup not finished",
                                   "Before starting:\n- " + "\n- ".join(probs) +
                                   "\n\nClick Setup wizard to fix this.", parent=self.root)
            return
        if observe_only:
            self.preview_var.set(True)
            self._preview_toggled()
        try:
            from maplebot.runner import BotRunner
            self.runner = BotRunner(self.profile, self.settings,
                                    on_status=self._on_status, on_preview=self._on_preview,
                                    observe_only=observe_only)
        except Exception as e:
            self._show_error(e)
            return
        self.thread = threading.Thread(target=self._work, args=(self.runner,), name="bot", daemon=True)
        self.thread.start()
        self._apply_ontop()  # Let the game cover the app so capture sees the game pixels.
        log.info("bot started")
        self.countdown_lbl.config(text=f"Starting in {int(round(self.settings.safety.start_delay_s))}... "
                                       "click into the game")
        self._refresh_problems()

    def _work(self, runner):
        """Worker thread. No Tk calls in here."""
        try:
            runner.run()
        except BaseException as e:      # noqa: BLE001 -- everything goes to the user, friendly
            log.error("bot stopped with an error:\n%s", traceback.format_exc())
            self.events.put(("error", e))
        finally:
            self.events.put(("done", None))

    def pause_resume(self):
        r = self.runner
        if not r or not self.running:
            return
        if r.status.paused:
            r.resume()
        else:
            r.pause("Paused by you")
        self.root.after(150, self._refresh_buttons)

    def stop(self):
        if self.runner:
            self.runner.stop("Stopped by you")
        self.countdown_lbl.config(text="")

    def open_wizard(self, kept_from=None):
        if self.running or self._updating:
            return
        if self.wizard is not None and self.wizard.winfo_exists():
            self.wizard.lift()
            return
        from .wizard import SetupWizard
        # The main window steps aside while the wizard is open (it would sit on top of it).
        self.root.withdraw()
        try:
            self.wizard = SetupWizard(self.root, self.profile, self.settings, on_saved=self._settings_saved,
                                      kept_from=kept_from)
        except Exception:
            self.root.deiconify()
            raise

        def closed(e):
            if e.widget is self.wizard:
                self.wizard = None
                if not self._closing:
                    self.root.deiconify()
                    self._apply_ontop()
                    self._refresh_maps()
                    self._refresh_problems()
        self.wizard.bind("<Destroy>", closed, add="+")

    def open_settings(self):
        if self.running or self._updating:
            return
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        from .settings_form import SettingsWindow
        self.settings_win = SettingsWindow(self.root, self.profile, self.settings, on_saved=self._settings_saved)

    def open_activity(self):
        if self.activity_win is not None and self.activity_win.winfo_exists():
            self.activity_win.lift();return
        from .activity import ActivityWindow
        self.activity_win=ActivityWindow(self.root,self.activity,self.copy_diagnostics,
                                         self.open_logs,self.settings.safety.pause_hotkey)

    def _record_activity(self,status):
        line=self.activity.record(status)
        if line and self.activity_win is not None and self.activity_win.winfo_exists():
            self.activity_win.append(line)

    def copy_diagnostics(self):
        keys=self.settings.keys
        lines=['MapleBot diagnostics',f'Map: {self.profile.current_map_name()}',
               f'Window title filter: {self.settings.calibration.window_title}',
               f'Keys: attack={keys.attack}, jump={keys.jump}, left={keys.left}, right={keys.right}',
               f'Platform: {sys.platform}; administrator={is_admin()}',
               f'Setup problems: {"; ".join(self._problems()) or "none"}',
               f'Logs: {self.profile.logs_dir}']
        if self.runner is not None and self.runner.run_dir:
            lines.append(f'Current run: {self.runner.run_dir}')
        lines.extend(['','Recent activity:',self.activity.text()])
        self.root.clipboard_clear()
        self.root.clipboard_append('\n'.join(lines))
        self.msg_lbl.config(text='Diagnostics copied. Paste them when reporting a problem.')

    def open_logs(self):
        try:
            open_folder(self.profile.logs_dir)
        except Exception as e:
            from tkinter import messagebox
            messagebox.showinfo("Logs folder", f"Logs are in:\n{self.profile.logs_dir}\n\n({e})",
                                parent=self.root)

    def check_for_updates(self):
        if self.running or self._updating or self._closing:
            return
        from tkinter import messagebox
        if self.settings_win is not None and self.settings_win.winfo_exists():
            messagebox.showinfo("Save settings first", "Save and close Settings before updating.",
                                parent=self.root)
            return
        self._updating = True
        self.update_lbl.config(text="Checking GitHub…")
        self._refresh_buttons()
        threading.Thread(target=self._update_work, name="update-check", daemon=True).start()

    def _update_work(self, update=None):
        """Worker: downloads never block Tk, and never touch widgets."""
        from . import updater
        try:
            if update is None:
                self.events.put(("update_checked", updater.check_update(REPO_ROOT)))
            else:
                stage = updater.prepare_update(REPO_ROOT, update)
                if self._closing:
                    import shutil
                    shutil.rmtree(stage, ignore_errors=True)
                else:
                    self.events.put(("update_prepared", stage))
        except Exception as e:
            log.exception("Update failed")
            self.events.put(("update_error", e))

    def _update_event(self, kind, payload):
        from tkinter import messagebox
        from . import updater
        if kind == "update_checked" and payload is not None:
            current = payload.installed[:8] if payload.installed else "original ZIP"
            install = messagebox.askyesno(
                "Update MapleBot?",
                f"New code is available: {current} → {payload.revision[:8]}.\n\n"
                "Download, back up the current app, and restart to install?\n"
                "Your saved setup, logs, recordings and downloaded assets stay in place.",
                parent=self.root)
            if install and not self._closing:
                self.update_lbl.config(text="Downloading and checking update…")
                threading.Thread(target=self._update_work, args=(payload,),
                                 name="update-download", daemon=True).start()
                return
            self.update_lbl.config(text="Update postponed")
        elif kind == "update_checked":
            self.update_lbl.config(text="You're up to date")
        elif kind == "update_prepared":
            try:
                if self.running:
                    raise updater.UpdateError("Stop the bot before installing an update.")
                updater.launch_installer(REPO_ROOT, payload)
            except Exception as e:
                import shutil
                shutil.rmtree(payload, ignore_errors=True)
                self._update_event("update_error", e)
                return
            self.on_close()
            return
        else:
            self.update_lbl.config(text="Update failed — current app kept")
            messagebox.showerror("Couldn't update MapleBot", str(payload), parent=self.root)
        self._updating = False
        self._refresh_buttons()

    # ---- runner callbacks (worker thread!) -------------------------------------
    def _on_status(self, st):
        self.events.put(("status", st))

    def _on_preview(self, img):
        # Worker thread: never read Tk variables here, only the plain mirror flag.
        if not self._preview_on:
            return
        with self._preview_lock:
            self._preview_img = img

    # ---- Tk thread polling -----------------------------------------------------
    def _poll(self):
        if self._closing:
            return
        last_status = None
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._record_activity(payload)
                    last_status = payload
                elif kind == "error":
                    self._show_error(payload)
                elif kind == "done":
                    self._on_done()
                elif kind.startswith("update_"):
                    self._update_event(kind, payload)
                    if self._closing:
                        return
        except queue.Empty:
            pass
        if last_status is not None:
            self._show_status(last_status)
        self._show_preview()
        self.root.after(POLL_MS, self._poll)

    def _show_status(self, st):
        for k, v in logic.format_status(st).items():
            if k == "message":
                self.msg_lbl.config(text=v.split("\n",1)[0])
            elif k in self.vals:
                self.vals[k].config(text=v)
        self.countdown_lbl.config(text=logic.countdown_text(st))
        self._set_badge(logic.hotkey_badge(st, self.settings))
        self._refresh_buttons()

    def _set_badge(self, text: str):
        if text:
            self.hotkey_badge.config(text=text)
            if not self._badge_shown:
                self.hotkey_badge.pack(after=self.countdown_lbl, fill="x", pady=(4, 0))
                self._badge_shown = True
        elif self._badge_shown:
            self.hotkey_badge.pack_forget()
            self._badge_shown = False

    def _show_preview(self):
        with self._preview_lock:
            img, self._preview_img = self._preview_img, None
        if img is None or not self._preview_on:
            return
        try:
            from .widgets import bgr_to_photo
            h, w = img.shape[:2]
            self._preview_photo = bgr_to_photo(img, logic.preview_size(w, h, PREVIEW_W))
            self.preview_lbl.config(image=self._preview_photo)
        except Exception:
            log.exception("preview display failed")

    def _preview_toggled(self):
        self._preview_on = bool(self.preview_var.get())
        if self._preview_on:
            self.legend.pack(fill="x")
            self.preview_lbl.pack(pady=4)
            if not self.running:
                self.preview_lbl.config(image="", text="The preview appears here while the bot runs.")
        else:
            self.legend.pack_forget()
            self.preview_lbl.pack_forget()
            self._preview_photo = None

    def _on_done(self):
        log.info("bot thread finished")
        self.countdown_lbl.config(text="")
        self._set_badge("")
        if self.thread is not None:
            self.thread.join(timeout=0.5)
        self.thread = None
        self._apply_ontop()
        self._refresh_problems()
        self.root.after(200, self._refresh_buttons)

    def _show_error(self, e: BaseException):
        from tkinter import messagebox
        title, text = logic.friendly_error(e)
        self.msg_lbl.config(text=text.split("\n")[0])
        log.error('%s: %s',title,text)
        line=self.activity.append(f'ERROR: {title}: {text}')
        if self.activity_win is not None and self.activity_win.winfo_exists():
            self.activity_win.append(line)
        if not self._closing:
            messagebox.showerror(title, text, parent=self.root)

    def on_close(self):
        self._closing = True
        if self.runner is not None:
            try:
                self.runner.stop("App closed")
            except Exception:
                log.exception("stop on close failed")
        if self.thread is not None:
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                log.warning("bot thread did not stop within 2 s; exiting anyway (daemon thread)")
        self.root.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _fatal(title: str, text: str) -> None:
    """Last-resort message when Tk itself can't start."""
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40 if "already open" in text else 0x10)
            return
    except Exception:
        pass
    print(f"{title}: {text}", file=sys.stderr or sys.__stdout__)


class AlreadyRunning(RuntimeError):
    """Another copy of the app holds the lock."""


def acquire_single_instance(logs_dir: Path):
    """Hold an exclusive lock on logs/app.lock for the life of the process.

    Returns the open file (keep a reference). Raises AlreadyRunning if another copy holds
    it. The OS drops the lock when the process exits or crashes, so a stale file never
    blocks a restart.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "app.lock"
    f = open(path, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        f.close()
        if lock_held_by_other(e):
            raise AlreadyRunning("The bot is already open.") from None
        raise                       # some other failure: caller carries on without the guard
    return f


def lock_held_by_other(e: OSError) -> bool:
    """Does this lock error mean another process holds the lock?

    flock: EWOULDBLOCK/EAGAIN (also EACCES on some systems). msvcrt.locking with
    LK_NBLCK: EACCES (13) or EDEADLK/EDEADLOCK (36 on Windows). Anything else (ENOLCK,
    EINVAL on network or odd file systems, ...) is not "already open".
    """
    import errno

    return e.errno in _busy_errnos(sys.platform)


def _busy_errnos(platform: str) -> set:
    import errno

    busy = {errno.EACCES, errno.EAGAIN, getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
            errno.EDEADLK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}
    if platform == "win32":
        busy |= {13, 36}            # msvcrt.locking: EACCES / EDEADLOCK (36 on Windows)
    return busy


def write_pid_file(logs_dir: Path) -> Path:
    """logs/app.pid lets start.bat check that *this* app started (not any pythonw.exe)."""
    import atexit

    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "app.pid"
    pid = str(os.getpid())
    try:
        path.write_text(pid, encoding="ascii")
    except OSError:
        return path

    def remove():
        try:
            if path.read_text(encoding="ascii").strip() == pid:
                path.unlink()
        except OSError:
            pass
    atexit.register(remove)
    return path


_instance_lock = None


def main() -> None:
    global _instance_lock
    profile = Profile.default()
    try:
        _instance_lock = acquire_single_instance(profile.logs_dir)
    except AlreadyRunning:
        # Don't touch app.pid / app.log: they belong to the copy that's open.
        _fatal("MapleBot", "The bot is already open. Look for the MapleBot window "
                           "(it may be behind the game or minimised to the taskbar).")
        raise SystemExit(3)
    except OSError:
        # Couldn't lock for another reason (odd/network file system): carry on without
        # the single-instance guard rather than refusing to open.
        logging.getLogger("maplebot.app").warning("single-instance lock unavailable", exc_info=True)
        _instance_lock = None
    write_pid_file(profile.logs_dir)
    logfile = setup_logging(profile.logs_dir)
    log.info("MapleBot starting (python %s, %s)", sys.version.split()[0], sys.platform)

    try:
        from maplebot.perception import enable_dpi_awareness
        enable_dpi_awareness()
    except Exception:
        log.exception("DPI awareness unavailable")

    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        _fatal("MapleBot", "This Python has no tkinter. Reinstall Python from python.org "
                           "(tick 'tcl/tk and IDLE' in the installer).")
        raise SystemExit(1)

    try:
        settings = profile.load()
    except Exception:
        log.exception("settings.yaml unreadable")
        settings = None

    try:
        root = tk.Tk()
    except tk.TclError as e:
        _fatal("MapleBot", f"Couldn't open a window: {e}")
        raise SystemExit(1)

    def report(exc, val, tb):
        log.error("UI error:\n%s", "".join(traceback.format_exception(exc, val, tb)))
        title, text = logic.friendly_error(val)
        try:
            messagebox.showerror(title, text, parent=root)
        except Exception:
            pass

    root.report_callback_exception = report

    if settings is None:
        bad = profile.settings_path
        backup = bad.with_suffix(".broken.yaml")
        try:
            os.replace(bad, backup)
        except OSError:
            pass
        messagebox.showwarning("Settings reset",
                               f"Your settings file couldn't be read, so it was moved to\n{backup}\n"
                               "and the setup wizard will start fresh.", parent=root)
        settings = Settings()

    App(root, profile, settings)
    log.info("log file: %s", logfile)
    root.mainloop()


if __name__ == "__main__":
    from maplebot.__main__ import run
    run()
