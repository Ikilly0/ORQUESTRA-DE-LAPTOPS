"""Percusión por patrones: la pista de batería de un tema, descompuesta en trozos estándar que se repiten.

Cada compás de la batería del MIDI se convierte en una "huella" (qué pieza suena en qué 1/8 de tiempo).
Los compases parecidos se agrupan; cada grupo es un PATRÓN, que se toca con el compás real más
frecuente del grupo. Los patrones reciben un nombre según lo que tienen (ritmo de ride, de hi-hat,
ráfaga de doble bombo, redoble de caja, fill de toms, silencio...) y se marcan como:
  - "loop": se repite compás tras compás hasta que eliges otro;
  - "una vez": se toca un solo compás y vuelve al patrón que sonaba antes (fills, redobles).

Para tocar: el baterista enciende el reloj (Empezar) y elige qué patrón suena; el cambio entra en el
siguiente compás. La guía muestra qué patrón usa el tema en cada compás.
"""
from collections import Counter, defaultdict

from instruments import drum_group
from song import GM_DRUMS, load_song, midi_tracks, track_events

SLOTS_PER_BEAT = 8           # cuantización: 1/8 de tiempo (fusas a este tempo)
SIMILAR = 0.6                # parecido mínimo (Jaccard) para que dos compases sean "el mismo patrón"
MAX_PATTERNS = 10            # teclas 1..9 y 0
CYMBALS = ("crash", "ride", "ride_bell", "hat", "hat_open")
TOMS = ("tom_hi", "tom_mid", "tom_floor")

_CACHE = {}


UNIT_BARS = 2                # un "trozo" = 2 compases (las frases del tema van de a 2 y 4 compases)


class Pattern:
    def __init__(self, pid, hits, units, kind, name=""):
        self.id = pid
        self.hits = hits          # [(slot, piece)] del trozo representativo (slot = 1/8 de tiempo, 2 compases)
        self.units = units        # trozos del tema que usan este patrón (número del primer compás)
        self.kind = kind          # "loop" | "silence"
        self.name = name

    @property
    def pieces(self):
        return Counter(pc for _, pc in self.hits)

    def __repr__(self):
        return f"<Pattern {self.id} {self.name} {self.kind} x{len(self.units)}>"


def drum_track(path):
    return next((t["index"] for t in midi_tracks(path) if t["drums"]), None)


def _bars(song, events, shift, meter, part=None):
    """{bar: set((slot, piece))}, bars starting `shift` beats after the song's own bar lines."""
    per_bar = meter * SLOTS_PER_BEAT
    out = defaultdict(set)
    for b0, _b1, pitch, _ch in events:
        piece = GM_DRUMS.get(pitch, "hat")
        if part and drum_group(piece) != part:
            continue
        beat = b0 - song.offset - shift
        bar = song.bar_of(beat + 1e-6)
        slot = round((beat - song.bar_start(bar)) * SLOTS_PER_BEAT)
        if slot >= per_bar:
            bar, slot = bar + 1, slot - per_bar
        out[bar].add((slot, piece))
    return out


def _best_shift(song, events):
    """The drummer's "1": how many beats after the song's bar line the crashes / kicks fall most."""
    best, score_best = 0, -1
    for shift in range(song.meter):
        bars = _bars(song, events, shift, song.meter)
        score = sum(3 * ((0, "crash") in h) + ((0, "kick") in h) for h in bars.values())
        if score > score_best:
            best, score_best = shift, score
    return best


def _sim(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _name(hits):
    c = Counter(pc for _, pc in hits)
    slots = defaultdict(list)
    for s, pc in hits:
        slots[pc].append(s)

    def burst(pc, n=4, width=5):  # n o más golpes seguidos de una pieza en `width` slots (1/8 de tiempo)
        s = sorted(slots.get(pc, []))
        return any(s[i + n - 1] - s[i] < width for i in range(len(s) - n + 1))

    if not hits:
        return "Silencio"
    if c["kick"] + c["kick2"] >= 20:
        return "Ráfaga de doble bombo"
    if burst("snare"):
        return "Redoble de caja"
    if sum(c[t] for t in TOMS) >= 2:
        return "Fill de toms"
    if any(burst(pc) for pc in CYMBALS):
        return "Ráfaga de platillos"
    if c["hat"] >= 20:
        return "Hi-hat rápido"
    if c["crash"] >= 4:
        return "Crashes"
    if c["ride"] >= 6:
        return "Ritmo con ride"
    if c["hat"] or c["hat_open"]:
        return "Ritmo con hi-hat"
    return "Ritmo"


def analyse(path, part=None):
    """-> dict(patterns, guide={first bar of each 2-bar trozo: pattern id}, shift, track, meter, first, last).
    part = "tambores" / "platillos" / None: only those pieces of the kit."""
    key = (path, part)
    if key in _CACHE:
        return _CACHE[key]
    song = load_song(path)
    tr = drum_track(path)
    if tr is None:
        return None
    events = track_events(path, tr)
    shift = _best_shift(song, events)
    meter = song.meter
    bars = _bars(song, events, shift, meter, part)
    last = song.bar_of(song.total_beats - 0.01)
    drum_bars = [b for b in bars if bars[b]]
    first = min(drum_bars) if drum_bars else 1
    first -= ((first - 1) // UNIT_BARS) * 0  # trozos start where the drums start
    start = first - UNIT_BARS * ((first - 1) // UNIT_BARS)  # ... and go back to the song's start in steps of 2
    per_bar = meter * SLOTS_PER_BEAT
    seq = []
    for u in range(start, last + 1, UNIT_BARS):
        h = set()
        for k in range(UNIT_BARS):
            for slot, pc in bars.get(u + k, ()):
                h.add((slot + per_bar * k, pc))
        seq.append((u, frozenset(h)))

    # 1) group: most frequent trozos first; each one joins the most similar group, or starts its own
    freq = Counter(h for _, h in seq)
    groups = []  # [representative hits, [member hits]]
    for h in sorted(freq, key=lambda h: (-freq[h], -len(h))):
        best = max(groups, key=lambda g: _sim(g[0], h), default=None)
        if best is not None and _sim(best[0], h) >= SIMILAR and bool(best[0]) == bool(h):
            best[1].append(h)
        else:
            groups.append([h, [h]])

    def weight(g):
        return sum(freq[h] for h in g[1])
    # 2) too many: fold the smallest groups into their most similar bigger one
    while len(groups) > MAX_PATTERNS:
        groups.sort(key=weight)
        small = groups.pop(0)
        same_kind = [g for g in groups if bool(g[0]) == bool(small[0])] or groups
        max(same_kind, key=lambda g: _sim(g[0], small[0]))[1].extend(small[1])
    # 3) the patterns, numbered in the order the song first uses them
    member = {h: gi for gi, g in enumerate(groups) for h in g[1]}
    first_use = {}
    for u, h in seq:
        first_use.setdefault(member[h], u)
    order = sorted(range(len(groups)), key=lambda gi: first_use.get(gi, 10 ** 6))
    remap = {gi: pos for pos, gi in enumerate(order)}
    patterns = []
    for pos, gi in enumerate(order):
        rep = groups[gi][0]
        units = [u for u, h in seq if member[h] == gi]
        patterns.append(Pattern(pos, sorted(rep), units, "silence" if not rep else "loop", _name(rep)))
    names = Counter(p.name for p in patterns)
    seen = Counter()
    for p in patterns:  # same name twice: Ritmo con ride A, B...
        if names[p.name] > 1:
            seen[p.name] += 1
            p.name = f"{p.name} {'ABCDEFGHIJ'[seen[p.name] - 1]}"
    guide = {u: remap[member[h]] for u, h in seq}
    out = {"patterns": patterns, "guide": guide, "shift": shift, "track": tr, "meter": meter,
           "first": start, "last": last, "unit": UNIT_BARS, "slots_per_bar": per_bar}
    _CACHE[key] = out
    return out


def unit_of(info, bar):
    """First bar of the 2-bar trozo that contains `bar`."""
    return info["first"] + ((bar - info["first"]) // info["unit"]) * info["unit"]


class PatternSequencer:
    """Which pattern each bar of the drums plays: the musical state, with no window, clock or audio.

    Everything is counted in bars of the song (never seconds), so the same patterns follow any tempo and
    a change always lands on a bar line. The state only changes through commands, plain dicts that can be
    saved or sent over the network to the other laptops later:
        {"type": "choose", "part": "tambores", "pattern": 3}   # comes in on the next bar of `quantize`
        {"type": "mode", "part": "tambores", "mode": "auto"}
    `listeners` are called with every command applied (a future network link subscribes there).

    mode "manual" / "band": you choose; "auto": the bars follow the song's own patterns.
    When the pattern you play is the one the song uses there, the bar is the song's exact bar."""

    MODES = ("manual", "band", "auto")

    def __init__(self, info, real_bars, part=None, mode="manual", quantize=1):
        self.info = info
        self.real = real_bars
        self.part = part
        self.mode = mode if mode in self.MODES else "manual"
        self.quantize = max(1, int(quantize))  # bars between possible changes
        self.current = None
        self.queued = None
        self.listeners = []
        self.hits_ok = 0
        self.bars_done = 0

    @property
    def patterns(self):
        return self.info["patterns"]

    def song_pattern(self, bar):
        return self.info["guide"].get(unit_of(self.info, bar))

    def command(self, cmd):
        """Applies a command (from the keys here or, later, from the network)."""
        if cmd.get("part", self.part) != self.part:
            return False
        if cmd.get("type") == "choose" and 0 <= cmd.get("pattern", -1) < len(self.patterns):
            if cmd.get("now") or self.current is None:
                self.current, self.queued = cmd["pattern"], None
            else:
                self.queued = cmd["pattern"]
        elif cmd.get("type") == "mode" and cmd.get("mode") in self.MODES:
            self.mode = cmd["mode"]
        else:
            return False
        for fn in self.listeners:
            fn(cmd)
        return True

    def choose(self, i, now=False):
        return self.command({"type": "choose", "part": self.part, "pattern": i, "now": now})

    def start(self, bar):
        """Starting at `bar`: nothing chosen yet -> the pattern the song uses there."""
        self.hits_ok = self.bars_done = 0
        if self.queued is not None:
            self.current, self.queued = self.queued, None
        if self.current is None or self.mode == "auto":
            self.current = self.song_pattern(bar) or 0

    def is_boundary(self, bar):
        return (bar - self.info["first"]) % self.quantize == 0

    def bar_hits(self, bar):
        """[(slot, piece)] that `bar` plays (slots of 1/SLOTS_PER_BEAT beat from its first beat)."""
        song_pid = self.song_pattern(bar)
        if self.mode == "auto" and song_pid is not None:
            self.current, self.queued = song_pid, None
        elif self.queued is not None and self.is_boundary(bar):
            self.current, self.queued = self.queued, None
        if self.current is None:
            self.current = song_pid or 0
        self.bars_done += 1
        if song_pid == self.current:  # the right pattern: the song's own bar
            self.hits_ok += 1
            return sorted(self.real.get(bar, ()))
        spb = self.info["slots_per_bar"]
        half = bar - unit_of(self.info, bar)  # which bar of the pattern's trozo
        return [(s - half * spb, pc) for s, pc in self.patterns[self.current].hits
                if half * spb <= s < (half + 1) * spb]
