"""Instrument definitions.

Every instrument is a class with some text fields (shown in the UI) and a
`voice(...)` method that returns a Voice: an object that renders audio in
blocks and knows how to release (fade out) when its key is let go.

Two kinds of instrument:
  kind = "pitched": each slot is a note. voice(freq, palm_mute=False, power=False)
  kind = "drums":   each slot is a drum piece. voice(piece="kick")

To add an instrument:
  1. Write a class that inherits from Instrument.
  2. Fill in id, name, description, color, info (and techniques if it has any).
  3. Implement voice(...) (reuse the Voice classes below).
  4. Add an instance to the INSTRUMENTS list at the bottom.
The UI picks it up automatically.
"""

import os
import wave

import numpy as np

SR = 48000  # sample rate shared with audio.py
TWO_PI = 2.0 * np.pi
FIFTH = 2 ** (7 / 12)


# --------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------
class Envelope:
    """Linear ADSR, rendered one block at a time (vectorised with numpy)."""

    def __init__(self, attack, decay, sustain, release):
        self.a = max(1, int(attack * SR))
        self.d = max(1, int(decay * SR))
        self.s = float(sustain)
        self.r = max(1, int(release * SR))
        self.t = 0
        self.released = False
        self.rel_t = 0
        self.rel_level = 0.0
        self.last = 0.0
        self.finished = False

    def release(self):
        if not self.released:
            self.released = True
            self.rel_t = 0
            self.rel_level = self.last

    def next(self, n):
        if self.finished:
            return np.zeros(n)
        if not self.released:
            t = self.t + np.arange(n)
            level = np.where(
                t < self.a,
                t / self.a,
                np.where(t < self.a + self.d, 1.0 - (1.0 - self.s) * (t - self.a) / self.d, self.s),
            )
            self.t += n
        else:
            t = self.rel_t + np.arange(n)
            level = np.clip(self.rel_level * (1.0 - t / self.r), 0.0, None)
            self.rel_t += n
            if self.rel_t >= self.r:
                self.finished = True
        self.last = float(level[-1])
        return level


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------
class KSString:
    """Karplus-Strong string, computed in chunks so numpy can do the work."""

    def __init__(self, freq, sustain_seconds=3.0, brightness=0.6):
        self.N = max(2, int(round(SR / freq - 0.5)))
        # loss per period so the string falls ~60 dB in `sustain_seconds`
        self.g = 0.001 ** (1.0 / (sustain_seconds * freq))
        noise = np.random.uniform(-1, 1, self.N + 1)
        # one-pole low-pass on the excitation: lower brightness = darker pick
        for i in range(1, len(noise)):
            noise[i] = brightness * noise[i] + (1 - brightness) * noise[i - 1]
        self.hist = noise - noise.mean()

    def render(self, n):
        out = np.empty(n)
        pos = 0
        N = self.N
        while pos < n:
            k = min(N, n - pos)
            new = self.g * 0.5 * (self.hist[1:1 + k] + self.hist[0:k])
            out[pos:pos + k] = new
            self.hist = np.concatenate((self.hist, new))[-(N + 1):]
            pos += k
        return out

    def energy(self):
        return float(np.max(np.abs(self.hist)))


def lowpass_fir(cutoff, taps=63):
    """Windowed-sinc low-pass. Used as a very simple guitar-cabinet filter."""
    m = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff / SR * m) * np.hamming(taps)
    return h / h.sum()


# --------------------------------------------------------------------------
# Voices (the actual sound generators)
# --------------------------------------------------------------------------
class Voice:
    def __init__(self, freq, env):
        self.freq = freq
        self.env = env
        self.done = False
        self.samples = 0  # samples rendered so far

    def release(self):
        self.env.release()

    def oscillate(self, n):  # override
        return np.zeros(n)

    def render(self, n):
        sig = self.oscillate(n)
        sig = sig * self.env.next(n)
        self.samples += n
        if self.env.finished:
            self.done = True
        return sig


class BufferVoice:
    """Plays a pre-computed buffer once (drums, metronome). Key release is ignored."""

    def __init__(self, buffer):
        self.buf = buffer
        self.pos = 0
        self.done = False

    def release(self):
        pass

    def render(self, n):
        chunk = self.buf[self.pos:self.pos + n]
        self.pos += n
        if self.pos >= len(self.buf):
            self.done = True
        if len(chunk) < n:
            chunk = np.concatenate((chunk, np.zeros(n - len(chunk))))
        return chunk


TABLE_N = 2048
_TABLES = {}


def _table(amps):
    """One cycle of sum(amp_k * sin(2*pi*k*x)), cached. amps[k-1] is harmonic k."""
    key = tuple(round(a, 5) for a in amps)
    tab = _TABLES.get(key)
    if tab is None:
        x = np.arange(TABLE_N) / TABLE_N
        tab = np.zeros(TABLE_N + 1)
        for k, a in enumerate(amps, start=1):
            if a:
                tab[:TABLE_N] += a * np.sin(TWO_PI * k * x)
        tab[TABLE_N] = tab[0]  # guard point for interpolation
        if len(_TABLES) > 400:
            _TABLES.clear()
        _TABLES[key] = tab
    return tab


def _read(tab, cycles):
    """Linear-interpolated table lookup; `cycles` is the phase in cycles (float array)."""
    pos = (cycles % 1.0) * TABLE_N
    i = pos.astype(np.int64)
    frac = pos - i
    return tab[i] + (tab[i + 1] - tab[i]) * frac


class AdditiveVoice(Voice):
    """Sum of harmonic partials [(frequency ratio, amplitude), ...] + optional vibrato.

    Rendered with wavetables (one per detuned copy), so the cost doesn't grow with the number
    of partials. Partials above ~0.45*SR are dropped (band-limited)."""

    def __init__(self, freq, env, partials, vibrato_rate=0.0, vibrato_depth=0.0, vibrato_delay=0.0):
        super().__init__(freq, env)
        self.vib_delay = vibrato_delay
        nyquist = SR * 0.45
        partials = [(r, a) for r, a in partials if freq * r < nyquist]
        norm = sum(a for _, a in partials) or 1.0
        groups = {}  # detune factor -> {harmonic: amp}
        for r, a in partials:
            k = max(1, int(round(r)))
            d = round(r / k, 5)
            groups.setdefault(d, {})
            groups[d][k] = groups[d].get(k, 0.0) + a / norm
        self.groups = []
        for d, hs in groups.items():
            amps = [hs.get(k, 0.0) for k in range(1, max(hs) + 1)]
            self.groups.append((d, _table(amps)))
        self.vib_rate = vibrato_rate
        self.vib_depth = vibrato_depth
        self.cycles = 0.0

    def oscillate(self, n):
        if self.vib_depth:
            t = (self.samples + np.arange(n)) / SR
            depth = self.vib_depth
            if self.vib_delay > 0:  # vibrato fades in after the delay, like a singer or a guitarist
                depth = self.vib_depth * np.clip((t - self.vib_delay) / 0.4, 0.0, 1.0)
            inst_f = self.freq * (1.0 + depth * np.sin(TWO_PI * self.vib_rate * t))
            cyc = self.cycles + np.cumsum(inst_f / SR)
        else:
            cyc = self.cycles + self.freq / SR * np.arange(1, n + 1)
        self.cycles = cyc[-1] % 1e6
        out = np.zeros(n)
        for d, tab in self.groups:
            out += _read(tab, cyc * d)
        return out


class PianoVoice(Voice):
    """Piano from two wavetables: a bright one that dies fast and a warm one that rings long,
    each on two strings detuned a hair (slow beating), plus a short hammer thump."""

    def __init__(self, freq, env, brightness=1.0):
        super().__init__(freq, env)
        self.base_tau = float(np.clip(3.0 * (261.6 / freq) ** 0.6, 0.6, 7.0))
        kmax = max(1, min(24, int(SR * 0.45 / freq)))
        bright = [(1.0 / k ** 1.3) * brightness ** (k - 1) for k in range(1, kmax + 1)]
        warm = [1.0, 0.35, 0.12, 0.05][:kmax]
        nb, nw = sum(bright), sum(warm)
        self.bright = _table([a / nb for a in bright])
        self.warm = _table([a / nw for a in warm])
        self.thump_f = min(freq, 200.0)
        self.ph = np.random.uniform(0, 1)

    def oscillate(self, n):
        k = np.arange(1, n + 1)
        t = (self.samples + k) / SR
        cyc = self.ph + self.freq * t
        b = 0.5 * (_read(self.bright, cyc) + _read(self.bright, cyc * 1.0007))
        w = 0.5 * (_read(self.warm, cyc) + _read(self.warm, cyc * 1.0007))
        out = b * np.exp(-t / (self.base_tau * 0.25)) * 0.7 + w * np.exp(-t / self.base_tau) * 0.5
        if self.samples < SR // 10:
            out += 0.15 * np.sin(TWO_PI * self.thump_f * t) * np.exp(-t / 0.012)
        if t[-1] > 1.0 and np.exp(-t[-1] / self.base_tau) < 1e-3:
            self.done = True
        return out


class LeadVoice(Voice):
    """Singing lead guitar: band-limited saw -> heavy drive -> cabinet, with delayed vibrato."""

    def __init__(self, freq, env, drive=6.0, cab_cutoff=4200, level=0.45):
        super().__init__(freq, env)
        self.osc = AdditiveVoice(freq, Envelope(0.001, 0.01, 1.0, 0.01),
                                 [(k, 1.0 / k) for k in range(1, 40)],
                                 vibrato_rate=5.5, vibrato_depth=0.008, vibrato_delay=0.35)
        self.drive = drive
        self.level = level
        self.h = lowpass_fir(cab_cutoff)
        self.tail = np.zeros(len(self.h) - 1)

    def oscillate(self, n):
        x = self.osc.oscillate(n)
        self.osc.samples += n
        y = np.tanh(self.drive * x)
        buf = np.concatenate((self.tail, y))
        self.tail = buf[-(len(self.h) - 1):]
        return np.convolve(buf, self.h, "valid") * self.level


def formant_partials(freq, formants, count=60):
    """Harmonic amplitudes shaped by vowel formants [(centre Hz, bandwidth Hz, gain), ...]."""
    parts = []
    for k in range(1, count):
        fk = freq * k
        if fk > SR * 0.45:
            break
        env = sum(g / (1 + ((fk - fc) / (bw / 2)) ** 2) for fc, bw, g in formants)
        parts.append((k, (env + 0.02) / k ** 0.5))
    return parts


class FMVoice(Voice):
    """Two-operator FM with a decaying modulation index (bells, electric piano)."""

    def __init__(self, freq, env, ratio, index, index_decay, amp_decay):
        super().__init__(freq, env)
        self.ratio = ratio
        self.index = index
        self.index_decay = index_decay
        self.amp_decay = amp_decay
        self.pc = 0.0
        self.pm = 0.0

    def oscillate(self, n):
        k = np.arange(1, n + 1)
        t = (self.samples + k) / SR
        pc = self.pc + TWO_PI * self.freq * k / SR
        pm = self.pm + TWO_PI * self.freq * self.ratio * k / SR
        self.pc, self.pm = pc[-1] % TWO_PI, pm[-1] % TWO_PI
        index = self.index * np.exp(-t / self.index_decay)
        return np.sin(pc + index * np.sin(pm)) * np.exp(-t / self.amp_decay)


class PluckVoice(Voice):
    """A single clean plucked string."""

    def __init__(self, freq, env, sustain_seconds=3.0, brightness=0.6):
        super().__init__(freq, env)
        self.string = KSString(freq, sustain_seconds, brightness)

    def oscillate(self, n):
        out = self.string.render(n)
        if self.samples > SR and self.string.energy() < 1e-4:
            self.done = True
        return out


class AmpVoice(Voice):
    """One or more strings -> distortion (tanh) -> cabinet low-pass.

    Distorting the strings together (not separately) gives the thick
    intermodulation that makes a distorted power chord sound like one.
    """

    def __init__(self, freqs, env, sustain, brightness, drive, cab_cutoff, level):
        super().__init__(freqs[0], env)
        self.strings = [KSString(f, sustain, brightness) for f in freqs]
        self.drive = drive
        self.level = level
        self.h = lowpass_fir(cab_cutoff)
        self.tail = np.zeros(len(self.h) - 1)

    def oscillate(self, n):
        x = sum(s.render(n) for s in self.strings)
        y = np.tanh(self.drive * x)
        buf = np.concatenate((self.tail, y))
        self.tail = buf[-(len(self.h) - 1):]
        if self.samples > SR and max(s.energy() for s in self.strings) < 1e-4:
            self.done = True
        return np.convolve(buf, self.h, "valid") * self.level


# --------------------------------------------------------------------------
# Drum sounds (synthesised, one buffer per hit)
# --------------------------------------------------------------------------
def _t(seconds):
    return np.arange(int(seconds * SR)) / SR


def _noise(n):
    return np.random.uniform(-1, 1, n)


def _hp(x):
    return np.diff(x, prepend=0.0)  # crude high-pass (first difference)


def _norm(x, peak):
    return x / (np.max(np.abs(x)) + 1e-9) * peak


def _sweep(t, f_end, f_start, tau):
    f = f_end + (f_start - f_end) * np.exp(-t / tau)
    return np.sin(np.cumsum(TWO_PI * f / SR))


def drum_kick():
    t = _t(0.5)
    body = _sweep(t, 48, 160, 0.03) * np.exp(-t / 0.22)
    click = _hp(_noise(len(t))) * np.exp(-t / 0.002) * 0.4
    return _norm(body + click, 1.0)


def drum_snare():
    t = _t(0.35)
    tone = np.sin(TWO_PI * 185 * t) * np.exp(-t / 0.05) + 0.5 * np.sin(TWO_PI * 330 * t) * np.exp(-t / 0.03)
    wires = _hp(_noise(len(t))) * np.exp(-t / 0.11)
    return _norm(0.6 * tone + wires, 0.85)


def _hat_noise(n):
    x = _hp(_noise(n))
    return x + np.roll(x, 1)  # band-pass around SR/4 (~12 kHz): bright but not fizzy


def drum_hat_closed():
    t = _t(0.12)
    return _norm(_hat_noise(len(t)) * np.exp(-t / 0.022), 0.4)


def drum_hat_open():
    t = _t(0.7)
    return _norm(_hat_noise(len(t)) * np.exp(-t / 0.2), 0.4)


def _metal(t, freqs):
    return sum(np.sign(np.sin(TWO_PI * f * t + np.random.uniform(0, TWO_PI))) for f in freqs)


def drum_crash():
    t = _t(2.2)
    n = _hp(_noise(len(t))) * np.exp(-t / 0.7)
    m = _hp(_metal(t, [540, 800, 1170, 1605, 2310, 3190])) * np.exp(-t / 0.9) * 0.15
    return _norm(n + m, 0.55)


def _band_noise(n, lo, hi):
    """White noise band-limited to [lo, hi] Hz (FFT mask)."""
    spec = np.fft.rfft(_noise(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, n)


def _cymbal_partials(t, n, lo, hi, tau_ref, rng):
    """A bank of inharmonic sine modes (like a real metal plate): low modes ring longer."""
    freqs = np.exp(rng.uniform(np.log(lo), np.log(hi), n)).astype(np.float32)
    taus = tau_ref * (1000.0 / freqs) ** 0.45
    amps = rng.uniform(0.4, 1.0, n).astype(np.float32) * (1000.0 / freqs) ** 0.35
    phases = rng.uniform(0, TWO_PI, n).astype(np.float32)
    tt = t.astype(np.float32)
    out = np.zeros(len(t), np.float32)
    for i in range(0, n, 12):  # 12 modes at a time as a matrix
        sl = slice(i, i + 12)
        out += (amps[sl, None] * np.sin(TWO_PI * freqs[sl, None] * tt + phases[sl, None])
                * np.exp(-tt / taus[sl, None])).sum(axis=0)
    return out.astype(float)


def drum_ride():
    """Ride: stick 'ping' on top of a shimmering wash of many metal modes (no square-wave buzz)."""
    rng = np.random.default_rng()
    t = _t(2.6)
    n = len(t)
    modes = _cymbal_partials(t, 48, 420, 11000, 1.4, rng)
    modes /= np.max(np.abs(modes)) + 1e-9
    ping = _band_noise(n, 3500, 9000) * np.exp(-t / 0.012)
    ping /= np.max(np.abs(ping)) + 1e-9
    wash = _band_noise(n, 5000, 14000) * np.exp(-t / 0.9)
    wash /= np.max(np.abs(wash)) + 1e-9
    attack = 1 - np.exp(-t / 0.002)
    return _norm(attack * (0.55 * modes + 0.12 * wash) + 0.45 * ping, 0.32)


def drum_ride_bell():
    """Bell of the ride: a few strong, clear modes that ring, plus the stick tick."""
    rng = np.random.default_rng()
    t = _t(2.4)
    base = rng.uniform(760, 800)
    out = np.zeros(len(t))
    for ratio, amp, tau in ((1.0, 1.0, 1.2), (2.02, 0.6, 0.9), (2.93, 0.45, 0.7), (4.16, 0.3, 0.5),
                            (5.43, 0.2, 0.35), (6.79, 0.12, 0.25)):
        out += amp * np.sin(TWO_PI * base * ratio * t + rng.uniform(0, TWO_PI)) * np.exp(-t / tau)
    out += 0.25 * _cymbal_partials(t, 25, 1500, 10000, 0.6, rng) / 10
    tick = _band_noise(len(t), 3000, 9000) * np.exp(-t / 0.008)
    return _norm(out / np.max(np.abs(out)) + 0.35 * tick / (np.max(np.abs(tick)) + 1e-9), 0.3)


def _tom(f):
    t = _t(0.7)
    body = _sweep(t, f, f * 1.6, 0.04) * np.exp(-t / 0.28)
    hit = _hp(_noise(len(t))) * np.exp(-t / 0.006) * 0.3
    return _norm(body + hit, 0.8)


# id -> (label, generator). Order = default order of the 8 slots.
DRUM_PIECES = {
    "kick": ("Kick", drum_kick),
    "kick2": ("Kick 2", drum_kick),  # second pedal: alternate two fingers for double bass
    "snare": ("Snare", drum_snare),
    "hat": ("Hi-hat", drum_hat_closed),
    "hat_open": ("Open hat", drum_hat_open),
    "crash": ("Crash", drum_crash),
    "tom_hi": ("Tom", lambda: _tom(170)),
    "tom_floor": ("Floor tom", lambda: _tom(95)),
    "ride": ("Ride", drum_ride),
    "tom_mid": ("Mid tom", lambda: _tom(130)),
    "ride_bell": ("Ride bell", drum_ride_bell),
}

# the kit split in two, so two people (or two hands) can share it
DRUM_GROUPS = {
    "tambores": ["kick", "kick2", "snare", "tom_hi", "tom_mid", "tom_floor"],
    "platillos": ["hat", "hat_open", "ride", "ride_bell", "crash"],
}
DRUM_GROUP_NAMES = {"tambores": "Tambores", "platillos": "Platillos"}


def drum_group(piece):
    return next((g for g, pieces in DRUM_GROUPS.items() if piece in pieces), None)


# Real recordings can replace the synthesised pieces: put  samples/<piece>.wav  next to app.py
# (for example samples/ride.wav, samples/snare.wav). 16/24-bit PCM, any sample rate, mono or stereo.
def app_data_dir():
    """Where the app keeps its own files (instruments, settings, logs, samples, your songs).
    From the source code: the code folder. As a built program (.exe / .app): next to the .exe on
    Windows and Linux; on macOS in ~/Library/Application Support/Keyboard Instruments (an .app
    should not write inside itself)."""
    import sys
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "darwin":
        d = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Keyboard Instruments")
        os.makedirs(d, exist_ok=True)
        return d
    return os.path.dirname(os.path.abspath(sys.executable))


SAMPLES_DIR = os.path.join(app_data_dir(), "samples")


def load_sample(piece):
    path = os.path.join(SAMPLES_DIR, piece + ".wav")
    if not os.path.isfile(path):
        return None
    try:
        with wave.open(path, "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if width == 2:
            x = np.frombuffer(raw, "<i2").astype(float) / 32768
        elif width == 3:
            b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
            v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(float) / (1 << 23)
        elif width == 4:
            x = np.frombuffer(raw, "<i4").astype(float) / 2 ** 31
        else:
            x = (np.frombuffer(raw, np.uint8).astype(float) - 128) / 128
        x = x.reshape(-1, ch).mean(axis=1)
        if rate != SR:
            pos = np.arange(int(len(x) * SR / rate)) * rate / SR
            x = np.interp(pos, np.arange(len(x)), x)
        return _norm(x, 0.8)
    except Exception as exc:
        print(f"sample {path}: {exc}", flush=True)
        return None


# --------------------------------------------------------------------------
# Instruments
# --------------------------------------------------------------------------
class Instrument:
    id = "base"
    name = "Base"
    kind = "pitched"  # or "drums"
    description = ""
    color = "#888888"
    techniques = ()  # any of "palm_mute", "power"
    # Free-form fields for the instrument window. Add whatever you need later.
    info = {}

    def voice(self, **params):
        raise NotImplementedError


# ---- band instruments -------------------------------------------------------
class DistortedGuitar(Instrument):
    id = "guitar"
    name = "Distorted guitar"
    description = "High-gain metal guitar. Palm mute and power chords, played live."
    color = "#d0452f"
    techniques = ("palm_mute", "power")
    info = {
        "Family": "Chordophone, electric (emulated)",
        "Synthesis": "Karplus-Strong strings -> tanh distortion -> cabinet low-pass (FIR)",
        "Palm mute": "Short, dark, tight chug (string damped ~0.35 s)",
        "Power chord": "Root + fifth + octave, distorted together",
        "Behaviour": "Rings while the key is held; releasing the key mutes the string",
    }

    def voice(self, freq, palm_mute=False, power=False, **_):
        freqs = [freq, freq * FIFTH, freq * 2] if power else [freq]
        if palm_mute:
            return AmpVoice(freqs, Envelope(0.001, 0.01, 1.0, 0.04), sustain=0.35, brightness=0.25,
                            drive=9.0, cab_cutoff=3200, level=0.55)
        return AmpVoice(freqs, Envelope(0.001, 0.01, 1.0, 0.06), sustain=4.0, brightness=0.6,
                        drive=12.0, cab_cutoff=4800, level=0.5)


class BassGuitar(Instrument):
    id = "bass"
    name = "Bass guitar"
    description = "Picked electric bass with a little grit. Palm mute for tight notes."
    color = "#c98a2f"
    techniques = ("palm_mute",)
    info = {
        "Family": "Chordophone, electric (emulated)",
        "Synthesis": "Karplus-Strong string -> soft saturation -> low-pass",
        "Palm mute": "Short, muted note",
        "Behaviour": "Rings while the key is held; releasing the key mutes the string",
    }

    def voice(self, freq, palm_mute=False, **_):
        if palm_mute:
            return AmpVoice([freq], Envelope(0.001, 0.01, 1.0, 0.04), sustain=0.3, brightness=0.3,
                            drive=2.0, cab_cutoff=1500, level=0.8)
        return AmpVoice([freq], Envelope(0.001, 0.01, 1.0, 0.07), sustain=2.5, brightness=0.5,
                        drive=2.5, cab_cutoff=2200, level=0.8)


class RollVoice:
    """Drum roll: the first hit at once, then one hit every `interval` samples until the key is released.
    Sample-accurate (hits are placed inside the audio blocks); every hit rings out after the release."""

    ACCENTS = (1.0, 0.72, 0.84, 0.68)  # a little accent pattern so the roll doesn't sound like a machine gun

    def __init__(self, make_hit, interval):
        self.make_hit = make_hit
        self.interval = max(64, interval)
        self.next = 0          # samples until the next hit, from the start of the next block
        self.count = 0
        self.hits = []         # [buffer, read position]; a negative position = starts later in the block
        self.released = False
        self.done = False

    def release(self):
        self.released = True

    def render(self, n):
        if not self.released:
            t = self.next
            while t < n:
                gain = self.ACCENTS[self.count % len(self.ACCENTS)] * (1.0 if self.count == 0 else 0.85)
                self.hits.append([self.make_hit() * gain, -t])
                self.count += 1
                t += self.interval
            self.next = t - n
        out = np.zeros(n)
        for h in self.hits:
            buf, pos = h
            src0, src1 = max(pos, 0), min(len(buf), pos + n)
            if src1 > src0:
                dst = src0 - pos
                out[dst:dst + (src1 - src0)] += buf[src0:src1]
            h[1] = pos + n
        self.hits = [h for h in self.hits if h[1] < len(h[0])]
        self.done = self.released and not self.hits
        return out


class DrumKit(Instrument):
    id = "drums"
    name = "Drum kit"
    kind = "drums"
    description = "Metal drum kit: two kick pedals, snare, hats, crash, toms. Each key is one hit."
    color = "#6f8f2f"
    info = {
        "Family": "Percussion (synthesised)",
        "Kick": "Sine sweep 160 -> 48 Hz + beater click",
        "Snare": "Two tones + high-passed noise (wires)",
        "Cymbals": "High-passed noise + inharmonic square partials",
        "Toms": "Sine sweeps with short attack noise",
        "Tip": "Kick + Kick 2 on two fingers = double bass pedal",
    }

    _cache = {}

    def _hit(self, piece):
        # synthesising a hit takes a few ms, so keep 4 variations of each piece and reuse them
        variants = self._cache.setdefault(piece, [])
        if not variants:
            sample = load_sample(piece)
            if sample is not None:  # a real recording: always that one
                variants += [sample] * 4
        if len(variants) < 4:
            variants.append(DRUM_PIECES[piece][1]())
            return variants[-1]
        return variants[np.random.randint(4)]

    def voice(self, piece="kick", roll=None, **_):
        """One hit. With roll = N samples: a roll (redoble), one hit every N samples while the key is held."""
        if roll:
            return RollVoice(lambda: self._hit(piece), int(roll))
        return BufferVoice(self._hit(piece))

    @classmethod
    def warm(cls, pieces=None):
        """Pre-computes the variations (call from a thread) so the first hits don't stall."""
        for piece in pieces or list(DRUM_PIECES):
            variants = cls._cache.setdefault(piece, [])
            if not variants:
                sample = load_sample(piece)
                if sample is not None:
                    variants += [sample] * 4
            while len(variants) < 4:
                variants.append(DRUM_PIECES[piece][1]())

# ---- Freesia band: piano, two guitars, voice --------------------------------
class Piano(Instrument):
    id = "piano"
    name = "Piano"
    description = "Acoustic-style piano: rich attack, long natural decay, key release dampens."
    color = "#e0b44a"
    info = {
        "Family": "Keyboard, struck strings (emulated)",
        "Synthesis": "Additive: up to 24 slightly inharmonic partials, low ones doubled by a detuned string, + hammer thump",
        "Decay": "Each partial fades at its own rate: bright attack, warm tail. Low notes ring longer",
        "Behaviour": "Hold the key to let it ring (like the sustain pedal); release to damp",
    }

    def voice(self, freq, **_):
        brightness = float(np.clip(0.93 - 0.08 * np.log2(freq / 261.6), 0.75, 0.97))
        return PianoVoice(freq, Envelope(0.002, 0.01, 1.0, 0.18), brightness=brightness)


class CleanGuitar(Instrument):
    id = "clean_guitar"
    name = "Clean guitar"
    description = "Clean electric guitar with a chorus-like shimmer, for arpeggios and soft parts."
    color = "#4ab0e0"
    techniques = ("palm_mute", "power")
    info = {
        "Family": "Chordophone, electric (emulated)",
        "Synthesis": "Two Karplus-Strong strings detuned ~3 cents -> gentle saturation -> low-pass",
        "Palm mute": "Short muted pluck",
        "Power chord": "Root + fifth + octave (open, clean dyads)",
        "Behaviour": "Rings while held; release to mute",
    }

    def voice(self, freq, palm_mute=False, power=False, **_):
        base = [freq, freq * FIFTH, freq * 2] if power else [freq]
        freqs = base + [f * 1.0017 for f in base]
        if palm_mute:
            return AmpVoice(freqs, Envelope(0.001, 0.01, 1.0, 0.04), sustain=0.3, brightness=0.35,
                            drive=1.2, cab_cutoff=3000, level=1.0)
        return AmpVoice(freqs, Envelope(0.001, 0.01, 1.0, 0.15), sustain=3.5, brightness=0.75,
                        drive=1.3, cab_cutoff=6500, level=0.9)


class LeadGuitar(Instrument):
    id = "lead_guitar"
    name = "Lead guitar"
    description = "Singing, sustained solo guitar with vibrato that blooms after the attack."
    color = "#e0604a"
    info = {
        "Family": "Chordophone, electric (emulated)",
        "Synthesis": "Band-limited sawtooth -> tanh overdrive -> cabinet low-pass",
        "Vibrato": "5.5 Hz, starts ~0.35 s after the note and fades in",
        "Behaviour": "Sustains while held (like feedback); use Up/Down for higher octaves",
    }

    def voice(self, freq, **_):
        return LeadVoice(freq, Envelope(0.004, 0.05, 0.85, 0.12))


class Vocal(Instrument):
    id = "vocal"
    name = "Voice (ah)"
    description = "Synth voice singing 'ah', for the vocal melody. Vibrato blooms on long notes."
    color = "#b07ae0"
    info = {
        "Family": "Voice (emulated)",
        "Synthesis": "Additive harmonics shaped by 'ah' vowel formants (730, 1090, 2440 Hz)",
        "Vibrato": "5 Hz, fades in after 0.3 s",
        "Behaviour": "Sustains while held; soft attack and release",
    }
    formants = [(730, 110, 1.0), (1090, 130, 0.6), (2440, 180, 0.25), (3400, 220, 0.1)]

    def voice(self, freq, **_):
        return AdditiveVoice(freq, Envelope(0.03, 0.1, 0.9, 0.18), formant_partials(freq, self.formants),
                             vibrato_rate=5.0, vibrato_depth=0.012, vibrato_delay=0.3)


# ---- other instruments (first proof of concept) ---------------------------
class Organ(Instrument):
    id = "organ"
    name = "Organ"
    description = "Drawbar-style organ: steady tone that holds while the key is down."
    color = "#c9793a"
    info = {
        "Family": "Keyboard / aerophone (emulated)",
        "Synthesis": "Additive: 6 sine partials (1, 2, 3, 4, 6, 8 x f0) + slight vibrato",
        "Envelope": "Fast attack, full sustain, short release",
        "Behaviour": "Sustains as long as the key is held",
    }

    def voice(self, freq, **_):
        return AdditiveVoice(
            freq,
            Envelope(0.01, 0.05, 0.9, 0.08),
            [(1, 1.0), (2, 0.5), (3, 0.35), (4, 0.2), (6, 0.12), (8, 0.1)],
            vibrato_rate=6.0,
            vibrato_depth=0.002,
        )


class PluckedString(Instrument):
    id = "pluck"
    name = "Plucked string"
    description = "Clean guitar/harp-like pluck that rings and decays by itself."
    color = "#3a8fc9"
    info = {
        "Family": "Chordophone (emulated)",
        "Synthesis": "Karplus-Strong: filtered noise burst in a tuned delay line",
        "Envelope": "Natural decay (~3 s); releasing the key damps the string",
        "Behaviour": "Percussive; each key press re-plucks",
    }

    def voice(self, freq, **_):
        return PluckVoice(freq, Envelope(0.001, 0.01, 1.0, 0.12), sustain_seconds=3.0, brightness=0.35)


class ElectricPiano(Instrument):
    id = "epiano"
    name = "Electric piano"
    description = "Bell-like FM keys, bright at the attack and mellow as it decays."
    color = "#8a4fc9"
    info = {
        "Family": "Keyboard (emulated)",
        "Synthesis": "2-operator FM, ratio 1:1, modulation index decays over time",
        "Envelope": "Instant attack, exponential decay (~1.5 s), short release",
        "Behaviour": "Percussive, velocity-less",
    }

    def voice(self, freq, **_):
        return FMVoice(freq, Envelope(0.002, 0.01, 1.0, 0.25), ratio=1.0, index=2.5, index_decay=0.35, amp_decay=1.5)


class Clarinet(Instrument):
    id = "clarinet"
    name = "Clarinet-ish"
    description = "Hollow reed tone (odd harmonics) with gentle vibrato."
    color = "#3ac98a"
    info = {
        "Family": "Aerophone, single reed (emulated)",
        "Synthesis": "Additive: odd harmonics only (1, 3, 5, 7, 9) + 5 Hz vibrato",
        "Envelope": "Soft attack, full sustain, medium release",
        "Behaviour": "Sustains as long as the key is held",
    }

    def voice(self, freq, **_):
        return AdditiveVoice(
            freq,
            Envelope(0.05, 0.1, 0.85, 0.15),
            [(1, 1.0), (3, 0.45), (5, 0.25), (7, 0.12), (9, 0.06)],
            vibrato_rate=5.0,
            vibrato_depth=0.004,
        )


class Pad(Instrument):
    id = "pad"
    name = "Synth pad"
    description = "Slow, wide pad made of three slightly detuned sawtooth waves."
    color = "#c93a6e"
    info = {
        "Family": "Synthesizer",
        "Synthesis": "Additive band-limited saws (12 harmonics) x 3, detuned +/- 7 cents",
        "Envelope": "Slow attack (0.4 s), long release (0.8 s)",
        "Behaviour": "Swells in, lingers after release",
    }

    def voice(self, freq, **_):
        partials = []
        for detune in (1.0, 1.004, 0.996):
            partials += [(k * detune, 1.0 / k) for k in range(1, 13)]
        return AdditiveVoice(freq, Envelope(0.4, 0.3, 0.8, 0.8), partials)


# ---- orchestral sounds (for MIDI tracks: strings, brass, winds, choir) -----------------
class TremoloVoice(AdditiveVoice):
    """AdditiveVoice with fast amplitude tremolo (bowed tremolo strings)."""

    def __init__(self, *args, tremolo_rate=12.0, gain=1.0, **kw):
        super().__init__(*args, **kw)
        self.trem = tremolo_rate
        self.gain = gain

    def oscillate(self, n):
        t = (self.samples + np.arange(n)) / SR
        return super().oscillate(n) * self.gain * (0.65 + 0.35 * np.sin(TWO_PI * self.trem * t))


def _saw(count, tilt=1.0, detunes=(1.0,)):
    parts = []
    for d in detunes:
        parts += [(k * d, 1.0 / k ** tilt) for k in range(1, count + 1)]
    return parts


class Strings(Instrument):
    id = "strings"
    name = "Cuerdas (violín / cello)"
    description = "Bowed strings: soft bow attack, warm body and vibrato. Violin, viola, cello."
    color = "#b5763c"
    info = {
        "Family": "Chordophone, bowed (emulated)",
        "Synthesis": "Additive sawtooth (bow) with a darker tilt, two slightly detuned bows",
        "Vibrato": "5.5 Hz, fades in after 0.25 s",
        "MIDI": "Used for General MIDI 40-43 (violin, viola, cello, contrabass)",
    }

    def voice(self, freq, **_):
        return AdditiveVoice(freq, Envelope(0.04, 0.2, 0.9, 0.25), _saw(18, 1.25, (1.0, 1.0025)),
                             vibrato_rate=5.5, vibrato_depth=0.006, vibrato_delay=0.25)


class StringEnsemble(Instrument):
    id = "string_ensemble"
    name = "Orquesta de cuerdas"
    description = "Wide string section: slow swell, several detuned players, no solo vibrato."
    color = "#9c6a3a"
    info = {
        "Family": "String section (emulated)",
        "Synthesis": "Additive sawtooth x 4 detuned players (+/- 8 cents), soft top end",
        "MIDI": "Used for General MIDI 48-51 (string ensembles, synth strings)",
    }

    def voice(self, freq, **_):
        return AdditiveVoice(freq, Envelope(0.08, 0.3, 0.85, 0.5),
                             _saw(14, 1.35, (1.0, 1.004, 0.996, 1.0015)),
                             vibrato_rate=4.5, vibrato_depth=0.002)


class TremoloStrings(Instrument):
    id = "tremolo_strings"
    name = "Cuerdas trémolo"
    description = "Strings with fast bow tremolo: tense, shimmering texture."
    color = "#8f5a2e"
    info = {
        "Family": "String section (emulated)",
        "Synthesis": "Detuned additive saws with 12 Hz amplitude tremolo",
        "MIDI": "Used for General MIDI 44 (tremolo strings)",
    }

    def voice(self, freq, **_):
        return TremoloVoice(freq, Envelope(0.03, 0.2, 0.9, 0.3), _saw(14, 1.3, (1.0, 1.003, 0.997)),
                            tremolo_rate=12.0, gain=2.2)  # x2.2 (+7 dB): was the quietest of the band


class Brass(Instrument):
    id = "brass"
    name = "Bronces"
    description = "Trumpet / horn section: bright, punchy attack that settles."
    color = "#d4a72c"
    info = {
        "Family": "Aerophone, brass (emulated)",
        "Synthesis": "Additive sawtooth with a bright tilt, short swell and late vibrato",
        "MIDI": "Used for General MIDI 56-63 (trumpet, trombone, horn, brass section)",
    }

    def voice(self, freq, **_):
        return AdditiveVoice(freq, Envelope(0.04, 0.15, 0.8, 0.15), _saw(20, 0.9, (1.0, 1.002)),
                             vibrato_rate=5.0, vibrato_depth=0.004, vibrato_delay=0.35)


class Flute(Instrument):
    id = "flute"
    name = "Flauta"
    description = "Soft, breathy flute: almost pure tone with a little second harmonic."
    color = "#6fb3c9"
    info = {
        "Family": "Aerophone, flute (emulated)",
        "Synthesis": "Sine + weak 2nd/3rd harmonics, gentle vibrato",
        "MIDI": "Used for General MIDI 72-79 (piccolo, flute, recorder, pan flute...)",
    }

    def voice(self, freq, **_):
        return AdditiveVoice(freq, Envelope(0.06, 0.1, 0.85, 0.15), [(1, 1.0), (2, 0.18), (3, 0.06)],
                             vibrato_rate=5.0, vibrato_depth=0.004, vibrato_delay=0.2)


# General MIDI program (0-127) -> our sound
def sound_for_program(program):
    p = int(program)
    table = [
        (0, 7, "piano"), (8, 15, "epiano"), (16, 23, "organ"), (24, 28, "clean_guitar"),
        (29, 31, "guitar"), (32, 39, "bass"), (40, 43, "strings"), (44, 44, "tremolo_strings"),
        (45, 45, "pluck"), (46, 46, "pluck"), (47, 47, "drums"), (48, 51, "string_ensemble"),
        (52, 54, "vocal"), (55, 55, "brass"), (56, 63, "brass"), (64, 71, "clarinet"), (72, 79, "flute"),
        (80, 87, "lead_guitar"), (88, 95, "pad"), (96, 103, "pad"), (104, 111, "pluck"),
        (112, 119, "drums"), (120, 127, "pad"),
    ]
    for lo, hi, sid in table:
        if lo <= p <= hi:
            return sid
    return "piano"


GM_NAMES = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet", "Celesta", "Glockenspiel",
    "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ",
    "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica",
    "Tango Accordion", "Nylon Guitar", "Steel Guitar", "Jazz Guitar", "Clean Electric Guitar",
    "Muted Guitar", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass",
    "Finger Bass", "Pick Bass", "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1",
    "Synth Bass 2", "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings",
    "Orchestral Harp", "Timpani", "String Ensemble 1", "String Ensemble 2", "Synth Strings 1",
    "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit", "Trumpet",
    "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section", "Synth Brass 1",
    "Synth Brass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn",
    "Bassoon", "Clarinet", "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi",
    "Whistle", "Ocarina", "Square Lead", "Saw Lead", "Calliope Lead", "Chiff Lead", "Charang Lead",
    "Voice Lead", "Fifths Lead", "Bass + Lead", "New Age Pad", "Warm Pad", "Polysynth Pad",
    "Choir Pad", "Bowed Pad", "Metallic Pad", "Halo Pad", "Sweep Pad", "Rain", "Soundtrack",
    "Crystal", "Atmosphere", "Brightness", "Goblins", "Echoes", "Sci-fi", "Sitar", "Banjo",
    "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo",
    "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter",
    "Applause", "Gunshot",
]


# Order here = order in the UI.
INSTRUMENTS = [Piano(), CleanGuitar(), LeadGuitar(), Vocal(), DistortedGuitar(), BassGuitar(), DrumKit(),
               Strings(), StringEnsemble(), TremoloStrings(), Brass(), Flute(),
               Pad(), Organ(), PluckedString(), ElectricPiano(), Clarinet()]
