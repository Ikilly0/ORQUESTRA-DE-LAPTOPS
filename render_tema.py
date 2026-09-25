"""Render de audio del tema con la misma síntesis y el mismo motor de la app (sin tarjeta de sonido).

Toca, como la pantalla "Escuchar tema completo", todos los instrumentos del tema desde un compás,
durante N segundos, y guarda un WAV. Ejemplo (el render entregado en la Tarea 1):

    python render_tema.py --desde 33 --segundos 75 --salida render-order.wav

Opciones: --tema (nombre del .mid en songs/), --desde (compás), --segundos, --bpm, --salida.
"""
import argparse
import os
import wave

import numpy as np

import audio
from audio import Engine
from instruments import INSTRUMENTS, SR, DrumKit, drum_group
from profiles import theme_profiles
from song import GM_DRUMS, load_song, track_events

HERE = os.path.dirname(os.path.abspath(__file__))
GAIN = 0.55  # el mismo nivel que usa "Escuchar tema completo" con los faders al 80 %


class GainVoice:
    def __init__(self, voice, gain):
        self.voice, self.gain, self.done = voice, gain, False

    def release(self):
        self.voice.release()

    def render(self, n):
        out = self.voice.render(n) * self.gain
        self.done = self.voice.done
        return out


def band_notes(path, song):
    """[(seg_on, seg_off, pitch, instrumento, parte)] de todos los instrumentos del tema, como en la app."""
    ins = {i.id: i for i in INSTRUMENTS}
    notes = []
    for p in theme_profiles(path):
        inst = ins[p["sound"]]
        part = p.get("part")
        for tr in [p["track"]] + p.get("extra_tracks", []):
            for b0, b1, pitch, _ch in track_events(path, tr):
                if inst.kind == "drums" and part and drum_group(GM_DRUMS.get(pitch, "hat")) != part:
                    continue
                notes.append((song.beat_to_seconds(b0 - song.offset), song.beat_to_seconds(b1 - song.offset),
                              pitch, inst))
    notes.sort(key=lambda n: n[0])
    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tema", default="Heaven Pierce Her - ORDER.mid")
    ap.add_argument("--desde", type=int, default=33, help="compás de inicio")
    ap.add_argument("--segundos", type=float, default=75.0)
    ap.add_argument("--bpm", type=float, default=None, help="tempo (por defecto el del MIDI)")
    ap.add_argument("--salida", default="render-order.wav")
    a = ap.parse_args()

    path = os.path.join(HERE, "songs", a.tema)
    song = load_song(path)
    speed = (a.bpm / song.bpm) if a.bpm else 1.0
    notes = band_notes(path, song)
    np.random.seed(2026)  # variaciones de batería y ruido reproducibles
    DrumKit.warm()

    engine = Engine()                 # el motor de audio de la app, usado sin stream
    engine.start_clock([], audible=False)
    sec0 = song.beat_to_seconds(max(0.0, song.bar_start(a.desde)))
    ptr = next((i for i, n in enumerate(notes) if n[0] >= sec0 - 1e-6), len(notes))
    blocks, out = int(a.segundos * SR / audio.BLOCK), []
    for _ in range(blocks):
        t = engine.clock_seconds()
        horizon = sec0 + (t + 0.3) * speed
        events = []
        while ptr < len(notes) and notes[ptr][0] < horizon:
            on, off, pitch, inst = notes[ptr]
            s_on = int(round((on - sec0) / speed * SR))
            s_off = int(round((off - sec0) / speed * SR))
            if inst.kind == "drums":
                voice = inst.voice(piece=GM_DRUMS.get(pitch, "hat"))
            else:
                voice = inst.voice(freq=440.0 * 2 ** ((pitch - 69) / 12))
            events += [(s_on, "on", ("r", ptr), GainVoice(voice, GAIN)), (max(s_off, s_on + 1), "off", ("r", ptr), None)]
            ptr += 1
        if events:
            engine.schedule(events)
        out.append(engine.mix(audio.BLOCK))
    y = np.concatenate(out)
    fade = min(len(y), int(1.5 * SR))  # 1,5 s de salida para no cortar en seco
    y[-fade:] *= np.linspace(1, 0, fade)
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    with wave.open(a.salida, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"{a.salida}: {len(y) / SR:.1f} s desde el compás {a.desde}, pico {np.abs(y).max():.2f}")


if __name__ == "__main__":
    main()
