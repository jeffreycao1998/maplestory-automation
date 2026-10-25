"""Setup-only sprite library dialog. Workers communicate through a queue, never Tk."""
from __future__ import annotations

from pathlib import Path
import queue
import shutil
import tempfile
import threading
import tkinter as tk
from tkinter import ttk

from .sprite_library import SpriteLibrary, install_pack


class SpriteDownloadWindow(tk.Toplevel):
    def __init__(self, wizard, on_installed):
        super().__init__(wizard)
        self.title("Download map sprites")
        self.transient(wizard)
        self.grab_set()
        self.on_installed = on_installed
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.busy = False
        self.matches = []
        self.image = wizard.image.copy() if wizard.image is not None else None
        self.templates = wizard.profile.templates_dir
        self.cache = wizard.profile.root / "sprite-cache"
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Choose the game's map name or numeric map ID.\n"
                  "Download its monsters' animation poses and known drop icons; no boxes needed.",
                  wraplength=520).pack(anchor="w")
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=8)
        self.query = tk.StringVar(value=wizard.profile.current_map_name())
        ttk.Entry(row, textvariable=self.query, width=42).pack(side="left", fill="x", expand=True)
        self.search_btn = ttk.Button(row, text="Search maps", command=self.search)
        self.search_btn.pack(side="left", padx=(6, 0))
        self.choice = tk.StringVar()
        self.combo = ttk.Combobox(outer, textvariable=self.choice, state="readonly", width=68)
        self.combo.pack(fill="x")
        self.download_btn = ttk.Button(outer, text="Download and use these sprites", command=self.download)
        self.download_btn.pack(fill="x", pady=8)
        self.download_btn.state(["disabled"])
        self.message = ttk.Label(outer, text="Search by map name to begin.", wraplength=520, justify="left")
        self.message.pack(anchor="w")
        ttk.Label(outer, text="Sources: GMS v83 catalog + maplestory.io. Classic can differ.\n"
                  "Check the highlighted matches, then use Observe only before enabling keys.",
                  foreground="#777777", wraplength=520).pack(anchor="w", pady=(8, 0))
        ttk.Button(outer, text="Close / cancel", command=self.close).pack(anchor="e", pady=(8, 0))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", lambda event: self.cancelled.set() if event.widget is self else None)
        self.after(100, self.poll)
        self.after(150, self.search)

    def _buttons(self, busy):
        self.busy = busy
        self.search_btn.state(["disabled"] if busy else ["!disabled"])
        self.download_btn.state(["!disabled"] if self.matches and not busy else ["disabled"])
        self.combo.config(state="disabled" if busy else "readonly")

    def search(self):
        if self.busy:
            return
        query = self.query.get().strip()
        if len(query) < 2:
            self.message.config(text="Enter at least two characters of the map name, or its ID.")
            return
        self._buttons(True)
        self.message.config(text="Loading map catalog (cached after the first search)…")
        threading.Thread(target=self.work, args=("search", query), daemon=True).start()

    def download(self):
        if self.busy or not self.matches or self.combo.current() < 0:
            return
        map_id = self.matches[self.combo.current()]["mapId"]
        self._buttons(True)
        self.message.config(text="Downloading animation frames and drop references…")
        threading.Thread(target=self.work, args=("download", map_id), daemon=True).start()

    def work(self, operation, value):
        output = None
        try:
            library = SpriteLibrary(self.cache, cancelled=self.cancelled.is_set)
            if operation == "search":
                self.events.put(("maps", library.search_maps(value)))
                return
            self.cache.mkdir(parents=True, exist_ok=True)
            output = Path(tempfile.mkdtemp(prefix="pack-", dir=self.cache))
            manifest = library.build_pack(value, output,
                        progress=lambda text: self.events.put(("progress", text)))
            detected = []
            if self.image is not None:
                self.events.put(("progress", "Checking sprites against your screenshot…"))
                from maplebot.perception.sprites import SpriteMatcher
                matcher = SpriteMatcher()
                for row in manifest["entries"]:
                    if row["kind"] == "mob":
                        matcher.add(output / row["file"])
                detected = matcher.find(self.image, 0.90)
            if self.cancelled.is_set():
                shutil.rmtree(output, ignore_errors=True)
            else:
                self.events.put(("downloaded", (output, manifest, detected)))
        except Exception as e:
            if output is not None:
                shutil.rmtree(output, ignore_errors=True)
            self.events.put(("error", str(e)))

    def poll(self):
        if self.cancelled.is_set():
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.message.config(text=value)
                elif kind == "maps":
                    self.matches = value
                    self.combo.config(values=[f"{r['mapName']} — {r['streetName']} ({r['mapId']})" for r in value])
                    if value:
                        self.combo.current(0)
                    self.message.config(text=f"{len(value)} maps found." if value else "No maps found. Try a shorter name or map ID.")
                    self._buttons(False)
                elif kind == "downloaded":
                    output, manifest, detected = value
                    try:
                        installed = install_pack(output, self.templates)
                        self.on_installed(installed, detected)
                        mobs = sum(r["kind"] == "mob" for r in manifest["entries"])
                        items = sum(r["kind"] == "item" for r in manifest["entries"])
                        text = (f"Installed {mobs} monster poses and {items} drop icons. "
                                f"Found {len(detected)} matches in this screenshot.\n"
                                "Close this window and click Next; drawing boxes is optional.")
                        if manifest["warnings"]:
                            text += "\nSome assets are unavailable: " + "; ".join(manifest["warnings"][:3])
                        self.message.config(text=text)
                    except Exception as e:
                        self.message.config(text=f"Could not install sprites: {e}")
                    finally:
                        shutil.rmtree(output, ignore_errors=True)
                        self._buttons(False)
                else:
                    self.message.config(text=f"Download failed: {value}. Your existing templates were kept.")
                    self._buttons(False)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def close(self):
        self.cancelled.set()
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "downloaded":
                    shutil.rmtree(payload[0], ignore_errors=True)
        except queue.Empty:
            pass
        self.destroy()
