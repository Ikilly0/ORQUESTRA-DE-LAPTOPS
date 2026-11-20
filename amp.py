"""Guitar amp that runs live on an instrument's channel (not per note).

Every note of the instrument is summed first and then goes through the amp, so the tail of one note and the
attack of the next are distorted together, like a real guitar into a real amp. Two stages:

- NamAmp: a Neural Amp Modeler capture (.nam, WaveNet architecture) run in numpy, block by block,
  keeping each dilated convolution's past samples between blocks. Same maths as NAM's PyTorch model.
- Cabinet: the speaker cabinet as a minimum-phase impulse response (a .wav), convolved block by block.
  Minimum phase so it adds no delay.

AmpBus joins both and is what the audio engine calls once per block (audio.py).
"""

import json
import wave

import numpy as np


class _Conv:
    """Causal dilated 1-D convolution, streaming. w: (out, in, k), b: (out,) or None."""

    def __init__(self, w, b, dilation):
        self.out, self.inp, self.k = w.shape
        self.d = dilation
        # one matmul for all taps: W2 (out, k*in) @ stacked (k*in, T); tap j looks back (k-1-j)*d samples
        self.w2 = np.concatenate([w[:, :, j] for j in range(self.k)], axis=1).astype(np.float32)
        self.b = None if b is None else b.astype(np.float32)[:, None]
        self.hist = np.zeros((self.inp, (self.k - 1) * self.d), np.float32)

    def __call__(self, x):
        n = x.shape[1]
        buf = np.concatenate((self.hist, x), axis=1) if self.hist.shape[1] else x
        if self.hist.shape[1]:
            self.hist = buf[:, -self.hist.shape[1]:]
        span = (self.k - 1) * self.d
        taps = [buf[:, span - (self.k - 1 - j) * self.d: span - (self.k - 1 - j) * self.d + n]
                for j in range(self.k)]
        y = self.w2 @ (np.concatenate(taps, axis=0) if self.k > 1 else taps[0])
        return y if self.b is None else y + self.b


class NamAmp:
    """A .nam WaveNet capture (non-gated layers, no extra head), processed in blocks."""

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("architecture") != "WaveNet":
            raise ValueError(f"only WaveNet .nam models are supported, not {data.get('architecture')}")
        cfg = data["config"]
        if cfg.get("head") is not None:
            raise ValueError("WaveNet with a head is not supported")
        w = np.asarray(data["weights"], np.float32)
        i = 0

        def take(shape):
            nonlocal i
            n = int(np.prod(shape))
            out = w[i:i + n].reshape(shape)
            i += n
            return out

        self.arrays = []
        for a in cfg["layers"]:
            if a.get("gated"):
                raise ValueError("gated WaveNet layers are not supported")
            if a.get("activation", "Tanh") != "Tanh":
                raise ValueError("only Tanh activation is supported")
            C, K = a["channels"], a["kernel_size"]
            arr = {"rechannel": _Conv(take((C, a["input_size"], 1)), None, 1), "layers": []}
            for d in a["dilations"]:
                conv = _Conv(take((C, C, K)), take((C,)), d)
                mixer = take((C, a["condition_size"], 1))[:, :, 0]
                one = _Conv(take((C, C, 1)), take((C,)), 1)
                arr["layers"].append((conv, mixer.astype(np.float32), one))
            hw = take((a["head_size"], C, 1))
            hb = take((a["head_size"],)) if a.get("head_bias") else None
            arr["head"] = _Conv(hw, hb, 1)
            self.arrays.append(arr)
        self.head_scale = float(w[i])
        i += 1
        if i != len(w):
            raise ValueError(f"weights left over: read {i} of {len(w)}")
        # NAM starts by feeding (receptive field - 1) samples of silence; the layer biases make the inner
        # signals non-zero even then, so prime the history the same way to match it exactly.
        rf = 1 + sum((a["kernel_size"] - 1) * sum(a["dilations"]) for a in cfg["layers"])
        self.process(np.zeros(rf - 1, np.float32))

    def process(self, x):
        """x: 1-D float block -> 1-D block of the same length."""
        c = np.asarray(x, np.float32)[None, :]
        y, head = c, None
        for arr in self.arrays:
            y = arr["rechannel"](y)
            for conv, mixer, one in arr["layers"]:
                z = np.tanh(conv(y) + mixer @ c)
                y = y + one(z)
                head = z if head is None else head + z
            head = arr["head"](head)
        return (self.head_scale * head)[0]


def read_wav_mono(path):
    with wave.open(path, "rb") as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
    else:
        raise ValueError(f"{path}: {8 * width}-bit wav not supported")
    return x.reshape(-1, ch).mean(axis=1), sr


class Cabinet:
    """Streaming convolution with an impulse response (overlap-add through FFT)."""

    def __init__(self, ir, block):
        self.ir = np.asarray(ir, np.float32)
        self.block = block
        self.nfft = 1 << int(np.ceil(np.log2(block + len(self.ir) - 1)))
        self.H = np.fft.rfft(self.ir, self.nfft)
        self.tail = np.zeros(self.nfft - block, np.float32)

    def process(self, x):
        n = len(x)
        if n != self.block:  # odd block size (rare): plain convolution
            y = np.convolve(x, self.ir)
        else:
            y = np.fft.irfft(np.fft.rfft(x, self.nfft) * self.H, self.nfft)
        y[:len(self.tail)] += self.tail[:len(y)] if len(y) <= len(self.tail) else self.tail
        out = y[:n]
        rest = y[n:]
        self.tail = np.zeros(self.nfft - self.block, np.float32)
        self.tail[:min(len(rest), len(self.tail))] = rest[:len(self.tail)]
        return out


class AmpBus:
    """Input gain -> amp -> cabinet -> output gain. Keeps running a little after the last note so tails
    ring out, then sleeps (no CPU) until the next note."""

    def __init__(self, amp, cabinet, gain_in, gain_out, sr):
        self.amp, self.cab = amp, cabinet
        self.gain_in, self.gain_out = gain_in, gain_out
        self.fader = 1.0  # mixer fader, applied after the amp (set by the mixer's GainVoice)
        self.idle = 0
        self.max_idle = int(1.5 * sr)

    def process(self, x):
        if not np.any(x):
            if self.idle >= self.max_idle:
                return None  # asleep: silent input and the tail already died out
            self.idle += len(x)
        else:
            self.idle = 0
        return self.cab.process(self.amp.process(x * self.gain_in)) * (self.gain_out * self.fader)
