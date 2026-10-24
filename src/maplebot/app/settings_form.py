"""
Settings window: one tab per section, generated from the Settings dataclasses.

Labels / tooltips / limits come from ``logic.FIELD_INFO`` etc.; a field without an
entry there still appears (with a label made from its name), so new settings show up.
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, Optional, Tuple

from maplebot.settings import Profile, Settings

from . import logic
from .widgets import HOTKEY_CHOICES, KeyPicker, Tooltip

log = logging.getLogger(__name__)


class SettingsWindow(tk.Toplevel):
    def __init__(self, master, profile: Profile, settings: Settings,
                 on_saved: Optional[Callable[[Settings], None]] = None):
        super().__init__(master)
        self.title("MapleBot settings")
        self.profile = profile
        self.base = settings
        self.on_saved = on_saved
        self.specs = logic.iter_fields(type(settings))
        self.inputs: Dict[Tuple[str, str], Tuple[str, object]] = {}   # key -> (kind, var|picker)
        self.resizable(False, True)
        try:
            self.attributes("-topmost", bool(master.attributes("-topmost")))
        except tk.TclError:
            pass

        ttk.Label(self, text="Hover over any setting to see what it does.",
                  padding=(10, 8, 10, 0)).pack(anchor="w")
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=8)
        self.section_of_tab: Dict[str, str] = {}
        for section, label, items in logic.sections(self.specs):
            frame = ttk.Frame(self.nb, padding=10)
            self.nb.add(frame, text=label)
            self.section_of_tab[str(frame)] = section
            for row, spec in enumerate(items):
                self._add_row(frame, row, spec, logic.get_value(settings, spec))
            frame.columnconfigure(1, weight=1)

        self.error = ttk.Label(self, foreground="#c00000", wraplength=520, justify="left")
        self.error.pack(fill="x", padx=10)
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        ttk.Button(bar, text="Reset this tab to defaults", command=self.reset_tab).pack(side="left")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Save", command=self.save).pack(side="right", padx=6)
        self.bind("<Escape>", lambda _e: self.destroy())

    # ---- rows ------------------------------------------------------------
    def _add_row(self, frame, row: int, spec: logic.FieldSpec, value):
        lbl = ttk.Label(frame, text=spec.label)
        lbl.grid(row=row, column=0, sticky="w", pady=3, padx=(0, 12))
        Tooltip(lbl, spec.tooltip)
        kind = spec.kind
        shown = logic.format_value(kind, value)
        if kind == "bool":
            var = tk.BooleanVar(value=bool(shown))
            w = ttk.Checkbutton(frame, variable=var)
            self.inputs[spec.key] = (kind, var)
        elif kind in ("key", "optional_key"):
            w = KeyPicker(frame, value=shown, optional=(kind == "optional_key"))
            w.error_cb = lambda msg: self.error.config(text=msg)
            self.inputs[spec.key] = (kind, w)
        elif kind == "hotkey":
            var = tk.StringVar(value=shown)
            w = ttk.Combobox(frame, textvariable=var, values=HOTKEY_CHOICES, width=14)
            self.inputs[spec.key] = (kind, var)
        else:
            var = tk.StringVar(value=shown)
            w = ttk.Entry(frame, textvariable=var, width=24)
            self.inputs[spec.key] = (kind, var)
        w.grid(row=row, column=1, sticky="w", pady=3)
        Tooltip(w, spec.tooltip)

    def _raw_values(self):
        out = {}
        for key, (kind, obj) in self.inputs.items():
            out[key] = obj.get()
        return out

    def _set_raw(self, key, value):
        kind, obj = self.inputs[key]
        obj.set(value)

    # ---- buttons -----------------------------------------------------------
    def reset_tab(self):
        section = self.section_of_tab.get(self.nb.select())
        if not section:
            return
        if section == "calibration" and not messagebox.askyesno(
                "Reset screen setup?",
                "This clears your minimap / HP / MP boxes and patrol edges. You'll need to "
                "run the setup wizard again. Continue?", parent=self):
            return
        for key, val in logic.defaults_for_section(section).items():
            if key in self.inputs:
                self._set_raw(key, val)
        self.error.config(text="Defaults filled in for this tab. Click Save to keep them.")

    def save(self):
        new, errors = logic.build_settings(self.base, self._raw_values(), self.specs)
        if errors:
            self.error.config(text="Please fix:\n- " + "\n- ".join(errors))
            return
        try:
            self.profile.save(new)
        except OSError as e:
            log.exception("saving settings failed")
            messagebox.showerror("Couldn't save", f"Couldn't write the settings file:\n{e}", parent=self)
            return
        if self.on_saved:
            self.on_saved(new)
        self.destroy()
