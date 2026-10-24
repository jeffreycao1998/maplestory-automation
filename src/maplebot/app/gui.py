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

from maplebot.settings import Profile, Settings

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
        self._preview_on = False        # plain mirror of preview_var, safe to read from the worker
        self.wizard = None
        self.settings_win = None

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
        ttk.Checkbutton(top, text="Keep on top", variable=self.ontop_var,
                        command=self._apply_ontop).pack(side="right")
        self.admin_lbl = ttk.Label(top, style="Problem.TLabel", wraplength=300, justify="left")
        self.admin_lbl.pack(side="left")
        if is_admin() is False:
            self.admin_lbl.config(text="Not running as admin: the game may ignore the bot's keys. "
                                       "Close this and double-click start.bat.")

        self.start_btn = ttk.Button(outer, text=logic.start_button_text(settings),
                                    style="Big.TButton", command=self.start)
        self.start_btn.pack(fill="x", pady=(8, 4))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        self.pause_btn = ttk.Button(row, text="Pause", style="Mid.TButton", command=self.pause_resume)
        self.pause_btn.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.stop_btn = ttk.Button(row, text="Stop", style="Mid.TButton", command=self.stop)
        self.stop_btn.pack(side="left", fill="x", expand=True, padx=(3, 0))

        self.countdown_lbl = ttk.Label(outer, style="Countdown.TLabel")
        self.countdown_lbl.pack(pady=(6, 0))
        self.problems_lbl = ttk.Label(outer, style="Problem.TLabel", wraplength=PREVIEW_W, justify="left")
        self.problems_lbl.pack(fill="x")

        grid = ttk.LabelFrame(outer, text="Status", padding=8)
        grid.pack(fill="x", pady=6)
        self.vals = {}
        fields = [("state", "State"), ("action", "Doing"), ("mobs", "Mobs seen"),
                  ("map_x", "Map position"), ("hp", "HP"), ("mp", "MP"),
                  ("attacks", "Attacks"), ("runtime", "Running for"), ("fps", "Checks/sec")]
        for i, (key, label) in enumerate(fields):
            r, c = divmod(i, 3)
            ttk.Label(grid, text=label + ":", foreground="#555555").grid(row=r * 2, column=c, sticky="w", padx=6)
            v = ttk.Label(grid, text="--", font=("Segoe UI", 11, "bold"), width=12)
            v.grid(row=r * 2 + 1, column=c, sticky="w", padx=6, pady=(0, 4))
            self.vals[key] = v
        self.msg_lbl = ttk.Label(grid, text="", wraplength=PREVIEW_W - 20, justify="left")
        self.msg_lbl.grid(row=6, column=0, columnspan=3, sticky="w", padx=6, pady=(4, 0))

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
        ttk.Button(brow, text="Open logs folder", command=self.open_logs).pack(
            side="left", expand=True, fill="x", padx=(3, 0))

        self._apply_ontop()
        self._refresh_buttons()
        self._refresh_problems()
        root.after(POLL_MS, self._poll)
        if self._problems():
            root.after(300, self.open_wizard)

    # ---- helpers -----------------------------------------------------------
    def _apply_ontop(self):
        try:
            self.root.attributes("-topmost", bool(self.ontop_var.get()))
        except self.tk.TclError:
            pass

    def _problems(self):
        probs = list(self.settings.problems())
        try:
            if not self.profile.mob_templates():
                probs.append("No mob pictures saved")
        except OSError:
            pass
        return probs

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
        can_start = not running and not self._problems()
        self.start_btn.state(["!disabled"] if can_start else ["disabled"])
        self.pause_btn.state(["!disabled"] if running else ["disabled"])
        self.stop_btn.state(["!disabled"] if running else ["disabled"])
        for b in (self.wizard_btn, self.settings_btn):
            b.state(["disabled"] if running else ["!disabled"])
        paused = bool(self.runner and self.runner.status.paused)
        self.pause_btn.config(text="Resume" if paused else "Pause")
        self.start_btn.config(text=logic.start_button_text(self.settings))

    def _settings_saved(self, s: Settings):
        self.settings = s
        self._refresh_problems()

    # ---- buttons -------------------------------------------------------------
    def start(self):
        if self.running:
            return
        probs = self._problems()
        if probs:
            from tkinter import messagebox
            messagebox.showwarning("Setup not finished",
                                   "Before starting:\n- " + "\n- ".join(probs) +
                                   "\n\nClick Setup wizard to fix this.", parent=self.root)
            return
        try:
            from maplebot.runner import BotRunner
            self.runner = BotRunner(self.profile, self.settings,
                                    on_status=self._on_status, on_preview=self._on_preview)
        except Exception as e:
            self._show_error(e)
            return
        self.thread = threading.Thread(target=self._work, args=(self.runner,), name="bot", daemon=True)
        self.thread.start()
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

    def open_wizard(self):
        if self.running:
            return
        if self.wizard is not None and self.wizard.winfo_exists():
            self.wizard.lift()
            return
        from .wizard import SetupWizard
        # The main window steps aside while the wizard is open (it would sit on top of it).
        self.root.withdraw()
        try:
            self.wizard = SetupWizard(self.root, self.profile, self.settings, on_saved=self._settings_saved)
        except Exception:
            self.root.deiconify()
            raise

        def closed(e):
            if e.widget is self.wizard:
                self.wizard = None
                if not self._closing:
                    self.root.deiconify()
                    self._apply_ontop()
                    self._refresh_problems()
        self.wizard.bind("<Destroy>", closed, add="+")

    def open_settings(self):
        if self.running:
            return
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        from .settings_form import SettingsWindow
        self.settings_win = SettingsWindow(self.root, self.profile, self.settings, on_saved=self._settings_saved)

    def open_logs(self):
        try:
            open_folder(self.profile.logs_dir)
        except Exception as e:
            from tkinter import messagebox
            messagebox.showinfo("Logs folder", f"Logs are in:\n{self.profile.logs_dir}\n\n({e})",
                                parent=self.root)

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
                    last_status = payload
                elif kind == "error":
                    self._show_error(payload)
                elif kind == "done":
                    self._on_done()
        except queue.Empty:
            pass
        if last_status is not None:
            self._show_status(last_status)
        self._show_preview()
        self.root.after(POLL_MS, self._poll)

    def _show_status(self, st):
        for k, v in logic.format_status(st).items():
            if k == "message":
                self.msg_lbl.config(text=v)
            elif k in self.vals:
                self.vals[k].config(text=v)
        self.countdown_lbl.config(text=logic.countdown_text(st))
        self._refresh_buttons()

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
        if self.thread is not None:
            self.thread.join(timeout=0.5)
        self.thread = None
        self._refresh_problems()
        self.root.after(200, self._refresh_buttons)

    def _show_error(self, e: BaseException):
        from tkinter import messagebox
        title, text = logic.friendly_error(e)
        self.msg_lbl.config(text=text.split("\n")[0])
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
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
            return
    except Exception:
        pass
    print(f"{title}: {text}", file=sys.stderr or sys.__stdout__)


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


def main() -> None:
    profile = Profile.default()
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
    main()
