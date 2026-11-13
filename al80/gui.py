"""The desktop window and tray icon. No browser involved.

Closing the window hides it to the tray and the screen keeps updating.
The tray icon needs the optional `pystray` package; without it, closing the
window minimises it instead.
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from . import panels
from .app import DEFAULT_PORT, MODES, ROTATABLE, TEST_CARDS, start

BG, CARD, LINE, FG, DIM = "#0d0f16", "#171a26", "#2a2f42", "#e9ecf4", "#8f97b0"
ACCENT, OK, WARN, BAD = "#50c8ff", "#5adc78", "#ffb43c", "#ff505a"
SCALE = 2
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AL80 Screen"

TARGETS = {"Picture page (still)": "picture", "GIF page (up to 160 frames)": "page",
           "Home-page strip (96×64, up to 42 frames)": "home",
           "Startup animation (up to 64 frames)": "boot"}
GAUGES = {"nothing": "off", "CPU load": "cpu", "GPU load": "gpu", "RAM use": "ram"}
STORAGE_TEXT = {
    "untested": ("Not tested yet", WARN),
    "ram": ("Tested: live frames are not stored, so no wear", OK),
    "flash": ("Tested: every upload is stored, so keep refreshes slow", BAD),
}
TEST_STEPS = (
    "When the upload ends:\n"
    "1. Unplug the cable and switch the keyboard fully off for 10 seconds.\n"
    "2. Plug it back in and press Fn+8 again and again to page through the stored pictures.\n"
    "3. Pick which numbered cards are still there.")


# ---------------------------------------------------------------- start with Windows
def launcher_command() -> str:
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    script = Path(__file__).resolve().parent.parent / "al80_app.pyw"
    return f'"{windowless if windowless.exists() else exe}" "{script}" --hidden'


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, launcher_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except OSError:
                pass


def tray_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([14, 4, 50, 60], radius=7, fill=(13, 15, 22), outline=(80, 200, 255), width=3)
    for y, w, color in ((16, 22, (90, 220, 120)), (28, 14, (255, 180, 60)), (40, 18, (80, 200, 255))):
        d.rectangle([21, y, 21 + w, y + 6], fill=color)
    return img


# ---------------------------------------------------------------- window
class Window:
    def __init__(self, engine, shutdown, hidden: bool = False):
        self.engine, self.shutdown = engine, shutdown
        self.root = root = tk.Tk()
        root.title("AL80 Screen")
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        self._style()
        self.tray = None
        self._photo = None
        self._shown_preview = None
        self._loading = False

        st = engine.settings
        self.mode = tk.StringVar(value=st["mode"])
        self.interval = tk.DoubleVar(value=st["min_interval"])
        self.dwell = tk.StringVar(value=str(st["dwell"]))
        self.gauge = tk.StringVar()
        self.readout = tk.BooleanVar(value=st["home_readout"])
        self.twelve = tk.BooleanVar(value=st["twelve_hour"])
        self.target = tk.StringVar(value=next(iter(TARGETS)))
        self.autostart = tk.BooleanVar(value=autostart_enabled())
        self.rotation = {name: tk.BooleanVar(value=name in st["rotation"]) for name in ROTATABLE}
        self.text = {key: tk.StringVar(value="" if st[key] is None else str(st[key]))
                     for key in ("badge_big", "badge_line1", "badge_line2", "footer",
                                 "weather_lat", "weather_lon")}

        left = ttk.Frame(root, style="Card.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="n", padx=(14, 7), pady=14)
        right = ttk.Frame(root, padding=0)
        right.grid(row=0, column=1, sticky="n", padx=(7, 14), pady=14)
        self._build_left(left)
        self._build_right(right)

        self._start_tray()
        if hidden and self.tray:
            root.withdraw()
        self.refresh()

    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        base = ("Segoe UI", 10) if sys.platform == "win32" else ("DejaVu Sans", 10)
        self.font, self.bold = base, (base[0], 10, "bold")
        s.configure(".", background=BG, foreground=FG, font=base, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, troughcolor="#0f1220", focuscolor=ACCENT)
        s.configure("Card.TFrame", background=CARD)
        s.configure("Card.TLabelframe", background=CARD, bordercolor=LINE, relief="solid")
        s.configure("Card.TLabelframe.Label", background=CARD, foreground=DIM, font=(base[0], 9, "bold"))
        for name in ("TLabel", "TCheckbutton", "TRadiobutton"):
            s.configure(name, background=CARD, foreground=FG)
            s.map(name, background=[("active", CARD)], foreground=[("disabled", "#5a6078")],
                  indicatorcolor=[("selected", ACCENT), ("!selected", "#0f1220")])
        s.configure("Dim.TLabel", foreground=DIM, font=(base[0], 9))
        s.configure("Head.TLabel", font=(base[0], 14, "bold"))
        s.configure("TButton", background="#20253a", foreground=FG, padding=(10, 5), relief="flat")
        s.map("TButton", background=[("active", "#2b3250"), ("disabled", "#191c2a")],
              foreground=[("disabled", "#5a6078")])
        s.configure("TEntry", fieldbackground="#0f1220", foreground=FG, insertcolor=FG)
        s.configure("TCombobox", fieldbackground="#0f1220", background="#20253a", foreground=FG,
                    arrowcolor=FG, selectbackground="#0f1220", selectforeground=FG)
        s.map("TCombobox", fieldbackground=[("readonly", "#0f1220")], foreground=[("readonly", FG)])
        s.configure("Horizontal.TScale", background=CARD)
        self.root.option_add("*TCombobox*Listbox.background", "#0f1220")
        self.root.option_add("*TCombobox*Listbox.foreground", FG)

    # ------------------------------------------------------------ layout
    def _build_left(self, f):
        ttk.Label(f, text="AL80 Screen", style="Head.TLabel").grid(sticky="w")
        ttk.Label(f, text="96 × 160, shown at 2×", style="Dim.TLabel").grid(sticky="w")
        bezel = tk.Frame(f, bg="#000000", padx=12, pady=12)
        bezel.grid(pady=(10, 8))
        self.preview = tk.Label(bezel, bg="#000000", bd=0)
        self.preview.pack()
        row = ttk.Frame(f, style="Card.TFrame")
        row.grid(sticky="w")
        self.dot = tk.Canvas(row, width=12, height=12, bg=CARD, highlightthickness=0)
        self.dot.grid(row=0, column=0, padx=(0, 6))
        self.conn = ttk.Label(row, text="", font=self.bold)
        self.conn.grid(row=0, column=1)
        self.note = ttk.Label(f, text="", style="Dim.TLabel", wraplength=216, justify="left")
        self.note.grid(sticky="w")
        stats = ttk.Frame(f, style="Card.TFrame")
        stats.grid(sticky="ew", pady=(8, 0))
        stats.columnconfigure(1, weight=1)
        self.stat = {}
        for i, (key, label) in enumerate((("panel", "Showing"), ("took", "Last upload took"),
                                          ("session", "Uploads this session"), ("life", "Uploads ever"))):
            ttk.Label(stats, text=label, style="Dim.TLabel").grid(row=i, column=0, sticky="w")
            self.stat[key] = ttk.Label(stats, text="–", font=(self.font[0], 9))
            self.stat[key].grid(row=i, column=1, sticky="e")
        ttk.Checkbutton(f, text="Start with Windows", variable=self.autostart,
                        command=self._toggle_autostart).grid(sticky="w", pady=(10, 0))
        buttons = ttk.Frame(f, style="Card.TFrame")
        buttons.grid(sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Hide to tray", command=self.hide).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Quit", command=self.quit).grid(row=0, column=1)

    def _group(self, parent, title):
        box = ttk.LabelFrame(parent, text=title.upper(), style="Card.TLabelframe", padding=(12, 6, 12, 10))
        box.grid(sticky="ew", pady=(0, 10))
        return box

    def _build_right(self, f):
        box = self._group(f, "What to show")
        self.mode_buttons = {}
        for i, mode in enumerate(MODES):
            b = ttk.Radiobutton(box, text=panels.LABELS[mode], value=mode, variable=self.mode,
                                command=lambda: self._set(mode=self.mode.get()))
            b.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 18), pady=1)
            self.mode_buttons[mode] = b
        self.rot_row = ttk.Frame(box, style="Card.TFrame")
        self.rot_row.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(self.rot_row, text="Rotate:", style="Dim.TLabel").grid(row=0, column=0)
        for i, name in enumerate(ROTATABLE):
            short = {"system": "System", "clock": "Clock", "badge": "Badge", "image": "Picture",
                     "external": "Pushed"}[name]
            ttk.Checkbutton(self.rot_row, text=short, variable=self.rotation[name],
                            command=self._set_rotation).grid(row=0, column=i + 1, padx=(6, 0))
        ttk.Label(self.rot_row, text="every", style="Dim.TLabel").grid(row=0, column=7, padx=(10, 4))
        self._entry(self.rot_row, self.dwell, 4, "dwell").grid(row=0, column=8)
        ttk.Label(self.rot_row, text="s", style="Dim.TLabel").grid(row=0, column=9, padx=(3, 0))

        box = self._group(f, "Live home page")
        ttk.Label(box, text="The keyboard draws this page itself. Feeding it sends a few bytes and "
                  "never uploads a picture, so it cannot wear anything out.",
                  style="Dim.TLabel", wraplength=470, justify="left").grid(columnspan=3, sticky="w")
        ttk.Label(box, text="Battery gauge follows").grid(row=1, column=0, sticky="w", pady=(6, 0))
        combo = ttk.Combobox(box, textvariable=self.gauge, values=list(GAUGES), state="readonly", width=12)
        combo.grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))
        combo.bind("<<ComboboxSelected>>", lambda e: self._set(gauge=GAUGES[self.gauge.get()]))
        ttk.Checkbutton(box, text="Clock digits show CPU : GPU : temperature (experimental)",
                        variable=self.readout,
                        command=lambda: self._set(home_readout=self.readout.get())
                        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        box = self._group(f, "Picture refresh and storage")
        box.columnconfigure(0, weight=1)
        row = ttk.Frame(box, style="Card.TFrame")
        row.grid(sticky="ew")
        row.columnconfigure(0, weight=1)
        scale = ttk.Scale(row, from_=0, to=60, variable=self.interval, command=self._interval_moved)
        scale.grid(row=0, column=0, sticky="ew")
        scale.bind("<ButtonRelease-1>", self._interval_released)
        self.interval_text = ttk.Label(row, text="", width=22, anchor="e")
        self.interval_text.grid(row=0, column=1, padx=(8, 0))
        self.storage = ttk.Label(box, text="", font=self.bold)
        self.storage.grid(sticky="w", pady=(6, 0))
        self.storage_help = ttk.Label(box, style="Dim.TLabel", wraplength=470, justify="left")
        self.storage_help.grid(sticky="w")
        row = ttk.Frame(box, style="Card.TFrame")
        row.grid(sticky="w", pady=(6, 0))
        self.test_button = ttk.Button(row, text="Run storage test…", command=self._storage_test)
        self.test_button.grid(row=0, column=0, padx=(0, 6))
        self.answers = ttk.Frame(box, style="Card.TFrame")
        self.answers.grid(sticky="w", pady=(6, 0))
        ttk.Label(self.answers, text="After the power cut, the cards still there were:",
                  style="Dim.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        for i, (seen, label) in enumerate((("first", "Only low numbers"), ("last", f"Up to {TEST_CARDS}"),
                                           ("none", "None"), ("all", f"All {TEST_CARDS}"))):
            ttk.Button(self.answers, text=label, command=lambda s=seen: self._storage_answer(s)
                       ).grid(row=1, column=i, padx=(0, 6), pady=(4, 0))

        box = self._group(f, "Your own picture or GIF")
        ttk.Combobox(box, textvariable=self.target, values=list(TARGETS), state="readonly", width=38
                     ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(box, text="Choose file…", command=self._choose_file).grid(row=0, column=3, padx=(8, 0))
        row = ttk.Frame(box, style="Card.TFrame")
        row.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for i, (label, action, ask) in enumerate((
                ("Show GIF page", "view_gif", None), ("Next stored picture", "next_picture", None),
                ("Sync clock", "clock", None),
                ("Clear pictures", "clear_pictures", "Delete every stored picture from the screen?"),
                ("Clear GIF", "clear_gif", "Delete the stored GIF from the screen?"))):
            ttk.Button(row, text=label, command=lambda a=action, q=ask: self._action(a, q)
                       ).grid(row=0, column=i, padx=(0, 6))

        box = self._group(f, "Details")
        ttk.Checkbutton(box, text="12-hour clock", variable=self.twelve,
                        command=lambda: self._set(twelve_hour=self.twelve.get())
                        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Button(box, text="Send a test alert", command=self._test_alert).grid(row=0, column=3, columnspan=3, sticky="e")
        ttk.Label(box, text="Badge").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._entry(box, self.text["badge_big"], 5, "badge_big").grid(row=1, column=1, sticky="w", pady=(6, 0))
        self._entry(box, self.text["badge_line1"], 14, "badge_line1").grid(row=1, column=2, padx=6, pady=(6, 0))
        self._entry(box, self.text["badge_line2"], 18, "badge_line2").grid(row=1, column=3, columnspan=3, pady=(6, 0))
        ttk.Label(box, text="Weather").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._entry(box, self.text["weather_lat"], 8, "weather_lat").grid(row=2, column=1, sticky="w", pady=(6, 0))
        self._entry(box, self.text["weather_lon"], 8, "weather_lon").grid(row=2, column=2, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(box, text="latitude, longitude. Off when empty; otherwise sent to open-meteo.com.",
                  style="Dim.TLabel", wraplength=250, justify="left").grid(row=2, column=3, columnspan=3, sticky="w", pady=(6, 0))

    def _entry(self, parent, var, width, key):
        entry = ttk.Entry(parent, textvariable=var, width=width)
        commit = lambda e=None: self._set(**{key: var.get()})
        entry.bind("<FocusOut>", commit)
        entry.bind("<Return>", commit)
        return entry

    # ------------------------------------------------------------ actions
    def _set(self, **patch):
        try:
            self.engine.update_settings(patch)
        except (ValueError, TypeError):
            pass                         # a half-typed number: the next refresh restores it
        self.refresh(once=True)

    def _set_rotation(self):
        self._set(rotation=[n for n in ROTATABLE if self.rotation[n].get()])

    def _interval_text(self, value: float):
        self.interval_text.config(text="as fast as it can" if value < 0.25 else f"at most every {value:g} s")

    def _interval_moved(self, _value):
        self._loading = True             # do not let refresh() fight the drag
        self._interval_text(round(self.interval.get() * 2) / 2)

    def _interval_released(self, _event):
        if self._loading:
            self._loading = False
            self._set(min_interval=round(self.interval.get() * 2) / 2)

    def _action(self, action, question=None):
        if question and not messagebox.askyesno("AL80 Screen", question, parent=self.root):
            return
        self.engine.do_action(action)
        self.refresh(once=True)

    def _choose_file(self):
        path = filedialog.askopenfilename(parent=self.root, title="Picture or GIF", filetypes=[
            ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.engine.upload_file(Path(path).read_bytes(), TARGETS[self.target.get()])
        except (OSError, ValueError) as exc:
            messagebox.showerror("AL80 Screen", f"Could not use that file.\n{exc}", parent=self.root)
        self.refresh(once=True)

    def _storage_test(self):
        if not messagebox.askokcancel("Storage test", (
                f"This deletes the pictures stored on the screen and uploads {TEST_CARDS} numbered "
                f"cards, which takes about half a minute.\n\n{TEST_STEPS}"), parent=self.root):
            return
        self.engine.start_storage_test()
        self.refresh(once=True)

    def _storage_answer(self, seen):
        self.engine.record_storage_result(seen)
        self.interval.set(self.engine.settings["min_interval"])
        if seen == "all":
            messagebox.showinfo("Storage test", "The screen holds more pictures than the test "
                                "uploaded, so this run cannot tell. No setting was changed.", parent=self.root)
        self.refresh(once=True)

    def _test_alert(self):
        self.engine.set_alert({"title": "Build finished", "lines": ["yunzii / main", "all tests passed"],
                               "level": "ok", "seconds": 12})

    def _toggle_autostart(self):
        try:
            set_autostart(self.autostart.get())
        except (OSError, ImportError):
            self.autostart.set(False)
            messagebox.showinfo("AL80 Screen", "Start with Windows is only available on Windows.",
                                parent=self.root)

    # ------------------------------------------------------------ tray and lifetime
    def _start_tray(self):
        try:
            import pystray
        except Exception:                # missing package, or no tray on this desktop
            return
        on_tk = lambda fn: (lambda *_: self.root.after(0, fn))
        mode_item = lambda mode: pystray.MenuItem(
            panels.LABELS[mode], on_tk(lambda: self._set(mode=mode)),
            checked=lambda _i: self.engine.settings["mode"] == mode, radio=True)
        menu = pystray.Menu(
            pystray.MenuItem("Open", on_tk(self.show), default=True),
            pystray.Menu.SEPARATOR,
            *[mode_item(m) for m in MODES],
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_tk(self.quit)))
        try:
            self.tray = pystray.Icon("al80", tray_image(), "AL80 Screen", menu)
            self.tray.run_detached()
        except Exception:
            self.tray = None

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()

    def quit(self):
        if self.tray:
            self.tray.stop()
        self.shutdown()
        self.root.destroy()

    # ------------------------------------------------------------ refresh
    def refresh(self, once: bool = False):
        s = self.engine.snapshot()
        st = s["settings"]
        image = self.engine.preview
        if image is not self._shown_preview:
            self._shown_preview = image
            self._photo = ImageTk.PhotoImage(image.resize((96 * SCALE, 160 * SCALE), Image.NEAREST))
            self.preview.config(image=self._photo)

        color = BAD if not s["connected"] else WARN if s["dry_run"] else OK
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 11, 11, fill=color, outline="")
        self.conn.config(text=s["busy"] or ("Keyboard not connected" if not s["connected"] else
                                            "Dry run, no keyboard" if s["dry_run"] else "Keyboard connected"))
        self.note.config(text="" if s["connected"] else s["device_error"])
        self.stat["panel"].config(text=panels.LABELS.get(s["panel"], s["panel"]).split(" (")[0])
        self.stat["took"].config(text=f"{s['last_upload_ms'] / 1000:.2f} s" if s["last_upload_ms"] else "–")
        self.stat["session"].config(text=f"{s['session_uploads']:,}")
        self.stat["life"].config(text=f"{s['lifetime_uploads']:,}")

        self.mode.set(st["mode"])
        self.mode_buttons["image"].state(["!disabled" if s["has_image"] else "disabled"])
        self.mode_buttons["external"].state(["!disabled" if s["has_external"] else "disabled"])
        if st["mode"] == "rotate":
            self.rot_row.grid()
        else:
            self.rot_row.grid_remove()
        self.gauge.set(next(k for k, v in GAUGES.items() if v == st["gauge"]))
        self.readout.set(st["home_readout"])
        self.twelve.set(st["twelve_hour"])
        for name, var in self.rotation.items():
            var.set(name in st["rotation"])
        if not self._loading:
            self.interval.set(st["min_interval"])
            self._interval_text(st["min_interval"])
        focused = self.root.focus_get()
        if not isinstance(focused, ttk.Entry):
            self.dwell.set(str(st["dwell"]))
            for key, var in self.text.items():
                var.set("" if st[key] is None else str(st[key]))

        text, color = STORAGE_TEXT[st["storage"]]
        testing = s["storage_test"]
        self.storage.config(text="Storage: " + ("test running…" if testing == "running" else
                                                "waiting for your answer" if testing == "waiting" else text),
                            foreground=WARN if testing else color)
        self.storage_help.config(text=TEST_STEPS if testing else
                                 "Applies to the picture panels only. The test shows whether live "
                                 "frames are written to the screen's permanent storage.")
        self.test_button.state(["disabled" if testing == "running" or not s["connected"] else "!disabled"])
        if testing == "waiting":
            self.answers.grid()
        else:
            self.answers.grid_remove()
        if not once:
            self.root.after(500, self.refresh)


def run(port: int = DEFAULT_PORT, dry_run: bool = False, hidden: bool = False):
    try:
        engine, shutdown = start(port, dry_run)
    except OSError:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("AL80 Screen", "AL80 Screen is already running. Look for its icon in the tray.")
        return
    window = Window(engine, shutdown, hidden)
    try:
        window.root.mainloop()
    finally:
        engine.stop.set()
