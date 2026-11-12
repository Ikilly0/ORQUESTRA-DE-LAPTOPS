"""Versiones de "Order" (Heaven Pierce Her) a partir del MIDI original.

    python versiones_order.py

Escribe en songs/:
  ORDER v1 simplificada.mid          todas las voces sin adornos (notas principales y su ritmo)
  ORDER v1 adornos.mid               los adornos que se sacaron, una pista por voz, en su tiempo original
  ORDER v2 simplificada 2 ritmicas.mid   la v1 con la guitarra ritmica separada en 2 (grave / aguda)
  ORDER v3 original 2 ritmicas.mid   el tema completo, con adornos, y la guitarra ritmica separada en 2

Que se considera adorno:
  - Guitarra ritmica: los compases donde deja los power chords (raiz + quinta) y hace una linea de
    una nota que sube y baja por la escala (o mueve el acorde una quinta arriba). En la version
    simplificada ese compas se reemplaza por las 2 notas principales en el patron de siempre:
    una nota larga y 4 corcheas rapidas.
  - Bajo: los compases de arpegio de relleno (raiz-quinta-octava) y las escalas de paso; se dejan
    en la raiz con el mismo ritmo.
  - Todas las voces: notas de adorno rapidas (glissandos, apoyaturas, tremolo), es decir notas de
    menos de una semicorchea pegadas a otra. El tremolo queda como una sola nota larga.
  La bateria no se toca (la app la toca por patrones).

Guitarra ritmica en 2: en cada golpe la nota mas baja va a la guitarra 1 y la mas alta a la 2; si hay
3 o mas notas se reparten parejo (la mitad de abajo a la 1, el resto a la 2). Cuando toca una sola nota
la tocan las dos, salvo los adornos de la v3, que van a la guitarra 2 (la aguda).
"""
import os
from collections import defaultdict

import mido

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "songs", "Heaven Pierce Her - ORDER.mid")
OUT = os.path.join(HERE, "songs")

RG = "Rhythm Guitar"
BASS = "Bass guitar"
RG2_CHANNEL = 7  # canal libre en el MIDI original

# Guitarra ritmica: compas con adorno -> las 2 notas principales de ese compas.
RG_FILL_BARS = {
    45: (35, 42), 61: (35, 42), 205: (35, 42), 221: (35, 42),      # linea 40-51-59-57 (Si5)
    51: (40, 47), 67: (40, 47), 211: (40, 47), 227: (40, 47),      # acorde que sube una quinta (Mi5)
    87: (40, 47), 91: (40, 47), 89: (41, 48), 93: (41, 48),        # Si-Do-Si-Do-Re-Do (Mi5 / Fa5)
    127: (38, 43), 131: (38, 43), 129: (39, 43), 133: (39, 43),    # arpegios Sol (Re-Sol / Mib-Sol)
}
# Bajo: compas de relleno -> raiz que se mantiene con el mismo ritmo.
BASS_FILL_BARS = {43: 33, 59: 33, 203: 33, 219: 33,   # 33-40-45 arpegio
                  51: 28, 67: 28, 211: 28, 227: 28,   # 28-35-40 arpegio
                  81: 29, 83: 28, 85: 29}             # escalas / octava de paso


def read_notes(track):
    """[start, dur, pitch, vel, channel] de una pista, sin notas duplicadas."""
    t, on, out, seen = 0, defaultdict(list), [], set()
    for m in track:
        t += m.time
        if m.type == "note_on" and m.velocity > 0:
            on[(m.channel, m.note)].append((t, m.velocity))
        elif m.type in ("note_off", "note_on") and on[(m.channel, m.note)]:
            s, v = on[(m.channel, m.note)].pop(0)
            if (s, m.note, m.channel) not in seen:
                seen.add((s, m.note, m.channel))
                out.append([s, t - s, m.note, v, m.channel])
    out.sort()
    return out


def other_messages(track, channel=None):
    """Todo lo que no es nota (nombre, programa, controles...), con tiempo absoluto."""
    t, out = 0, []
    for m in track:
        t += m.time
        if m.type in ("note_on", "note_off", "end_of_track"):
            continue
        if channel is not None and hasattr(m, "channel"):
            m = m.copy(channel=channel)
        out.append((t, m))
    return out


def build_track(name, meta, notes):
    ev = []
    for t, m in meta:
        if m.type == "track_name":
            m = m.copy(name=name)
        ev.append((t, 0, m))
    if not any(m.type == "track_name" for _, m in meta):
        ev.append((0, 0, mido.MetaMessage("track_name", name=name)))
    for s, d, p, v, c in notes:
        ev.append((s, 2, mido.Message("note_on", note=p, velocity=v, channel=c)))
        ev.append((s + max(1, d), 1, mido.Message("note_off", note=p, velocity=0, channel=c)))
    ev.sort(key=lambda e: (e[0], e[1]))
    tr, last = mido.MidiTrack(), 0
    for t, _, m in ev:
        tr.append(m.copy(time=t - last))
        last = t
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


def fast_ornaments(notes):
    """Notas de menos de una semicorchea pegadas a otra (glissando, apoyatura, tremolo)."""
    starts = sorted({n[0] for n in notes})
    gap = {}
    for i, s in enumerate(starts):
        prev = s - starts[i - 1] if i else 10 ** 9
        nxt = starts[i + 1] - s if i + 1 < len(starts) else 10 ** 9
        gap[s] = min(prev, nxt)
    return [n for n in notes if gap[n[0]] < 24 and n[1] < 24]


def simplify(name, notes, bar):
    """-> (notas principales, adornos)."""
    orn = fast_ornaments(notes)
    ids = {id(n) for n in orn}
    keep = [list(n) for n in notes if id(n) not in ids]
    # tremolo (la misma nota repetida muy rapido) -> una sola nota larga
    by_pitch = defaultdict(list)
    for n in orn:
        by_pitch[(n[2], n[0] // bar)].append(n)
    for (p, b), grp in by_pitch.items():
        if len(grp) >= 6:
            grp.sort()
            keep.append([grp[0][0], grp[-1][0] + grp[-1][1] - grp[0][0], p, grp[0][3], grp[0][4]])

    if name == RG:
        fills = [n for n in keep if n[0] // bar in RG_FILL_BARS]
        keep = [n for n in keep if n[0] // bar not in RG_FILL_BARS]
        orn += fills
        # compas 101, tiempo 3: el original repite el Do (48) dos veces; va el power chord Fa5
        b101 = [n for n in keep if n[0] == 101 * bar + 192]
        if len(b101) == 1:
            keep.append([b101[0][0], b101[0][1], 41, b101[0][3], b101[0][4]])
        vel = fills[0][3] if fills else 104
        ch = fills[0][4] if fills else 4
        for b, pair in RG_FILL_BARS.items():
            if not any(n[0] // bar == b for n in fills):
                continue
            # patron de la guitarra: una nota larga + 4 corcheas rapidas
            for off, dur in ((0, 92), (96, 20), (144, 20), (192, 20), (240, 20)):
                for p in pair:
                    keep.append([b * bar + off, dur, p, vel, ch])
    elif name == BASS:
        out = []
        for n in keep:
            root = BASS_FILL_BARS.get(n[0] // bar)
            if root is not None and n[2] != root:
                orn.append(n)
                out.append([n[0], n[1], root, n[3], n[4]])
            else:
                out.append(n)
        keep = out
    keep.sort()
    orn.sort()
    return keep, orn


def split_two(notes, ornaments=()):
    """Guitarra ritmica -> (guitarra 1 grave, guitarra 2 aguda)."""
    orn = {(n[0], n[2]) for n in ornaments}
    groups = defaultdict(list)
    for n in notes:
        groups[n[0]].append(n)
    low, high = [], []
    for s in sorted(groups):
        g = sorted(groups[s], key=lambda n: n[2])
        if len(g) == 1:
            n = g[0]
            if (n[0], n[2]) in orn:
                high.append(list(n))
            else:
                low.append(list(n))
                high.append(list(n))
        else:
            half = len(g) // 2
            low += [list(n) for n in g[:half]]
            high += [list(n) for n in g[half:]]
    for n in high:
        n[4] = RG2_CHANNEL
    return low, high


def main():
    mf = mido.MidiFile(SRC)
    bar = mf.ticks_per_beat * 3  # 3/4
    parts = []  # (indice, nombre, meta, notas originales, principales, adornos)
    for i, tr in enumerate(mf.tracks):
        notes = read_notes(tr)
        name = tr.name
        drums = notes and all(n[4] == 9 for n in notes)
        if notes and not drums:
            keep, orn = simplify(name, notes, bar)
        else:
            keep, orn = notes, []
        parts.append((i, name, other_messages(tr), notes, keep, orn))

    def write(fname, which, split_rg, with_orn_tracks=False):
        out = mido.MidiFile(type=1, ticks_per_beat=mf.ticks_per_beat)
        for i, name, meta, orig, keep, orn in parts:
            notes = orig if which == "orig" else keep
            if which == "orn":
                if i < 2:
                    out.tracks.append(build_track(name, meta, []))
                elif orn:
                    out.tracks.append(build_track("Adornos - " + name, meta, orn))
                continue
            if split_rg and name == RG:
                rg_orn = orn if which == "orig" else []
                low, high = split_two(notes, rg_orn)
                ch2 = other_messages(mf.tracks[i], RG2_CHANNEL)
                out.tracks.append(build_track("Rhythm Guitar 1 (grave)", meta, low))
                out.tracks.append(build_track("Rhythm Guitar 2 (aguda)", ch2, high))
            elif which == "orig" or not orn:
                out.tracks.append(mf.tracks[i].copy())  # pista sin cambios: tal cual el original
            else:
                out.tracks.append(build_track(name, meta, notes))
        path = os.path.join(OUT, fname)
        out.save(path)
        print("->", os.path.relpath(path, HERE))

    write("ORDER v1 simplificada.mid", "keep", False)
    write("ORDER v1 adornos.mid", "orn", False)
    write("ORDER v2 simplificada 2 ritmicas.mid", "keep", True)
    write("ORDER v3 original 2 ritmicas.mid", "orig", True)

    for i, name, meta, orig, keep, orn in parts:
        if orn:
            print(f"  {name}: {len(orn)} notas de adorno")


if __name__ == "__main__":
    main()
