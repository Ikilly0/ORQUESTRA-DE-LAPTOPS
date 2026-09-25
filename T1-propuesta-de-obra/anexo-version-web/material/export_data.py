"""Genera order-data.js (datos de la obra para la estación web) a partir del MIDI.

Usa los módulos de la app de escritorio (carpeta keyboard-instruments, un nivel más arriba):
    cd keyboard-instruments
    python T1-propuesta-de-obra/material/export_data.py
Lee songs/Heaven Pierce Her - ORDER.mid y escribe T1-propuesta-de-obra/material/order-data.js.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, APP)

from instruments import INSTRUMENTS, drum_group  # noqa: E402
from profiles import theme_profiles  # noqa: E402
from song import GM_DRUMS, chord_intervals, chord_name, load_song, track_events  # noqa: E402

MIDI = os.path.join(APP, "songs", "Heaven Pierce Her - ORDER.mid")


def r3(x):
    return round(x, 3)


def main():
    s = load_song(MIDI)
    ins = {i.id: i for i in INSTRUMENTS}
    last = s.bar_of(s.total_beats - 0.01)
    bars = [r3(s.beat_to_seconds(max(0, s.bar_start(b)))) for b in range(1, last + 2)]
    beats = [r3(s.beat_to_seconds(b)) for b in range(0, int(s.total_beats) + 1)]
    chords = [{"t": r3(s.beat_to_seconds(e["start"])), "name": chord_name(e["root"], e["quality"]),
               "pcs": sorted({(e["root"] + i) % 12 for i in chord_intervals(e["quality"])}), "root": e["root"]}
              for e in s.events]
    roles = []
    for p in theme_profiles(MIDI):  # one instrument per MIDI track (drums split, guitar 2 + solo merged)
        ev = []
        for tr in [p["track"]] + p.get("extra_tracks", []):
            for b0, b1, pitch, _ch in track_events(MIDI, tr):
                if p.get("part") and drum_group(GM_DRUMS.get(pitch, "hat")) != p["part"]:
                    continue
                value = GM_DRUMS.get(pitch, "hat") if p["sound"] == "drums" else pitch
                ev.append([r3(s.beat_to_seconds(b0 - s.offset)), r3(s.beat_to_seconds(b1 - s.offset)), value])
        ev.sort(key=lambda x: x[0])
        slots = [{"k": sl["key"], "m": sl.get("midi"), "p": sl.get("piece")} for sl in p["slots"]]
        roles.append({"name": p["name"], "sound": p["sound"], "color": ins[p["sound"]].color,
                      "slots": slots, "ev": ev})
    data = {"title": "Order (reinterpretación)", "bpm": s.bpm, "meter": s.meter, "key": s.key_name(),
            "bars": bars, "beats": beats, "chords": chords, "roles": roles}
    out = os.path.join(HERE, "order-data.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("// Generado desde songs/Heaven Pierce Her - ORDER.mid con export_data.py (no editar a mano)\n")
        f.write("window.ORDER_DATA = " + json.dumps(data, separators=(",", ":"), ensure_ascii=False) + ";\n")
    print("escrito", out, sum(len(r["ev"]) for r in roles), "notas")


if __name__ == "__main__":
    main()
