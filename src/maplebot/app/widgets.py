"""
Small Tk building blocks shared by the main window, wizard and settings form.

Tk code only; the maths lives in ``logic.py`` (testable without a display).
"""
from __future__ import annotations

import logging
import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Sequence, Tuple

from . import keymap
from .logic import View

log = logging.getLogger(__name__)

CYAN = "#00c8ff"
GREEN = "#22dd55"
ORANGE = "#ff9900"
RED = "#ff3344"
MAGENTA = "#ff44ff"
YELLOW = "#ffee00"


# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------

class Tooltip:
    """Hover help for any widget."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 400, wrap: int = 320):
        self.widget, self.text, self.delay, self.wrap = widget, text, delay_ms, wrap
        self._after: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        if text:
            widget.bind("<Enter>", self._schedule, add="+")
            widget.bind("<Leave>", self._hide, add="+")
            widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self):
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 16
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(tip, text=self.text, justify="left", wraplength=self.wrap,
                     background="#ffffe0", relief="solid", borderwidth=1,
                     padx=6, pady=4).pack()
            tip.wm_geometry(f"+{x}+{y}")
            self._tip = tip
        except tk.TclError:
            self._tip = None

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def bgr_to_photo(image, size: Optional[Tuple[int, int]] = None, region: Optional[Sequence[int]] = None,
                 nearest: bool = False):
    """numpy BGR image (optionally a region of it) -> ImageTk.PhotoImage at ``size``."""
    from PIL import Image, ImageTk

    img = image
    if region is not None:
        x, y, w, h = region
        img = img[y:y + h, x:x + w]
    pil = Image.fromarray(img[:, :, ::-1].copy())
    if size and (pil.width, pil.height) != tuple(size):
        resample = Image.NEAREST if nearest else Image.BILINEAR
        pil = pil.resize((max(1, int(size[0])), max(1, int(size[1]))), resample)
    return ImageTk.PhotoImage(pil)


def file_thumbnail(path, max_side: int = 72):
    from PIL import Image, ImageTk

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        return ImageTk.PhotoImage(im)


# ---------------------------------------------------------------------------
# Image canvas with box-drag / click selection
# ---------------------------------------------------------------------------

class ImageCanvas(ttk.Frame):
    """Shows (part of) a screenshot scaled to fit; reports drags/clicks in image pixels.

    mode: "box" -> on_box(box); "line" -> on_line(start, end); "click" -> on_click(x, y).
    None is view only. All callbacks receive original image coordinates.
    Overlays (boxes / markers in image coords) are redrawn on every refresh.
    """

    def __init__(self, master, max_w: int = 900, max_h: int = 480):
        super().__init__(master)
        self.max_w, self.max_h = max_w, max_h
        self.canvas = tk.Canvas(self, width=max_w, height=max_h, background="#202020",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.image = None
        self.view: Optional[View] = None
        self.mode: Optional[str] = None
        self.on_box: Optional[Callable] = None
        self.on_click: Optional[Callable] = None
        self.on_line: Optional[Callable] = None
        self._photo = None
        self._drag_start = None
        self._drag_id = None
        self.overlays: List[Tuple] = []    # ("box", box, colour, label) | ("dot", (x, y), colour) | ("vline", x, (y0, y1), colour)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)

    def show(self, image, region: Optional[Sequence[int]] = None, zoom: bool = False):
        self.image = image
        if image is None:
            self.view = None
            self._photo = None
            self.canvas.delete("all")
            self.canvas.config(width=self.max_w, height=self.max_h)
            self.canvas.create_text(self.max_w // 2, self.max_h // 2, fill="#bbbbbb",
                                    text="No screenshot yet", font=("Segoe UI", 14))
            return
        ih, iw = image.shape[:2]
        self.view = View.fit(iw, ih, self.max_w, self.max_h, region=region, zoom=zoom)
        v = self.view
        cw, ch = v.canvas_size
        self._photo = bgr_to_photo(image, (cw, ch), region=(v.ox, v.oy, v.w, v.h), nearest=v.scale > 1.5)
        self.canvas.config(width=cw, height=ch)
        self.redraw()

    def redraw(self):
        self.canvas.delete("all")
        if self._photo is None or self.view is None:
            return
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        v = self.view
        for ov in self.overlays:
            kind = ov[0]
            if kind == "box":
                _, box, colour, label = ov
                x0, y0, x1, y1 = v.box_to_canvas(box)
                self.canvas.create_rectangle(x0, y0, x1, y1, outline=colour, width=2)
                if label:
                    self.canvas.create_text(x0 + 3, y0 - 2, anchor="sw", text=label, fill=colour,
                                            font=("Segoe UI", 9, "bold"))
            elif kind == "dot":
                _, (x, y), colour = ov
                cx, cy = v.to_canvas(x + 0.5, y + 0.5)
                r = max(6, int(3 * v.scale))
                self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=colour, width=3)
            elif kind == "polyline":
                _, points, colour = ov
                if len(points) >= 2:
                    coords = [coord for point in points for coord in v.to_canvas(*point)]
                    self.canvas.create_line(*coords, fill=colour, width=2)
            elif kind == "vline":
                _, x, (y0, y1), colour = ov
                cx0, cy0 = v.to_canvas(x, y0)
                _, cy1 = v.to_canvas(x, y1)
                self.canvas.create_line(cx0, cy0, cx0, cy1, fill=colour, width=2)

    def _press(self, e):
        if self.view is None or self.mode is None:
            return
        self._drag_start = (e.x, e.y)
        if self.mode == "box":
            self._drag_id = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=YELLOW,
                                                         width=2, dash=(4, 2))
        elif self.mode == "line":
            self._drag_id = self.canvas.create_line(e.x, e.y, e.x, e.y, fill=YELLOW,
                                                   width=2, dash=(4, 2))

    def _motion(self, e):
        if self._drag_id is not None and self._drag_start:
            x0, y0 = self._drag_start
            self.canvas.coords(self._drag_id, x0, y0, e.x, e.y)

    def _release(self, e):
        start, self._drag_start = self._drag_start, None
        if self._drag_id is not None:
            self.canvas.delete(self._drag_id)
            self._drag_id = None
        if start is None or self.view is None:
            return
        if self.mode == "box":
            box = self.view.drag_to_box(start[0], start[1], e.x, e.y)
            if self.on_box:
                self.on_box(box)        # None = too small (treated as a mis-click)
        elif self.mode == "line":
            if self.on_line:
                self.on_line(self.view.to_image(*start), self.view.to_image(e.x, e.y))
        elif self.mode == "click":
            x, y = self.view.to_image(e.x, e.y)
            if self.on_click:
                self.on_click(x, y)


# ---------------------------------------------------------------------------
# Taking a screenshot of the game with the app out of the way
# ---------------------------------------------------------------------------

def capture_game(root: tk.Misc, hide: Sequence[tk.Misc], window_title: str, seconds: int,
                 on_done: Callable[[object, Optional[BaseException]], None]) -> None:
    """Hide ``hide`` windows, count down ``seconds`` in a tiny corner overlay, grab
    the game's client area, restore the windows, then call ``on_done(image, error)``.

    Never blocks the Tk thread (uses ``after``).
    """
    hidden = []
    for w in hide:
        try:
            if w.winfo_exists() and w.state() != "withdrawn":
                w.withdraw()
                hidden.append(w)
        except tk.TclError:
            pass

    overlay = tk.Toplevel(root)
    overlay.wm_overrideredirect(True)
    overlay.attributes("-topmost", True)
    lbl = tk.Label(overlay, text="", font=("Segoe UI", 16, "bold"), background="#222222",
                   foreground="#ffee00", padx=16, pady=10)
    lbl.pack()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    overlay.wm_geometry(f"+{max(0, sw - 380)}+{max(0, sh - 140)}")

    def restore():
        for w in hidden:
            try:
                w.deiconify()
                w.lift()
            except tk.TclError:
                pass

    def grab():
        image, err = None, None
        try:
            from maplebot.perception import ScreenCapture
            cap = ScreenCapture(window_title)
            try:
                image = cap.grab().image
            finally:
                cap.close()
        except Exception as e:     # shown to the user by on_done
            log.warning("screenshot failed", exc_info=True)
            err = e
        try:
            overlay.destroy()
        except tk.TclError:
            pass
        restore()
        on_done(image, err)

    def tick(n):
        if n <= 0:
            try:
                overlay.withdraw()      # keep the overlay out of the screenshot
            except tk.TclError:
                pass
            root.after(250, grab)
            return
        lbl.config(text=f"Screenshot in {n}...  click the game")
        root.after(1000, lambda: tick(n - 1))

    tick(max(1, int(seconds)))


# ---------------------------------------------------------------------------
# "Press a key" capture
# ---------------------------------------------------------------------------

class KeyPicker(ttk.Frame):
    """A dropdown of key names plus a "Press a key" button that listens for one key."""

    def __init__(self, master, value: str = "", optional: bool = False,
                 on_change: Optional[Callable[[str], None]] = None,
                 choices: Optional[List[str]] = None):
        super().__init__(master)
        self.optional = optional
        self.on_change = on_change
        if choices is None:
            choices = available_key_names()
        self.choices = ([""] if optional else []) + list(choices)
        self.var = tk.StringVar(value=value)
        self.combo = ttk.Combobox(self, textvariable=self.var, values=self.choices, width=12)
        self.combo.pack(side="left")
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.combo.bind("<FocusOut>", lambda _e: self._changed())
        self.button = ttk.Button(self, text="Press a key", command=self.listen, width=16)
        self.button.pack(side="left", padx=(6, 0))
        self.shown = ttk.Label(self, text=keymap.display_name(value), width=14)
        self.shown.pack(side="left", padx=(8, 0))
        if optional:
            ttk.Button(self, text="None", width=6, command=lambda: self.set("")).pack(side="left", padx=(4, 0))
        self._top = None
        self._bind_id = None
        self._timeout = None
        self.error_cb: Optional[Callable[[str], None]] = None

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, name: str):
        self.var.set(name)
        self._changed()

    def _changed(self):
        name = self.get()
        if name:
            try:
                from maplebot.controls import normalize_key
                canon = normalize_key(name)
            except ValueError as e:
                self.shown.config(text="?")
                if self.error_cb:
                    self.error_cb(str(e))
                return
            except Exception:          # controls not importable: keep what was typed
                canon = name.lower()
            if canon != name:
                self.var.set(canon)
            name = canon
        self.shown.config(text=keymap.display_name(name))
        if self.on_change:
            self.on_change(name)

    def listen(self):
        top = self.winfo_toplevel()
        self._stop_listening()
        self._top = top
        self.button.config(text="Press a key now...")
        top.focus_set()        # so Space/Enter don't re-press the focused button
        self._bind_id = top.bind("<KeyPress>", self._got_key, add="+")
        self._timeout = self.after(6000, self._stop_listening)

    def _stop_listening(self):
        if self._timeout:
            try:
                self.after_cancel(self._timeout)
            except tk.TclError:
                pass
            self._timeout = None
        if self._top is not None and self._bind_id is not None:
            try:
                self._top.unbind("<KeyPress>", self._bind_id)
            except tk.TclError:
                pass
        self._top, self._bind_id = None, None
        try:
            self.button.config(text="Press a key")
        except tk.TclError:
            pass

    def _got_key(self, e):
        name = keymap.name_from_event(e.keysym, getattr(e, "keycode", None),
                                      windows=sys.platform == "win32",
                                      available=available_key_names())
        self._stop_listening()
        if name is None:
            if self.error_cb:
                self.error_cb(f"The bot can't press '{e.keysym}'. Pick a key from the list instead.")
        else:
            self.set(name)
        return "break"


def available_key_names() -> List[str]:
    try:
        from maplebot.controls import available_keys
        return list(available_keys())
    except Exception:
        log.warning("controls.available_keys unavailable", exc_info=True)
        return ["ctrl", "alt", "shift", "space", "left", "right", "up", "down",
                "insert", "delete", "home", "end", "pageup", "pagedown"] + \
            list("abcdefghijklmnopqrstuvwxyz") + list("1234567890") + [f"f{i}" for i in range(1, 13)]


HOTKEY_CHOICES = [f"f{i}" for i in range(1, 13)] + ["pause", "scroll_lock", "insert", "home",
                                                     "end", "page_up", "page_down"]


# ---------------------------------------------------------------------------
# Small text prompt
# ---------------------------------------------------------------------------

def ask_text(master, title: str, prompt: str, initial: str = "", hint: str = "") -> Optional[str]:
    """Modal one-line text prompt. Returns the text, or None if cancelled."""
    top = tk.Toplevel(master)
    top.title(title)
    top.resizable(False, False)
    try:
        top.attributes("-topmost", True)
        top.transient(master)
    except tk.TclError:
        pass
    result = {"value": None}
    frame = ttk.Frame(top, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=prompt).pack(anchor="w")
    var = tk.StringVar(value=initial)
    entry = ttk.Entry(frame, textvariable=var, width=36)
    entry.pack(fill="x", pady=(6, 2))
    if hint:
        ttk.Label(frame, text=hint, foreground="#777777").pack(anchor="w")

    def ok(_e=None):
        result["value"] = var.get()
        top.destroy()

    def cancel(_e=None):
        top.destroy()

    row = ttk.Frame(frame)
    row.pack(fill="x", pady=(10, 0))
    ttk.Button(row, text="Cancel", command=cancel).pack(side="right")
    ttk.Button(row, text="OK", command=ok).pack(side="right", padx=6)
    top.bind("<Return>", ok)
    top.bind("<Escape>", cancel)
    top.protocol("WM_DELETE_WINDOW", cancel)
    try:
        entry.focus_set()
        entry.select_range(0, "end")
        top.grab_set()
        master.wait_window(top)
    except tk.TclError:
        pass
    return result["value"]
