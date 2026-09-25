"""Keyboard Instruments - play instruments live with the computer keyboard.

Main menu:
  * Modificar instrumento: create / edit instruments (sound, how many keys, which key plays which note).
  * Tocar instrumento: pick an instrument and play it, freely or over a song's chords (read from a MIDI;
    the MIDI itself is never played, only its harmony is used).

While playing: Space = palm mute (hold), Enter = power chords, Up/Down = octave shift,
Left/Right = previous/next chord (song mode).

Run:  python app.py
"""

import glob
import json
import os
import sys


def _fix_tcl_paths():
    """Python 3.13.0 on Windows: inside a venv Tkinter can't find Tcl/Tk ('Can't find a usable
    init.tcl'). Point it to the base Python's tcl folder before tkinter loads."""
    if not sys.platform.startswith("win") or os.environ.get("TCL_LIBRARY"):
        return
    for base in (sys.base_prefix, sys.exec_prefix, os.path.dirname(sys.executable)):
        tcl = sorted(glob.glob(os.path.join(base, "tcl", "tcl8*")))
        tk_ = sorted(glob.glob(os.path.join(base, "tcl", "tk8*")))
        tcl = [d for d in tcl if os.path.exists(os.path.join(d, "init.tcl"))]
        if tcl:
            os.environ["TCL_LIBRARY"] = tcl[-1]
            if tk_:
                os.environ["TK_LIBRARY"] = tk_[-1]
            return


_fix_tcl_paths()

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402
from bisect import bisect_left  # noqa: E402
from tkinter import ttk  # noqa: E402

from audio import Engine  # noqa: E402
import threading  # noqa: E402
from instruments import DRUM_GROUPS, DRUM_PIECES, INSTRUMENTS, SR, DrumKit, drum_group  # noqa: E402
from profiles import (KEY_POSITIONS, LAYOUTS, MAX_KEYS, theme_key, theme_profiles, default_profiles, free_key, drum_part_profiles, merged_profile, theme_merges,  # noqa: E402
                      is_drums, load_profiles, piano_layout, save_profiles, scale_notes)
from song import (GM_DRUMS, INTERVAL_LABELS, ROLES, chord_intervals, load_song, chord_name, midi_tracks,  # noqa: E402
                  track_events)

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.json")
PROFILES_FILE = os.path.join(HERE, "profiles.json")

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
OCTAVES = [str(o) for o in range(0, 8)]
PIECE_LABELS = [label for label, _ in DRUM_PIECES.values()]
PIECE_IDS = list(DRUM_PIECES)
SOUND_NAMES = {i.id: i.name for i in INSTRUMENTS}
KEY_LABELS = {
    "semicolon": ";", "comma": ",", "period": ".", "slash": "/", "apostrophe": "'",
    "bracketleft": "[", "bracketright": "]", "backslash": "\\", "minus": "-",
    "equal": "=", "grave": "`", "space": "Space", "ntilde": "Ñ",
}
RESERVED_KEYS = {"space", "return", "up", "down", "left", "right", "escape", "tab"}

BG = "#1e1f24"
PANEL = "#2a2c33"
FG = "#e8e8ea"
MUTED = "#9a9ca5"
ACCENT = "#3fb66b"
BLACK_KEY = "#131418"
SHARPS = {1, 3, 6, 8, 10}


def key_label(keysym):
    return KEY_LABELS.get(keysym, keysym.upper())


def midi_of(name, octave):
    return 12 * (int(octave) + 1) + NOTE_NAMES.index(name)


def freq_of(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def note_text(midi):
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def instrument_by_id(sound):
    return next((i for i in INSTRUMENTS if i.id == sound), INSTRUMENTS[0])


def find_midi_files():
    """[(label, path)] of .mid files in the app folder, its parent, Downloads, Desktop and Music."""
    home = os.path.expanduser("~")
    folders = [HERE, os.path.dirname(HERE), os.path.join(home, "Downloads"), os.path.join(home, "Desktop"),
               os.path.join(home, "Music")]
    found, seen = [], set()
    for folder in folders:
        for pattern in ("*.mid", "*.midi", "*.MID"):
            for path in sorted(glob.glob(os.path.join(folder, pattern))):
                key = os.path.normcase(os.path.abspath(path))
                if key in seen:
                    continue
                seen.add(key)
                found.append((f"{os.path.basename(path)}   ({os.path.basename(folder) or folder})", path))
    return found


SONGS_DIR = os.path.join(HERE, "songs")
NO_TRACK = "(ninguna: solo acordes)"
NO_THEME = "Sin tema (modo libre)"
# Friendly names for known files (matched against the lower-case file name)
THEME_NAMES = [("order", "Order - Ultrakill"), ("freesia", "Freesia - Guilty Gear")]


def theme_label(path):
    low = os.path.basename(path).lower()
    for key, name in THEME_NAMES:
        if key in low:
            return name
    return os.path.splitext(os.path.basename(path))[0]


def find_themes():
    """[(name, path)]: every .mid in the app's songs folder, then .mid files found elsewhere
    (Downloads, Desktop...) that aren't already there. Names must be unique."""
    themes, seen_files, seen_names = [], set(), set()
    candidates = [p for p in sorted(glob.glob(os.path.join(SONGS_DIR, "*.mid*")))]
    candidates += [p for _, p in find_midi_files()]
    for path in candidates:
        base = os.path.basename(path).lower()
        if base in seen_files:
            continue
        seen_files.add(base)
        name = theme_label(path)
        while name in seen_names:
            name += " *"
        seen_names.add(name)
        themes.append((name, path))
    return themes


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def maximize(win):
    """Opens a window maximized ("windowed fullscreen": fills the screen, keeps the title bar).
    F11 switches to real fullscreen and back."""
    def go():
        try:
            win.state("zoomed")  # Windows
        except tk.TclError:
            try:
                win.attributes("-zoomed", True)  # Linux
            except tk.TclError:
                win.geometry(f"{win.winfo_screenwidth()}x{win.winfo_screenheight()}+0+0")
    win.after(10, go)

    def check():  # no window manager honoured it: size it to the screen ourselves
        try:
            if win.winfo_width() < 0.9 * win.winfo_screenwidth():
                win.geometry(f"{win.winfo_screenwidth()}x{win.winfo_screenheight() - 60}+0+0")
        except tk.TclError:
            pass
    win.after(400, check)

    def toggle(_e=None):
        win.attributes("-fullscreen", not win.attributes("-fullscreen"))
    win.bind("<F11>", toggle, add="+")


def apply_style(root):
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL)
    st.configure("TFrame", background=BG)
    st.configure("Panel.TFrame", background=PANEL)
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("Panel.TLabel", background=PANEL, foreground=FG)
    st.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
    st.configure("BgMuted.TLabel", background=BG, foreground=MUTED)
    st.configure("H1.TLabel", font=("TkDefaultFont", 16, "bold"))
    st.configure("Title.TLabel", font=("TkDefaultFont", 22, "bold"))
    st.configure("H2.TLabel", background=PANEL, font=("TkDefaultFont", 11, "bold"))
    st.configure("Chord.TLabel", background=PANEL, font=("TkDefaultFont", 30, "bold"))
    st.configure("Big.TLabel", background=PANEL, font=("TkDefaultFont", 13))
    for name, bg in (("TRadiobutton", PANEL), ("TCheckbutton", BG), ("Panel.TCheckbutton", PANEL)):
        st.configure(name, background=bg, foreground=FG, indicatorbackground="#3a3d47")
        st.map(name, background=[("active", bg)],
               indicatorbackground=[("selected", ACCENT), ("!selected", "#3a3d47")])
    st.configure("TButton", background="#3a3d47", foreground=FG, padding=6)
    st.map("TButton", background=[("active", "#4a4e5a")])
    st.configure("Go.TButton", background="#2f7d4f", font=("TkDefaultFont", 11, "bold"), padding=10)
    st.map("Go.TButton", background=[("active", "#3a9a61")])
    st.configure("Menu.TButton", background="#34374a", font=("TkDefaultFont", 15, "bold"), padding=(20, 26))
    st.map("Menu.TButton", background=[("active", "#44485e")])
    st.configure("Key.TButton", padding=2)
    st.configure("TCombobox", fieldbackground=PANEL, background="#3a3d47", foreground=FG, arrowcolor=FG)
    st.map("TCombobox",
           fieldbackground=[("readonly", "#3a3d47")], foreground=[("readonly", FG)],
           selectbackground=[("readonly", "#3a3d47")], selectforeground=[("readonly", FG)])
    st.configure("TSpinbox", fieldbackground="#3a3d47", foreground=FG, arrowcolor=FG)
    st.configure("TEntry", fieldbackground="#3a3d47", foreground=FG, insertcolor=FG)
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", FG)


# ==========================================================================
# Main menu
# ==========================================================================
class MainMenu(tk.Tk):
    """Screen 1: choose the theme (song). Screen 2: configure instruments or play."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.title("Keyboard Instruments")
        self.configure(bg=BG)
        self.minsize(600, 460)
        maximize(self)
        apply_style(self)
        self.all_profiles = load_profiles(PROFILES_FILE)
        save_profiles(PROFILES_FILE, self.all_profiles)  # creates the file with the defaults the first time
        self.theme_key = None
        self.profiles = self._visible()  # instruments shown: global ones + the current theme's
        self.settings = load_settings()
        self.themes = find_themes()
        self.song = None
        self.theme_name = None
        self.theme_path = None

        self.theme_frame = ttk.Frame(self, padding=30)
        self.menu_frame = ttk.Frame(self, padding=30)
        self._build_theme_screen()
        self._build_menu_screen()
        self.show_themes()
        self.protocol("WM_DELETE_WINDOW", self.close)

    # ---- screen 1: theme ------------------------------------------------------
    def _build_theme_screen(self):
        f = self.theme_frame
        ttk.Label(f, text="Keyboard Instruments", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text="Orquesta de laptops: toca instrumentos con el teclado del computador.",
                  style="BgMuted.TLabel").pack(anchor="w", pady=(0, 24))
        ttk.Label(f, text="Elige el tema", style="H1.TLabel").pack(anchor="w")
        names = [name for name, _ in self.themes] + [NO_THEME]
        self.theme_var = tk.StringVar()
        self.theme_box = ttk.Combobox(f, textvariable=self.theme_var, values=names, state="readonly",
                                      font=("TkDefaultFont", 13), width=36)
        self.theme_box.pack(anchor="w", fill="x", pady=(8, 6), ipady=4)
        self.theme_box.bind("<<ComboboxSelected>>", lambda _e: self._preview_theme())
        self.theme_info = ttk.Label(f, text="", style="BgMuted.TLabel", wraplength=520, justify="left")
        self.theme_info.pack(anchor="w", pady=(0, 20))
        ttk.Button(f, text="Continuar  \u2192", style="Go.TButton", command=self.choose_theme).pack(anchor="e")
        self.audio_label_1 = ttk.Label(f, text="", style="BgMuted.TLabel")
        self.audio_label_1.pack(anchor="w", side="bottom")

        saved = self.settings.get("theme_path")
        names_by_path = {os.path.normcase(os.path.abspath(p)): n for n, p in self.themes}
        if saved is None and self.themes:
            self.theme_var.set(self.themes[0][0])
        elif saved and os.path.normcase(os.path.abspath(saved)) in names_by_path:
            self.theme_var.set(names_by_path[os.path.normcase(os.path.abspath(saved))])
        else:
            self.theme_var.set(NO_THEME)
        self._preview_theme()

    def _theme_path(self):
        return dict(self.themes).get(self.theme_var.get())

    def _preview_theme(self):
        path = self._theme_path()
        if path is None:
            self.theme_info.config(text="Sin tema: tocas libremente las notas que configuraste.", foreground=MUTED)
            return
        self.theme_info.config(text=f"Archivo: {os.path.basename(path)}", foreground=MUTED)

    def choose_theme(self):
        path = self._theme_path()
        self.song, self.theme_path, self.theme_name = None, None, None
        if path is not None:
            self.config(cursor="watch")
            self.update_idletasks()
            try:
                self.song = load_song(path)
                self.theme_path, self.theme_name = path, self.theme_var.get()
            except Exception as exc:
                self.config(cursor="")
                self.theme_info.config(text=f"No se pudo leer el MIDI: {exc}", foreground="#ff7070")
                return
            self.config(cursor="")
        self.settings["theme_path"] = self.theme_path or ""
        save_settings(self.settings)
        self.theme_key = theme_key(self.theme_path) if self.theme_path else None
        if self.theme_key and not any(p.get("theme") == self.theme_key for p in self.all_profiles):
            try:  # first time with this theme: one instrument per MIDI track
                taken = {p["name"] for p in self.all_profiles if not p.get("theme")}
                new = theme_profiles(self.theme_path)
                for p in new:  # don't clash with the global Guitarra / Piano
                    if p["name"] in taken:
                        p["name"] += f" ({self.theme_name.split(' - ')[0]})"
                self.all_profiles += new
                save_profiles(PROFILES_FILE, self.all_profiles)
            except Exception as exc:
                print("theme instruments:", exc, flush=True)
        self._remember_tracks()
        self._split_theme_drums()
        self._merge_theme_tracks()
        self.profiles = self._visible()
        self.show_menu()

    def _split_theme_drums(self):
        """Older theme drum kits (one instrument with every piece) become two: tambores + platillos."""
        changed = False
        for i in range(len(self.all_profiles) - 1, -1, -1):
            p = self.all_profiles[i]
            if (p.get("theme") == self.theme_key and is_drums(p["sound"]) and "part" not in p
                    and isinstance(p.get("track"), int)):
                name = p["name"].split(" (")[0]
                self.all_profiles[i:i + 1] = drum_part_profiles(name, self.theme_key, p["track"])
                changed = True
        if changed:
            save_profiles(PROFILES_FILE, self.all_profiles)

    def _merge_theme_tracks(self):
        """Tracks one person plays together (e.g. Guitarra 2 + solista in Order) become one instrument."""
        if not self.theme_path or not self.theme_key:
            return
        changed = False
        keys = LAYOUTS["Central + superior (A... L, luego Q... P)"]
        for name, tracks in theme_merges(self.theme_path):
            mine = [p for p in self.all_profiles if p.get("theme") == self.theme_key]
            if any(p["name"] == name for p in mine):
                continue
            parts = [p for p in mine if p.get("track") in tracks and "part" not in p]
            main = next((p for p in parts if p["track"] == tracks[0]), None)
            if main is None or len(parts) < len(tracks):
                continue
            at = self.all_profiles.index(main)
            merged = merged_profile(self.theme_path, name, tracks, main["sound"], keys)
            self.all_profiles = [p for p in self.all_profiles if p not in parts]
            self.all_profiles.insert(min(at, len(self.all_profiles)), merged)
            changed = True
        if changed:
            save_profiles(PROFILES_FILE, self.all_profiles)

    def _remember_tracks(self):
        """Theme instruments made by older versions don't know their MIDI track: find it once, by name."""
        missing = [p for p in self.all_profiles if p.get("theme") == self.theme_key and "track" not in p]
        if not missing:
            return
        try:
            gen = {q["name"]: q["track"] for q in theme_profiles(self.theme_path)}
        except Exception:
            return
        for p in missing:
            t = gen.get(p["name"], gen.get(p["name"].split(" (")[0]))
            if t is not None:
                p["track"] = t
        save_profiles(PROFILES_FILE, self.all_profiles)

    # ---- screen 2: configure / play ---------------------------------------------
    def _build_menu_screen(self):
        f = self.menu_frame
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.theme_title = ttk.Label(top, text="", style="Title.TLabel")
        self.theme_title.pack(side="left")
        ttk.Button(top, text="\u2190 Cambiar tema", command=self.show_themes).pack(side="right")
        self.theme_summary = ttk.Label(f, text="", style="BgMuted.TLabel", wraplength=540, justify="left")
        self.theme_summary.pack(anchor="w", pady=(4, 24))
        ttk.Button(f, text="Tocar instrumento", style="Menu.TButton",
                   command=self.open_play).pack(fill="x", pady=8)
        ttk.Button(f, text="Configurar instrumentos", style="Menu.TButton",
                   command=self.open_editor).pack(fill="x", pady=8)
        self.listen_button = ttk.Button(f, text="\u266B Escuchar tema completo", style="Menu.TButton",
                                        command=self.open_listen)
        self.listen_button.pack(fill="x", pady=8)
        self.audio_label_2 = ttk.Label(f, text="", style="BgMuted.TLabel")
        self.audio_label_2.pack(anchor="w", pady=(24, 0))

    def _audio_text(self):
        if self.engine.error:
            return f"Error de audio: {self.engine.error}"
        return f"Audio: {self.engine.device_name}"

    def show_themes(self):
        self.menu_frame.pack_forget()
        self.theme_frame.pack(expand=True)
        self.audio_label_1.config(text=self._audio_text())
        self.title("Keyboard Instruments - Elegir tema")

    def show_menu(self):
        self.theme_frame.pack_forget()
        self.menu_frame.pack(expand=True)
        if self.song is not None:
            self.theme_title.config(text=self.theme_name)
            self.theme_summary.config(text=self.song.summary())
            self.listen_button.pack(fill="x", pady=8, before=self.audio_label_2)
        else:
            self.theme_title.config(text="Sin tema")
            self.theme_summary.config(text="Modo libre: cada tecla toca la nota que configuraste.")
            self.listen_button.pack_forget()
        self.audio_label_2.config(text=self._audio_text())
        self.title(f"Keyboard Instruments - {self.theme_name or 'Sin tema'}")

    def open_play(self):
        self.withdraw()
        PlayChooser(self)

    def open_editor(self):
        self.withdraw()
        EditorWindow(self)

    def open_listen(self):
        if self.song is None:
            return
        self.withdraw()
        ListenWindow(self)

    def back_here(self):
        self.deiconify()
        self.lift()

    def _visible(self):
        theme = [p for p in self.all_profiles if self.theme_key and p.get("theme") == self.theme_key]
        return theme + [p for p in self.all_profiles if not p.get("theme")]  # the theme's instruments first

    def save_profiles(self):
        """self.profiles is the visible list (edited in place by the editor); merge it back."""
        hidden = [p for p in self.all_profiles if p.get("theme") and p.get("theme") != self.theme_key]
        self.all_profiles = [p for p in self.profiles if not p.get("theme")] + \
            [p for p in self.profiles if p.get("theme")] + hidden
        save_profiles(PROFILES_FILE, self.all_profiles)

    def close(self):
        save_settings(self.settings)
        self.engine.stop()
        self.destroy()


# ==========================================================================
# Modificar instrumento
# ==========================================================================
class EditorWindow(tk.Toplevel):
    COLS = 10  # slots per row in the grid

    def __init__(self, menu):
        super().__init__(menu)
        self.menu = menu
        self.engine = menu.engine
        self.title("Configurar instrumentos")
        self.configure(bg=BG)
        self.minsize(1000, 700)
        maximize(self)
        self.idx = max(0, min(menu.settings.get("edit_profile", 0), len(menu.profiles) - 1))
        self.capturing = None
        self.slot_widgets = []
        self._build()
        self._load_profile(self.idx)
        self.bind("<KeyPress>", self._capture_key)
        self.protocol("WM_DELETE_WINDOW", self.back)

    @property
    def profile(self):
        return self.menu.profiles[self.idx]

    # ---- layout -----------------------------------------------------------
    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        head = ttk.Frame(root)
        head.pack(fill="x")
        ttk.Label(head, text="Configurar instrumentos", style="H1.TLabel").pack(side="left")
        ttk.Button(head, text="← Guardar y volver al menú", command=self.back).pack(side="right")

        # --- which instrument ----------------------------------------------
        top = ttk.Frame(root, style="Panel.TFrame", padding=12)
        top.pack(fill="x", pady=(12, 0))
        ttk.Label(top, text="Instrumento:", style="Muted.TLabel").grid(row=0, column=0, sticky="e")
        self.profile_var = tk.StringVar()
        self.profile_box = ttk.Combobox(top, textvariable=self.profile_var, state="readonly", width=24)
        self.profile_box.grid(row=0, column=1, sticky="w", padx=8)
        self.profile_box.bind("<<ComboboxSelected>>", lambda _e: self._load_profile(self.profile_box.current()))
        ttk.Button(top, text="Nuevo", command=self._new).grid(row=0, column=2, padx=2)
        ttk.Button(top, text="Duplicar", command=self._duplicate).grid(row=0, column=3, padx=2)
        ttk.Button(top, text="Borrar", command=self._delete).grid(row=0, column=4, padx=2)

        ttk.Label(top, text="Nombre:", style="Muted.TLabel").grid(row=1, column=0, sticky="e", pady=(10, 0))
        self.name_var = tk.StringVar()
        name = ttk.Entry(top, textvariable=self.name_var, width=26)
        name.grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))
        name.bind("<KeyRelease>", lambda _e: self._rename())
        ttk.Label(top, text="Sonido:", style="Muted.TLabel").grid(row=1, column=2, sticky="e", pady=(10, 0))
        self.sound_var = tk.StringVar()
        self.sound_box = ttk.Combobox(top, textvariable=self.sound_var, state="readonly", width=20,
                                      values=[i.name for i in INSTRUMENTS])
        self.sound_box.grid(row=1, column=3, columnspan=2, sticky="w", padx=8, pady=(10, 0))
        self.sound_box.bind("<<ComboboxSelected>>", lambda _e: self._change_sound())
        self.sound_desc = ttk.Label(top, text="", style="Muted.TLabel")
        self.sound_desc.grid(row=2, column=1, columnspan=6, sticky="w", padx=8, pady=(4, 0))

        self.tracks = []
        if self.menu.theme_path:
            try:
                self.tracks = midi_tracks(self.menu.theme_path)
            except Exception:
                self.tracks = []
        self.pc_sel = set()

        # --- tools: only the essentials ---------------------------------------------
        tools = ttk.Frame(root, style="Panel.TFrame", padding=12)
        tools.pack(fill="x", pady=(12, 0))
        ttk.Label(tools, text="Cantidad de teclas:", style="Muted.TLabel").grid(row=0, column=0, sticky="e")
        self.count_var = tk.StringVar()
        count = ttk.Spinbox(tools, from_=1, to=MAX_KEYS, textvariable=self.count_var, width=5,
                            command=self._change_count)
        count.grid(row=0, column=1, sticky="w", padx=8)
        count.bind("<Return>", lambda _e: self._change_count())
        count.bind("<FocusOut>", lambda _e: self._change_count())
        ttk.Label(tools, text="Subir / bajar todas las notas:", style="Muted.TLabel").grid(row=0, column=2,
                                                                                         sticky="e", padx=(30, 6))
        ttk.Button(tools, text="\u25C0", width=4, command=lambda: self._rotate(-1)).grid(row=0, column=3)
        ttk.Button(tools, text="\u25B6", width=4, command=lambda: self._rotate(1)).grid(row=0, column=4, padx=4)

        big = ttk.Frame(tools, style="Panel.TFrame")
        big.grid(row=1, column=0, columnspan=6, sticky="w", pady=(12, 0))
        ttk.Button(big, text="Cambiar config de teclado...", style="Go.TButton",
                   command=lambda: KeyboardDialog(self)).pack(side="left")
        self.midi_info = ttk.Label(tools, text="", style="Muted.TLabel", wraplength=900, justify="left")
        self.midi_info.grid(row=2, column=0, columnspan=6, sticky="w", pady=(8, 0))

        # --- slots (scrollable) ----------------------------------------------------
        ttk.Label(root, text="Cada casilla: tecla (clic y presiona la tecla que quieras), nota y octava, "
                             "▶ para escuchar.", style="BgMuted.TLabel").pack(anchor="w", pady=(12, 4))
        wrap = ttk.Frame(root, style="Panel.TFrame")
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = ttk.Frame(self.canvas, style="Panel.TFrame", padding=8)
        self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>",
                             lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"))

        self.status = ttk.Label(root, text="Los cambios se guardan solos.", style="BgMuted.TLabel")
        self.status.pack(anchor="w", pady=(8, 0))

    def _wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass

    # ---- profile management -------------------------------------------------
    def _refresh_list(self):
        self.profile_box.config(values=[p["name"] for p in self.menu.profiles])
        self.profile_box.current(self.idx)

    def _load_profile(self, idx):
        self._stop_capture()
        self.idx = max(0, min(idx, len(self.menu.profiles) - 1))
        self.menu.settings["edit_profile"] = self.idx
        p = self.profile
        self._refresh_list()
        self.name_var.set(p["name"])
        self.sound_var.set(SOUND_NAMES.get(p["sound"], p["sound"]))
        self.sound_desc.config(text=instrument_by_id(p["sound"]).description)
        self.count_var.set(str(len(p["slots"])))
        if not is_drums(p["sound"]):
            self.pc_sel = {s["midi"] % 12 for s in p["slots"]}
        self._build_slots()

    def _changed(self, msg="Cambios guardados."):
        self.menu.save_profiles()
        self.status.config(text=msg)

    def _new(self):
        base = default_profiles()[1]
        base["name"] = f"Instrumento {len(self.menu.profiles) + 1}"
        if self.menu.theme_key:
            base["theme"] = self.menu.theme_key
        self.menu.profiles.append(base)
        self._load_profile(len(self.menu.profiles) - 1)
        self._changed("Nuevo instrumento creado (copia del Piano por defecto).")

    def _duplicate(self):
        p = json.loads(json.dumps(self.profile))
        p["name"] += " (copia)"
        self.menu.profiles.insert(self.idx + 1, p)
        self._load_profile(self.idx + 1)
        self._changed("Instrumento duplicado.")

    def _delete(self):
        if len(self.menu.profiles) <= 1:
            self.status.config(text="Tiene que quedar al menos un instrumento.")
            return
        name = self.profile["name"]
        del self.menu.profiles[self.idx]
        self._load_profile(max(0, self.idx - 1))
        self._changed(f"Borrado: {name}.")

    def _restore(self):
        names = {p["name"] for p in default_profiles()}
        self.menu.profiles = [p for p in self.menu.profiles if p["name"] not in names]
        self.menu.profiles[0:0] = default_profiles()
        self._load_profile(0)
        self._changed("Guitarra y Piano restaurados a sus valores por defecto.")

    def _rename(self):
        self.profile["name"] = self.name_var.get().strip() or "Sin nombre"
        values = list(self.profile_box.cget("values"))
        values[self.idx] = self.profile["name"]
        self.profile_box.config(values=values)
        self.profile_var.set(self.profile["name"])
        self.menu.save_profiles()

    @staticmethod
    def _track_label(t):
        return f"{t['name'][:28]}  ({t['instrument']})"

    def _set_sound(self, sound):
        p = self.profile
        was_drums, now_drums = is_drums(p["sound"]), is_drums(sound)
        p["sound"] = sound
        if was_drums != now_drums:
            if now_drums:
                p["slots"] = [{"key": s["key"], "piece": PIECE_IDS[i % len(PIECE_IDS)]}
                              for i, s in enumerate(p["slots"])]
            else:
                notes = scale_notes(40, "Mayor", len(p["slots"]))
                p["slots"] = [{"key": s["key"], "midi": m} for s, m in zip(p["slots"], notes)]

    def apply_layout(self, layout, low_oct=3, high_oct=4):
        if layout == "piano":
            self.profile["slots"] = piano_layout(low_oct, high_oct)
            self.count_var.set(str(len(self.profile["slots"])))
            self.pc_sel = {x["midi"] % 12 for x in self.profile["slots"]}
            self._build_slots()
            self._changed("Teclado tipo piano: blancas en Z X C... y Q W E..., negras en la fila de arriba.")
            return
        self.layout_var = tk.StringVar(value=layout)
        self._fill_keys()

    def _change_sound(self):
        sound = next((i.id for i in INSTRUMENTS if i.name == self.sound_var.get()), "piano")
        self._set_sound(sound)
        self._load_profile(self.idx)
        self._changed(f"Sonido: {self.sound_var.get()}.")

    def _change_count(self):
        try:
            n = max(1, min(MAX_KEYS, int(float(self.count_var.get()))))
        except ValueError:
            n = len(self.profile["slots"])
        slots = self.profile["slots"]
        if n == len(slots):
            self.count_var.set(str(n))
            return
        drums = is_drums(self.profile["sound"])
        while len(slots) > n:
            slots.pop()
        while len(slots) < n:
            k = free_key({s["key"] for s in slots})
            if k is None:
                break
            if drums:
                ids = DRUM_GROUPS.get(self.profile.get("part"), PIECE_IDS)
                slots.append({"key": k, "piece": ids[len(slots) % len(ids)]})
            else:
                prev = slots[-1]["midi"] if slots else 52
                slots.append({"key": k, "midi": min(prev + 2, 108)})
        self.count_var.set(str(len(slots)))
        self._build_slots()
        self._changed(f"{len(slots)} teclas.")

    def _pcs_text(self):
        return " ".join(NOTE_NAMES[pc] for pc in sorted(self.pc_sel)) or "(ninguna)"

    def _rotate(self, step):
        """Every key moves to the next (step=+1) or previous (-1) note of the marked scale.
        E F# G# A ... becomes F# G# A B ... (each key goes up one note; nothing wraps around).
        With a drum kit, the pieces move one box instead."""
        slots = self.profile["slots"]
        if not slots:
            return
        if "piece" in slots[0]:
            values = [s["piece"] for s in slots]
            values = values[-step:] + values[:-step]
            for s, v in zip(slots, values):
                s["piece"] = v
            self._build_slots()
            self._changed("Piezas movidas una casilla.")
            return
        pcs = self.pc_sel or {s["midi"] % 12 for s in slots}
        moved = []
        for s in slots:
            m = s["midi"] + step
            while 0 <= m <= 127 and m % 12 not in pcs:
                m += step
            moved.append(m if 0 <= m <= 127 else s["midi"])
        for s, m in zip(slots, moved):
            s["midi"] = m
        self._build_slots()
        self._changed(("Subieron" if step > 0 else "Bajaron") + " todas las notas un paso de la escala "
                      f"({self._pcs_text() if self.pc_sel else 'notas actuales'}).")

    def _fill_keys(self):
        order = LAYOUTS[self.layout_var.get()]
        slots = self.profile["slots"]
        n = min(len(order), len(slots))
        used = set(order[:n])
        old = [s["key"] for s in slots]
        for i, s in enumerate(slots):
            if i < n:
                s["key"] = order[i]
            else:  # keep the old key if it's still free, else find one
                k = old[i] if old[i] not in used else free_key(used)
                s["key"] = k
                used.add(k)
        self._build_slots()
        self._changed("Teclas asignadas.")

    # ---- slot grid ------------------------------------------------------------
    def _build_slots(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.slot_widgets = []
        drums = is_drums(self.profile["sound"])
        for i, s in enumerate(self.profile["slots"]):
            r, c = divmod(i, self.COLS)
            cell = tk.Frame(self.grid_frame, bg="#33363f", padx=4, pady=4)
            cell.grid(row=r, column=c, padx=3, pady=3, sticky="n")
            tk.Label(cell, text=str(i + 1), bg="#33363f", fg=MUTED, font=("TkDefaultFont", 8)).pack()
            kb = ttk.Button(cell, text=key_label(s["key"]), width=7, style="Key.TButton",
                            command=lambda i=i: self._start_capture(i))
            kb.pack(pady=1)
            if drums:
                pv = tk.StringVar(value=DRUM_PIECES[s["piece"]][0])
                box = ttk.Combobox(cell, textvariable=pv, values=PIECE_LABELS, width=8, state="readonly")
                box.pack(pady=1)
                box.bind("<<ComboboxSelected>>", lambda _e, i=i, v=pv: self._set_piece(i, v.get()))
            else:
                nv = tk.StringVar(value=NOTE_NAMES[s["midi"] % 12])
                ov = tk.StringVar(value=str(s["midi"] // 12 - 1))
                nb = ttk.Combobox(cell, textvariable=nv, values=NOTE_NAMES, width=4, state="readonly")
                nb.pack(pady=1)
                ob = ttk.Combobox(cell, textvariable=ov, values=OCTAVES, width=4, state="readonly")
                ob.pack(pady=1)
                for box in (nb, ob):
                    box.bind("<<ComboboxSelected>>", lambda _e, i=i, n=nv, o=ov: self._set_note(i, n, o))
            ttk.Button(cell, text="▶", width=4, style="Key.TButton",
                       command=lambda i=i: self._preview(i)).pack(pady=1)
            self.slot_widgets.append(kb)

    def _set_note(self, i, nv, ov):
        self.profile["slots"][i]["midi"] = midi_of(nv.get(), ov.get())
        self._changed()
        self._preview(i)

    def _set_piece(self, i, label):
        self.profile["slots"][i]["piece"] = PIECE_IDS[PIECE_LABELS.index(label)]
        self._changed()
        self._preview(i)

    def _preview(self, i):
        s = self.profile["slots"][i]
        ins = instrument_by_id(self.profile["sound"])
        tag = ("preview", i)
        if "piece" in s:
            self.engine.note_on(tag, ins, piece=s["piece"])
        else:
            self.engine.note_on(tag, ins, freq=freq_of(s["midi"]))
        self.after(450, lambda: self.engine.note_off(tag))

    # ---- key capture ----------------------------------------------------------
    def _start_capture(self, i):
        self._stop_capture()
        self.capturing = i
        self.slot_widgets[i].config(text="presiona...")
        self.status.config(text="Presiona la tecla que quieres para esa casilla (Esc cancela).")
        self.focus_set()

    def _stop_capture(self):
        if self.capturing is not None and self.capturing < len(self.slot_widgets):
            i = self.capturing
            try:
                self.slot_widgets[i].config(text=key_label(self.profile["slots"][i]["key"]))
            except tk.TclError:
                pass
        self.capturing = None

    def _capture_key(self, event):
        if self.capturing is None:
            return
        i = self.capturing
        k = event.keysym.lower()
        if k in RESERVED_KEYS or k.startswith(("shift", "control", "alt", "super", "caps", "win", "meta")):
            self._stop_capture()
            self.status.config(text="Esa tecla está reservada (Espacio, Enter, flechas, Esc, Tab).")
            return "break"
        slots = self.profile["slots"]
        other = next((j for j, s in enumerate(slots) if s["key"] == k and j != i), None)
        if other is not None:  # swap so no key plays two notes
            slots[other]["key"] = slots[i]["key"]
            self.slot_widgets[other].config(text=key_label(slots[other]["key"]))
        slots[i]["key"] = k
        self._stop_capture()
        self._changed(f"Casilla {i + 1} = tecla {key_label(k)}.")
        return "break"

    def back(self):
        self._stop_capture()
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")
        self.menu.save_profiles()
        self.destroy()
        self.menu.back_here()



# ==========================================================================
# Small dialogs of the editor
# ==========================================================================
class KeyboardDialog(tk.Toplevel):
    """Cambiar config de teclado: which computer keys are used, in which order."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.title("Config de teclado")
        self.configure(bg=BG)
        self.transient(editor)
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="Config de teclado", style="H1.TLabel").pack(anchor="w")
        ttk.Label(root, text="Las notas se reparten de grave a agudo en este orden de teclas:",
                  style="BgMuted.TLabel").pack(anchor="w", pady=(8, 6))
        self.choice = tk.StringVar(value=list(LAYOUTS)[1])
        panel = ttk.Frame(root, style="Panel.TFrame", padding=10)
        panel.pack(fill="x")
        for name in LAYOUTS:
            ttk.Radiobutton(panel, text=name, value=name, variable=self.choice).pack(anchor="w", pady=1)
        ttk.Radiobutton(panel, text="Tipo piano: blancas en Z X C... y Q W E..., negras en la fila de arriba",
                        value="piano", variable=self.choice).pack(anchor="w", pady=(6, 1))
        octs = ttk.Frame(panel, style="Panel.TFrame")
        octs.pack(anchor="w", padx=24)
        ttk.Label(octs, text="fila Z empieza en C", style="Muted.TLabel").pack(side="left")
        self.low = tk.StringVar(value="3")
        self.high = tk.StringVar(value="4")
        ttk.Combobox(octs, textvariable=self.low, values=OCTAVES, width=3, state="readonly").pack(side="left", padx=4)
        ttk.Label(octs, text="  fila Q empieza en C", style="Muted.TLabel").pack(side="left")
        ttk.Combobox(octs, textvariable=self.high, values=OCTAVES, width=3, state="readonly").pack(side="left", padx=4)
        ttk.Label(root, text="Tipo piano cambia también las notas (37 teclas cromáticas).",
                  style="BgMuted.TLabel").pack(anchor="w", pady=(8, 0))
        btns = ttk.Frame(root)
        btns.pack(fill="x", pady=(16, 0))
        ttk.Button(btns, text="Aplicar", style="Go.TButton", command=self._ok).pack(side="right")
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="right", padx=8)

    def _ok(self):
        choice, low, high = self.choice.get(), int(self.low.get()), int(self.high.get())
        self.destroy()
        self.editor.apply_layout(choice, low, high)


# ==========================================================================
# Tocar instrumento: choose what to play
# ==========================================================================
class PlayChooser(tk.Toplevel):
    """Tocar instrumento: pick the instrument, Manual (you play) or Automático (practice), and the tempo."""

    def __init__(self, menu):
        super().__init__(menu)
        self.menu = menu
        self.engine = menu.engine
        self.song = menu.song
        self.title("Tocar instrumento")
        self.configure(bg=BG)
        self.minsize(700, 380)
        maximize(self)
        self.tracks = []
        if self.menu.theme_path:
            try:
                self.tracks = midi_tracks(self.menu.theme_path)
            except Exception:
                self.tracks = []
        self._build()
        self._restore()
        self.protocol("WM_DELETE_WINDOW", self.back)

    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(expand=True)
        head = ttk.Frame(root)
        head.pack(fill="x")
        ttk.Label(head, text="Tocar instrumento", style="H1.TLabel").pack(side="left")
        ttk.Button(head, text="← Volver al menú", command=self.back).pack(side="right")
        ttk.Label(root, text=f"Tema: {self.menu.theme_name or 'Sin tema'}", style="BgMuted.TLabel").pack(anchor="w")

        box = ttk.Frame(root, style="Panel.TFrame", padding=12)
        box.pack(fill="x", pady=(12, 0))
        ttk.Label(box, text="Instrumento", style="H2.TLabel").grid(row=0, column=0, sticky="w")
        self.profile_var = tk.StringVar()
        self.profile_box = ttk.Combobox(box, textvariable=self.profile_var, state="readonly", width=30,
                                        values=[p["name"] for p in self.menu.profiles])
        self.profile_box.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.profile_box.bind("<<ComboboxSelected>>", lambda _e: self._update())
        self.profile_info = ttk.Label(box, text="", style="Muted.TLabel")
        self.profile_info.grid(row=1, column=1, sticky="w", padx=12, pady=(6, 0))

        sp = ttk.Frame(root, style="Panel.TFrame", padding=12)
        sp.pack(fill="x", pady=(12, 0))
        ttk.Label(sp, text="Modo", style="H2.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        self.mode_var = tk.StringVar(value="manual")
        ttk.Radiobutton(sp, text="Manual: tocas tú (las notas que vienen se ven en pantalla)", value="manual",
                        variable=self.mode_var, command=self._update).grid(row=1, column=0, columnspan=3,
                                                                           sticky="w", pady=(6, 0))
        self.auto_radio = ttk.Radiobutton(sp, text="Automático: para practicar, la app toca la pista sola",
                                          value="auto", variable=self.mode_var, command=self._update)
        self.auto_radio.grid(row=2, column=0, columnspan=3, sticky="w")
        self.band_radio = ttk.Radiobutton(sp, text="Práctica con banda: suena el resto de la banda y tú tocas tu "
                                          "instrumento", value="band", variable=self.mode_var, command=self._update)
        self.band_radio.grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Label(sp, text="Pista:", style="Muted.TLabel").grid(row=4, column=0, sticky="e", pady=(10, 0))
        self.track_var = tk.StringVar()
        self.track_box = ttk.Combobox(sp, textvariable=self.track_var, state="readonly", width=42,
                                      values=[NO_TRACK] + [f"{t['name'][:28]}  ({t['instrument']})"
                                                           for t in self.tracks])
        self.track_box.grid(row=4, column=1, sticky="w", padx=8, pady=(10, 0))
        self.track_box.bind("<<ComboboxSelected>>", lambda _e: self._update())
        ttk.Label(sp, text="Tempo:", style="Muted.TLabel").grid(row=5, column=0, sticky="e", pady=(8, 0))
        trow = ttk.Frame(sp, style="Panel.TFrame")
        trow.grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        base = int(round(self.song.bpm)) if self.song else 120
        self.tempo_var = tk.StringVar(value=str(base))
        self.tempo_spin = ttk.Spinbox(trow, from_=20, to=400, textvariable=self.tempo_var, width=6)
        self.tempo_spin.pack(side="left")
        ttk.Label(trow, text=f"BPM   (original: {base})", style="Muted.TLabel").pack(side="left", padx=6)
        self.pct_buttons = []
        for pct in (50, 75, 100):
            b = ttk.Button(trow, text=f"{pct}%", width=5,
                           command=lambda pct=pct: self.tempo_var.set(str(round(base * pct / 100))))
            b.pack(side="left", padx=2)
            self.pct_buttons.append(b)
        self.hint = ttk.Label(sp, text="", style="Muted.TLabel", wraplength=640, justify="left")
        self.hint.grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", pady=(16, 0))
        self.status = ttk.Label(bottom, text="", style="BgMuted.TLabel")
        self.status.pack(side="left")
        self.go = ttk.Button(bottom, text="Tocar  →", style="Go.TButton", command=self._play)
        self.go.pack(side="right")

    def _profile(self):
        i = self.profile_box.current()
        return self.menu.profiles[i] if 0 <= i < len(self.menu.profiles) else None

    def _profile_track(self, p):
        """Index of the MIDI track this instrument comes from (theme instruments), or None."""
        if p is None or not self.tracks:
            return None
        if isinstance(p.get("track"), int) and p.get("theme") == self.menu.theme_key:
            return p["track"]
        return None

    def _selected_track(self):
        i = self.track_box.current()
        return self.tracks[i - 1]["index"] if 1 <= i <= len(self.tracks) else None

    def _restore(self):
        s = self.menu.settings
        names = [p["name"] for p in self.menu.profiles]
        self.profile_box.current(names.index(s["play_profile"]) if s.get("play_profile") in names else 0)
        self.mode_var.set(s.get("play_mode", "manual") if self.tracks else "manual")
        self._last_profile = None
        self._update()

    def _update(self):
        p = self._profile()
        if p:
            self.profile_info.config(text=f"{SOUND_NAMES.get(p['sound'], p['sound'])}, {len(p['slots'])} teclas")
        if p is not self._last_profile:  # new instrument: follow its own track
            own = self._profile_track(p)
            idx = [t["index"] for t in self.tracks]
            self.track_box.current(idx.index(own) + 1 if own in idx else 0)
            self._last_profile = p
        has_song = self.song is not None
        for w in [self.track_box, self.tempo_spin] + self.pct_buttons:
            w.state(["!disabled"] if has_song else ["disabled"])
        self.auto_radio.state(["!disabled"] if self.tracks else ["disabled"])
        self.band_radio.state(["!disabled"] if self.tracks else ["disabled"])
        auto = self.mode_var.get() == "auto"
        band = self.mode_var.get() == "band"
        track = self._selected_track()
        ok = p is not None and len(p["slots"]) > 0
        msg = ""
        if (auto or band) and track is None:
            ok, msg = False, ("Automático" if auto else "Práctica con banda") + " necesita una pista."
        if not has_song:
            self.hint.config(text="Sin tema: tocas libre con tus teclas (con metrónomo si quieres).")
        elif band:
            self.hint.config(text="La app toca todos los demás instrumentos del tema y tú tocas la pista elegida "
                                  "con tus teclas, con las notas cayendo en pantalla. El volumen de la banda se "
                                  "ajusta en la ventana.")
        elif auto:
            self.hint.config(text="La app toca la pista elegida nota por nota al tempo que pongas. Las notas "
                                  "que vienen se ven en pantalla y se marcan tus teclas. Puedes tocar encima.")
        elif track is not None:
            self.hint.config(text="Tocas tú. En pantalla se ven las notas de la pista acercándose a la línea: "
                                  "presiona la tecla que marca cuando la nota llega.")
        else:
            self.hint.config(text="Tocas tú sobre la canción; se muestran los acordes y el compás.")
        self.go.state(["!disabled"] if ok else ["disabled"])
        self.status.config(text=msg)

    def _play(self):
        p = self._profile()
        if p is None:
            return
        auto = self.mode_var.get() == "auto"
        s = self.menu.settings
        s.update({"play_profile": p["name"], "play_mode": self.mode_var.get()})
        save_settings(s)
        try:
            bpm = max(20.0, min(400.0, float(self.tempo_var.get())))
        except ValueError:
            bpm = self.song.bpm if self.song else 120
        self.withdraw()
        PlayWindow(self, self.engine, p, song=self.song, track=self._selected_track() if self.song else None,
                   auto=auto, bpm=bpm, band=self.mode_var.get() == "band")

    def back_from_play(self):
        self.deiconify()
        self.lift()

    def back(self):
        self.destroy()
        self.menu.back_here()


# ==========================================================================
# The instrument
# ==========================================================================
class PlayWindow(tk.Toplevel):
    def __init__(self, chooser, engine, profile, song=None, role_id=None, track=None, auto=False, bpm=None,
                 band=False):
        super().__init__(chooser)
        self.chooser = chooser
        self.engine = engine
        self.profile = profile
        self.ins = instrument_by_id(profile["sound"])
        self.slots = profile["slots"]
        self.drums = self.ins.kind == "drums"
        self.part = profile.get("part") if self.drums else None  # "tambores" / "platillos" / None
        self.keymap = {s["key"]: i for i, s in enumerate(self.slots)}
        self.held = set()
        self.pending_release = {}
        self.pads = []
        self.octave_shift = 0
        self.palm = False
        self.power_var = tk.BooleanVar(value=False)

        # song mode state
        self.song = song
        self.role_id = role_id
        self.role = ROLES.get(role_id) if role_id else None
        self.melody = role_id == "melody"
        # following the theme: Manual (you play) or Automático (the app plays the track)
        self.track = track if song is not None else None
        self.auto = bool(auto) and self.track is not None
        self.band = bool(band) and self.track is not None and not self.auto  # practice with the band
        self.band_notes = []     # [(on, off, pitch, instrument)] of every other instrument of the theme
        self.band_starts = []
        self.band_ptr = 0
        self.band_gain = [0.5]   # shared with the scheduled voices: the slider acts at once
        self.auto_bpm = bpm or (song.bpm if song else 120)
        self.auto_events = []    # [(song_seconds_on, song_seconds_off, pitch)] of the followed track
        self.auto_starts = []    # their start times, for bisect
        self.auto_ptr = 0
        self.auto_sounding = []  # [(on, off, slot index)] for lighting pads
        self.auto_drums = False
        self.auto_name = ""
        if song is not None and role_id is None:
            self.role = {"name": "Automático (práctica)" if self.auto else
                         ("Práctica con banda" if self.band else "Manual"), "pattern": [], "step": 1.0,
                         "tip": ("La app toca la pista sola, nota por nota. Puedes tocar encima." if self.auto
                                 else "Presiona la tecla marcada cuando la nota llega a la línea.")}
        if self.track is not None:
            path = chooser.menu.theme_path
            info = next((t for t in midi_tracks(path) if t["index"] == self.track), None)
            self.auto_drums = bool(info and info["drums"])
            self.auto_name = info["name"] if info else f"Pista {self.track}"
            if self.track == profile.get("track") and profile.get("extra_tracks"):
                self.auto_name = profile["name"]
            tracks = [self.track]
            if self.track == profile.get("track"):
                tracks += profile.get("extra_tracks", [])  # e.g. Guitarra 2 + solista: both tracks
            evs = sorted(ev for tr in tracks for ev in track_events(path, tr))
            for b0, b1, pitch, _ch in evs:
                if self.part and drum_group(GM_DRUMS.get(pitch, "hat")) != self.part:
                    continue  # the other half of the kit is someone else's
                self.auto_events.append((song.beat_to_seconds(b0 - song.offset),
                                         song.beat_to_seconds(b1 - song.offset), pitch))
            self.auto_starts = [ev[0] for ev in self.auto_events]
        if self.band:
            self._load_band(chooser.menu, profile)
        if self.drums or self.auto_drums or self.band:  # synthesise the drum hits now, not on the first hit
            threading.Thread(target=DrumKit.warm, daemon=True).start()
        self.cur_idx = 0
        self.start_beat = 0.0   # song beat that corresponds to transport beat 0
        self.running = False
        self.sec0 = 0.0
        self.countin = 0.0
        self.speed = 1.0
        self.pat_pos = 0
        self.slot_midis = [s.get("midi") for s in self.slots]
        self.slot_labels = ["" for _ in self.slots]
        self.tones = []
        self.pad_base = [PANEL] * len(self.slots)
        if not self.drums:  # sharps look like the black keys of a piano
            self.pad_base = [BLACK_KEY if s["midi"] % 12 in SHARPS else PANEL for s in self.slots]

        self.title(f"{profile['name']}" + (f" - {self.role['name']}" if self.role else ""))
        self.configure(bg=BG)
        self.minsize(1100, 640)
        maximize(self)
        self._build()
        if self.song is not None:
            self._set_chord(0)
            self._tick_id = self.after(25, self._tick)

        self.bind("<KeyPress>", self._on_press)
        self.bind("<KeyRelease>", self._on_release)
        self.bind("<FocusOut>", self._on_focus_out)
        self.protocol("WM_DELETE_WINDOW", self.back)
        self.after(50, self.focus_force)

    # ---- layout -----------------------------------------------------------
    def _refocus(self, *_):
        self.after(1, self.focus_set)

    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x")
        tk.Frame(header, bg=self.ins.color, width=10, height=40).pack(side="left", padx=(0, 10))
        ttk.Label(header, text=self.profile["name"], style="H1.TLabel").pack(side="left")
        sub = f"   {self.ins.name}" + (f"  ·  {self.role['name']}" if self.role else "")
        ttk.Label(header, text=sub, style="BgMuted.TLabel").pack(side="left")
        ttk.Button(header, text="← Volver", command=self.back, takefocus=False).pack(side="right")

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, pady=12)

        self.highway = None
        self.hw_lit = set()
        if self.auto_events:
            self._build_highway_layout(body)
            return

        # --- left: instrument info ----------------------------------------------------------------
        info = ttk.Frame(body, style="Panel.TFrame", padding=12)
        if not self.auto_events:
            info.pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(info, text="Sobre el instrumento", style="H2.TLabel").pack(anchor="w")
        ttk.Label(info, text=self.ins.description, style="Panel.TLabel", wraplength=220,
                  justify="left").pack(anchor="w", pady=(6, 10))
        for k, v in self.ins.info.items():
            ttk.Label(info, text=k, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(info, text=v, style="Panel.TLabel", wraplength=220,
                      justify="left").pack(anchor="w", pady=(0, 6))
        ttk.Label(info, text="Más información: próximamente", style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

        # --- right side --------------------------------------------------------
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        if self.song is not None:
            self._build_song_panel(right)
        else:
            ttk.Label(right, text="Toca con las teclas (o haz clic en los pads).",
                      style="BgMuted.TLabel").pack(anchor="w")

        self._build_tech_row(right)

        self._build_pads(right)
        self._build_controls(right)

    def _load_band(self, menu, profile):
        """Every theme instrument except mine (for a drum half: only my half is left out)."""
        song, path = self.song, menu.theme_path
        for p in menu.profiles:
            if p is profile or p.get("theme") != menu.theme_key or not isinstance(p.get("track"), int):
                continue
            tracks = [p["track"]] + p.get("extra_tracks", [])
            if self.track in tracks and (not p.get("part") or not self.part or p.get("part") == self.part):
                continue  # that's the part I'm playing
            ins = instrument_by_id(p["sound"])
            part = p.get("part") if ins.kind == "drums" else None
            for tr in tracks:
                for b0, b1, pitch, _ch in track_events(path, tr):
                    if part and drum_group(GM_DRUMS.get(pitch, "hat")) != part:
                        continue
                    self.band_notes.append((song.beat_to_seconds(b0 - song.offset),
                                            song.beat_to_seconds(b1 - song.offset), pitch, ins))
        self.band_notes.sort(key=lambda n: n[0])
        self.band_starts = [n[0] for n in self.band_notes]

    def _band_reset(self, from_sec):
        if self.band:
            self.band_ptr = bisect_left(self.band_starts, from_sec - 1e-6)

    def _band_schedule(self, t):
        """Hands the engine the band's notes of the next ~0.3 s (sample-exact, on the song clock)."""
        horizon = self.sec0 + (t - self.countin) * self.speed + 0.3 * self.speed
        events = []
        while self.band_ptr < len(self.band_notes) and self.band_notes[self.band_ptr][0] < horizon:
            on, off, pitch, ins = self.band_notes[self.band_ptr]
            tag = ("band", self.band_ptr)
            s_on = int(round((self.countin + (on - self.sec0) / self.speed) * SR))
            s_off = int(round((self.countin + (off - self.sec0) / self.speed) * SR))
            if ins.kind == "drums":
                voice = ins.voice(piece=GM_DRUMS.get(pitch, "hat"))
            else:
                voice = ins.voice(freq=freq_of(pitch))
            events += [(s_on, "on", tag, GainVoice(voice, self.band_gain, 0)),
                       (max(s_off, s_on + 1), "off", tag, None)]
            self.band_ptr += 1
        if events:
            self.engine.schedule(events)

    def _build_tech_row(self, parent):
        tech = ttk.Frame(parent)
        tech.pack(fill="x", pady=(6, 0))
        self.palm_label = None
        self.oct_label = None
        if "palm_mute" in self.ins.techniques:
            self.palm_label = tk.Label(tech, text="PALM MUTE  (mantén Espacio)", bg=PANEL, fg=MUTED,
                                       padx=10, pady=4, font=("TkDefaultFont", 10, "bold"))
            self.palm_label.pack(side="left", padx=(0, 8))
        if "power" in self.ins.techniques:
            ttk.Checkbutton(tech, text="Power chords  (Enter)", variable=self.power_var,
                            takefocus=False, command=self._refocus).pack(side="left", padx=(0, 8))
        if not self.drums:
            self.oct_label = ttk.Label(tech, text="", style="BgMuted.TLabel")
            self.oct_label.pack(side="left", padx=(8, 0))
            self._update_octave_label()
        # add / remove keys at both ends while playing
        edit = ttk.Frame(tech)
        edit.pack(side="right")
        low_txt, high_txt = ("pieza", "pieza") if self.drums else ("grave", "aguda")
        for text, cmd in ((f"\uFF0B nota {low_txt}", lambda: self._add_note(-1)),
                          ("\u2212", lambda: self._remove_note(-1)),
                          ("\u2212", lambda: self._remove_note(1)),
                          (f"nota {high_txt} \uFF0B", lambda: self._add_note(1))):
            ttk.Button(edit, text=text, takefocus=False, command=cmd,
                       width=3 if text == "\u2212" else None).pack(side="left", padx=2)
        self.edit_label = ttk.Label(tech, text="", style="BgMuted.TLabel")
        self.edit_label.pack(side="right", padx=10)
        if self.band:  # practice with the band: its volume
            bf = ttk.Frame(tech)
            bf.pack(side="left", padx=(16, 0))
            ttk.Label(bf, text="Volumen banda", style="BgMuted.TLabel").pack(side="left")
            self.band_vol = tk.IntVar(value=60)
            ttk.Scale(bf, from_=0, to=100, orient="horizontal", length=160, variable=self.band_vol,
                      command=lambda _v: self._band_volume()).pack(side="left", padx=6)
            self._band_volume()

    # ---- adding / removing keys while playing -------------------------------------
    def _band_volume(self):
        v = float(self.band_vol.get())
        self.band_gain[0] = 0.55 * (v / 60.0) ** 1.5 if v > 0 else 0.0
        self._refocus()

    def _add_note(self, side):
        """side=-1: a new key with the next note below the lowest one; +1: above the highest."""
        slots = self.slots
        if len(slots) >= MAX_KEYS:
            self.edit_label.config(text=f"Máximo {MAX_KEYS} teclas.")
            return self._refocus()
        key = free_key({s["key"] for s in slots} | RESERVED_KEYS, side)
        if key is None:
            self.edit_label.config(text="No quedan teclas libres.")
            return self._refocus()
        if self.drums:
            used = {s["piece"] for s in slots}
            ids = DRUM_GROUPS.get(self.part, PIECE_IDS)
            piece = next((pc for pc in ids if pc not in used),
                         next((pc for pc in PIECE_IDS if pc not in used), ids[0]))
            new = {"key": key, "piece": piece}
            slots.insert(0 if side < 0 else len(slots), new)
            msg = f"Nueva tecla {key_label(key)}: {DRUM_PIECES[piece][0]}"
        else:
            notes = [s["midi"] for s in slots]
            pcs = {m % 12 for m in notes}  # stay in the instrument's scale
            m = (min(notes) if side < 0 else max(notes)) + side
            while 0 < m < 127 and m % 12 not in pcs:
                m += side
            m = max(0, min(127, m))
            new = {"key": key, "midi": m}
            slots.insert(0 if side < 0 else len(slots), new)
            msg = f"Nueva tecla {key_label(key)}: {note_text(m)}"
        self._keys_changed()
        self.edit_label.config(text=msg)
        self._refocus()

    def _remove_note(self, side):
        slots = self.slots
        if len(slots) <= 1:
            return self._refocus()
        if self.drums:
            i = 0 if side < 0 else len(slots) - 1
        else:
            vals = [s["midi"] for s in slots]
            i = vals.index(min(vals) if side < 0 else max(vals))
        gone = slots.pop(i)
        self._keys_changed()
        self.edit_label.config(text=f"Quitada la tecla {key_label(gone['key'])}")
        self._refocus()

    def _keys_changed(self):
        """Refresh everything that depends on the key list and save the instrument."""
        self._release_all()
        self.keymap = {s["key"]: i for i, s in enumerate(self.slots)}
        self.slot_midis = [s.get("midi") for s in self.slots]
        self.slot_labels = ["" for _ in self.slots]
        self.pad_base = ([BLACK_KEY if s["midi"] % 12 in SHARPS else PANEL for s in self.slots]
                         if not self.drums else [PANEL] * len(self.slots))
        self.__dict__.pop("_slot_cache", None)
        self.hw_lit = set()
        self.auto_sounding = []
        if self.highway is not None:
            order = sorted(range(len(self.slots)), key=lambda i: (self.slot_midis[i] if not self.drums else i))
            self.lanes = {slot: n for n, slot in enumerate(order)}
        elif getattr(self, "pads_frame", None) is not None:
            self.pads_frame.destroy()
            self.pads = []
            self._build_pads(self.pads_parent, before=self.ctl_frame)
        if self.song is not None and self.role_id is not None:
            self._set_chord(self.cur_idx)  # song-role modes recompute the notes per chord
        try:
            self.chooser.menu.save_profiles()
        except Exception:
            pass

    def _build_highway_layout(self, body):
        """Song panel on top, then the falling notes with the keyboard right under the line."""
        self._build_song_panel(body)
        self._build_tech_row(body)
        self._build_highway(body)
        self._build_controls(body)

    def _build_controls(self, right):
        # volume (+ metronome in free mode)
        ctl = ttk.Frame(right)
        ctl.pack(fill="x")
        self.ctl_frame = ctl
        ttk.Label(ctl, text="Volumen", style="BgMuted.TLabel").pack(side="left")
        self.vol_var = tk.DoubleVar(value=self.engine.volume)
        vol = ttk.Scale(ctl, from_=0.0, to=1.0, variable=self.vol_var, takefocus=False,
                        command=lambda v: setattr(self.engine, "volume", float(v)))
        vol.pack(side="left", fill="x", expand=True, padx=8)
        vol.bind("<ButtonRelease-1>", self._refocus)

        if self.song is None:
            self.metro_var = tk.BooleanVar(value=False)
            self.bpm_var = tk.StringVar(value=str(self.engine.metro_bpm))
            ttk.Checkbutton(ctl, text="Metrónomo", variable=self.metro_var, takefocus=False,
                            command=self._metro_changed).pack(side="left", padx=(16, 4))
            bpm = ttk.Spinbox(ctl, from_=30, to=300, textvariable=self.bpm_var, width=5,
                              command=self._metro_changed)
            bpm.pack(side="left")
            bpm.bind("<Return>", lambda _e: (self._metro_changed(), self._refocus()))
            bpm.bind("<FocusOut>", lambda _e: self._metro_changed())
            ttk.Label(ctl, text="BPM", style="BgMuted.TLabel").pack(side="left", padx=(4, 8))
            self.bar_var = tk.StringVar(value=str(self.engine.metro_beats_per_bar))
            bar = ttk.Spinbox(ctl, from_=1, to=12, textvariable=self.bar_var, width=3,
                              command=self._metro_changed)
            bar.pack(side="left")
            bar.bind("<Return>", lambda _e: (self._metro_changed(), self._refocus()))
            bar.bind("<FocusOut>", lambda _e: self._metro_changed())
            ttk.Label(ctl, text="tiempos/compás", style="BgMuted.TLabel").pack(side="left", padx=(4, 0))


    def _build_pads(self, parent, before=None):
        """Pads placed like the physical keyboard (rows staggered), so each pad sits where its key is."""
        pads = tk.Frame(parent, bg=BG)
        if before is not None:
            pads.pack(fill="both", expand=True, pady=10, before=before)
        else:
            pads.pack(fill="both", expand=True, pady=10)
        self.pads_frame, self.pads_parent = pads, parent
        placed = {}
        others = []
        for i, s in enumerate(self.slots):
            pos = KEY_POSITIONS.get(s["key"])
            if pos:
                placed[i] = pos
            else:
                others.append(i)
        rows_used = sorted({r for r, _ in placed.values()})
        grid_row = {r: n for n, r in enumerate(rows_used)}
        extra_row = len(rows_used)
        ncols = max([c + 2 for _, c in placed.values()] + [2 * len(others), 2])
        for c in range(ncols):
            pads.columnconfigure(c, weight=1, uniform="half")
        big = len(self.slots) <= 12
        for i, s in enumerate(self.slots):
            if i in placed:
                r, c = placed[i]
                gr = grid_row[r]
            else:
                gr, c = extra_row, 2 * others.index(i)
            base = self.pad_base[i]
            pad = tk.Frame(pads, bg=base, highlightthickness=3, highlightbackground="#3a3d47")
            pad.grid(row=gr, column=c, columnspan=2, sticky="nsew", padx=2, pady=2)
            lk = tk.Label(pad, text=key_label(s["key"]), bg=base, fg=FG,
                          font=("TkDefaultFont", 20 if big else 14, "bold"))
            lk.pack(expand=True, pady=(10 if big else 4, 0))
            ln = tk.Label(pad, text="", bg=base, fg=MUTED, font=("TkDefaultFont", 10 if big else 8))
            ln.pack(pady=(0, 10 if big else 4))
            for w in (pad, lk, ln):
                w.bind("<ButtonPress-1>", lambda _e, i=i: self._pad_down(i))
                w.bind("<ButtonRelease-1>", lambda _e, i=i: self._pad_up(i))
            self.pads.append((pad, lk, ln))
        for r in range(extra_row + (1 if others else 0)):
            pads.rowconfigure(r, weight=1)
        self._update_pad_labels()

    def _build_song_panel(self, parent):
        sp = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        sp.pack(fill="x")
        top = ttk.Frame(sp, style="Panel.TFrame")
        top.pack(fill="x")
        self.chord_label = ttk.Label(top, text="", style="Chord.TLabel", width=7)
        self.chord_label.pack(side="left")
        mid = ttk.Frame(top, style="Panel.TFrame")
        mid.pack(side="left", fill="x", expand=True, padx=12)
        self.next_label = ttk.Label(mid, text="", style="Big.TLabel")
        self.next_label.pack(anchor="w")
        self.bar_label = ttk.Label(mid, text="", style="Muted.TLabel")
        self.bar_label.pack(anchor="w")
        self.progress = tk.Canvas(mid, height=10, bg="#3a3d47", highlightthickness=0)
        self.progress.pack(fill="x", pady=(6, 0))
        self.progress_bar = self.progress.create_rectangle(0, 0, 0, 10, fill=self.ins.color, width=0)

        tr = ttk.Frame(sp, style="Panel.TFrame")
        tr.pack(fill="x", pady=(8, 0))
        self.start_button = ttk.Button(tr, text="▶ Empezar", width=10, takefocus=False,
                                       command=self._toggle_run)
        self.start_button.pack(side="left")
        ttk.Label(tr, text="  Desde", style="Muted.TLabel").pack(side="left")
        self.sections = [(lab.replace("Bar", "Compás"), b) for lab, b in self.song.sections(8)]
        self.section_var = tk.StringVar(value=self.sections[0][0])
        sec = ttk.Combobox(tr, textvariable=self.section_var, values=[x[0] for x in self.sections], width=18,
                           state="readonly", takefocus=False)
        sec.pack(side="left", padx=6)
        sec.bind("<<ComboboxSelected>>", lambda _e: (self._jump_section(), self._refocus()))
        start_bpm = self.auto_bpm
        self.song_bpm_var = tk.StringVar(value=str(int(round(start_bpm))))
        bpm = ttk.Spinbox(tr, from_=20, to=400, textvariable=self.song_bpm_var, width=5)
        bpm.pack(side="left", padx=(8, 2))
        bpm.bind("<Return>", self._refocus)
        ttk.Label(tr, text="BPM", style="Muted.TLabel").pack(side="left")
        self.click_var = tk.BooleanVar(value=not (self.auto or self.band))
        ttk.Checkbutton(tr, text="Click", variable=self.click_var, style="Panel.TCheckbutton", takefocus=False,
                        command=self._click_changed).pack(side="left", padx=(12, 0))
        self.guide_var = tk.BooleanVar(value=False)
        if self.role_id is not None:
            ttk.Checkbutton(tr, text="Guía: marcar la próxima tecla del patrón", variable=self.guide_var,
                            style="Panel.TCheckbutton", takefocus=False,
                            command=lambda: (self._update_pattern(), self._refocus())).pack(side="left",
                                                                                            padx=(12, 0))
        else:
            ttk.Label(tr, text=f"   (original {self.song.bpm:g})", style="Muted.TLabel").pack(side="left")

        self.pattern_label = ttk.Label(sp, text="", style="Panel.TLabel", wraplength=760, justify="left")
        self.pattern_label.pack(anchor="w", pady=(8, 0))
        ttk.Label(sp, text=f"{self.role['tip']}  ←/→ = acorde anterior/siguiente. "
                           f"{self.song.meter} tiempos por compás, 1 compás de cuenta.",
                  style="Muted.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(2, 0))

    def _update_pad_labels(self):
        if not self.pads:
            return
        for i, s in enumerate(self.slots):
            ln = self.pads[i][2]
            if self.drums:
                ln.config(text=DRUM_PIECES[s["piece"]][0])
            elif self.slot_midis[i] is not None:
                text = note_text(self.slot_midis[i] + 12 * self.octave_shift)
                if self.slot_labels[i]:
                    text += f"  {self.slot_labels[i]}"
                ln.config(text=text)

    def _update_octave_label(self):
        if self.oct_label is not None:
            self.oct_label.config(text=f"Octava: {self.octave_shift:+d}  (↑/↓)")

    def _metro_changed(self):
        try:
            bpm = int(float(self.bpm_var.get()))
        except ValueError:
            bpm = self.engine.metro_bpm
        try:
            beats = int(float(self.bar_var.get()))
        except ValueError:
            beats = self.engine.metro_beats_per_bar
        self.engine.metro_audible = True
        self.engine.set_metronome(self.metro_var.get(), bpm, beats)
        self.bpm_var.set(str(self.engine.metro_bpm))
        self.bar_var.set(str(self.engine.metro_beats_per_bar))
        self._refocus()

    # ---- song mode ------------------------------------------------------------
    def _pattern(self):
        pat = self.role["pattern"] if self.role else []
        return [p for p in pat if p <= len(getattr(self, "tones", []))]

    def _set_chord(self, idx):
        idx = max(0, min(len(self.song.events) - 1, idx))
        self.cur_idx = idx
        e = self.song.events[idx]
        self.chord_label.config(text=chord_name(e["root"], e["quality"]))
        if self.role is not None and self.role_id is None:
            pass  # Manual / Automático: keys stay as configured
        elif not self.drums and not self.melody:
            # every key keeps its register but snaps to the nearest note of the current chord
            ivs = chord_intervals(e["quality"])
            pcs = {(e["root"] + iv) % 12: iv for iv in ivs}
            self.slot_midis, self.slot_labels = [], []
            for s in self.slots:
                m = s["midi"]
                best = min((t for t in range(m - 6, m + 7) if t % 12 in pcs), key=lambda t: (abs(t - m), t))
                self.slot_midis.append(best)
                self.slot_labels.append(INTERVAL_LABELS[pcs[best % 12]])
            self.tones = sorted(set(self.slot_midis))
        if self.melody:
            tones = {(e["root"] + iv) % 12 for iv in chord_intervals(e["quality"])}
            self.pad_base = ["#46506a" if m is not None and m % 12 in tones else PANEL for m in self.slot_midis]
            for i in range(len(self.pads)):
                self._light(i, False)
        self.pat_pos = 0
        self._update_pad_labels()
        self._update_pattern()

    def _update_pattern(self):
        pat = self._pattern()
        for pad, _, _ in self.pads:
            pad.config(highlightbackground="#3a3d47")
        if self.role_id is None:
            what = (f"la pista \"{self.auto_name}\" ({len(self.auto_events)} notas)" if self.auto_events
                    else "los acordes")
            self.pattern_label.config(text=("Automático: la app toca " if self.auto else
                                            "Práctica con banda: suena el resto y tú sigues " if self.band
                                            else "Manual: sigues ") + what +
                                      ".  Empezar / Detener;  Desde = saltar a un compás." +
                                      ("  Mantén una tecla = redoble." if self.drums else ""))
            return
        if not pat or self.drums:
            self.pattern_label.config(text="Libre: sin patrón fijo. Escucha y elige." if not self.drums
                                      else "Lleva el tiempo de la banda. Mantén una tecla = redoble al tempo.")
            return
        # pattern steps are positions in the chord's tones, low to high (1 = lowest note you have)
        target = self.tones[pat[self.pat_pos % len(pat)] - 1]
        nxt = self.slot_midis.index(target)
        if self.guide_var.get():
            self.pads[nxt][0].config(highlightbackground="#f0f0f0")
        step = {1.0: "negras", 0.5: "corcheas", 3.0: "una nota por compás"}.get(self.role["step"], "")
        seq = "  ".join(f"{note_text(self.tones[p - 1])}" for p in pat)
        hint = "(borde blanco = próxima tecla)" if self.guide_var.get() else "(activa Guía para marcarlo en los pads)"
        self.pattern_label.config(text=f"Patrón sugerido ({step}):   {seq}      {hint}")

    # The song clock: the engine counts seconds since Empezar; the song's tempo map turns them into beats,
    # so tempo changes in the MIDI (ritardandos etc.) are followed. The BPM box scales the whole song.
    def _speed(self):
        try:
            return max(0.25, min(4.0, float(self.song_bpm_var.get()) / self.song.bpm))
        except (ValueError, ZeroDivisionError):
            return 1.0

    def _song_seconds_now(self):
        """Song time (seconds at original tempo) right now, or None while stopped / counting in."""
        t = self.engine.clock_seconds()
        if t is None:
            return None
        return self.sec0 + (t - self.countin) * self.speed

    def _clicks_from(self, beat):
        """Clicks for every whole song beat from `beat` on, as clock sample positions."""
        clicks = []
        b = int(np.ceil(beat - 1e-6))
        while b <= self.song.total_beats:
            t = self.countin + (self.song.beat_to_seconds(b) - self.sec0) / self.speed
            accent = (b - self.song.phase) % self.song.meter == 0
            clicks.append((int(round(t * SR)), accent))
            b += 1
        return clicks

    def _toggle_run(self):
        if self.running:
            self._stop_run()
        else:
            self.speed = self._speed()
            e = self.song.events[self.cur_idx]
            self.start_beat = max(0.0, self.song.bar_start(self.song.bar_of(e["start"])))
            self.sec0 = self.song.beat_to_seconds(self.start_beat)
            beat_len = 60.0 / (self.song.bpm_at(self.start_beat) * self.speed)
            self.countin = self.song.meter * beat_len
            clicks = [(int(round(i * beat_len * SR)), i == 0) for i in range(self.song.meter)]
            self.engine.start_clock(clicks + self._clicks_from(self.start_beat), audible=True)
            self._auto_reset(self.sec0)
            self._band_reset(self.sec0)
            self.running = True
            self.start_button.config(text="\u25A0 Detener")
        self._refocus()

    def _stop_run(self):
        self.engine.stop_clock()
        if self.auto:
            self.engine.all_off()
            self._auto_light(set())
        elif self.band:
            self.engine.all_off()
        self.running = False
        self.start_button.config(text="\u25B6 Empezar")

    def _click_changed(self):
        t = self.engine.clock_seconds()
        if t is not None and t >= self.countin:
            self.engine.metro_audible = self.click_var.get()
        self._refocus()

    def _move_to_beat(self, beat):
        """While running: make `beat` sound right now and reschedule the clicks."""
        t = self.engine.clock_seconds()
        if t is None:
            return
        self.sec0 = self.song.beat_to_seconds(beat) - (max(t, self.countin) - self.countin) * self.speed
        self.engine.set_clicks(self._clicks_from(beat))
        if self.auto or self.band:
            self.engine.clear_scheduled()
            self.engine.all_off()
            self._auto_reset(self.song.beat_to_seconds(beat))
            self._band_reset(self.song.beat_to_seconds(beat))

    # ---- falling notes: the track's notes fall towards the line; columns = my keys, low -> high ---------
    HW_W = 900
    HW_H = 520
    HW_KEYS = 86     # the keyboard strip under the line
    HW_AHEAD = 3.0   # real seconds visible above the line

    def _slot_for(self, pitch):
        """Which of my keys plays this MIDI note: the exact pitch, else the nearest one (drums: by piece)."""
        cache = self.__dict__.setdefault("_slot_cache", {})
        if pitch in cache:
            return cache[pitch]
        if self.drums:
            piece = GM_DRUMS.get(pitch, "hat")
            if pitch == 35 and any(sl["piece"] == "kick2" for sl in self.slots):
                piece = "kick2"  # GM 35 / 36 = the two bass drums: second pedal on its own key
            idx = next((i for i, sl in enumerate(self.slots) if sl["piece"] == piece), None)
        else:
            cands = [(abs(m - pitch), i) for i, m in enumerate(self.slot_midis) if m is not None]
            idx = min(cands)[1] if cands else None
        cache[pitch] = idx
        return idx

    def _build_highway(self, parent):
        # every key of the instrument is a column, low notes on the left (drums: in their order)
        order = sorted(range(len(self.slots)),
                       key=lambda i: (self.slot_midis[i] if not self.drums else i))
        self.lanes = {slot: n for n, slot in enumerate(order)}
        box = ttk.Frame(parent, style="Panel.TFrame", padding=6)
        box.pack(fill="both", expand=True, pady=(8, 8))
        self.highway = tk.Canvas(box, width=self.HW_W, height=self.HW_H, bg="#16171b", highlightthickness=0)
        self.highway.pack(fill="both", expand=True)
        self.highway.bind("<ButtonPress-1>", self._hw_click)
        self.highway.bind("<ButtonRelease-1>", self._hw_release)
        self.max_dur = max((off - on for on, off, _ in self.auto_events), default=1.0)

    def _hw_slot_at(self, x):
        n = max(1, len(self.lanes))
        lane = int(x / (max(self.highway.winfo_width(), 1) / n))
        return next((slot for slot, ln in self.lanes.items() if ln == lane), None)

    def _hw_click(self, event):
        if event.y > self.highway.winfo_height() - self.HW_KEYS:
            slot = self._hw_slot_at(event.x)
            if slot is not None:
                self._hw_clicked = slot
                self._pad_down(slot)

    def _hw_release(self, _event):
        slot = getattr(self, "_hw_clicked", None)
        if slot is not None:
            self._pad_up(slot)
            self._hw_clicked = None

    def _draw_highway(self, now_song):
        hw = self.highway
        if hw is None:
            return
        hw.delete("all")
        w = max(hw.winfo_width(), 200)
        h = max(hw.winfo_height(), 200)
        n = max(1, len(self.lanes))
        lane_w = w / n
        y_hit = h - self.HW_KEYS
        span = self.HW_AHEAD * (self.speed if self.running else self._speed())
        pps = y_hit / span  # pixels per song-second
        font_key = ("TkDefaultFont", max(8, min(18, int(lane_w * 0.4))), "bold")
        font_note = ("TkDefaultFont", max(7, min(10, int(lane_w * 0.22))))
        for slot, lane in self.lanes.items():
            x = lane * lane_w
            black = not self.drums and self.slot_midis[slot] is not None and self.slot_midis[slot] % 12 in SHARPS
            hw.create_rectangle(x, 0, x + lane_w, y_hit, width=0,
                                fill="#101114" if black else ("#1b1c21" if lane % 2 == 0 else "#16171b"))
            # the key itself, right under the line
            lit = slot in self.hw_lit
            hw.create_rectangle(x + 2, y_hit + 4, x + lane_w - 2, h - 2, width=0,
                                fill=self.ins.color if lit else (BLACK_KEY if black else "#2e3038"))
            hw.create_text(x + lane_w / 2, y_hit + self.HW_KEYS * 0.42, text=key_label(self.slots[slot]["key"]),
                           fill=FG, font=font_key)
            sub = (DRUM_PIECES[self.slots[slot]["piece"]][0] if self.drums
                   else note_text(self.slot_midis[slot] + 12 * self.octave_shift))
            hw.create_text(x + lane_w / 2, y_hit + self.HW_KEYS * 0.78, text=sub,
                           fill=FG if lit else MUTED, font=font_note)
        hw.create_line(0, y_hit, w, y_hit, fill="#f0f0f0", width=3)
        drums = self.drums or self.auto_drums
        lo = bisect_left(self.auto_starts, now_song - self.max_dur)
        hi = bisect_left(self.auto_starts, now_song + span)
        for on, off, pitch in self.auto_events[lo:hi]:
            if drums:  # hits: short blocks, whatever the MIDI length says
                off = on + 0.09
            if off < now_song:
                continue
            slot = self._slot_for(pitch)
            if slot not in self.lanes:
                continue
            x = self.lanes[slot] * lane_w
            y_on = y_hit - (on - now_song) * pps          # the note's bottom reaches the line at its start
            y_off = min(y_on - 8, y_hit - (off - now_song) * pps + 2)  # small gap between repeated notes
            active = on <= now_song < off
            hw.create_rectangle(x + 3, max(0, y_off), x + lane_w - 3, min(y_hit, y_on), width=0,
                                fill="#ffffff" if active else self.ins.color)

    # ---- automatic playback ------------------------------------------------------
    def _auto_reset(self, from_sec):
        """Next note to schedule = first one starting at/after from_sec (song seconds)."""
        if not self.auto:
            return
        self.auto_ptr = next((i for i, ev in enumerate(self.auto_events) if ev[0] >= from_sec - 1e-6),
                             len(self.auto_events))
        self.auto_sounding = []

    def _auto_schedule(self, t):
        """Hands the engine every note starting in the next ~0.3 s, with sample-exact positions."""
        now_song = self.sec0 + (t - self.countin) * self.speed
        horizon = now_song + 0.3 * self.speed
        kit = instrument_by_id("drums") if (self.auto_drums or self.drums) else None
        events = []
        while self.auto_ptr < len(self.auto_events) and self.auto_events[self.auto_ptr][0] < horizon:
            on, off, pitch = self.auto_events[self.auto_ptr]
            tag = ("auto", self.auto_ptr)
            s_on = int(round((self.countin + (on - self.sec0) / self.speed) * SR))
            s_off = int(round((self.countin + (off - self.sec0) / self.speed) * SR))
            if kit is not None:
                voice = kit.voice(piece=GM_DRUMS.get(pitch, "hat"))
            else:
                voice = self.ins.voice(freq=freq_of(pitch))
            events += [(s_on, "on", tag, voice), (max(s_off, s_on + 1), "off", tag, None)]
            idx = self._slot_for(pitch)
            if idx is not None:
                self.auto_sounding.append((on, off if kit is None else on + 0.12, idx))
            self.auto_ptr += 1
        if events:
            self.engine.schedule(events)
        lit = {idx for on, off, idx in self.auto_sounding if on <= now_song < off}
        self.auto_sounding = [x for x in self.auto_sounding if x[1] > now_song]
        self._auto_light(lit)

    def _auto_light(self, lit):
        prev = getattr(self, "_auto_lit", set())
        for i in prev - lit:
            self._light(i, False)
        for i in lit - prev:
            self._light(i, True)
        self._auto_lit = lit

    def _jump_section(self):
        beat = dict(self.sections).get(self.section_var.get(), 0.0)
        if self.running:
            self._move_to_beat(beat)
        self._set_chord(self.song.event_index_at(beat))

    def _step_chord(self, delta):
        target = max(0, min(len(self.song.events) - 1, self.cur_idx + delta))
        if self.running:
            self._move_to_beat(self.song.events[target]["start"])
        self._set_chord(target)

    def _tick(self):
        t = self.engine.clock_seconds() if self.running else None
        e = self.song.events[self.cur_idx]
        if self.running and t is None:
            self._stop_run()
        pos = None
        if t is not None:
            pos = t - self.countin
            if self.auto:
                self._auto_schedule(t)
            if self.band:
                self._band_schedule(t)
        if pos is not None and pos < 0:
            beats_left = int(-pos / (self.countin / self.song.meter)) + 1
            self.bar_label.config(text=f"Cuenta:  {beats_left}")
            beat = self.start_beat
        elif pos is not None:
            if self.engine.metro_audible != self.click_var.get():
                self.engine.metro_audible = self.click_var.get()
            beat = self.song.seconds_to_beat(self.sec0 + pos * self.speed)
            if beat >= self.song.total_beats:
                self._stop_run()
                self.bar_label.config(text="Fin de la canción")
            else:
                idx = self.song.event_index_at(beat)
                if idx != self.cur_idx:
                    self._set_chord(idx)
                e = self.song.events[self.cur_idx]
                bar = self.song.bar_of(beat)
                in_bar = int((beat - self.song.phase) % self.song.meter) + 1
                self.bar_label.config(text=f"Compás {bar}   ·   tiempo {in_bar} de {self.song.meter}")
        else:
            beat = e["start"]
            self.bar_label.config(text=f"Compás {self.song.bar_of(beat)}   ·   detenido "
                                       f"(Empezar o ←/→)")
        frac = min(1.0, max(0.0, (beat - e["start"]) / e["beats"]))
        if self.cur_idx + 1 < len(self.song.events):
            n = self.song.events[self.cur_idx + 1]
            left = max(0, int(round(n["start"] - beat + 0.49)))
            self.next_label.config(text=f"siguiente:  {chord_name(n['root'], n['quality'])}    en {left} "
                                        f"tiempo{'s' if left != 1 else ''}")
        else:
            self.next_label.config(text="último acorde")
        w = self.progress.winfo_width()
        self.progress.coords(self.progress_bar, 0, 0, w * frac, 10)
        if self.highway is not None:
            if t is not None:
                now_song = self.sec0 + (t - self.countin) * self.speed
            else:
                now_song = self.song.beat_to_seconds(beat)
            self._draw_highway(now_song)
        if getattr(self, "_tick_id", None):
            self.after_cancel(self._tick_id)  # never more than one refresh loop
        self._tick_id = self.after(33, self._tick)

    # ---- visuals ------------------------------------------------------------
    def _light(self, i, on):
        if not self.pads:  # falling-notes keyboard: drawn on the canvas
            if on:
                self.hw_lit.add(i)
            else:
                self.hw_lit.discard(i)
            return
        pad, lk, ln = self.pads[i]
        bg = self.ins.color if on else self.pad_base[i]
        for w in (pad, lk, ln):
            w.config(bg=bg)
        ln.config(fg=FG if on else MUTED)

    def _set_palm(self, on):
        self.palm = on
        if self.palm_label is not None:
            self.palm_label.config(bg=self.ins.color if on else PANEL, fg=FG if on else MUTED)

    # ---- notes ------------------------------------------------------------
    def _start(self, tag, i):
        s = self.slots[i]
        if self.drums:  # holding the key = a roll (redoble) in 16ths at the song's tempo
            self.engine.note_on(tag, self.ins, piece=s["piece"], roll=self._roll_samples())
        else:
            freq = freq_of(self.slot_midis[i] + 12 * self.octave_shift)
            self.engine.note_on(tag, self.ins, freq=freq, palm_mute=self.palm, power=self.power_var.get())
        self._light(i, True)
        if self.song is not None and self._pattern() and not self.drums:
            self.pat_pos += 1
            self._update_pattern()

    def _roll_bpm(self):
        """Tempo for the drum roll: the song's tempo right now (with the BPM box), else the metronome's."""
        try:
            if self.song is not None:
                now = self._song_seconds_now() if self.running else None
                if now is not None:
                    return self.song.bpm_at(self.song.seconds_to_beat(now)) * self.speed
                return float(self.song_bpm_var.get())
            return float(self.bpm_var.get())
        except (AttributeError, ValueError, TypeError):
            return float(self.engine.metro_bpm or 120)

    def _roll_samples(self):
        bpm = max(40.0, min(300.0, self._roll_bpm()))
        return int(SR * 60.0 / bpm / 4)  # semicorcheas (16ths)

    def _stop(self, tag, i):
        self.engine.note_off(tag)
        self._light(i, False)

    def _on_press(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Entry)):  # typing the BPM
            return
        k = event.keysym.lower()
        if k in self.pending_release:  # Linux auto-repeat: release+press pair -> ignore
            self.after_cancel(self.pending_release.pop(k))
            return
        if k in self.held:  # Windows auto-repeat: repeated press
            return
        if k in self.keymap:
            self.held.add(k)
            self._start(("key", k), self.keymap[k])
        elif k == "space" and "palm_mute" in self.ins.techniques:
            self.held.add(k)
            self._set_palm(True)
        elif k == "return" and "power" in self.ins.techniques:
            self.power_var.set(not self.power_var.get())
        elif k in ("up", "down") and not self.drums:
            self.octave_shift = max(-3, min(3, self.octave_shift + (1 if k == "up" else -1)))
            self._update_octave_label()
            self._update_pad_labels()
        elif k in ("left", "right") and self.song is not None:
            self._step_chord(1 if k == "right" else -1)
        return "break"

    def _on_release(self, event):
        k = event.keysym.lower()
        if k not in self.held:
            return
        self.pending_release[k] = self.after(30, lambda: self._really_release(k))

    def _really_release(self, k):
        self.pending_release.pop(k, None)
        self.held.discard(k)
        if k in self.keymap:
            self._stop(("key", k), self.keymap[k])
        elif k == "space":
            self._set_palm(False)

    def _pad_down(self, i):
        self._start(("pad", i), i)

    def _pad_up(self, i):
        self._stop(("pad", i), i)

    def _on_focus_out(self, event):
        if event.widget is self:
            self._release_all()

    def _release_all(self):
        for k in list(self.held):
            if k in self.pending_release:
                self.after_cancel(self.pending_release.pop(k))
            self._really_release(k)

    def back(self):
        self._release_all()
        if self.auto or self.band:
            self.engine.clear_scheduled()
        self.running = False
        if getattr(self, "_tick_id", None):
            self.after_cancel(self._tick_id)
        self.engine.set_metronome(False)
        self.engine.stop_clock()
        if self.engine.rec_blocks is not None:
            self.engine.stop_recording(os.path.join(HERE, "recordings"))
        self.engine.all_off()
        self.destroy()
        self.chooser.back_from_play()


class GainVoice:
    """Scales another voice by gains[idx] (read every block, so moving a fader acts at once)."""

    def __init__(self, voice, gains, idx):
        self.voice = voice
        self.gains = gains
        self.idx = idx
        self.done = False

    def release(self):
        self.voice.release()

    def render(self, n):
        out = self.voice.render(n) * self.gains[self.idx]
        self.done = self.voice.done
        return out


# mixer order, left to right: rhythm section first, then harmony, then melody / top
MIX_ORDER = ["drums", "bass", "guitar", "clean_guitar", "lead_guitar", "pluck", "piano", "epiano", "organ",
             "pad", "strings", "string_ensemble", "tremolo_strings", "brass", "clarinet", "flute", "vocal"]
DRUM_PART_ORDER = {"tambores": 0, "platillos": 1, None: 2}


def band_events(menu):
    """Every theme instrument that follows a MIDI track: [(name, instrument, part)] and the merged notes
    [(song_seconds_on, song_seconds_off, pitch, instrument_index)], sorted by start."""
    song, path = menu.song, menu.theme_path
    parts, notes = [], []
    band = [p for p in menu.profiles if p.get("theme") == menu.theme_key and isinstance(p.get("track"), int)]
    rank = {s: i for i, s in enumerate(MIX_ORDER)}
    band.sort(key=lambda p: (rank.get(p["sound"], len(MIX_ORDER)), DRUM_PART_ORDER.get(p.get("part"), 2),
                             p["track"]))
    for p in band:
        ins = instrument_by_id(p["sound"])
        part = p.get("part") if ins.kind == "drums" else None
        idx = len(parts)
        parts.append((p["name"], ins, part))
        for b0, b1, pitch, _ch in (ev for tr in [p["track"]] + p.get("extra_tracks", [])
                                   for ev in track_events(path, tr)):
            if ins.kind == "drums" and part and drum_group(GM_DRUMS.get(pitch, "hat")) != part:
                continue
            notes.append((song.beat_to_seconds(b0 - song.offset), song.beat_to_seconds(b1 - song.offset),
                          pitch, idx))
    notes.sort()
    return parts, notes


class ListenWindow(tk.Toplevel):
    """Proof of concept: the app plays every instrument of the theme at once, so everybody hears the
    whole piece before splitting it among the laptops. Tick/untick instruments to hear them alone."""

    GAIN = 0.55

    def __init__(self, menu):
        super().__init__(menu)
        self.menu = menu
        self.engine = menu.engine
        self.song = menu.song
        self.configure(bg=BG)
        self.title(f"Escuchar tema completo - {menu.theme_name}")
        self.protocol("WM_DELETE_WINDOW", self.back)
        self.parts, self.notes = band_events(menu)
        self.starts = [n[0] for n in self.notes]
        self.end_sec = max((n[1] for n in self.notes), default=0.0)
        self.running = False
        self.ptr = 0
        self.sec0 = 0.0
        self.countin = 0.3
        self.speed = 1.0
        self.lit_until = [0.0] * len(self.parts)
        self._tick_id = None
        self._save_id = None
        threading.Thread(target=DrumKit.warm, daemon=True).start()

        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text=f"♫ {menu.theme_name}", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="← Volver", command=self.back).pack(side="right")
        ttk.Label(outer, text="Todos los instrumentos del tema tocados por la app. Desmarca «Suena» para escuchar la obra "
                  "sin ese instrumento, y usa el fader de cada uno para su volumen (se guarda).",
                  style="BgMuted.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(4, 14))

        mixer = ttk.Frame(outer)
        mixer.pack(fill="x")
        saved = self.menu.settings.get("mix", {}).get(self.menu.theme_key, {})
        self.on_vars, self.leds, self.vol_vars, self.vol_labels = [], [], [], []
        self.gains = [0.0] * len(self.parts)
        for i, (name, ins, _part) in enumerate(self.parts):
            strip = tk.Frame(mixer, bg=PANEL, padx=6, pady=8)
            strip.grid(row=0, column=i, sticky="nsew", padx=3)
            mixer.columnconfigure(i, weight=1, uniform="strip")
            tk.Frame(strip, bg=ins.color, height=4).pack(fill="x", pady=(0, 6))
            tk.Label(strip, text=name, bg=PANEL, fg=FG, font=("TkDefaultFont", 10, "bold"),
                     wraplength=120, justify="center", height=2).pack(fill="x")
            tk.Label(strip, text=ins.name, bg=PANEL, fg=MUTED, font=("TkDefaultFont", 8),
                     wraplength=120, justify="center", height=2).pack(fill="x")
            led = tk.Canvas(strip, width=18, height=18, bg=PANEL, highlightthickness=0)
            led.create_oval(2, 2, 16, 16, fill="#3a3c44", outline="", tags="dot")
            led.pack(pady=4)
            var = tk.BooleanVar(value=True)
            tk.Checkbutton(strip, text="Suena", variable=var, bg=PANEL, fg=FG, selectcolor=BG,
                           activebackground=PANEL, activeforeground=FG, highlightthickness=0,
                           command=self._mute_changed).pack()
            vol = tk.IntVar(value=int(saved.get(name, 80)))
            tk.Scale(strip, from_=100, to=0, orient="vertical", variable=vol, showvalue=False, length=220,
                     width=22, sliderlength=26, bg=PANEL, fg=FG, troughcolor="#1a1b20", activebackground=ins.color,
                     highlightthickness=0, bd=0, command=lambda _v, i=i: self._volume_changed(i)).pack(pady=(8, 2))
            lab = tk.Label(strip, text="", bg=PANEL, fg=MUTED, font=("TkDefaultFont", 9))
            lab.pack()
            self.on_vars.append(var)
            self.leds.append((led, ins.color))
            self.vol_vars.append(vol)
            self.vol_labels.append(lab)
            self._volume_changed(i, save=False)
        sel = ttk.Frame(outer)
        sel.pack(fill="x", pady=(6, 0))
        ttk.Button(sel, text="Todos", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(sel, text="Ninguno", command=lambda: self._set_all(False)).pack(side="left", padx=6)
        ttk.Button(sel, text="Volúmenes al 80%", command=self._reset_volumes).pack(side="left", padx=6)

        ctl = ttk.Frame(outer)
        ctl.pack(fill="x", pady=(18, 6))
        self.play_button = ttk.Button(ctl, text="▶ Reproducir", style="Go.TButton", command=self._toggle)
        self.play_button.pack(side="left")
        ttk.Label(ctl, text="Desde:", ).pack(side="left", padx=(18, 4))
        self.sections = [(lab.replace("Bar", "Compás"), b) for lab, b in self.song.sections(8)]
        self.section_var = tk.StringVar(value=self.sections[0][0])
        ttk.Combobox(ctl, textvariable=self.section_var, values=[x[0] for x in self.sections], width=20,
                     state="readonly").pack(side="left")
        ttk.Label(ctl, text="BPM:", ).pack(side="left", padx=(18, 4))
        self.bpm_var = tk.StringVar(value=str(int(round(self.song.bpm))))
        ttk.Spinbox(ctl, from_=30, to=300, textvariable=self.bpm_var, width=5).pack(side="left")
        ttk.Label(ctl, text=f"(original {self.song.bpm:.0f})", style="BgMuted.TLabel").pack(side="left", padx=6)

        self.progress = tk.Canvas(outer, height=14, bg=PANEL, highlightthickness=0)
        self.progress.pack(fill="x", pady=(10, 2))
        self.progress.bind("<Button-1>", self._seek)
        self.pos_label = ttk.Label(outer, text="", style="BgMuted.TLabel")
        self.pos_label.pack(anchor="w")
        if not self.parts:
            self.pos_label.config(text="Este tema no tiene instrumentos con pista MIDI.")
        maximize(self)
        self._tick()

    # ---- controls -----------------------------------------------------------------
    def _volume_changed(self, i, save=True):
        v = self.vol_vars[i].get()
        # fader 0..100 -> gain; 80 = the default level, 100 = about +4 dB
        self.gains[i] = self.GAIN * (v / 80.0) ** 1.5 if v > 0 else 0.0
        self.vol_labels[i].config(text=f"{v}%")
        if save:
            if self._save_id:
                self.after_cancel(self._save_id)
            self._save_id = self.after(600, self._save_mix)

    def _save_mix(self):
        self._save_id = None
        mix = self.menu.settings.setdefault("mix", {})
        mix[self.menu.theme_key] = {name: self.vol_vars[i].get() for i, (name, _ins, _p) in enumerate(self.parts)}
        save_settings(self.menu.settings)

    def _reset_volumes(self):
        for i, v in enumerate(self.vol_vars):
            v.set(80)
            self._volume_changed(i)

    def _set_all(self, on):
        for v in self.on_vars:
            v.set(on)
        self._mute_changed()

    def _mute_changed(self):
        # notes already handed to the engine stop at once for muted instruments
        for i, v in enumerate(self.on_vars):
            if not v.get():
                self.lit_until[i] = 0.0
        if self.running:
            now = self._song_now()
            self._restart(now if now is not None else self.sec0)

    def _speed(self):
        try:
            return max(0.25, min(4.0, float(self.bpm_var.get()) / self.song.bpm))
        except (ValueError, ZeroDivisionError):
            return 1.0

    def _song_now(self):
        t = self.engine.clock_seconds()
        if t is None:
            return None
        return self.sec0 + max(0.0, t - self.countin) * self.speed

    def _restart(self, from_sec, countin=0.15):
        self.engine.clear_scheduled()
        self.engine.all_off()
        self.speed = self._speed()
        self.sec0 = max(0.0, from_sec)
        self.countin = countin
        self.engine.start_clock([], audible=False)
        self.ptr = bisect_left(self.starts, self.sec0 - 1e-6)
        self.running = True
        self.play_button.config(text="■ Detener")

    def _toggle(self):
        if self.running:
            self._stop()
        else:
            beat = dict(self.sections).get(self.section_var.get(), 0.0)
            self._restart(self.song.beat_to_seconds(beat), countin=0.3)

    def _stop(self):
        self.running = False
        self.engine.stop_clock()
        self.engine.all_off()
        self.play_button.config(text="▶ Reproducir")

    def _seek(self, event):
        if self.end_sec <= 0:
            return
        sec = self.end_sec * event.x / max(1, self.progress.winfo_width())
        if self.running:
            self._restart(sec)
        else:  # just move the start point to the nearest rehearsal mark before it
            beat = self.song.seconds_to_beat(sec)
            lab = [lab for lab, b in self.sections if b <= beat + 1e-6]
            if lab:
                self.section_var.set(lab[-1])

    # ---- playback -------------------------------------------------------------------
    def _schedule(self, t):
        now = self.sec0 + (t - self.countin) * self.speed
        horizon = now + 0.3 * self.speed
        events = []
        while self.ptr < len(self.notes) and self.notes[self.ptr][0] < horizon:
            on, off, pitch, idx = self.notes[self.ptr]
            if self.on_vars[idx].get():
                name, ins, _part = self.parts[idx]
                tag = ("full", self.ptr)
                s_on = int(round((self.countin + (on - self.sec0) / self.speed) * SR))
                s_off = int(round((self.countin + (off - self.sec0) / self.speed) * SR))
                if ins.kind == "drums":
                    voice = ins.voice(piece=GM_DRUMS.get(pitch, "hat"))
                    off = on + 0.12
                else:
                    voice = ins.voice(freq=freq_of(pitch))
                events += [(s_on, "on", tag, GainVoice(voice, self.gains, idx)), (max(s_off, s_on + 1), "off", tag, None)]
                self.lit_until[idx] = max(self.lit_until[idx], off)
            self.ptr += 1
        if events:
            self.engine.schedule(events)
        return now

    def _tick(self):
        now = None
        if self.running:
            t = self.engine.clock_seconds()
            if t is None:
                self._stop()
            else:
                now = self._schedule(t)
                if now > self.end_sec + 1.0:
                    self._stop()
                    self.pos_label.config(text="Fin del tema.")
        self._draw(now)
        self._tick_id = self.after(40, self._tick)

    def _draw(self, now):
        for i, (led, color) in enumerate(self.leds):
            on = now is not None and now < self.lit_until[i] and self.on_vars[i].get()
            led.itemconfig("dot", fill=color if on else "#3a3c44")
        c = self.progress
        c.delete("all")
        w = c.winfo_width()
        if self.end_sec <= 0:
            return
        for _lab, b in self.sections:
            x = w * self.song.beat_to_seconds(b) / self.end_sec
            c.create_line(x, 0, x, 14, fill="#44475a")
        pos = now if now is not None else self.song.beat_to_seconds(dict(self.sections).get(self.section_var.get(), 0))
        x = w * min(1.0, pos / self.end_sec)
        c.create_rectangle(0, 0, x, 14, fill=ACCENT if now is not None else "#44485e", width=0)
        if now is not None:
            beat = self.song.seconds_to_beat(min(now, self.end_sec))
            m, s = divmod(int(now), 60)
            em, es = divmod(int(self.end_sec), 60)
            n_on = sum(v.get() for v in self.on_vars)
            self.pos_label.config(text=f"Compás {self.song.bar_of(beat)}    {m}:{s:02d} / {em}:{es:02d}    "
                                  f"{n_on} de {len(self.parts)} instrumentos    (clic en la barra para saltar)")

    def back(self):
        if self._tick_id:
            self.after_cancel(self._tick_id)
        if self._save_id:
            self.after_cancel(self._save_id)
            self._save_mix()
        self._stop()
        self.engine.clear_scheduled()
        self.destroy()
        self.menu.back_here()


def main():
    # Diagnostics: Python errors go to error.log, native crashes (audio driver) to crash.log.
    import faulthandler
    import traceback
    crash = open(os.path.join(HERE, "crash.log"), "w", encoding="utf-8")
    faulthandler.enable(crash)
    try:
        print("Starting audio...", flush=True)
        engine = Engine()
        engine.start()
        print("Audio:", engine.device_name or engine.error, flush=True)
        app = MainMenu(engine)
        print("Window open.", flush=True)
        app.mainloop()
    except Exception:
        text = traceback.format_exc()
        print(text, flush=True)
        with open(os.path.join(HERE, "error.log"), "w", encoding="utf-8") as f:
            f.write(text)
        raise


if __name__ == "__main__":
    main()
