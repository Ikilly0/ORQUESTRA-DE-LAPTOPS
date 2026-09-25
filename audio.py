"""Audio engine: mixes the active voices (plus an optional metronome) into one output stream."""

import os
import sys
import threading
import time
import wave

import numpy as np

from instruments import SR, BufferVoice

try:
    import sounddevice as sd
    _SD_ERROR = None
except Exception as exc:  # PortAudio missing, etc.
    sd = None
    _SD_ERROR = exc

BLOCK = 512
MAX_VOICES = 64


class DelayedVoice:
    """Starts a voice `delay` samples into the current block (for sample-accurate scheduled notes)."""

    def __init__(self, voice, delay):
        self.voice = voice
        self.delay = delay
        self.done = False

    def release(self):
        self.voice.release()

    def render(self, n):
        if self.delay >= n:
            self.delay -= n
            return np.zeros(n)
        out = np.zeros(n)
        d = self.delay
        out[d:] = self.voice.render(n - d)
        self.delay = 0
        self.done = self.voice.done
        return out


class SequenceVoice:
    """Several notes one after another as a single voice: [(start_sample, length_samples, voice)].
    Used for the bajadas (Shift + Espacio): the whole run plays from one key press."""

    def __init__(self, notes):
        self.notes = sorted(notes, key=lambda n: n[0])
        self.t = 0
        self.active = []  # [(end_sample, voice)]
        self.next = 0
        self.done = False

    def release(self):
        pass  # the run always finishes (like a real one: once started, it falls to the end)

    def render(self, n):
        out = np.zeros(n)
        end = self.t + n
        while self.next < len(self.notes) and self.notes[self.next][0] < end:
            start, length, voice = self.notes[self.next]
            self.next += 1
            self.active.append([start + length, DelayedVoice(voice, max(0, start - self.t))])
        for item in self.active:
            stop, v = item
            if stop is not None and stop < end:  # its release falls in this block
                k = max(0, stop - self.t)
                if k:
                    out[:k] += v.render(k)
                v.release()
                item[0] = None  # released: it only rings out now
                if n - k:
                    out[k:] += v.render(n - k)
            else:
                out += v.render(n)
        self.active = [item for item in self.active if not item[1].done]
        self.t = end
        self.done = self.next >= len(self.notes) and not self.active
        return out


def _click(freq):
    t = np.arange(int(0.03 * SR)) / SR
    return np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.006)


class Engine:
    def __init__(self):
        self.voices = []  # list of (tag, voice)
        self.lock = threading.Lock()
        self.stream = None
        self.error = None
        self.device_name = ""
        self.volume = 0.7
        # metronome
        self.metro_on = False
        self.metro_bpm = 105
        self.metro_volume = 0.5
        self.metro_pos = 0  # samples until the next beat
        self.metro_beat = 0
        self.metro_beats_per_bar = 3
        self.metro_audible = True
        self.metro_samples = 0      # samples since the transport started
        self.metro_start_beat = 0.0  # beat number at sample 0 (negative = count-in)
        # song clock: clicks at precomputed sample positions (follows tempo changes)
        self.clock_on = False
        self.clock_samples = 0
        self.clock_clicks = []  # sorted [(sample, accent)]
        self.pending = []       # scheduled notes on the clock: [(sample, kind, tag, voice)]
        self.click_hi = _click(1600)
        self.click_lo = _click(1000)
        # recording (list of mono blocks while recording, else None)
        self.rec_blocks = None

    # ---- stream ---------------------------------------------------------
    def _candidates(self):
        """Devices to try, best first. On Windows prefer WASAPI (much lower latency than MME)."""
        out = []
        try:
            default = sd.query_devices(kind="output")
            if sys.platform.startswith("win"):
                prefix = default["name"][:20]
                for i, d in enumerate(sd.query_devices()):
                    api = sd.query_hostapis(d["hostapi"])["name"].lower()
                    if "wasapi" in api and d["max_output_channels"] >= 2 and d["name"].startswith(prefix):
                        out.append((i, 0.012))
                        break
            api = sd.query_hostapis(default["hostapi"])["name"].lower()
            out.append((None, 0.03 if ("mme" in api or "directsound" in api) else "low"))
        except Exception:
            out.append((None, "low"))
        return out

    def start(self):
        if sd is None:
            self.error = f"sounddevice could not load: {_SD_ERROR}"
            return False
        last_error = None
        candidates = self._candidates()
        if os.environ.get("KI_SAFE_AUDIO"):  # skip WASAPI / custom latency
            candidates = [(None, "high")]
        for device, latency in candidates:
            print(f"  trying output device {device} latency {latency}", flush=True)
            try:
                self.stream = sd.OutputStream(
                    device=device, samplerate=SR, blocksize=BLOCK, channels=2, dtype="float32",
                    latency=latency, callback=self._callback,
                )
                self.stream.start()
                dev = sd.query_devices(self.stream.device)
                api = sd.query_hostapis(dev["hostapi"])["name"]
                self.device_name = f"{dev['name']} ({api}, {self.stream.latency * 1000:.0f} ms)"
                self.error = None
                return True
            except Exception as exc:
                last_error = exc
                self.stream = None
        self.error = str(last_error)
        return False

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    # ---- notes ------------------------------------------------------------
    def note_on(self, tag, instrument, **params):
        self.voice_on(tag, instrument.voice(**params))

    def voice_on(self, tag, voice):
        """Starts an already built voice (e.g. a SequenceVoice) under a tag."""
        with self.lock:
            for t, v in self.voices:
                if t == tag:
                    v.release()
            self.voices = [(None if t == tag else t, v) for t, v in self.voices]
            self.voices.append((tag, voice))
            if len(self.voices) > MAX_VOICES:
                self.voices = self.voices[-MAX_VOICES:]

    def note_off(self, tag):
        with self.lock:
            for t, v in self.voices:
                if t == tag:
                    v.release()
            self.voices = [(None if t == tag else t, v) for t, v in self.voices]

    def all_off(self):
        with self.lock:
            for _, v in self.voices:
                v.release()

    # ---- metronome --------------------------------------------------------
    def set_metronome(self, on, bpm=None, beats_per_bar=None):
        with self.lock:
            if beats_per_bar:
                self.metro_beats_per_bar = max(1, min(12, int(beats_per_bar)))
            if bpm:
                self.metro_bpm = max(30, min(300, int(bpm)))
            if on and not self.metro_on:
                self.metro_pos = 0
                self.metro_beat = 0
                self.metro_samples = 0
                self.metro_start_beat = 0.0
            self.metro_on = on

    def start_transport(self, bpm, beats_per_bar, count_in_beats, audible=True):
        """Starts the shared clock: beat -count_in_beats is right now. Clicks fall on whole beats."""
        with self.lock:
            self.metro_bpm = max(30, min(300, int(bpm)))
            self.metro_beats_per_bar = max(1, min(12, int(beats_per_bar)))
            self.metro_audible = audible
            self.metro_pos = 0
            self.metro_beat = 0
            self.metro_samples = 0
            self.metro_start_beat = -float(count_in_beats)
            self.metro_on = True

    def position(self):
        """Current beat of the transport (float), or None when stopped."""
        with self.lock:
            if not self.metro_on:
                return None
            return self.metro_start_beat + self.metro_samples / (SR * 60.0 / self.metro_bpm)

    # ---- song clock (tempo map aware) -------------------------------------------
    def start_clock(self, clicks, audible=True):
        """Starts a clock at sample 0 with clicks at the given [(sample, accent)] positions."""
        with self.lock:
            self.metro_on = False
            self.clock_samples = 0
            self.clock_clicks = sorted(clicks)
            self.metro_audible = audible
            self.clock_on = True

    def set_clicks(self, clicks):
        """Replaces the upcoming clicks (positions on the same clock)."""
        with self.lock:
            self.clock_clicks = sorted(c for c in clicks if c[0] >= self.clock_samples)

    def stop_clock(self):
        with self.lock:
            self.clock_on = False
            self.clock_clicks = []
            self.pending = []

    def schedule(self, events):
        """Adds [(sample, "on"/"off", tag, voice_or_None)] on the song clock (sample accurate note starts)."""
        with self.lock:
            self.pending += events
            self.pending.sort(key=lambda e: e[0])

    def clear_scheduled(self):
        with self.lock:
            self.pending = []

    def _process_pending(self, frames):
        if not self.clock_on or not self.pending:
            return
        end = self.clock_samples + frames
        while self.pending and self.pending[0][0] < end:
            pos, kind, tag, voice = self.pending.pop(0)
            if kind == "on":
                for t, v in self.voices:
                    if t == tag:
                        v.release()
                self.voices = [(None if t == tag else t, v) for t, v in self.voices]
                self.voices.append((tag, DelayedVoice(voice, max(0, pos - self.clock_samples))))
            else:
                for t, v in self.voices:
                    if t == tag:
                        v.release()
                self.voices = [(None if t == tag else t, v) for t, v in self.voices]
        if len(self.voices) > MAX_VOICES:
            self.voices = self.voices[-MAX_VOICES:]

    def clock_seconds(self):
        with self.lock:
            return self.clock_samples / SR if self.clock_on else None

    def _clock_clicks(self, frames):
        if not self.clock_on:
            return []
        new = []
        end = self.clock_samples + frames
        while self.clock_clicks and self.clock_clicks[0][0] < end:
            pos, accent = self.clock_clicks.pop(0)
            off = max(0, pos - self.clock_samples)
            if self.metro_audible:
                click = self.click_hi if accent else self.click_lo
                new.append((None, BufferVoice(np.concatenate((np.zeros(off), click * self.metro_volume)))))
        self.clock_samples = end
        return new

    def _schedule_clicks(self, frames):
        """Adds click voices for every beat that falls in this block (sample accurate)."""
        if not self.metro_on:
            return []
        interval = int(SR * 60 / self.metro_bpm)
        new = []
        pos = self.metro_pos
        while pos < frames:
            click = self.click_hi if self.metro_beat % self.metro_beats_per_bar == 0 else self.click_lo
            if self.metro_audible:
                new.append((None, BufferVoice(np.concatenate((np.zeros(pos), click * self.metro_volume)))))
            self.metro_beat += 1
            pos += interval
        self.metro_pos = pos - frames
        self.metro_samples += frames
        return new

    # ---- recording --------------------------------------------------------
    def start_recording(self):
        self.rec_blocks = []

    def stop_recording(self, folder):
        """Stops recording and writes a 16-bit stereo WAV. Returns the file path (or None)."""
        blocks, self.rec_blocks = self.rec_blocks, None
        if not blocks:
            return None
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, time.strftime("take-%Y%m%d-%H%M%S.wav"))
        mono = np.clip(np.concatenate(blocks), -1, 1)
        pcm = (np.repeat(mono[:, None], 2, axis=1) * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        return path

    # ---- mixing -----------------------------------------------------------
    def mix(self, frames):
        with self.lock:
            self._process_pending(frames)
            self.voices += self._schedule_clicks(frames) + self._clock_clicks(frames)
            active = list(self.voices)
        out = np.zeros(frames)
        for _, v in active:
            out += v.render(frames)
        with self.lock:
            self.voices = [(t, v) for t, v in self.voices if not v.done]
        return np.tanh(out * self.volume)  # soft clip keeps chords safe

    def _callback(self, outdata, frames, _time, _status):
        try:
            mono = self.mix(frames).astype(np.float32)
        except Exception:
            mono = np.zeros(frames, dtype=np.float32)
        if self.rec_blocks is not None:
            self.rec_blocks.append(mono.copy())
        outdata[:, 0] = mono
        outdata[:, 1] = mono
