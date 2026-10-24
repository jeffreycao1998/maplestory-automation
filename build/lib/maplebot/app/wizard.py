"""
Setup wizard: one window, Back / Next, ten steps (see ``logic.WIZARD_STEPS``).

Everything the user selects on a screenshot is converted back to real game-client
pixels by ``logic.View``. Mob / name-tag crops are written straight to
``profile/templates/``; settings are written only when the user clicks Save.
"""
from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, List, Optional

from maplebot.settings import Profile, Settings

from . import logic
from .logic import WIZARD_STEPS, WizardContext
from .widgets import (CYAN, GREEN, MAGENTA, ORANGE, RED, ImageCanvas, KeyPicker, Tooltip,
                      capture_game, file_thumbnail)

log = logging.getLogger(__name__)

CAPTURE_COUNTDOWN_S = 3


class SetupWizard(tk.Toplevel):
    def __init__(self, master: tk.Tk, profile: Profile, settings: Settings,
                 on_saved: Optional[Callable[[Settings], None]] = None,
                 hide_also: Optional[List[tk.Misc]] = None):
        super().__init__(master)
        self.title("MapleBot setup")
        self.master_root = master
        self.profile = profile
        self.on_saved = on_saved
        self.hide_also = hide_also or [master]
        self.ctx = WizardContext(settings=Settings.from_dict(settings.to_dict()))
        self.ctx.n_mob_templates = len(profile.mob_templates())
        # A profile that was set up before keeps its patrol edges unless the user changes them.
        self.ctx.patrol_preset = profile.settings_path.exists() and bool(settings.calibration.minimap_box)
        self.session_boxes: list = []  # mob boxes drawn on the current screenshot
        self.image = None             # current full-res BGR screenshot
        self.patrol_image = None      # last screenshot taken in the patrol step
        self.step = 0
        self.busy = False
        self._thumbs = []             # keep PhotoImage refs alive
        self.bar_target = tk.StringVar(value="hp")

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.canvas_w = max(480, min(960, sw - 160))
        self.canvas_h = max(300, min(540, sh - 360))

        head = ttk.Frame(self, padding=(12, 10, 12, 0))
        head.pack(fill="x")
        self.step_lbl = ttk.Label(head, font=("Segoe UI", 13, "bold"))
        self.step_lbl.pack(anchor="w")
        self.dots = ttk.Label(head, foreground="#777777")
        self.dots.pack(anchor="w")
        self.instr = ttk.Label(head, wraplength=self.canvas_w, justify="left", font=("Segoe UI", 10))
        self.instr.pack(anchor="w", pady=(6, 4))

        self.body = ttk.Frame(self, padding=(12, 4))
        self.body.pack(fill="both", expand=True)

        self.msg = ttk.Label(self, wraplength=self.canvas_w, justify="left", padding=(12, 2))
        self.msg.pack(fill="x")

        nav = ttk.Frame(self, padding=12)
        nav.pack(fill="x")
        self.back_btn = ttk.Button(nav, text="< Back", command=self.back)
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next >", command=self.next)
        self.next_btn.pack(side="right")
        self.skip_btn = ttk.Button(nav, text="Skip", command=self.skip)
        self.skip_btn.pack(side="right", padx=6)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show_step(0)
        self.lift()
        self.focus_force()

    # ------------------------------------------------------------------ nav
    @property
    def s(self) -> Settings:
        return self.ctx.settings

    def key(self) -> str:
        return WIZARD_STEPS[self.step].key

    def info(self, text: str, colour: str = "#1a6e1a"):
        self.msg.config(text=text, foreground=colour)

    def error(self, text: str):
        self.msg.config(text=text, foreground="#c00000")

    def show_step(self, i: int):
        self.step = max(0, min(i, len(WIZARD_STEPS) - 1))
        st = WIZARD_STEPS[self.step]
        self.step_lbl.config(text=logic.step_label(self.step))
        self.dots.config(text="  ".join("●" if j <= self.step else "○" for j in range(len(WIZARD_STEPS))))
        self.instr.config(text=st.instruction)
        self.msg.config(text="")
        for w in self.body.winfo_children():
            w.destroy()
        self._thumbs = []
        getattr(self, f"_build_{st.key}")(self.body)
        self.back_btn.state(["!disabled"] if self.step > 0 else ["disabled"])
        if st.optional:
            self.skip_btn.pack(side="right", padx=6)
        else:
            self.skip_btn.pack_forget()
        self.next_btn.config(text="Save" if st.key == "summary" else "Next >")

    def next(self):
        if self.busy:
            return
        problem = logic.step_problem(self.key(), self.ctx)
        if problem:
            self.error(problem)
            return
        if self.key() == "summary":
            self.save()
            return
        self.show_step(self.step + 1)

    def skip(self):
        if not self.busy:
            self.show_step(self.step + 1)

    def back(self):
        if not self.busy:
            self.show_step(self.step - 1)

    def close(self):
        if self.busy:
            return
        if messagebox.askyesno("Leave setup?",
                               "Close setup without saving?\n\nMob pictures you already saved "
                               "are kept; everything else is not saved.", parent=self):
            self.destroy()

    def save(self):
        try:
            self.profile.save(self.s)
        except OSError as e:
            log.exception("saving settings failed")
            messagebox.showerror("Couldn't save", f"Couldn't write the settings file:\n{e}", parent=self)
            return
        if self.on_saved:
            self.on_saved(self.s)
        messagebox.showinfo("All set!",
                            "Setup saved.\n\nClick Start in the main window, then click into the game "
                            "within 3 seconds. Press F11 to pause and F12 to stop at any time."
                            .replace("F11", self.s.safety.pause_hotkey.upper())
                            .replace("F12", self.s.safety.stop_hotkey.upper()), parent=self)
        self.destroy()

    # ------------------------------------------------------------ screenshots
    def take_screenshot(self, on_image: Callable[[object], None]):
        """Hide the app, count down, grab the game, restore; then on_image(image)."""
        if self.busy:
            return
        self.busy = True
        self.info("Hiding for a moment... click on the game window now.", "#555555")

        def done(image, err):
            self.busy = False
            try:
                self.lift()
                self.focus_force()
            except tk.TclError:
                return
            if err is not None:
                title, text = logic.friendly_error(err)
                self.error(f"{title}: {text}")
                return
            if logic.looks_blank(image):
                self.error("The screenshot is blank. Make sure the game isn't minimised and nothing "
                           "covers it, then try again.")
                return
            on_image(image)

        capture_game(self.master_root, [self] + list(self.hide_also), self.s.calibration.window_title,
                     CAPTURE_COUNTDOWN_S, done)

    def _set_main_image(self, image):
        old = self.ctx.screenshot_size
        self.image = image
        h, w = image.shape[:2]
        self.ctx.screenshot_size = (w, h)
        self.session_boxes = []
        if old and old != (w, h):
            self.info(f"The game window size changed ({old[0]} x {old[1]} -> {w} x {h}). "
                      "Draw the boxes again on this screenshot.", "#a05a00")
        self._refresh_dot()

    def _retake_button(self, parent, then: Callable[[], None]):
        def go():
            def got(img):
                self._set_main_image(img)
                then()
            self.take_screenshot(got)
        return ttk.Button(parent, text="Retake screenshot", command=go)

    def _need_screenshot(self, parent) -> bool:
        if self.image is not None:
            return True
        ttk.Label(parent, text="No screenshot yet -- go back one step and take one.").pack(pady=20)
        return False

    def _overlays(self, extra=()):
        c = self.s.calibration
        ovs = []
        if c.minimap_box:
            ovs.append(("box", tuple(c.minimap_box), CYAN, "minimap"))
        if c.hp_bar_box:
            ovs.append(("box", tuple(c.hp_bar_box), RED, "HP"))
        if c.mp_bar_box:
            ovs.append(("box", tuple(c.mp_bar_box), "#3388ff", "MP"))
        return ovs + list(extra)

    # ------------------------------------------------------------ 1. window
    def _build_window(self, f):
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=10)
        ttk.Label(row, text="Window title contains:").pack(side="left")
        self.title_var = tk.StringVar(value=self.s.calibration.window_title or "MapleStory")
        e = ttk.Entry(row, textvariable=self.title_var, width=28)
        e.pack(side="left", padx=8)
        Tooltip(e, "Usually 'MapleStory'. If your private server's window has another name, "
                   "type part of it here.")
        ttk.Button(row, text="Find window", command=self._find_window).pack(side="left")
        self.window_lbl = ttk.Label(f, font=("Segoe UI", 11))
        self.window_lbl.pack(anchor="w", pady=6)
        ttk.Label(f, foreground="#555555", wraplength=self.canvas_w, justify="left",
                  text="Tips: use windowed mode (not full screen), and don't move or resize the game "
                       "window after setup. If you do, run this setup again.").pack(anchor="w", pady=10)
        if self.ctx.window_box:
            self._show_window_box()
        else:
            self.after(200, lambda: self._find_window(quiet=True))

    def _find_window(self, quiet: bool = False):
        title = self.title_var.get().strip() or "MapleStory"
        self.s.calibration.window_title = title
        try:
            from maplebot.perception import find_client_area
            self.ctx.window_box = tuple(find_client_area(title))
        except Exception as e:
            self.ctx.window_box = None
            log.info("find window failed: %s", e)
            if quiet:
                return
            msg = str(e) if type(e).__name__ == "WindowNotFound" else logic.friendly_error(e)[1]
            self.window_lbl.config(text=msg, foreground="#c00000")
            return
        self._show_window_box()
        self.msg.config(text="")

    def _show_window_box(self):
        x, y, w, h = self.ctx.window_box
        self.window_lbl.config(text=f"Found it: game area is {w} x {h} pixels. Click Next.",
                               foreground="#1a6e1a")

    # -------------------------------------------------------- 2. screenshot
    def _build_screenshot(self, f):
        bar = ttk.Frame(f)
        bar.pack(anchor="w", pady=(0, 6))
        ttk.Button(bar, text="Take screenshot",
                   command=lambda: self.take_screenshot(lambda img: (self._set_main_image(img),
                                                                     self._show_shot()))).pack(side="left")
        ttk.Label(bar, foreground="#555555",
                  text="  Stand somewhere with a monster or two on screen.").pack(side="left")
        self.canvas = ImageCanvas(f, self.canvas_w, self.canvas_h)
        self.canvas.pack()
        self._show_shot()

    def _show_shot(self):
        self.canvas.overlays = self._overlays()
        self.canvas.show(self.image)
        if self.image is not None:
            w, h = self.ctx.screenshot_size
            self.info(f"Got it ({w} x {h}). If you can see the game here, click Next.")

    # ----------------------------------------------------------- 3. minimap
    def _build_minimap(self, f):
        if not self._need_screenshot(f):
            return
        bar = ttk.Frame(f)
        bar.pack(anchor="w", pady=(0, 6))
        self._retake_button(bar, self._redraw_minimap).pack(side="left")
        self.canvas = ImageCanvas(f, self.canvas_w, self.canvas_h)
        self.canvas.pack()
        self.canvas.mode = "box"
        self.canvas.on_box = self._got_minimap
        self._redraw_minimap()

    def _redraw_minimap(self):
        self.canvas.overlays = self._overlays()
        self.canvas.show(self.image)

    def _got_minimap(self, box):
        if box is None:
            self.error("That was a click, not a drag. Press, hold and drag a box around the minimap.")
            return
        problem = logic.validate_box_on_screen(box, self.ctx.screenshot_size, "minimap", max_fraction=0.3)
        if problem:
            self.error(problem)
            return
        self.s.calibration.minimap_box = list(box)
        self._refresh_dot()
        self._redraw_minimap()
        self.info("Minimap saved (blue box). Drag again to redo it, or click Next.")

    # --------------------------------------------------------------- 4. dot
    def _refresh_dot(self):
        """Where the bot sees your dot with the current colour range."""
        c = self.s.calibration
        self.ctx.dot_pos = None
        if self.image is None or not c.minimap_box:
            return
        try:
            from maplebot.perception.calib import minimap_player_position
            self.ctx.dot_pos = minimap_player_position(self.image, c.minimap_box,
                                                       c.player_dot_hsv_low, c.player_dot_hsv_high)
        except Exception:
            log.exception("minimap_player_position failed")

    def _build_dot(self, f):
        if not self._need_screenshot(f):
            return
        if not self.s.calibration.minimap_box:
            ttk.Label(f, text="Set the minimap box first (go back one step).").pack(pady=20)
            return
        bar = ttk.Frame(f)
        bar.pack(anchor="w", pady=(0, 6))
        ttk.Button(bar, text="Try again", command=self._dot_try_again).pack(side="left")
        self._retake_button(bar, self._redraw_dot).pack(side="left", padx=6)
        self.canvas = ImageCanvas(f, self.canvas_w, self.canvas_h)
        self.canvas.pack()
        self.canvas.mode = "click"
        self.canvas.on_click = self._got_dot_click
        self._redraw_dot()
        if self.ctx.dot_pos is not None:
            self.info("The green circle shows where the bot thinks you are. If it's on your yellow "
                      "dot, click Next. If not, click your dot.")

    def _dot_region(self):
        x, y, w, h = self.s.calibration.minimap_box
        m = 6
        return [x - m, y - m, w + 2 * m, h + 2 * m]

    def _redraw_dot(self):
        extra = []
        if self.ctx.dot_pos is not None:
            extra.append(("dot", logic.minimap_dot_to_image(self.s.calibration.minimap_box, self.ctx.dot_pos),
                          GREEN))
        self.canvas.overlays = [("box", tuple(self.s.calibration.minimap_box), CYAN, "")] + extra
        self.canvas.show(self.image, region=self._dot_region(), zoom=True)

    def _dot_try_again(self):
        self.ctx.dot_pos = None
        self._redraw_dot()
        self.info("Click right on the middle of your yellow dot.", "#555555")

    def _got_dot_click(self, x, y):
        from maplebot.perception.calib import hsv_range_from_sample, minimap_player_position

        c = self.s.calibration
        sampled = hsv_range_from_sample(self.image, x, y)
        current = (list(c.player_dot_hsv_low), list(c.player_dot_hsv_high))
        cands = logic.dot_range_candidates(sampled, current)

        def locate(lo, hi):
            return minimap_player_position(self.image, c.minimap_box, lo, hi)

        lo, hi, pos = logic.pick_dot_range(cands, locate, c.minimap_box, (x, y))
        if lo is not None:
            c.player_dot_hsv_low, c.player_dot_hsv_high = list(lo), list(hi)
            self.ctx.dot_pos = pos
            self.info(f"Found you at {pos[0]:.2f} across the minimap (green circle). "
                      "If the circle is on your yellow dot, click Next; otherwise click Try again.")
        else:
            self._refresh_dot()       # keep the previous colours
            self.ctx.dot_pos = None
            self.error("That doesn't look like your dot -- the bot would see you somewhere else. "
                       "Click right in the middle of your own yellow dot (not a party member's "
                       "orange dot or an NPC).")
        self._redraw_dot()

    # -------------------------------------------------------------- 5. mobs
    def _build_mobs(self, f):
        if not self._need_screenshot(f):
            return
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        self._retake_button(bar, self._redraw_mobs).pack(side="left")
        ttk.Label(bar, foreground="#555555",
                  text="  Each box is saved right away. To add another mob, just drag another box "
                       "(retake the screenshot to catch other poses).").pack(side="left")
        self.canvas = ImageCanvas(f, self.canvas_w, self.canvas_h - 90)
        self.canvas.pack()
        self.canvas.mode = "box"
        self.canvas.on_box = self._got_mob
        self.thumb_row = ttk.Frame(f)
        self.thumb_row.pack(fill="x", pady=6)
        self._redraw_mobs()
        self._refresh_templates()

    def _redraw_mobs(self):
        self.canvas.overlays = self._overlays(("box", b, ORANGE, "mob") for b in self.session_boxes)
        self.canvas.show(self.image)

    def _got_mob(self, box):
        if box is None:
            self.error("That was a click. Press, hold and drag a box around a monster.")
            return
        problem = logic.validate_box_on_screen(box, self.ctx.screenshot_size, "monster",
                                               min_w=8, min_h=8, max_fraction=0.15)
        if problem:
            self.error(problem)
            return
        mm = self.s.calibration.minimap_box
        if mm and _overlaps(box, mm):
            self.error("That box is on the minimap. Draw it around a monster in the game area.")
            return
        path = logic.next_mob_template_path(self.profile.templates_dir)
        try:
            logic.save_crop(self.image, box, path)
        except Exception as e:
            log.exception("saving mob crop failed")
            self.error(f"Couldn't save the picture: {e}")
            return
        self.session_boxes.append(tuple(box))
        self._redraw_mobs()
        self._refresh_templates()
        n = self.ctx.n_mob_templates
        tip = " Add 1-2 more in different poses (walking, turned) for best results." if n < 3 else ""
        self.info(f"Saved {path.name}. You have {n} mob picture{'s' if n != 1 else ''}.{tip}")

    def _refresh_templates(self):
        for w in self.thumb_row.winfo_children():
            w.destroy()
        self._thumbs = []
        paths = self.profile.mob_templates()
        self.ctx.n_mob_templates = len(paths)
        if not paths:
            ttk.Label(self.thumb_row, text="No mob pictures yet.", foreground="#777777").pack(side="left")
            return
        ttk.Label(self.thumb_row, text="Saved:").pack(side="left", padx=(0, 6))
        for p in paths:
            self._thumb(self.thumb_row, p, lambda p=p: self._delete_template(p))

    def _thumb(self, parent, path: Path, on_delete):
        cell = ttk.Frame(parent, padding=2)
        cell.pack(side="left", padx=3)
        try:
            ph = file_thumbnail(path, 56)
            self._thumbs.append(ph)
            ttk.Label(cell, image=ph).pack()
        except Exception:
            ttk.Label(cell, text="(unreadable)").pack()
        ttk.Button(cell, text="Delete", width=7, command=on_delete).pack()

    def _delete_template(self, path: Path):
        try:
            path.unlink()
        except OSError as e:
            self.error(f"Couldn't delete {path.name}: {e}")
            return
        self.session_boxes = []          # boxes no longer map to files 1:1
        if self.key() == "mobs":
            self._redraw_mobs()
            self._refresh_templates()
        else:
            self.show_step(self.step)
        self.info(f"Deleted {path.name}.", "#555555")

    # ------------------------------------------------------- 6. name tag
    def _build_player(self, f):
        if not self._need_screenshot(f):
            return
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        self._retake_button(bar, self._redraw_player).pack(side="left")
        self.canvas = ImageCanvas(f, self.canvas_w, self.canvas_h - 90)
        self.canvas.pack()
        self.canvas.mode = "box"
        self.canvas.on_box = self._got_player
        self.thumb_row = ttk.Frame(f)
        self.thumb_row.pack(fill="x", pady=6)
        self._redraw_player()

    def _redraw_player(self):
        self.canvas.overlays = self._overlays()
        self.canvas.show(self.image)
        for w in self.thumb_row.winfo_children():
            w.destroy()
        self._thumbs = []
        p = self.profile.player_template()
        if p:
            ttk.Label(self.thumb_row, text="Saved name tag:").pack(side="left", padx=(0, 6))
            self._thumb(self.thumb_row, p, lambda: self._delete_player(p))

    def _got_player(self, box):
        if box is None:
            self.error("That was a click. Drag a box around your name tag.")
            return
        problem = logic.validate_box_on_screen(box, self.ctx.screenshot_size, "name tag",
                                               min_w=10, min_h=6, max_fraction=0.05)
        if problem:
            self.error(problem)
            return
        try:
            logic.save_crop(self.image, box, self.profile.templates_dir / "player.png")
        except Exception as e:
            log.exception("saving name tag failed")
            self.error(f"Couldn't save the picture: {e}")
            return
        self._redraw_player()
        self.info("Name tag saved. Click Next.")

    def _delete_player(self, p: Path):
        try:
            p.unlink()
        except OSError as e:
            self.error(f"Couldn't delete: {e}")
            return
        self._redraw_player()

    # --------------------------------------------------------- 7. HP / MP
    def _build_bars(self, f):
        if not self._need_screenshot(f):
            return
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Now drawing:").pack(side="left")
        ttk.Radiobutton(bar, text="HP bar (red)", value="hp", variable=self.bar_target).pack(side="left", padx=4)
        ttk.Radiobutton(bar, text="MP bar (blue)", value="mp", variable=self.bar_target).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear HP", command=lambda: self._clear_bar("hp")).pack(side="left", padx=(16, 2))
        ttk.Button(bar, text="Clear MP", command=lambda: self._clear_bar("mp")).pack(side="left", padx=2)
        self._retake_button(bar, self._redraw_bars).pack(side="left", padx=(16, 0))
        self.canvas = ImageCanvas(f, self.canvas_w, self.canvas_h - 40)
        self.canvas.pack()
        self.canvas.mode = "box"
        self.canvas.on_box = self._got_bar
        self.fill_lbl = ttk.Label(f, font=("Segoe UI", 10))
        self.fill_lbl.pack(anchor="w", pady=4)
        self._redraw_bars()

    def _redraw_bars(self):
        self.canvas.overlays = self._overlays()
        self.canvas.show(self.image)
        c = self.s.calibration
        parts = []
        try:
            from maplebot.perception.calib import bar_fill
        except Exception:
            bar_fill = None
        for name, box in (("HP", c.hp_bar_box), ("MP", c.mp_bar_box)):
            if not box:
                parts.append(f"{name}: not set")
                continue
            v = bar_fill(self.image, box) if bar_fill else None
            parts.append(f"{name} reads {logic.format_percent(v)} full" if v is not None
                         else f"{name}: can't read it -- draw the box again")
        self.fill_lbl.config(text="   |   ".join(parts) +
                             "\nCheck these match the game (e.g. full HP should read about 100%).")

    def _got_bar(self, box):
        which = self.bar_target.get()
        name = "HP bar" if which == "hp" else "MP bar"
        if box is None:
            self.error(f"That was a click. Drag a box along the {name}.")
            return
        problem = logic.validate_bar_box(box, self.ctx.screenshot_size, name)
        if problem:
            self.error(problem)
            return
        if which == "hp":
            self.s.calibration.hp_bar_box = list(box)
            if not self.s.calibration.mp_bar_box:
                self.bar_target.set("mp")
        else:
            self.s.calibration.mp_bar_box = list(box)
        self._redraw_bars()
        self.info(f"{name} saved.")

    def _clear_bar(self, which):
        if which == "hp":
            self.s.calibration.hp_bar_box = None
        else:
            self.s.calibration.mp_bar_box = None
        self._redraw_bars()

    # ---------------------------------------------------------- 8. patrol
    def _build_patrol(self, f):
        c = self.s.calibration
        if not c.minimap_box:
            ttk.Label(f, text="Set the minimap box first (step 3).").pack(pady=20)
            return
        grid = ttk.Frame(f)
        grid.pack(anchor="w", pady=6)
        self.left_var = tk.StringVar(value=_frac(self.ctx.patrol_left_set if self.ctx.patrol_left_set is not None else c.patrol_left))
        self.right_var = tk.StringVar(value=_frac(self.ctx.patrol_right_set if self.ctx.patrol_right_set is not None else c.patrol_right))
        ttk.Button(grid, text="Set left", width=12, command=lambda: self._set_edge("left")).grid(row=0, column=0, pady=3)
        ttk.Label(grid, text="Left end:").grid(row=0, column=1, padx=(12, 4))
        le = ttk.Spinbox(grid, from_=0.0, to=1.0, increment=0.01, width=6, textvariable=self.left_var,
                         command=self._edges_typed)
        le.grid(row=0, column=2)
        ttk.Button(grid, text="Set right", width=12, command=lambda: self._set_edge("right")).grid(row=1, column=0, pady=3)
        ttk.Label(grid, text="Right end:").grid(row=1, column=1, padx=(12, 4))
        re_ = ttk.Spinbox(grid, from_=0.0, to=1.0, increment=0.01, width=6, textvariable=self.right_var,
                          command=self._edges_typed)
        re_.grid(row=1, column=2)
        for w in (le, re_):
            w.bind("<FocusOut>", lambda _e: self._edges_typed())
            Tooltip(w, "Position across the minimap: 0 = far left, 1 = far right. "
                       "You can nudge it here; move the ends inward a little if the bot walks off a ledge.")
        ttk.Label(f, foreground="#555555", wraplength=self.canvas_w, justify="left",
                  text="Pick a flat stretch of one platform. Stop a few steps before ledges and "
                       "walls. The pink lines below show the training area on the minimap.").pack(anchor="w", pady=6)
        self.canvas = ImageCanvas(f, self.canvas_w, max(160, self.canvas_h - 160))
        self.canvas.pack()
        self._redraw_patrol()

    def _edges_typed(self):
        for side, var in (("left", self.left_var), ("right", self.right_var)):
            try:
                v = float(var.get().replace(",", "."))
            except ValueError:
                continue
            v = min(1.0, max(0.0, v))
            setattr(self.ctx, f"patrol_{side}_set", v)
            setattr(self.s.calibration, f"patrol_{side}", v)
        self._redraw_patrol()
        p = logic.validate_patrol(self.s.calibration.patrol_left, self.s.calibration.patrol_right)
        if p:
            self.error(p)
        else:
            self.msg.config(text="")

    def _set_edge(self, side: str):
        def got(image):
            from maplebot.perception.calib import minimap_player_position
            c = self.s.calibration
            pos = minimap_player_position(image, c.minimap_box, c.player_dot_hsv_low, c.player_dot_hsv_high)
            self.patrol_image = image
            if pos is None:
                self._redraw_patrol()
                self.error("Couldn't see your yellow dot on the minimap. Make sure the minimap is "
                           "open and the game window hasn't moved, then try again (or redo steps 3-4).")
                return
            x = round(float(pos[0]), 3)
            setattr(self.ctx, f"patrol_{side}_set", x)
            setattr(c, f"patrol_{side}", x)
            (self.left_var if side == "left" else self.right_var).set(_frac(x))
            self._redraw_patrol()
            l, r = self.ctx.patrol_left_set, self.ctx.patrol_right_set
            problem = logic.validate_patrol(l, r) if (l is not None and r is not None) else None
            if problem:
                self.error(problem)
            elif side == "left" and r is None:
                self.info(f"Left end set at {x:.2f}. Now walk to the RIGHT end and click Set right.")
            elif side == "right" and l is None:
                self.info(f"Right end set at {x:.2f}. Now walk to the LEFT end and click Set left.")
            else:
                self.info(f"Training area: {l:.2f} to {r:.2f}. Click Next.")
        self.take_screenshot(got)

    def _redraw_patrol(self):
        img = self.patrol_image if self.patrol_image is not None else self.image
        if img is None:
            self.canvas.show(None)
            return
        c = self.s.calibration
        x, y, w, h = c.minimap_box
        ovs = [("box", tuple(c.minimap_box), CYAN, "")]
        for frac in (c.patrol_left, c.patrol_right):
            ovs.append(("vline", x + frac * w, (y, y + h), MAGENTA))
        pos = None
        try:
            from maplebot.perception.calib import minimap_player_position
            pos = minimap_player_position(img, c.minimap_box, c.player_dot_hsv_low, c.player_dot_hsv_high)
        except Exception:
            log.exception("dot lookup failed")
        if pos is not None:
            ovs.append(("dot", logic.minimap_dot_to_image(c.minimap_box, pos), GREEN))
        self.canvas.overlays = ovs
        m = 6
        self.canvas.show(img, region=[x - m, y - m, w + 2 * m, h + 2 * m], zoom=True)

    # ------------------------------------------------------------ 9. keys
    def _build_keys(self, f):
        k, sk = self.s.keys, self.s.skill
        grid = ttk.Frame(f)
        grid.pack(anchor="w", pady=4)
        rows = [("attack", "Attack skill key", False, "The key your attack skill is on, e.g. Ctrl."),
                ("jump", "Jump key", False, "Your jump key, e.g. Alt."),
                ("hp_potion", "HP potion key (optional)", True, "Leave as (none) to not drink HP potions."),
                ("mp_potion", "MP potion key (recommended)", True,
                 "Magicians run out of MP fast. Set this (and the MP bar in step 7) so the bot drinks MP potions.")]
        for r, (field, label, optional, tip) in enumerate(rows):
            lbl = ttk.Label(grid, text=label)
            lbl.grid(row=r, column=0, sticky="w", pady=4, padx=(0, 12))
            Tooltip(lbl, tip)
            picker = KeyPicker(grid, value=getattr(k, field), optional=optional,
                               on_change=lambda v, field=field: setattr(self.s.keys, field, v))
            picker.error_cb = self.error
            picker.grid(row=r, column=1, sticky="w")
        ttk.Label(f, foreground="#555555",
                  text="Walking uses the arrow keys. Change that in Settings if yours differ.").pack(anchor="w", pady=(0, 10))

        sf = ttk.LabelFrame(f, text="Your attack skill", padding=8)
        sf.pack(fill="x", pady=4)
        self.range_var = tk.IntVar(value=int(sk.range_px))
        ttk.Label(sf, text="Reach (pixels left/right of you):").grid(row=0, column=0, sticky="w")
        range_lbl = ttk.Label(sf, text=f"{sk.range_px} px", width=8)

        def on_range(v):
            val = int(float(v))
            self.s.skill.range_px = val
            range_lbl.config(text=f"{val} px")
        sc = ttk.Scale(sf, from_=50, to=900, orient="horizontal", length=320,
                       variable=self.range_var, command=on_range)
        sc.grid(row=0, column=1, padx=8)
        range_lbl.grid(row=0, column=2)
        ttk.Label(sf, foreground="#555555",
                  text="Start around 300 px and check the green box in the live preview: it should "
                       "end where your Magic Claw stops hitting. A bit short is safer than too long.").grid(row=1, column=0, columnspan=3, sticky="w")

        ttk.Label(sf, text="Time between casts (seconds):").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.cd_var = tk.StringVar(value=f"{sk.cooldown_s:.2f}")
        cd = ttk.Spinbox(sf, from_=0.1, to=3.0, increment=0.05, width=6, textvariable=self.cd_var,
                         command=self._cd_changed)
        cd.grid(row=2, column=1, sticky="w", padx=8, pady=(8, 0))
        cd.bind("<FocusOut>", lambda _e: self._cd_changed())
        ttk.Label(sf, foreground="#555555",
                  text="Magic Claw: ~0.6-0.7 s.  Energy Bolt: ~0.7 s.  Too short wastes key presses; "
                       "too long attacks slowly.").grid(row=3, column=0, columnspan=3, sticky="w")

    def _cd_changed(self):
        try:
            v = float(self.cd_var.get().replace(",", "."))
        except ValueError:
            self.error("Time between casts must be a number like 0.65.")
            return
        if not (0.05 <= v <= 10):
            self.error("Time between casts should be between 0.05 and 10 seconds.")
            return
        self.s.skill.cooldown_s = v
        self.msg.config(text="")

    # --------------------------------------------------------- 10. summary
    def _build_summary(self, f):
        if hasattr(self, "cd_var"):
            try:
                self._cd_changed()
            except tk.TclError:
                pass
        box = ttk.LabelFrame(f, text="Your setup", padding=10)
        box.pack(fill="x", pady=6)
        for line in logic.summary_lines(self.ctx, self.profile.player_template() is not None):
            ttk.Label(box, text=line).pack(anchor="w", pady=1)
        problem = logic.step_problem("summary", self.ctx)
        if problem:
            ttk.Label(f, foreground="#c00000", text=problem, wraplength=self.canvas_w).pack(anchor="w", pady=6)
        else:
            ttk.Label(f, foreground="#1a6e1a",
                      text="Everything needed is set. Click Save.").pack(anchor="w", pady=6)


def _frac(v) -> str:
    return f"{float(v):.2f}"


def _overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
