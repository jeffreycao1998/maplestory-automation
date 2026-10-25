"""
Setup wizard: one window, Back / Next, eleven steps (see ``logic.WIZARD_STEPS``).

Everything the user selects on a screenshot is converted back to real game-client
pixels by ``logic.View``. Mob / name-tag crops are written straight to
``profile/templates/``; settings are written only when the user clicks Save.
"""
from __future__ import annotations

import logging
import time
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
                 hide_also: Optional[List[tk.Misc]] = None,
                 kept_from: Optional[str] = None):
        """``kept_from``: name of the map this one was created from (its window title,
        dot colour, name tag and HP/MP bars were copied), so those steps can be kept."""
        super().__init__(master)
        try:
            self.map_name = profile.current_map_name()
        except Exception:
            self.map_name = None
        self.title(logic.wizard_title(self.map_name))
        self.master_root = master
        self.profile = profile
        self.on_saved = on_saved
        self.hide_also = hide_also or [master]
        self.ctx = WizardContext(settings=Settings.from_dict(settings.to_dict()))
        self.ctx.n_mob_templates = len(profile.mob_templates())
        self.ctx.has_player_template = profile.player_template() is not None
        # A map that was set up before keeps its patrol edges unless the user changes them
        # (per map: profile paths always point at the current map).
        map_set_up = _safe(profile.map_is_set_up, False)
        self.ctx.patrol_preset = map_set_up and bool(settings.calibration.minimap_box)
        self.kept_from = kept_from
        self.kept = logic.kept_steps(self.ctx.settings, self.ctx.has_player_template,
                                     map_set_up, kept_from)
        self.redo: set = set()
        self.session_boxes: list = []  # mob boxes drawn on the current screenshot
        self.image = None             # current full-res BGR screenshot
        self.patrol_image = None      # last screenshot taken in the patrol step
        self.step = 0
        self.busy = False
        self._hud_preview = None
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
        self._stop_hud_preview()
        self.step = max(0, min(i, len(WIZARD_STEPS) - 1))
        st = WIZARD_STEPS[self.step]
        self.step_lbl.config(text=logic.step_label(self.step, self.map_name))
        self.dots.config(text="  ".join("●" if j <= self.step else "○" for j in range(len(WIZARD_STEPS))))
        self.instr.config(text=st.instruction)
        self.msg.config(text="")
        for w in self.body.winfo_children():
            w.destroy()
        self._thumbs = []
        if st.key == "dot":
            self._refresh_dot()
        kept = self._is_kept(st.key)
        if kept:
            self._kept_banner(self.body, st.key)
        getattr(self, f"_build_{st.key}")(self.body)
        if kept and st.key in ("dot", "player", "bars") and hasattr(self, "canvas"):
            self.canvas.mode = None          # look, don't touch -- until Redo
        elif (st.key == "dot" and st.key in self.kept and st.key not in self.redo
              and self.ctx.dot_pos is None and self.image is not None and self.s.calibration.minimap_box):
            self.info(f"The dot colour from {self.kept_from} doesn't find you on this map. "
                      "Click your yellow dot.", "#a05a00")
        self.back_btn.state(["!disabled"] if self.step > 0 else ["disabled"])
        if st.optional:
            self.skip_btn.pack(side="right", padx=6)
        else:
            self.skip_btn.pack_forget()
        self.next_btn.config(text="Save" if st.key == "summary" else "Next >")

    # ------------------------------------------------------------ kept steps
    def _is_kept(self, key: str) -> bool:
        if key not in self.kept or key in self.redo:
            return False
        c = self.s.calibration
        if key == "dot":
            return self.ctx.dot_pos is not None
        if key == "player":
            return self.profile.player_template() is not None
        if key == "bars":
            return bool(c.hp_bar_box)
        return True

    def _kept_banner(self, f, key: str):
        box = ttk.Frame(f, padding=(8, 6))
        box.pack(fill="x", pady=(0, 8))
        ttk.Label(box, text=logic.kept_message(self.kept_from or "your other map"),
                  foreground="#1a5e9a", font=("Segoe UI", 10, "bold"),
                  wraplength=self.canvas_w - 120, justify="left").pack(side="left")
        if key in ("dot", "player", "bars"):
            ttk.Button(box, text="Redo", command=lambda: self._redo(key)).pack(side="right")

    def _redo(self, key: str):
        self.redo.add(key)
        self.show_step(self.step)

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
        if self.key() == "patrol":
            c = self.s.calibration
            warn = logic.patrol_y_warning(self.ctx.patrol_left_y, self.ctx.patrol_right_y,
                                          logic.tolerance_above(c))
            if warn and not messagebox.askyesno("Different bridges?", warn + "\n\nContinue anyway?",
                                                parent=self):
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
        new_name = logic.clean_map_name(self.name_var.get()) if hasattr(self, "name_var") else None
        if new_name and self.map_name and new_name != self.map_name:
            try:
                self.profile.rename_map(self.profile.current_map, new_name)
                self.map_name = new_name
            except Exception as e:          # MapError: friendly text
                self.error(logic.friendly_error(e)[1])
                return
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

    def _stop_hud_preview(self):
        reader = getattr(self,"_hud_preview",None)
        if reader is not None:
            reader.close()
            self._hud_preview = None

    def destroy(self):
        self._stop_hud_preview()
        super().destroy()

    def _build_supplies(self, f):
        if not self._need_screenshot(f):
            return
        options = {"HP potion count":"hp_stock_box", "MP potion count":"mp_stock_box",
                   "Meso balance":"mesos_box", "Notifications":"notification_box"}
        self.hud_options = options
        self.hud_target = tk.StringVar(value="HP potion count")
        bar = ttk.Frame(f); bar.pack(fill="x",pady=4)
        ttk.Combobox(bar,textvariable=self.hud_target,values=list(options),state="readonly",width=24).pack(side="left")
        ttk.Button(bar,text="Clear region",command=self._clear_hud_region).pack(side="left",padx=6)
        ttk.Button(bar,text="Test OCR",command=self._test_hud).pack(side="left")
        self._retake_button(bar,self._redraw_hud).pack(side="left",padx=6)
        self.hud_enable = tk.BooleanVar(value=self.s.ocr.enabled)
        ttk.Checkbutton(f,text="Enable local HUD OCR (install the OCR extra first)",variable=self.hud_enable,
                        command=lambda:setattr(self.s.ocr,"enabled",self.hud_enable.get())).pack(anchor="w")
        self.canvas = ImageCanvas(f,self.canvas_w,self.canvas_h-60);self.canvas.pack()
        self.canvas.mode="box";self.canvas.on_box=self._got_hud_region
        self._redraw_hud()

    def _redraw_hud(self):
        self.canvas.overlays = self._overlays()
        for label,field in self.hud_options.items():
            box = getattr(self.s.calibration,field)
            if box:
                self.canvas.overlays.append(("box",box,CYAN,label))
        self.canvas.show(self.image)

    def _got_hud_region(self,box):
        if box is None or box[2]<4 or box[3]<6 or box[2]>1000 or box[3]>300:
            self.error("Draw a small box around the complete count or message text.")
            return
        setattr(self.s.calibration,self.hud_options[self.hud_target.get()],list(box))
        self._redraw_hud()

    def _clear_hud_region(self):
        setattr(self.s.calibration,self.hud_options[self.hud_target.get()],None)
        self._redraw_hud()

    def _test_hud(self):
        self._stop_hud_preview()
        from maplebot.perception.hud import HUDReader
        self._hud_preview = HUDReader(self.s)
        deadline = time.monotonic()+4
        def poll():
            reader = self._hud_preview
            if reader is None or not self.winfo_exists() or self.key()!="supplies":
                self._stop_hud_preview();return
            snapshot = reader.poll(self.image,time.monotonic())
            if any(key in snapshot for key in ("hp_stock","mp_stock","mesos")):
                self.info(f"Confirmed readings from this screenshot: {snapshot}. Check against the game before enabling.")
                self._stop_hud_preview();return
            if reader.error or time.monotonic()>=deadline:
                self.error(reader.error or "No confirmed reading. Rebox the complete digits and retest; blank is not zero.")
                self._stop_hud_preview();return
            self.after(100,poll)
        poll()

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
        self.s.calibration.reference_size = [w,h]
        self.session_boxes = []
        self._sprite_boxes = []             # matches belonged to the previous screenshot
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
        ttk.Button(bar, text="Auto-detect minimap", command=lambda: self._auto_regions("minimap")).pack(side="left", padx=8)
        self.canvas = ImageCanvas(f, self.canvas_w, self.canvas_h)
        self.canvas.pack()
        self.canvas.mode = "box"
        self.canvas.on_box = self._got_minimap
        self._redraw_minimap()

    def _redraw_minimap(self):
        self.canvas.overlays = self._overlays()
        self.canvas.show(self.image)

    def _auto_regions(self, which):
        from maplebot.perception.autosetup import suggest_regions
        proposals = suggest_regions(self.image)
        fields = ("minimap_box",) if which == "minimap" else ("hp_bar_box", "mp_bar_box")
        if not all(name in proposals for name in fields):
            self.error("No confident match. Draw the region manually; the full bar frame must be visible.")
            return
        for name in fields:
            setattr(self.s.calibration, name, proposals[name])
        if which == "minimap":
            self._refresh_dot()
            self._redraw_minimap()
        else:
            self._redraw_bars()
        self.info("Suggested regions shown. Check the boxes and readings; drag to correct before saving.")

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

        # The sampler's own range is tried first and used whenever it finds the dot at the
        # click; the stricter / default ranges are only a safety net.
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
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(0, 6))
        ttk.Button(row, text="Download sprites for this map…", command=self._download_sprites).pack(side="left")
        ttk.Label(row, text="  Automatic monster poses and known drops. Drawing boxes is optional.",
                  foreground="#555555").pack(side="left")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        self._retake_button(bar, self._redraw_mobs).pack(side="left")
        self.crop_kind = tk.StringVar(value="mob")
        ttk.Radiobutton(bar, text="Monster", variable=self.crop_kind, value="mob").pack(side="left", padx=4)
        ttk.Radiobutton(bar, text="Drop / mesos (optional)", variable=self.crop_kind, value="item").pack(side="left", padx=4)
        ttk.Radiobutton(bar, text="Stop alert", variable=self.crop_kind, value="alert").pack(side="left", padx=4)
        ttk.Label(bar, foreground="#555555",
                  text="  Optional: save an example if downloaded sprites miss a monster.").pack(side="left")
        self.canvas = ImageCanvas(f, self.canvas_w, self.canvas_h - 90)
        self.canvas.pack()
        self.canvas.mode = "box"
        self.canvas.on_box = self._got_mob
        self.thumb_row = ttk.Frame(f)
        self.thumb_row.pack(fill="x", pady=6)
        self._redraw_mobs()
        self._refresh_templates()

    def _redraw_mobs(self):
        boxes = [("box", b, ORANGE, "example") for b in self.session_boxes]
        boxes += [("box", b, GREEN, "sprite match") for b in getattr(self, "_sprite_boxes", [])]
        self.canvas.overlays = self._overlays(boxes)
        self.canvas.show(self.image)

    def _download_sprites(self):
        window = getattr(self, "sprite_download_win", None)
        if window is not None and window.winfo_exists():
            window.lift()
            return
        from .sprite_download import SpriteDownloadWindow
        self.sprite_download_win = SpriteDownloadWindow(self, self._sprites_installed)

    def _sprites_installed(self, manifest, matches):
        self._sprite_boxes = [(int(m.x-m.w/2), int(m.y-m.h/2), int(m.w), int(m.h)) for m in matches]
        self._refresh_templates()
        self._redraw_mobs()
        self.info(f"Downloaded sprites for {manifest['map_name']}. "
                  f"{len(matches)} matches highlighted in green. No mob boxes required; click Next. "
                  "Check Observe only before starting with keys.")

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
        is_item = self.crop_kind.get() == "item"
        is_alert = self.crop_kind.get() == "alert"
        if is_alert:
            self.profile.alerts_dir.mkdir(parents=True, exist_ok=True)
            i = 1
            while (self.profile.alerts_dir / f"alert_{i:03d}.png").exists():
                i += 1
            path = self.profile.alerts_dir / f"alert_{i:03d}.png"
        elif is_item:
            i = 1
            while (self.profile.templates_dir / f"item_{i:03d}.png").exists():
                i += 1
            path = self.profile.templates_dir / f"item_{i:03d}.png"
        else:
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
        if is_alert:
            self.info(f"Saved {path.name}. Matching this prompt pauses the bot for you to handle manually.")
            return
        if is_item:
            self.info(f"Saved {path.name}. The bot will look for drops matching this picture.")
            return
        n = self.ctx.n_mob_templates
        tip = " Add 1-2 more in different poses (walking, turned) for best results." if n < 3 else ""
        self.info(f"Saved {path.name}. You have {n} mob picture{'s' if n != 1 else ''}.{tip}")

    def _refresh_templates(self):
        for w in self.thumb_row.winfo_children():
            w.destroy()
        self._thumbs = []
        paths = self.profile.mob_templates()
        self.ctx.n_mob_templates = len(paths)
        paths += self.profile.item_templates() + self.profile.alert_templates()
        if not paths:
            ttk.Label(self.thumb_row, text="No mob pictures yet.", foreground="#777777").pack(side="left")
            return
        ttk.Label(self.thumb_row, text="Saved:").pack(side="left", padx=(0, 6))
        for p in paths[:10]:
            self._thumb(self.thumb_row, p, lambda p=p: self._delete_template(p))
        if len(paths) > 10:
            ttk.Label(self.thumb_row, text=f"+ {len(paths)-10} more references").pack(side="left")

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
        self.ctx.has_player_template = p is not None
        if p:
            ttk.Label(self.thumb_row, text="Saved name tag:").pack(side="left", padx=(0, 6))
            self._thumb(self.thumb_row, p, lambda: self._delete_player(p))
        else:
            ttk.Label(self.thumb_row, foreground="#777777",
                      text="No name tag saved yet (required).").pack(side="left")

    def _got_player(self, box):
        if box is None:
            self.error("That was a click. Drag a box around your name tag.")
            return
        problem = logic.validate_box_on_screen(box, self.ctx.screenshot_size, "name tag",
                                               min_w=10, min_h=6, max_fraction=0.05)
        if problem:
            self.error(problem)
            return
        mm = self.s.calibration.minimap_box
        if mm and _overlaps(box, mm):
            self.error("That box is on the minimap. Draw it around the name tag under your character.")
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
        self.ctx.has_player_template = False
        self.redo.add("player")
        self.show_step(self.step)

    # --------------------------------------------------------- 7. HP / MP
    def _build_bars(self, f):
        if not self._need_screenshot(f):
            return
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Now drawing:").pack(side="left")
        ttk.Radiobutton(bar, text="HP bar (red, required)", value="hp", variable=self.bar_target).pack(side="left", padx=4)
        ttk.Radiobutton(bar, text="MP bar (blue, recommended)", value="mp", variable=self.bar_target).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear HP", command=lambda: self._clear_bar("hp")).pack(side="left", padx=(16, 2))
        ttk.Button(bar, text="Clear MP", command=lambda: self._clear_bar("mp")).pack(side="left", padx=2)
        self._retake_button(bar, self._redraw_bars).pack(side="left", padx=(16, 0))
        ttk.Button(bar, text="Auto-detect bars", command=lambda: self._auto_regions("bars")).pack(side="left", padx=8)
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
                parts.append("HP: not set yet (required)" if name == "HP"
                             else "MP: not set (recommended -- magicians run out of MP fast)")
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
                  text="Pick one bridge, set both edges on that same bridge, a little in from its "
                       "ends (stop a few steps before ledges and walls). The pink lines below show "
                       "the training area on the minimap.").pack(anchor="w", pady=6)
        self.canvas = ImageCanvas(f, self.canvas_w, max(160, self.canvas_h - 160))
        self.canvas.pack()
        self._redraw_patrol()
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=4)
        ttk.Button(row, text="Choose safe spot", command=self._choose_safe_spot).pack(side="left")
        ttk.Button(row, text="Clear safe spot", command=self._clear_safe_spot).pack(side="left", padx=8)
        ttk.Label(row, text="Pick a known platform away from mobs and ledges.").pack(side="left")

    def _choose_safe_spot(self):
        self.canvas.mode = "click"
        self.canvas.on_click = self._got_safe_spot
        self.info("Click the minimap where you want to stop when supplies are low.")

    def _got_safe_spot(self, x, y):
        try:
            self.s.calibration.safe_spot = logic.pick_safe_spot(self.s, (x,y))
        except ValueError as exc:
            self.error(str(exc))
            return
        self.canvas.mode = None
        self._redraw_patrol()
        self.info("Safe spot marked in white. Set stock estimates and reserve thresholds in Settings → Safe retreat.")

    def _clear_safe_spot(self):
        self.s.calibration.safe_spot = None
        self.canvas.mode = None
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
            setattr(self.ctx, f"patrol_{side}_y", round(float(pos[1]), 3))
            setattr(c, f"patrol_{side}", x)
            previous_y = c.patrol_y
            c.patrol_y = logic.combine_patrol_y(self.ctx.patrol_left_y, self.ctx.patrol_right_y, c.patrol_y)
            (self.left_var if side == "left" else self.right_var).set(_frac(x))
            self._redraw_patrol()
            l, r = self.ctx.patrol_left_set, self.ctx.patrol_right_set
            problem = logic.validate_patrol(l, r) if (l is not None and r is not None) else None
            bridge = logic.patrol_y_warning(self.ctx.patrol_left_y, self.ctx.patrol_right_y,
                                            logic.tolerance_above(c), previous_y)
            if problem:
                self.error(problem)
            elif bridge:
                self.error(bridge)
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
        band = logic.patrol_band(c)
        if band is not None:
            y0, y1 = y + band[0] * h, y + band[1] * h
        else:
            y0, y1 = y, y + h
        for frac in (c.patrol_left, c.patrol_right):
            ovs.append(("vline", x + frac * w, (y0, y1), MAGENTA))
        pos = None
        try:
            from maplebot.perception.calib import minimap_player_position
            pos = minimap_player_position(img, c.minimap_box, c.player_dot_hsv_low, c.player_dot_hsv_high)
        except Exception:
            log.exception("dot lookup failed")
        if pos is not None:
            ovs.append(("dot", logic.minimap_dot_to_image(c.minimap_box, pos), GREEN))
        if c.safe_spot is not None:
            ovs.append(("dot", logic.minimap_dot_to_image(c.minimap_box,c.safe_spot), "#ffffff"))
        for pl in c.platforms:
            ovs.append(("polyline", [logic.minimap_dot_to_image(c.minimap_box, point)
                                     for point in pl["points"]], CYAN))
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
                ("pickup", "Pickup key", True, "Collect drops and mesos; default Z. Empty disables pickup."),
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
        if self.map_name:
            row = ttk.Frame(f)
            row.pack(anchor="w", pady=(0, 6))
            ttk.Label(row, text="Map name:").pack(side="left")
            self.name_var = tk.StringVar(value=self.map_name)
            e = ttk.Entry(row, textvariable=self.name_var, width=32)
            e.pack(side="left", padx=8)
            Tooltip(e, "Shown in the Map list in the main window. Example: The Tree That Grew II")
        box = ttk.LabelFrame(f, text="Your setup", padding=10)
        box.pack(fill="x", pady=6)
        self.ctx.has_player_template = self.profile.player_template() is not None
        for line in logic.summary_lines(self.ctx):
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


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        log.warning("profile call failed", exc_info=True)
        return default
