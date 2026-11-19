"""Batería por secciones de audio: trozos reales de la canción que el baterista elige y que suenan en loop.

Un tema puede traer, junto a su MIDI, una carpeta `songs/<Tema>.secciones/` con los audios y un
`secciones.json`:

    {"bpm": 90, "compas_tiempos": 4,
     "secciones": [{"nombre": "Intro", "archivo": "01 Intro.wav", "compas": 1, "compases": 8}, ...]}

`compas` es el compás del tema (1 = el primero) donde está esa sección; `compases`, cuánto dura.
Si la carpeta existe, la batería del tema se toca con estas secciones en vez de los patrones del MIDI.

Cómo suenan (SectionSequencer): la sección elegida se repite en loop. Elegir otra la deja en cola y entra
recién cuando termina la que suena, nunca a la mitad. Todo se cuenta en compases, desde el inicio.
"""
import json
import os
import wave

import numpy as np

KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "minus", "equal"]
KEY_TEXT = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="]


def folder_for(theme_path):
    if not theme_path:
        return None
    d = os.path.splitext(theme_path)[0] + ".secciones"
    return d if os.path.isfile(os.path.join(d, "secciones.json")) else None


class Section:
    def __init__(self, name, path, bar, bars):
        self.name, self.path, self.bar, self.bars = name, path, bar, bars
        self.buffer = None


def load(theme_path):
    """{"bpm", "meter", "sections": [Section]} of the theme, or None if it has no sections."""
    d = folder_for(theme_path)
    if d is None:
        return None
    with open(os.path.join(d, "secciones.json"), encoding="utf-8") as f:
        data = json.load(f)
    secs = [Section(s["nombre"], os.path.join(d, s["archivo"]), int(s.get("compas", 1)), int(s["compases"]))
            for s in data.get("secciones", [])[:len(KEYS)] if os.path.isfile(os.path.join(d, s["archivo"]))]
    if not secs:
        return None
    return {"bpm": float(data.get("bpm", 120)), "meter": int(data.get("compas_tiempos", 4)), "sections": secs}


def read_wav(path, sr):
    """Mono float samples at `sr`."""
    with wave.open(path) as w:
        n, ch, width, rate = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float64) / 32768
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        x = ((b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
             << 8 >> 8).astype(np.float64) / 8388608
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float64) / 2147483648
    else:
        x = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128) / 128
    x = x.reshape(-1, ch).mean(axis=1)
    if rate != sr:
        t = np.arange(int(len(x) * sr / rate)) * rate / sr
        x = np.interp(t, np.arange(len(x)), x)
    return x


def prepare(info, sr):
    """Loads every buffer, cut or padded to exactly its bars (so the loops never drift), with tiny fades."""
    bar_len = info["meter"] * 60.0 / info["bpm"]
    fade = int(0.004 * sr)
    for s in info["sections"]:
        x = read_wav(s.path, sr)
        n = int(round(s.bars * bar_len * sr))
        x = np.pad(x, (0, max(0, n - len(x))))[:n].copy()
        ramp = np.linspace(0, 1, fade)
        x[:fade] *= ramp
        x[-fade:] *= ramp[::-1]
        s.buffer = x


class SectionSequencer:
    """Which section plays in each bar, counted from the start. Commands are plain dicts (also for the network):
        {"type": "choose", "part": "secciones", "pattern": 3}
    The choice is queued and comes in when the section that plays ends."""

    def __init__(self, sections):
        self.sections = sections
        self.part = "secciones"
        self.plan = []        # [(first_bar, index)]: what has been decided, in order
        self.queued = None
        self.first = 0        # section to start with
        self.listeners = []

    def command(self, cmd):
        if cmd.get("type") != "choose" or not 0 <= cmd.get("pattern", -1) < len(self.sections):
            return False
        i = cmd["pattern"]
        if not self.plan:
            self.first = i
        else:
            self.queued = i
        for fn in self.listeners:
            fn(cmd)
        return True

    def choose(self, i):
        return self.command({"type": "choose", "part": self.part, "pattern": i})

    def start(self):
        self.plan = [(0, self.first)]
        self.queued = None

    def reset(self):
        self.first = self.plan[-1][1] if self.plan else self.first
        self.plan, self.queued = [], None

    def end_bar(self):
        b, i = self.plan[-1]
        return b + self.sections[i].bars

    def advance(self):
        """Decides the next section (the queued one, else the same again) and returns (first_bar, index)."""
        nxt = self.queued if self.queued is not None else self.plan[-1][1]
        self.queued = None
        self.plan.append((self.end_bar(), nxt))
        return self.plan[-1]

    def at(self, bar):
        """(index, bar inside it) of what plays at `bar` (fractional), or None before the start."""
        cur = None
        for b, i in self.plan:
            if b <= bar:
                cur = (i, bar - b)
            else:
                break
        return cur
