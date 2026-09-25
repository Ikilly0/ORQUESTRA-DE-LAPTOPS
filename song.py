"""Song mode: reads a MIDI file and turns it into a chord timeline.

The app does NOT play the MIDI. It only extracts the harmony (which chord
sounds in each bar) so every performer can play live over it: the keys of
each station become the tones of the current chord.

Pipeline:
  1. Read all notes (start, duration, pitch) from every track.
  2. Find the grid offset (some MIDIs are shifted against their own beat grid).
  3. Guess the meter (3 or 4 beats per bar) from where the bass notes fall,
     and the downbeat phase from which bar alignment gives the clearest chords.
  4. For every bar (optionally half bar) match the notes against chord templates.
  5. Merge repeated chords into events: [{"start": beat, "beats": n, "root": pc, "quality": q}, ...]
"""

import os

import numpy as np

try:
    import mido
except Exception:  # handled by the UI
    mido = None

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# quality -> (intervals, symbol shown to the performer)
QUALITIES = {
    "maj": ([0, 4, 7], ""),
    "min": ([0, 3, 7], "m"),
    "7": ([0, 4, 7, 10], "7"),
    "maj7": ([0, 4, 7, 11], "maj7"),
    "m7": ([0, 3, 7, 10], "m7"),
    "dim": ([0, 3, 6], "dim"),
    "sus4": ([0, 5, 7], "sus4"),
}

MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def chord_name(root, quality):
    return NOTE_NAMES[root] + QUALITIES[quality][1]


def chord_intervals(quality):
    return QUALITIES[quality][0]


_MIDI_CACHE, _SONG_CACHE, _TRACKS_CACHE = {}, {}, {}


def _merged(mf):
    """All messages of all tracks as (absolute tick, msg), in time order, without copying them
    (much faster than mido.merge_tracks)."""
    out = []
    for n, tr in enumerate(mf.tracks):
        tick = 0
        for i, msg in enumerate(tr):
            tick += msg.time
            out.append((tick, n, i, msg))
    out.sort(key=lambda x: (x[0], x[1], x[2]))
    return [(t, m) for t, _, _, m in out]


def _templates():
    rows, info = [], []
    for q, (iv, _) in QUALITIES.items():
        for r in range(12):
            t = np.zeros(12)
            t[[(r + i) % 12 for i in iv]] = 1
            rows.append(t / np.linalg.norm(t))
            pen = (0.03 if len(iv) == 4 else 0.0) + (0.04 if q in ("sus4", "dim") else 0.0)
            info.append((r, q, pen))
    return np.array(rows), info


_TPL = None


def _stamp(path):
    try:
        return (os.path.abspath(path), os.path.getmtime(path))
    except OSError:
        return (path, 0)


def load_midi(path):
    """Parsed MIDI file, cached (parsing a big MIDI takes a moment)."""
    key = _stamp(path)
    if key not in _MIDI_CACHE:
        _MIDI_CACHE[key] = mido.MidiFile(path)
    return _MIDI_CACHE[key]


def load_song(path):
    """Analysed Song, cached."""
    key = _stamp(path)
    if key not in _SONG_CACHE:
        _SONG_CACHE[key] = Song(path)
    return _SONG_CACHE[key]


class Song:
    def __init__(self, path):
        if mido is None:
            raise RuntimeError("The 'mido' package is missing: pip install mido")
        self.path = path
        mf = load_midi(path)
        self.tpb = mf.ticks_per_beat
        self.bpm = 120.0
        self.midi_meter = None
        self.notes = self._read(mf)  # list of (start_beat, dur_beats, pitch)
        if not self.notes:
            raise RuntimeError("The MIDI file has no notes.")
        self.offset = self._grid_offset()
        self.notes = [(s - self.offset, d, p) for s, d, p in self.notes]
        self.key_root, self.key_mode = self._key()
        self.meter, self.phase = self._meter()
        self.events = self._chords()
        self.total_beats = self.events[-1]["start"] + self.events[-1]["beats"]

    # ---- reading ----------------------------------------------------------
    def _read(self, mf):
        notes, tick, active = [], 0, {}
        tempo_set = False
        self.tempo_changes = []  # [(raw beat, bpm)]
        for tick, msg in _merged(mf):
            if msg.type == "set_tempo":
                bpm = mido.tempo2bpm(msg.tempo)
                if self.tempo_changes and abs(self.tempo_changes[-1][0] - tick / self.tpb) < 1e-9:
                    self.tempo_changes[-1] = (tick / self.tpb, bpm)
                elif not self.tempo_changes or abs(self.tempo_changes[-1][1] - bpm) > 1e-6:
                    self.tempo_changes.append((tick / self.tpb, bpm))
                if not tempo_set and tick == 0:
                    self.bpm = round(bpm, 1)
                    tempo_set = True
            elif msg.type == "time_signature" and self.midi_meter is None:
                self.midi_meter = msg.numerator
            elif msg.type == "note_on" and msg.velocity > 0 and msg.channel != 9:
                active.setdefault((msg.channel, msg.note), []).append(tick)
            elif msg.type in ("note_off", "note_on") and active.get((msg.channel, msg.note)):
                start = active[(msg.channel, msg.note)].pop(0)
                notes.append((start / self.tpb, max(tick - start, 1) / self.tpb, msg.note))
        notes.sort()
        return notes

    def _grid_offset(self):
        """Shift (in beats) that puts most note starts on the 8th-note grid."""
        starts = np.array([s for s, _, _ in self.notes])
        best, best_score = 0.0, -1
        for off in np.arange(0, 0.5, 1 / 48):
            x = (starts - off) % 0.5
            score = np.mean(np.minimum(x, 0.5 - x) < 0.02)
            if score > best_score + 1e-9:
                best, best_score = off, score
        return float(best)

    def _key(self):
        v = np.zeros(12)
        for _, d, p in self.notes:
            v[p % 12] += d
        best = (-2, 0, "major")
        for r in range(12):
            for mode, prof in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
                c = np.corrcoef(v, np.roll(prof, r))[0, 1]
                if c > best[0]:
                    best = (c, r, mode)
        return best[1], best[2]

    # ---- meter ------------------------------------------------------------
    def _bass_onsets(self):
        """Beat positions of the lowest note of each onset cluster (the 'left hand')."""
        by_start = {}
        for s, _, p in self.notes:
            k = round(s * 4) / 4
            by_start[k] = min(by_start.get(k, 999), p)
        lows = sorted(by_start.values())
        cutoff = lows[len(lows) // 3] if lows else 60  # lowest third of the texture
        return [s for s, p in by_start.items() if p <= cutoff and abs(s - round(s)) < 0.05]

    def _meter(self):
        onsets = self._bass_onsets()
        best_meter, best_peak = self.midi_meter or 4, 0.0
        for cyc in (3, 4):
            h = np.zeros(cyc)
            for s in onsets:
                h[int(round(s)) % cyc] += 1
            peak = h.std() / (h.mean() + 1e-9)  # an uneven pattern reveals the bar length
            if peak > best_peak + 0.15:
                best_meter, best_peak = cyc, peak
        # phase: which bar alignment gives the most confident chords
        best_phase, best_conf = 0, -1
        for phase in range(best_meter):
            conf = np.mean([c[2][2] for c in self._segment_chords(best_meter, phase, split=False)])
            if conf > best_conf:
                best_phase, best_conf = phase, conf
        return best_meter, best_phase

    # ---- chords -----------------------------------------------------------
    def _arrays(self):
        if getattr(self, "_arr_n", None) != len(self.notes):
            arr = np.array(self.notes, dtype=float).reshape(-1, 3)
            self._st, self._en, self._pi = arr[:, 0], arr[:, 0] + arr[:, 1], arr[:, 2].astype(int)
            self._pc = self._pi % 12
            self._arr_n = len(self.notes)
        return self._st, self._en, self._pi, self._pc

    def _chroma(self, a, b):
        st, en, pi, pc = self._arrays()
        mask = (en > a) & (st < b)
        if not mask.any():
            return np.zeros(12), None
        overlap = np.minimum(en[mask], b) - np.maximum(st[mask], a)
        v = np.bincount(pc[mask], weights=overlap, minlength=12)
        starting = mask & (st >= a - 0.05)
        bass = int(pi[starting].min()) % 12 if starting.any() else None
        return v, bass

    @staticmethod
    def _match(v, bass):
        global _TPL
        n = np.linalg.norm(v)
        if n == 0:
            return None
        v = v / n
        if _TPL is None:
            _TPL = _templates()
        mat, info = _TPL
        scores = mat @ v
        for j, (r, q, pen) in enumerate(info):
            scores[j] -= pen
            if bass == r:
                scores[j] += 0.08
        j = int(np.argmax(scores))  # first best, same order as before
        r, q, _ = info[j]
        best = (r, q, float(scores[j]))
        # a diminished triad is usually a dominant 7th without its root (G#dim = E7): if that root
        # is actually sounding, call it the 7th chord
        if q == "dim":
            r7 = (r - 4) % 12
            if v[r7] > 0.25 * v.max():
                best = (r7, "7", best[2])
        return best

    def _segment_chords(self, meter, phase, split=True):
        """[(start, beats, (root, quality, score)), ...] per bar, optionally split in two."""
        end = max(s + d for s, d, _ in self.notes)
        out = []
        start = phase - meter if phase > 0 else 0
        while start < end:
            a, b = start, start + meter
            v, bass = self._chroma(max(a, 0), b)
            whole = self._match(v, bass)
            if whole is None:
                start = b
                continue
            if split:
                cut = a + (2 if meter == 3 else meter // 2)
                v1, b1 = self._chroma(max(a, 0), cut)
                v2, b2 = self._chroma(cut, b)
                h1, h2 = self._match(v1, b1), self._match(v2, b2)
                if (h1 and h2 and h1[:2] != h2[:2]
                        and min(h1[2], h2[2]) > whole[2] + 0.08):
                    out.append((max(a, 0), cut - max(a, 0), h1))
                    out.append((cut, b - cut, h2))
                    start = b
                    continue
            out.append((max(a, 0), b - max(a, 0), whole))
            start = b
        return [(s, n, (r, q, sc)) for s, n, (r, q, sc) in out]

    def _chords(self):
        events = []
        for s, n, (r, q, sc) in self._segment_chords(self.meter, self.phase, split=True):
            if events and events[-1]["root"] == r and events[-1]["quality"] == q:
                events[-1]["beats"] += n
            else:
                events.append({"start": s, "beats": n, "root": r, "quality": q})
        return events

    # ---- queries used by the UI ---------------------------------------------
    def bar_of(self, beat):
        """Bar number (1 = first full bar; 0 = pickup before it, if any)."""
        return int(np.floor((beat - self.phase) / self.meter)) + 1

    def bar_start(self, bar):
        return self.phase + (bar - 1) * self.meter

    def event_index_at(self, beat):
        idx = 0
        for i, e in enumerate(self.events):
            if e["start"] <= beat + 1e-6:
                idx = i
            else:
                break
        return idx

    def sections(self, every_bars=8):
        """Rehearsal marks: (label, beat) every N bars, labelled with the chord found there."""
        marks = []
        last_bar = self.bar_of(self.total_beats - 0.01)
        for bar in range(1, last_bar + 1, every_bars):
            beat = max(0.0, self.bar_start(bar))
            e = self.events[self.event_index_at(beat)]
            marks.append((f"Bar {bar}  ({chord_name(e['root'], e['quality'])})", beat))
        return marks

    def key_name(self):
        return f"{NOTE_NAMES[self.key_root]} {self.key_mode}"

    # ---- tempo map (songs can speed up / slow down) -----------------------------
    def _segments(self):
        """[(song beat, seconds at that beat, bpm)] at scale 1.0. Song beats are shifted by the grid offset."""
        if getattr(self, "_segs", None) is None:
            changes = [(b - self.offset, bpm) for b, bpm in self.tempo_changes] or [(0.0, self.bpm)]
            if changes[0][0] > -1e9:
                changes.insert(0, (-1e6, changes[0][1]))
            segs, t = [], 0.0
            prev_b, prev_bpm = None, None
            for b, bpm in changes:
                if prev_b is not None:
                    t += (b - prev_b) * 60.0 / prev_bpm
                segs.append((b, t, bpm))
                prev_b, prev_bpm = b, bpm
            # make song beat 0 = second 0
            t0 = self._sec_raw(segs, 0.0)
            self._segs = [(b, tt - t0, bpm) for b, tt, bpm in segs]
        return self._segs

    @staticmethod
    def _sec_raw(segs, beat):
        seg = segs[0]
        for s in segs:
            if s[0] <= beat:
                seg = s
            else:
                break
        return seg[1] + (beat - seg[0]) * 60.0 / seg[2]

    def beat_to_seconds(self, beat):
        return self._sec_raw(self._segments(), beat)

    def seconds_to_beat(self, sec):
        segs = self._segments()
        seg = segs[0]
        for s in segs:
            if s[1] <= sec:
                seg = s
            else:
                break
        return seg[0] + (sec - seg[1]) * seg[2] / 60.0

    def bpm_at(self, beat):
        segs = self._segments()
        seg = segs[0]
        for s in segs:
            if s[0] <= beat:
                seg = s
            else:
                break
        return seg[2]

    def summary(self):
        tempo = f"{self.bpm:g} BPM" + (" (con cambios de tempo)" if len(self.tempo_changes) > 1 else "")
        mode = "mayor" if self.key_mode == "major" else "menor"
        return (f"{NOTE_NAMES[self.key_root]} {mode}, {tempo}, {self.meter} tiempos por compás, "
                f"{len(self.events)} cambios de acorde, {self.bar_of(self.total_beats - 0.01)} compases")


# --------------------------------------------------------------------------
# Roles: what each station plays in song mode
# --------------------------------------------------------------------------
INTERVAL_LABELS = {0: "R", 1: "b9", 2: "9", 3: "b3", 4: "3", 5: "4", 6: "b5", 7: "5", 8: "#5", 9: "6",
                   10: "b7", 11: "7"}


def chord_slots(root, quality, low, count=8):
    """The keys of a station for one chord: [(midi, label), ...], low to high.

    Chord tones stacked upwards from the root placed in the octave that starts at `low`
    (a MIDI note number): R, 3, 5, (7), 8, 3, 5, ... Labels get one arrow per octave up
    (the octave itself is "8", two octaves "15").
    """
    iv = chord_intervals(quality)
    r = low + (root - low) % 12
    out = []
    octave = 0
    while len(out) < count:
        for i in iv:
            if len(out) >= count:
                break
            o = 12 * octave + i
            if i == 0 and octave > 0:
                label = {1: "8", 2: "15", 3: "22"}.get(octave, "R" + "\u2191" * octave)
            else:
                label = INTERVAL_LABELS[i] + "\u2191" * octave
            out.append((r + o, label))
        octave += 1
    return out


# pattern = suggested order of the 8 keys (1-8), one step per `step` of a beat.
ROLES = {
    "bass": {
        "name": "Bajo - fundamentales", "instrument": "bass", "low": 28, "step": 1.0,
        "pattern": [1, 3, 4],
        "tip": "Una nota por tiempo. Cae en la fundamental (R) justo en cada cambio de acorde; camina con 5 y 8.",
    },
    "piano_lh": {
        "name": "Piano mano izquierda - acordes quebrados", "instrument": "piano", "low": 40, "step": 0.5,
        "pattern": [1, 3, 4, 5, 4, 3],
        "tip": "Corcheas subiendo y bajando. Mantenlas parejas: toda la banda se apoya en ti.",
    },
    "piano_rh": {
        "name": "Piano mano derecha - arpegio agudo", "instrument": "piano", "low": 57, "step": 0.5,
        "pattern": [4, 5, 6, 8, 6, 5],
        "tip": "Corcheas en el registro agudo. En los acordes con 7 prueba esa nota (color).",
    },
    "guitar_arp": {
        "name": "Guitarra 1 - arpegio limpio", "instrument": "clean_guitar", "low": 40, "step": 0.5,
        "pattern": [1, 3, 4, 5, 6, 5],
        "tip": "Deja sonar las notas (mantén las teclas). Cambia el orden cuando el acorde se repite.",
    },
    "guitar_counter": {
        "name": "Guitarra 2 - contra-arpegio", "instrument": "clean_guitar", "low": 52, "step": 0.5,
        "pattern": [6, 5, 4, 2, 3, 4],
        "tip": "Baja mientras la Guitarra 1 sube. Escúchala y rellena sus espacios.",
    },
    "guitar_power": {
        "name": "Guitarra 3 - power chords (partes fuertes)", "instrument": "guitar", "low": 40, "step": 1.0,
        "pattern": [1, 1, 1],
        "tip": "Enter activa los power chords. Mantén Espacio para palm mute en las partes suaves.",
    },
    "pad": {
        "name": "Voz / colchón - notas largas", "instrument": "vocal", "low": 52, "step": 3.0,
        "pattern": [2, 3, 5],
        "tip": "Una nota larga por acorde. Elige la más cercana a la anterior (conducción de voces).",
    },
    "melody": {
        "name": "Melodía - improvisar sobre la escala", "instrument": "lead_guitar", "low": 64, "step": 0.5,
        "pattern": [],
        "tip": "Tus teclas tocan las notas que configuraste. Pads destacados = notas del acorde actual: "
               "cae en ellas en el tiempo 1.",
    },
    "drums": {
        "name": "Batería", "instrument": "drums", "low": 0, "step": 1.0,
        "pattern": [],
        "tip": "Bombo en el tiempo 1, caja en el último; platillo al inicio de cada sección.",
    },
}


def midi_tracks(path):
    key = _stamp(path)
    if key not in _TRACKS_CACHE:
        _TRACKS_CACHE[key] = _midi_tracks(path)
    return [dict(t) for t in _TRACKS_CACHE[key]]


def _midi_tracks(path):
    """[{index, name, program, instrument, channel, notes, low, high, drums}] for every track with notes.
    A MIDI file holds no sound, only which General MIDI instrument each track asks for (its program)."""
    from instruments import GM_NAMES
    mf = load_midi(path)
    out = []
    for i, tr in enumerate(mf.tracks):
        program, channels, pitches = None, set(), []
        for msg in tr:
            if msg.type == "program_change" and program is None:
                program = msg.program
            elif msg.type == "note_on" and msg.velocity > 0:
                channels.add(msg.channel)
                pitches.append(msg.note)
        if not pitches:
            continue
        drums = 9 in channels
        prog = program or 0
        out.append({"index": i, "name": tr.name.strip() or f"Pista {i}", "program": prog,
                    "instrument": "Drums" if drums else GM_NAMES[prog], "channel": min(channels),
                    "notes": len(pitches), "low": min(pitches), "high": max(pitches), "drums": drums})
    return out


def note_stats(path, track=None):
    """How much each note is used in a MIDI (whole file, or one track). No note order is kept."""
    song = load_song(path)
    pcs, pitches = {}, {}
    if track is None:
        notes = [p for _, _, p in song.notes]
    else:
        notes = [m.note for m in load_midi(path).tracks[track]
                 if m.type == "note_on" and m.velocity > 0 and m.channel != 9]
    for p in notes:
        pcs[p % 12] = pcs.get(p % 12, 0) + 1
        pitches[p] = pitches.get(p, 0) + 1
    return {"pcs": pcs, "pitches": pitches, "key_root": song.key_root, "key_mode": song.key_mode}


def track_events(path, track):
    """[(start_beat, end_beat, pitch, channel)] of one track, in raw MIDI beats (for automatic playback)."""
    mf = load_midi(path)
    tpb = mf.ticks_per_beat
    out, tick, active = [], 0, {}
    for msg in mf.tracks[track]:
        tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            active.setdefault((msg.channel, msg.note), []).append(tick)
        elif msg.type in ("note_off", "note_on") and active.get((msg.channel, msg.note)):
            start = active[(msg.channel, msg.note)].pop(0)
            out.append((start / tpb, max(tick, start + 1) / tpb, msg.note, msg.channel))
    out.sort()
    return out


# General MIDI drum note -> our drum piece
GM_DRUMS = {35: "kick", 36: "kick", 37: "snare", 38: "snare", 39: "snare", 40: "snare",
            41: "tom_floor", 43: "tom_floor", 45: "tom_mid", 47: "tom_mid", 48: "tom_hi", 50: "tom_hi",
            42: "hat", 44: "hat", 46: "hat_open", 49: "crash", 52: "crash", 55: "crash", 57: "crash",
            51: "ride", 53: "ride_bell", 59: "ride"}
