"""Análisis de la fuente (Parte 1) y comparación con los modelos (Parte 2).

    pip install numpy scipy matplotlib
    python analisis.py              # analiza ../grabacion/vaso-golpe.wav, escribe figuras y ../modelos/modos.js
    python analisis.py --comparar   # además iguala sonoridad y compara con ../audio/golpe-*.wav

Ajustes de análisis (iguales para la fuente y los modelos):
  * Espectrograma: ventana Hann de 2048 muestras (42,7 ms a 48 kHz), salto de 256 (5,3 ms).
    Con 2048 la resolución es 23,4 Hz: separa los modos 2596 y 2725 Hz (129 Hz de distancia)
    y sigue mostrando la caída de modos que duran 0,2–0,5 s. Con 512 se juntan; con 8192 el
    ataque se borronea por 170 ms.
  * Espectros instantáneos: ventana Hann de 4096 (85 ms), en el ataque y en la extinción.
  * Envolvente: RMS en ventanas de 5 ms.
"""
import json
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as sig

sys.stdout.reconfigure(encoding="utf-8")
T2 = pathlib.Path(__file__).resolve().parent.parent
FIG = T2 / "img"
FUENTE = T2 / "grabacion" / "vaso-golpe.wav"
NFFT, HOP = 2048, 256
plt.rcParams.update({"figure.dpi": 110, "font.size": 9})


def leer(path):
    sr, x = wavfile.read(path)
    if x.dtype.kind == "i":
        x = x / float(np.iinfo(x.dtype).max)
    x = x.astype(float)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return sr, x


def inicio(x, sr):
    """Índice del ataque: primera muestra que supera el 30 % del pico, menos 2 ms."""
    return max(0, int(np.argmax(np.abs(x) > 0.3 * np.abs(x).max())) - int(0.002 * sr))


def envolvente_db(x, sr, ms=5):
    h = int(sr * ms / 1000)
    e = np.sqrt([np.mean(x[i:i + h] ** 2) for i in range(0, len(x) - h, h)])
    return np.arange(len(e)) * h / sr, 20 * np.log10(e + 1e-9)


def espectro(x, sr, t, n=4096):
    i = int(t * sr)
    seg = x[i:i + n]
    seg = np.pad(seg, (0, n - len(seg))) * np.hanning(n)
    F = np.abs(np.fft.rfft(seg, 4 * n)) / (n / 4)
    return np.fft.rfftfreq(4 * n, 1 / sr), 20 * np.log10(F + 1e-9)


def caida_modo(x, sr, f):
    """Nivel inicial (dB) y T60 (s) de un modo: pasabanda angosto + envolvente + recta en dB."""
    sos = sig.butter(2, [f * 0.985, f * 1.015], "bandpass", fs=sr, output="sos")
    y = sig.sosfiltfilt(sos, x)
    t, e = envolvente_db(y, sr, 5)
    a = int(np.argmax(e))
    piso = np.percentile(e[-20:], 50) + 6
    sel = (t > t[a] + 0.01) & (e > max(e[a] - 40, piso))
    if sel.sum() < 4:
        return e[a], np.nan, t, e
    m, _ = np.polyfit(t[sel], e[sel], 1)
    return e[a], -60 / m, t, e   # nivel = pico de la envolvente del modo


def modos(x, sr, fmin=1000, n=8, rango_db=45):
    f, L = espectro(x, sr, 0.0, 8192)
    p, _ = sig.find_peaks(L, prominence=10, distance=int(60 / (f[1] - f[0])))
    p = [k for k in p if f[k] > fmin]
    p = sorted(p, key=lambda k: -L[k])[:n]
    p = [k for k in p if L[k] > L[p[0]] - rango_db]
    res = []
    for k in sorted(p):
        b, t60, *_ = caida_modo(x, sr, f[k])
        res.append({"f": round(float(f[k]), 1), "nivel": float(b), "t60": float(t60)})
    ref = max(res, key=lambda m: m["nivel"])
    for m in res:
        m["a"] = round(m["nivel"] - ref["nivel"], 1)
    return res, ref["f"]


def espectrograma(ax, x, sr, titulo, vmin=-110):
    f, t, Z = sig.spectrogram(x, sr, window="hann", nperseg=NFFT, noverlap=NFFT - HOP,
                              scaling="spectrum", mode="magnitude")
    Z = 20 * np.log10(Z + 1e-12)
    im = ax.pcolormesh(t, f / 1000, Z, vmin=vmin, vmax=vmin + 90, shading="auto", cmap="magma")
    ax.set_ylim(0, 16)
    ax.set_xlabel("tiempo (s)")
    ax.set_ylabel("kHz")
    ax.set_title(titulo)
    return im


def parte1():
    FIG.mkdir(exist_ok=True)
    sr, x = leer(FUENTE)
    x = x[inicio(x, sr):]
    t = np.arange(len(x)) / sr
    te, e = envolvente_db(x, sr)
    ms, fref = modos(x, sr)

    # 1. Forma de onda y envolvente
    fig, ax = plt.subplots(2, 1, figsize=(9, 4.5), sharex=True)
    ax[0].plot(t, x, lw=0.4)
    ax[0].set_ylabel("amplitud")
    ax[0].set_title("Forma de onda")
    ax[1].plot(te, e)
    ax[1].set_ylabel("dBFS (RMS 5 ms)")
    ax[1].set_xlabel("tiempo (s)")
    ax[1].set_title("Envolvente de amplitud")
    ax[1].grid(alpha=0.3)
    ins = ax[0].inset_axes([0.55, 0.35, 0.42, 0.6])
    z = t < 0.012
    ins.plot(t[z] * 1000, x[z], lw=0.6)
    ins.set_title("primeros 12 ms", fontsize=8)
    ins.set_xlabel("ms", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "fuente-onda-envolvente.png")

    # 2. Espectrograma
    fig, ax = plt.subplots(figsize=(9, 4))
    im = espectrograma(ax, x, sr, f"Espectrograma (Hann {NFFT}, salto {HOP})")
    fig.colorbar(im, label="dB")
    fig.tight_layout()
    fig.savefig(FIG / "fuente-espectrograma.png")

    # 3. Espectros en dos instantes
    instantes = [(0.0, "ataque (0–85 ms)"), (0.25, "extinción (250–335 ms)")]
    fig, ax = plt.subplots(figsize=(9, 4))
    for ti, et in instantes:
        f, L = espectro(x, sr, ti)
        ax.plot(f / 1000, L, lw=0.7, label=et)
    for m in ms:
        ax.axvline(m["f"] / 1000, color="gray", lw=0.4, ls=":")
        ax.text(m["f"] / 1000, 5, f'{m["f"]:.0f}', rotation=90, fontsize=7, va="top")
    ax.set_xlim(0, 16)
    ax.set_ylim(-110, 10)
    ax.set_xlabel("kHz")
    ax.set_ylabel("dB")
    ax.legend()
    ax.set_title("Espectros (Hann 4096)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "fuente-espectros.png")

    # 4. Caída de cada modo
    fig, ax = plt.subplots(figsize=(9, 4))
    for m in ms:
        b, t60, tm, em = caida_modo(x, sr, m["f"])
        ax.plot(tm, em, lw=0.8, label=f'{m["f"]:.0f} Hz, T60 {t60:.2f} s')
    ax.set_xlabel("tiempo (s)")
    ax.set_ylabel("dB")
    ax.set_ylim(-120, None)
    ax.legend(fontsize=7)
    ax.set_title("Caída de cada modo (pasabanda ±1,5 %)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "fuente-caida-modos.png")

    # Ruido: zumbido de red y ataque
    f, L = espectro(x, sr, 0.0, 8192)
    zumbido = L[np.argmin(np.abs(f - 50))]
    print(f"Fuente: {FUENTE.name}, {len(x) / sr:.2f} s desde el ataque, pico {np.abs(x).max():.3f}")
    print(f"Zumbido 50 Hz: {zumbido:.1f} dB")
    print(f"{'f (Hz)':>8} {'razón':>6} {'nivel':>6} {'T60':>5}")
    for m in ms:
        print(f'{m["f"]:8.1f} {m["f"] / fref:6.3f} {m["a"]:6.1f} {m["t60"]:5.2f}')

    # Tabla de modos para los modelos
    datos = {"fuente": "grabacion/" + FUENTE.name, "fRef": fref,
             "modos": [{"f": m["f"], "a": m["a"], "t60": round(m["t60"], 3)} for m in ms if np.isfinite(m["t60"])]}
    (T2 / "modelos" / "modos.js").write_text(
        "// Modos del vaso medidos en la grabación (los escribe analisis/analisis.py; no editar a mano).\n"
        "// f: frecuencia (Hz), a: nivel relativo al modo principal (dB), t60: tiempo en caer 60 dB (s).\n"
        "const MODOS = " + json.dumps(datos, indent=2, ensure_ascii=False) + ";\n", encoding="utf-8")
    (T2 / "analisis" / "modos.json").write_text(json.dumps(ms, indent=2), encoding="utf-8")


# ---------- Sonoridad (ITU-R BS.1770, sin gating: los clips son de un solo golpe) ----------
def k_weight(x, sr):
    # Filtros de la norma recalculados para sr (Pyloudnorm/BS.1770-4)
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = np.tan(np.pi * f0 / sr)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = np.tan(np.pi * f0 / sr)
    b2 = [1, -2, 1]
    a2 = [1, 2 * (K * K - 1) / (1 + K / Q + K * K), (1 - K / Q + K * K) / (1 + K / Q + K * K)]
    return sig.lfilter(b2, a2, sig.lfilter(b1, a1, x))


def lufs(x, sr):
    y = k_weight(x, sr)
    return -0.691 + 10 * np.log10(np.mean(y ** 2) + 1e-12)


def comparar(objetivo=-20.0, dur=0.85):
    sr, x = leer(FUENTE)
    clips = {"original": x[inicio(x, sr):]}
    for m in ("aditiva", "modal"):
        s2, y = leer(T2 / "audio" / f"golpe-{m}.wav")
        assert s2 == sr
        clips[m] = y[inicio(y, sr):]
    out = T2 / "audio" / "comparacion"
    out.mkdir(exist_ok=True)
    n = int(dur * sr)
    for k, y in clips.items():
        clips[k] = np.pad(y[:n], (int(0.05 * sr), max(0, n - len(y))))
    # Si algún clip quedara sobre −1 dBFS, se baja el objetivo para todos (misma sonoridad, sin saturar).
    picos = [20 * np.log10(np.abs(y).max()) + objetivo - lufs(y, sr) for y in clips.values()]
    objetivo -= max(0.0, max(picos) + 1.0)
    print(f"\nSonoridad igualada a {objetivo:.1f} LUFS (BS.1770 sin gating, {dur} s desde el ataque):")
    for k, y in clips.items():
        g = 10 ** ((objetivo - lufs(y, sr)) / 20)
        y = y * g
        clips[k] = y
        print(f"  {k:9s} ganancia {20 * np.log10(g):+6.1f} dB, pico {20 * np.log10(np.abs(y).max()):6.1f} dBFS")
        wavfile.write(out / f"{k}.wav", sr, (np.clip(y, -1, 1) * 8388607).astype(np.int32) << 8)
    # Los tres seguidos, con 0,4 s de silencio
    sil = np.zeros(int(0.4 * sr))
    seguidos = np.concatenate([np.concatenate([clips[k], sil]) for k in ("original", "aditiva", "modal")])
    wavfile.write(out / "original-aditiva-modal.wav", sr, (np.clip(seguidos, -1, 1) * 8388607).astype(np.int32) << 8)

    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8), sharey=True)
    for a, k in zip(ax, clips):
        im = espectrograma(a, clips[k], sr, k)
    fig.colorbar(im, ax=ax, label="dB")
    fig.savefig(FIG / "comparacion-espectrogramas.png", bbox_inches="tight")

    fig, ax = plt.subplots(1, 2, figsize=(13, 3.8))
    for k, y in clips.items():
        f, L = espectro(y, sr, 0.05)
        ax[0].plot(f / 1000, L, lw=0.7, label=k)
        te, e = envolvente_db(y, sr)
        ax[1].plot(te, e, lw=0.9, label=k)
    ax[0].set_xlim(0, 16); ax[0].set_ylim(-120, 0); ax[0].set_xlabel("kHz"); ax[0].set_title("Espectro del ataque (Hann 4096)")
    ax[1].set_xlabel("s"); ax[1].set_ylim(-100, None); ax[1].set_title("Envolvente (RMS 5 ms)")
    for a in ax:
        a.legend(); a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "comparacion-espectros-envolventes.png")


if __name__ == "__main__":
    parte1()
    if "--comparar" in sys.argv:
        comparar()
