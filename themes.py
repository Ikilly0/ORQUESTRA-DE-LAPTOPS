"""Theme settings, read from songs/temas.json (the code doesn't know any song by name).

A theme is just a .mid in songs/: the app makes one instrument per track on its own. temas.json only adds
what can't be guessed from the MIDI. Each entry:

    {
      "archivo": "heaven pierce her - order",   # words in the .mid's file name (no matter upper/lower case)
      "nombre": "Order - Ultrakill",            # name in the list of themes
      "juntar": [{"nombre": "Guitarra 2 + solista", "pistas": ["Guitar Etc", "Lead Guitar"]}],
      "teclas": {"Cello": "numeros"},           # instrument -> its own row ("numeros" = ` 1 .. 0 - =)
      "bajadas": ["Guitarra 2 + solista"],      # instruments that play their fast runs with Shift + Espacio
      "sonidos": {"Electric Guitar": "guitar_amp"}  # track -> sound id, instead of the one guessed from GM
    }

Only "archivo" is needed. Pistas are the track names of the MIDI (or their numbers). When several entries
match a file, the longest "archivo" wins, so "order" names every version of Order and
"heaven pierce her - order" adds the settings of the original MIDI only.
"""

import json
import os
import sys

from instruments import app_data_dir

CONFIG_NAME = "temas.json"
_CODE_DIR = os.path.dirname(os.path.abspath(__file__))
# the user's songs/ first (next to the program), then the ones bundled in a build
CONFIG_DIRS = [os.path.join(app_data_dir(), "songs"),
               os.path.join(getattr(sys, "_MEIPASS", _CODE_DIR), "songs"),
               os.path.join(_CODE_DIR, "songs")]

_cache = {"stamp": None, "entries": []}


def _files():
    seen, out = set(), []
    for d in CONFIG_DIRS:
        path = os.path.normcase(os.path.abspath(os.path.join(d, CONFIG_NAME)))
        if path not in seen and os.path.isfile(path):
            seen.add(path)
            out.append(path)
    return out


def entries():
    """Every entry of every temas.json (the user's first). Re-read when a file changes."""
    files = _files()
    stamp = tuple((p, os.path.getmtime(p)) for p in files)
    if stamp == _cache["stamp"]:
        return _cache["entries"]
    out, seen = [], set()
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"{CONFIG_NAME}: no se pudo leer {path}: {exc}", flush=True)
            continue
        for e in data.get("temas", []) if isinstance(data, dict) else data:
            key = str(e.get("archivo", "")).strip().lower() if isinstance(e, dict) else ""
            if key and key not in seen:
                seen.add(key)
                out.append(dict(e, archivo=key))
    _cache.update(stamp=stamp, entries=out)
    return out


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0].lower()


def entry_for(path):
    """The entry for this MIDI (longest matching "archivo"), or {}."""
    if not path:
        return {}
    stem = _stem(path)
    best = {}
    for e in entries():
        if e["archivo"] in stem and len(e["archivo"]) > len(best.get("archivo", "")):
            best = e
    return best


def label(path):
    """Name shown in the list of themes. Versions keep their own words: "Order - Ultrakill (v1 ...)"."""
    stem = os.path.splitext(os.path.basename(path))[0]
    e = entry_for(path)
    if not e.get("nombre"):
        return stem
    extra = stem[len(e["archivo"]):].strip(" -_()") if stem.lower().startswith(e["archivo"]) else ""
    return f"{e['nombre']} ({extra})" if extra else e["nombre"]


def _track_index(path, ref):
    """A track given by number or by (the start of) its name in the MIDI -> its index, or None."""
    if isinstance(ref, int):
        return ref
    from song import midi_tracks
    want = str(ref).strip().lower()
    tracks = midi_tracks(path)
    for exact in (True, False):
        for t in tracks:
            name = t["name"].strip().lower()
            if name == want if exact else name.startswith(want):
                return t["index"]
    return None


def merges(path):
    """[(new instrument name, [track indexes, main one first])]: tracks one person plays together."""
    out = []
    for m in entry_for(path).get("juntar", []):
        idx = [_track_index(path, r) for r in m.get("pistas", [])]
        if m.get("nombre") and len(idx) >= 2 and None not in idx:
            out.append((m["nombre"], idx))
        else:
            print(f"{CONFIG_NAME}: no encontré las pistas de {m!r} en {os.path.basename(path)}", flush=True)
    return out


def row_keys(path):
    """{instrument name: row name}: instruments that play on a row of their own."""
    return dict(entry_for(path).get("teclas", {}))


def sounds(path):
    """{track index: sound id}: tracks that use another sound than the one guessed from their GM instrument."""
    out = {}
    for ref, sound in entry_for(path).get("sonidos", {}).items():
        idx = _track_index(path, ref)
        if idx is None:
            print(f"{CONFIG_NAME}: no encontré la pista {ref!r} de \"sonidos\" en {os.path.basename(path)}", flush=True)
        else:
            out[idx] = str(sound)
    return out


def run_instruments(path):
    """Instrument names that play their fast runs (bajadas) with Shift + Espacio."""
    return list(entry_for(path).get("bajadas", []))
