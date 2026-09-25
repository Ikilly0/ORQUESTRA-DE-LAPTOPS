"""User instruments ("perfiles"): a sound + a list of keys, each key playing one note (or drum piece).

Saved in profiles.json next to the app. Default profiles: Guitarra and Piano.
Profile format:
    {"name": "Piano", "sound": "piano",
     "slots": [{"key": "a", "midi": 52}, ...]}          # pitched sounds
     "slots": [{"key": "a", "piece": "kick"}, ...]}     # drum kit
"""

import json
import os

from instruments import DRUM_GROUP_NAMES, DRUM_GROUPS, DRUM_PIECES, INSTRUMENTS

MAX_KEYS = 40

ROW_NUMBERS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "minus", "equal"]
ROW_TOP = ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "bracketleft", "bracketright"]
ROW_HOME = ["a", "s", "d", "f", "g", "h", "j", "k", "l", "semicolon", "apostrophe"]
ROW_BOTTOM = ["z", "x", "c", "v", "b", "n", "m", "comma", "period", "slash"]

# Physical position of each key: (row, start in half-key units). Used to draw the pads like a keyboard.
KEY_POSITIONS = {}
for _row, (_keys, _offset) in enumerate(((["grave"] + ROW_NUMBERS, 0), (ROW_TOP + ["backslash"], 3),
                                         (ROW_HOME, 4), (ROW_BOTTOM, 5))):
    for _i, _k in enumerate(_keys):
        KEY_POSITIONS[_k] = (_row, _offset + 2 * _i)

# Key orders for "quick fill keys": low notes first.
# The letter/number keys of each row (no ; ' [ ] etc.): the home row goes A to L.
HOME_MAIN = ROW_HOME[:9]      # a s d f g h j k l
TOP_MAIN = ROW_TOP[:10]       # q w e r t y u i o p
NUM_MAIN = ROW_NUMBERS[:10]   # 1 2 3 4 5 6 7 8 9 0
LOW_MAIN = ROW_BOTTOM[:7]     # z x c v b n m
# Adding keys while playing: higher notes continue A..L, then Q..P, then 1..0;
# lower notes take the bottom row from M leftwards (M, N, B, ... Z), so lower = further left.
HIGH_ORDER = HOME_MAIN + TOP_MAIN + NUM_MAIN
LOW_ORDER = list(reversed(LOW_MAIN))
EXTRA_KEYS = ["semicolon", "apostrophe", "comma", "period", "slash", "bracketleft", "bracketright",
              "minus", "equal"]

LAYOUTS = {
    "Fila central (A ... L)": HOME_MAIN,
    "Central + superior (A... L, luego Q... P)": HOME_MAIN + TOP_MAIN,
    "Inferior + central + superior (Z... M, A... L, Q... P)": LOW_MAIN + HOME_MAIN + TOP_MAIN,
    "Las 4 filas (Z... M, A... L, Q... P, 1... 0)": LOW_MAIN + HOME_MAIN + TOP_MAIN + NUM_MAIN,
}
# layouts of older versions (to update instruments saved with them)
_OLD_HOME_TOP = ROW_HOME + ROW_TOP

# Scales as semitones inside one octave; they repeat upwards to fill any number of keys.
SCALES = {
    "Mayor": [0, 2, 4, 5, 7, 9, 11],
    "Menor natural": [0, 2, 3, 5, 7, 8, 10],
    "Menor armonica": [0, 2, 3, 5, 7, 8, 11],
    "Dorica": [0, 2, 3, 5, 7, 9, 10],
    "Frigia": [0, 1, 3, 5, 7, 8, 10],
    "Mixolidia": [0, 2, 4, 5, 7, 9, 10],
    "Pentatonica mayor": [0, 2, 4, 7, 9],
    "Pentatonica menor": [0, 3, 5, 7, 10],
    "Blues": [0, 3, 5, 6, 7, 10],
    "Cromatica": list(range(12)),
    "Tonos enteros": [0, 2, 4, 6, 8, 10],
}

SOUND_IDS = [i.id for i in INSTRUMENTS]


def scale_notes(root_midi, scale, count):
    steps = SCALES[scale]
    return [root_midi + 12 * (i // len(steps)) + steps[i % len(steps)] for i in range(count)]


def make_profile(name, sound, count, root_midi, scale, layout):
    keys = LAYOUTS[layout][:count]
    notes = scale_notes(root_midi, scale, len(keys))
    return {"name": name, "sound": sound, "slots": [{"key": k, "midi": m} for k, m in zip(keys, notes)]}


# Piano-style layout (like trackers / DAWs): white keys on the Z and Q rows,
# black keys just above them on the A and number rows.
PIANO_WHITE_LOW = ["z", "x", "c", "v", "b", "n", "m", "comma", "period", "slash"]          # C D E F G A B C D E
PIANO_BLACK_LOW = {"s": 1, "d": 3, "g": 6, "h": 8, "j": 10, "l": 13, "semicolon": 15}      # semitones above C
PIANO_WHITE_HIGH = ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "bracketleft", "bracketright"]
PIANO_BLACK_HIGH = {"2": 1, "3": 3, "5": 6, "6": 8, "7": 10, "9": 13, "0": 15, "equal": 18}
WHITE_STEPS = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19]


def piano_layout(low_octave=3, high_octave=4):
    """Slots for the piano-style layout. Lower row starts at C<low_octave>, upper row at C<high_octave>."""
    slots = []
    for whites, blacks, octave in ((PIANO_WHITE_LOW, PIANO_BLACK_LOW, low_octave),
                                   (PIANO_WHITE_HIGH, PIANO_BLACK_HIGH, high_octave)):
        c = 12 * (octave + 1)
        notes = [(k, c + WHITE_STEPS[i]) for i, k in enumerate(whites)] + [(k, c + st) for k, st in blacks.items()]
        slots += [{"key": k, "midi": m} for k, m in sorted(notes, key=lambda x: x[1])]
    return slots


def default_profiles():
    return [
        # E major, like Freesia. Guitar: 20 keys from E2 (home row = low, top row = high).
        make_profile("Guitarra", "clean_guitar", 19, 40, "Mayor", "Central + superior (A... L, luego Q... P)"),
        # Piano: laid out like a piano. Whites on Z (C3...) and Q (C4...), blacks on A and 1 rows.
        {"name": "Piano", "sound": "piano", "slots": piano_layout(3, 4)},
    ]


def is_drums(sound):
    return next((i.kind == "drums" for i in INSTRUMENTS if i.id == sound), False)


def sanitize(profile):
    """Makes sure a profile loaded from disk is usable."""
    sound = profile.get("sound") if profile.get("sound") in SOUND_IDS else "piano"
    drums = is_drums(sound)
    slots = []
    used = set()
    for s in profile.get("slots", [])[:MAX_KEYS]:
        k = str(s.get("key", "")).lower()
        if not k or k in used:
            continue
        used.add(k)
        if drums:
            slots.append({"key": k, "piece": s.get("piece") if s.get("piece") in DRUM_PIECES else "kick"})
        else:
            m = s.get("midi", 60)
            slots.append({"key": k, "midi": int(m) if isinstance(m, (int, float)) else 60})
    out = {"name": str(profile.get("name", "Sin nombre")), "sound": sound, "slots": slots}
    if profile.get("theme"):
        out["theme"] = str(profile["theme"])
    if isinstance(profile.get("track"), int):
        out["track"] = profile["track"]
        extra = [t for t in profile.get("extra_tracks", []) if isinstance(t, int)]
        if extra:
            out["extra_tracks"] = extra
    if drums and profile.get("part") in DRUM_GROUPS:
        out["part"] = profile["part"]
    if profile.get("all_notes"):
        out["all_notes"] = True
    return out


def drum_part_profiles(name, key, track):
    """A MIDI drum track as two instruments: drums (kick, snare, toms) and cymbals (hats, ride, crash)."""
    out = []
    for part, pieces in DRUM_GROUPS.items():
        out.append({"name": f"{name}: {DRUM_GROUP_NAMES[part].lower()}", "sound": "drums", "theme": key,
                    "track": track, "part": part,
                    "slots": [{"key": k, "piece": pc} for k, pc in zip(HOME_MAIN, pieces)]})
    return out


def theme_key(path):
    return os.path.basename(path).lower()


def _clean_track_name(name):
    import re
    name = re.sub(r"\(.*?\)", "", name).strip() or name.strip()
    return name[:30]


TRACK_NAMES_ES = {
    "cello": "Cello", "violin": "Violín", "viola": "Viola", "bass guitar": "Bajo", "bass": "Bajo",
    "tremolo strings": "Cuerdas trémolo", "string ens": "Orquesta de cuerdas", "strings": "Cuerdas",
    "string ensemble 1": "Orquesta de cuerdas", "rhythm guitar": "Guitarra rítmica",
    "lead guitar": "Guitarra solista", "guitar etc": "Guitarra 2", "guitar": "Guitarra", "piano": "Piano",
    "acoustic grand piano": "Piano", "choir": "Coro", "choir aahs": "Coro", "drums": "Batería",
    "flute": "Flauta", "trumpet": "Trompeta", "organ": "Órgano",
}


def theme_profiles(path):
    """One instrument per track of a MIDI theme: our sound closest to the track's General MIDI instrument,
    and its keys filled with the notes that track plays most (real octave, low to high)."""
    from instruments import sound_for_program
    from song import midi_tracks, note_stats
    key = theme_key(path)
    out, names = [], set()
    home_top = LAYOUTS["Central + superior (A... L, luego Q... P)"]
    for t in midi_tracks(path):
        name = _clean_track_name(t["name"])
        if name.lower() in {"sforzando", "miditrack", "track", "untitled", ""} or name.lower().startswith("pista"):
            name = t["instrument"]
        name = TRACK_NAMES_ES.get(name.lower(), name)
        if name.lower().startswith("etc"):
            name = TRACK_NAMES_ES.get(t["instrument"].lower(), t["instrument"]) + " 2"
        base, n = name, 2
        while name in names:
            name = f"{base} {n}"
            n += 1
        names.add(name)
        if t["drums"]:
            out += drum_part_profiles(name, key, t["index"])
            continue
        st = note_stats(path, t["index"])
        pad = _notes_for(st, t["low"], t["high"], 8) if len(st["pitches"]) < 8 else ()
        top = all_notes(st["pitches"], pad)  # every note the track plays gets its own key
        out.append({"name": name, "sound": sound_for_program(t["program"]), "theme": key, "track": t["index"],
                    "all_notes": True, "slots": note_slots(top)})
    return _apply_merges(path, out, home_top)


# Tracks that one person plays together: they take turns in the song, never at the same time.
# theme -> [(new name, [track indexes, main one first])]
THEME_MERGES = {
    "heaven pierce her - order.mid": [("Guitarra 2 + solista", [9, 8])],
}


def _notes_for(st, low, high, count_max):
    """The notes a track plays most (real octave), low to high; padded with scale notes around its range."""
    total = sum(st["pcs"].values()) or 1
    pcs = {pc for pc, n in st["pcs"].items() if n / total >= 0.05} or set(st["pcs"])
    # the key's notes, plus any single pitch that is used a lot (a C# the solo repeats, say)
    pool = sorted(m for m, n in st["pitches"].items() if m % 12 in pcs or n / total >= 0.015)
    count = max(8, min(count_max, len(pool)))
    top = sorted(sorted(pool, key=lambda m: -st["pitches"][m])[:count])
    used = set(top)
    order = (list(range(high + 1, high + 13)) + list(range(low - 1, low - 13, -1))
             + list(range(high + 13, 128)) + list(range(low - 13, -1, -1)))
    for m in order:  # too few different notes: add scale notes around the track's range
        if len(top) >= count:
            break
        if 0 <= m < 128 and m % 12 in pcs and m not in used:
            top.append(m)
            used.add(m)
    return sorted(top)


def keys_for_notes(n):
    """Keys for n notes, low to high. Up to 19: A..L then Q..P. More: the lowest notes go on the bottom row
    (ending at M, next to A), the highest on the number row, then ; ' , . / [ ] - =."""
    home_top = HOME_MAIN + TOP_MAIN
    if n <= len(home_top):
        return home_top[:n]
    low = min(len(LOW_MAIN), n - len(home_top))
    keys = LOW_MAIN[len(LOW_MAIN) - low:] + home_top + NUM_MAIN + EXTRA_KEYS
    return keys[:n]


def track_pitch_counts(path, tracks):
    """{pitch: times played} of the given tracks (drum channel excluded)."""
    from song import note_stats
    counts = {}
    for tr in tracks:
        for m, n in note_stats(path, tr)["pitches"].items():
            counts[m] = counts.get(m, 0) + n
    return counts


def all_notes(counts, extra=()):
    """Every pitch a track plays (the most played ones if there are more than MAX_KEYS), low to high."""
    pitches = set(counts) | set(extra)
    if len(pitches) > MAX_KEYS:
        pitches = set(sorted(pitches, key=lambda m: -counts.get(m, 0))[:MAX_KEYS])
    return sorted(pitches)


def note_slots(pitches):
    return [{"key": k, "midi": m} for k, m in zip(keys_for_notes(len(pitches)), pitches)]


def merged_profile(path, name, tracks, sound, keys=None):
    """One instrument that follows several tracks: keys = the notes they play most, all together."""
    from song import note_stats
    stats = [note_stats(path, tr)["pitches"] for tr in tracks]
    counts = {}
    for s in stats:
        for m, n in s.items():
            counts[m] = counts.get(m, 0) + n
    # every note that is important for one of the tracks (>= 5% of it: the whole short solo), then the
    # most played notes overall
    top = all_notes(counts)  # every note both tracks play, one key each
    out = {"name": name, "sound": sound, "theme": theme_key(path), "track": tracks[0], "all_notes": True,
           "extra_tracks": list(tracks[1:]), "slots": note_slots(top)}
    strip_run_notes(path, out)  # notes only used in a bajada (Shift + Espacio) get no key
    return out


def theme_merges(path):
    return THEME_MERGES.get(theme_key(path), [])


# Fast runs ("bajadas") that one instrument plays with a single gesture, Shift + Espacio, instead of
# note by note. theme -> instrument names. The runs themselves are found in the MIDI: chromatic
# descents of RUN_MIN_NOTES notes or more, each note right after the previous one.
THEME_RUNS = {
    "heaven pierce her - order.mid": ["Guitarra 2 + solista"],
}
RUN_MIN_NOTES = 12
RUN_MAX_GAP = 0.25  # beats between two notes of a run


def find_runs(events):
    """[(start_beat, end_beat, pitch, ...)] sorted -> [[event, ...], ...]: the fast chromatic descents."""
    runs, cur = [], []
    for ev in events:
        if cur and ev[2] == cur[-1][2] - 1 and 0 < ev[0] - cur[-1][0] <= RUN_MAX_GAP + 1e-6:
            cur.append(ev)
            continue
        if len(cur) >= RUN_MIN_NOTES:
            runs.append(cur)
        cur = [ev]
    if len(cur) >= RUN_MIN_NOTES:
        runs.append(cur)
    return runs


def has_runs(path, profile):
    names = THEME_RUNS.get(theme_key(path), []) if path else []
    return profile.get("name", "").split(" (")[0] in names and isinstance(profile.get("track"), int)


def profile_runs(path, profile):
    """The runs (bajadas) this instrument plays with Shift + Espacio, as lists of MIDI events."""
    if not has_runs(path, profile):
        return []
    from song import track_events
    tracks = [profile["track"]] + profile.get("extra_tracks", [])
    return find_runs(sorted(ev for tr in tracks for ev in track_events(path, tr)))


def run_only_notes(path, profile):
    """Notes the instrument plays only inside its runs: they don't need a key of their own."""
    runs = profile_runs(path, profile)
    if not runs:
        return set()
    in_runs = {}
    for run in runs:
        for ev in run:
            in_runs[ev[2]] = in_runs.get(ev[2], 0) + 1
    total = track_pitch_counts(path, [profile["track"]] + profile.get("extra_tracks", []))
    return {m for m, n in in_runs.items() if total.get(m, 0) <= n}


def strip_run_notes(path, profile):
    """Removes the keys of the notes that only appear in the runs. Returns True if something changed."""
    drop = run_only_notes(path, profile)
    if not drop or not any(s.get("midi") in drop for s in profile["slots"]):
        return False
    keep = sorted(s["midi"] for s in profile["slots"] if s.get("midi") not in drop)
    profile["slots"] = note_slots(keep)
    return True


def _apply_merges(path, profiles, keys):
    for name, tracks in theme_merges(path):
        mine = [p for p in profiles if p.get("track") in tracks and "part" not in p]
        if len(mine) < len(tracks):
            continue
        main = next(p for p in mine if p["track"] == tracks[0])
        merged = merged_profile(path, name, tracks, main["sound"], keys)
        at = profiles.index(main)
        profiles = [p for p in profiles if p not in mine]
        profiles.insert(min(at, len(profiles)), merged)
    return profiles


def _old_default_piano():
    keys = ROW_BOTTOM + ROW_HOME + ROW_TOP
    notes = scale_notes(40, "Mayor", 30)
    return {"name": "Piano", "sound": "piano", "slots": [{"key": k, "midi": m} for k, m in zip(keys, notes)]}


def load_profiles(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        profiles = [sanitize(p) for p in data if isinstance(p, dict)]
        # the untouched old default Piano becomes the new piano-style one
        old = _old_default_piano()
        for i, p in enumerate(profiles):
            if p["name"] == "Piano" and p["sound"] == "piano" and p["slots"] == old["slots"]:
                profiles[i] = {"name": "Piano", "sound": "piano", "slots": piano_layout(3, 4)}
            # instruments laid out on the old home row (with ; and ') move to A..L then Q..P
            keys = [sl["key"] for sl in p["slots"]]
            new_order = HOME_MAIN + TOP_MAIN
            if 9 < len(keys) <= len(new_order) and keys == _OLD_HOME_TOP[:len(keys)]:
                for sl, k in zip(p["slots"], new_order):
                    sl["key"] = k
        if profiles:
            return profiles
    except (OSError, ValueError, TypeError):
        pass
    return default_profiles()


def save_profiles(path, profiles):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=1, ensure_ascii=False)
        return True
    except OSError:
        return False


def free_key(used, side=1):
    """A key not already used. side=+1 (higher notes): A..L, Q..P, 1..0 first;
    side=-1 (lower notes): the bottom row from M leftwards first."""
    first = LOW_ORDER + HIGH_ORDER if side < 0 else HIGH_ORDER + LOW_ORDER
    for k in first + EXTRA_KEYS:
        if k not in used:
            return k
    for c in "abcdefghijklmnopqrstuvwxyz0123456789":
        if c not in used:
            return c
    return None
