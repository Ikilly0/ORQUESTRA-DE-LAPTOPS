// Orden / Desorden: síntesis con Web Audio API.
// Todas las funciones reciben el contexto (AudioContext en vivo u OfflineAudioContext para el render),
// así el render suena exactamente como la estación en vivo.
"use strict";

// ---- azar reproducible (el render usa semilla fija) ----------------------------------------
function makeRng(seed) {
  let a = seed >>> 0;
  return function () {  // mulberry32
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const mtof = (m) => 440 * Math.pow(2, (m - 69) / 12);

// ---- timbres -----------------------------------------------------------------------------
// Cada timbre: cómo se construye una nota. level = volumen relativo dentro de la banda.
const SOUNDS = {
  guitar:          { label: "Guitarra distorsionada", level: 0.30 },
  bass:            { label: "Bajo eléctrico", level: 0.55 },
  piano:           { label: "Piano", level: 0.42 },
  strings:         { label: "Cuerdas (violín / cello)", level: 0.26 },
  string_ensemble: { label: "Orquesta de cuerdas", level: 0.22 },
  tremolo_strings: { label: "Cuerdas trémolo", level: 0.30 },
  vocal:           { label: "Coro (ah)", level: 0.30 },
  drums:           { label: "Batería", level: 0.85 },
};

class Synth {
  constructor(ctx, seed = 1) {
    this.ctx = ctx;
    this.rng = makeRng(seed);
    this.out = ctx.createGain();
    this.out.gain.value = 0.6;
    // compresor suave como "master": evita que la banda completa sature
    this.comp = ctx.createDynamicsCompressor();
    this.comp.threshold.value = -16;
    this.comp.knee.value = 12;
    this.comp.ratio.value = 4;
    this.comp.attack.value = 0.005;
    this.comp.release.value = 0.2;
    this.out.connect(this.comp).connect(ctx.destination);
    this.distCurve = this._curve(9);
    this.drumBufs = {};
    this._buildDrums();
  }

  _curve(drive) {
    const n = 2048, c = new Float32Array(n);
    for (let i = 0; i < n; i++) { const x = (i / (n - 1)) * 2 - 1; c[i] = Math.tanh(drive * x) / Math.tanh(drive); }
    return c;
  }

  _env(gainNode, t, attack, peak, decay, sustain) {
    const g = gainNode.gain;
    g.setValueAtTime(0.0001, t);
    g.linearRampToValueAtTime(peak, t + attack);
    g.setTargetAtTime(peak * sustain, t + attack, decay);
  }

  _osc(type, freq, t, detuneCents = 0) {
    const o = this.ctx.createOscillator();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    o.detune.setValueAtTime(detuneCents, t);
    o.start(t);
    return o;
  }

  _vibrato(oscs, t, rate, cents, delay) {
    const lfo = this.ctx.createOscillator(), depth = this.ctx.createGain();
    lfo.frequency.value = rate;
    depth.gain.setValueAtTime(0, t);
    depth.gain.linearRampToValueAtTime(cents, t + delay);
    lfo.connect(depth);
    oscs.forEach((o) => depth.connect(o.detune));
    lfo.start(t);
    return lfo;
  }

  // Toca una nota. dest = nodo de salida de la estación. Devuelve {release(t)}.
  // Si dur viene dado, la nota se suelta sola en t + dur.
  note(sound, midi, t, dest, dur = null, vel = 1) {
    const ctx = this.ctx;
    if (sound === "drums") return this.hit(midi, t, dest, vel);
    const f = mtof(midi), s = SOUNDS[sound] || SOUNDS.piano;
    const amp = ctx.createGain(), oscs = [], extra = [];
    let tail = 0.25, head = amp;
    const peak = s.level * vel;

    if (sound === "guitar") {
      // dos sierras desafinadas -> distorsión tanh -> "caja" (paso bajo) = guitarra con overdrive
      oscs.push(this._osc("sawtooth", f, t, -7), this._osc("sawtooth", f, t, 7), this._osc("square", f / 2, t));
      const pre = ctx.createGain(); pre.gain.value = 0.5;
      const shaper = ctx.createWaveShaper(); shaper.curve = this.distCurve; shaper.oversample = "2x";
      const cab = ctx.createBiquadFilter(); cab.type = "lowpass"; cab.frequency.value = 3400; cab.Q.value = 0.9;
      const cab2 = ctx.createBiquadFilter(); cab2.type = "peaking"; cab2.frequency.value = 1400; cab2.gain.value = 4;
      oscs.forEach((o) => o.connect(pre));
      pre.connect(shaper).connect(cab).connect(cab2).connect(amp);
      this._env(amp, t, 0.004, peak, 2.5, 0.35);
      tail = 0.08;
    } else if (sound === "bass") {
      oscs.push(this._osc("sawtooth", f, t), this._osc("sine", f, t));
      const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.Q.value = 2;
      lp.frequency.setValueAtTime(2200, t); lp.frequency.setTargetAtTime(500, t, 0.08);
      const sat = ctx.createWaveShaper(); sat.curve = this._curve(2);
      oscs.forEach((o) => o.connect(lp));
      lp.connect(sat).connect(amp);
      this._env(amp, t, 0.003, peak, 1.2, 0.45);
      tail = 0.08;
    } else if (sound === "piano") {
      // fundamental + armónicos que mueren antes (brillo inicial, cola cálida)
      const parts = [[1, 1.0, 1.6], [2, 0.45, 0.8], [3, 0.2, 0.5], [4, 0.1, 0.3]];
      const tauScale = Math.max(0.4, Math.min(2.5, 400 / f));
      parts.forEach(([h, a, tau]) => {
        const o = this._osc(h === 1 ? "triangle" : "sine", f * h * (1 + 0.0004 * h), t);
        const g = ctx.createGain();
        g.gain.setValueAtTime(0.0001, t);
        g.gain.linearRampToValueAtTime(a, t + 0.003);
        g.gain.setTargetAtTime(0.0001, t + 0.003, tau * tauScale);
        o.connect(g).connect(amp);
        oscs.push(o);
      });
      amp.gain.value = peak;
      tail = 0.12;
    } else if (sound === "vocal") {
      // sierra -> tres formantes de la vocal "ah" en paralelo, con vibrato
      oscs.push(this._osc("sawtooth", f, t), this._osc("sawtooth", f, t, 8));
      const mix = ctx.createGain(); mix.gain.value = 0.5;
      oscs.forEach((o) => o.connect(mix));
      [[730, 8, 1.0], [1090, 9, 0.6], [2440, 12, 0.3]].forEach(([fr, q, a]) => {
        const bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = fr; bp.Q.value = q;
        const g = ctx.createGain(); g.gain.value = a * 3;
        mix.connect(bp).connect(g).connect(amp);
      });
      extra.push(this._vibrato(oscs, t, 5.2, 18, 0.35));
      this._env(amp, t, 0.12, peak, 0.3, 0.85);
      tail = 0.35;
    } else {
      // cuerdas: sierras desafinadas -> paso bajo, arco suave, vibrato tardío
      const wide = sound === "string_ensemble" ? 14 : 6;
      oscs.push(this._osc("sawtooth", f, t, -wide), this._osc("sawtooth", f, t, wide), this._osc("sawtooth", f, t));
      const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = Math.min(6000, 1800 + f * 2);
      oscs.forEach((o) => o.connect(lp));
      if (sound === "tremolo_strings") {
        // trémolo de arco: la amplitud sube y baja 12 veces por segundo
        const trem = ctx.createGain(); trem.gain.value = 0.65;
        const lfo = ctx.createOscillator(), depth = ctx.createGain();
        lfo.frequency.value = 12; depth.gain.value = 0.35;
        lfo.connect(depth).connect(trem.gain); lfo.start(t);
        extra.push(lfo);
        lp.connect(trem).connect(amp);
      } else {
        lp.connect(amp);
        extra.push(this._vibrato(oscs, t, 5.5, 10, 0.4));
      }
      this._env(amp, t, sound === "string_ensemble" ? 0.18 : 0.09, peak, 0.4, 0.9);
      tail = 0.3;
    }
    head.connect(dest);

    let released = false;
    const release = (tr) => {
      if (released) return;
      released = true;
      tr = Math.max(tr, t + 0.01);
      amp.gain.cancelScheduledValues(tr);
      amp.gain.setTargetAtTime(0.0001, tr, tail / 3);
      const end = tr + tail * 2 + 0.05;
      oscs.concat(extra).forEach((o) => { try { o.stop(end); } catch (e) { /* ya detenido */ } });
    };
    if (dur !== null) release(t + dur);
    return { release };
  }

  // ---- batería: golpes pre-calculados (con variaciones) -----------------------------------
  _buf(seconds, fill) {
    const sr = this.ctx.sampleRate, n = Math.floor(seconds * sr);
    const b = new AudioBuffer({ length: n, sampleRate: sr, numberOfChannels: 1 });
    const d = b.getChannelData(0);
    fill(d, sr);
    let peak = 0;
    for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(d[i]));
    return { b, peak };
  }

  _norm(res, level) {
    const d = res.b.getChannelData(0), k = level / (res.peak || 1);
    for (let i = 0; i < d.length; i++) d[i] *= k;
    return res.b;
  }

  _buildDrums() {
    const R = this.rng, noise = () => R() * 2 - 1;
    const sweep = (d, sr, f0, f1, tau, decay) => {
      let ph = 0;
      for (let i = 0; i < d.length; i++) {
        const t = i / sr, f = f1 + (f0 - f1) * Math.exp(-t / tau);
        ph += 2 * Math.PI * f / sr;
        d[i] += Math.sin(ph) * Math.exp(-t / decay);
      }
    };
    const hp = (d) => { let prev = 0; for (let i = 0; i < d.length; i++) { const x = d[i]; d[i] = x - prev; prev = x; } };
    const modes = (d, sr, n, lo, hi, tauRef, level) => {  // modos inarmónicos de un platillo
      for (let k = 0; k < n; k++) {
        const f = Math.exp(Math.log(lo) + R() * (Math.log(hi) - Math.log(lo)));
        const tau = tauRef * Math.pow(1000 / f, 0.45), a = level * (0.4 + 0.6 * R()) * Math.pow(1000 / f, 0.35);
        const ph = R() * 2 * Math.PI, w = 2 * Math.PI * f / sr;
        for (let i = 0; i < d.length; i++) d[i] += a * Math.sin(w * i + ph) * Math.exp(-i / sr / tau);
      }
    };
    const variants = {
      kick: () => this._norm(this._buf(0.5, (d, sr) => {
        sweep(d, sr, 160, 48, 0.03, 0.22);
        for (let i = 0; i < d.length; i++) d[i] += noise() * 0.4 * Math.exp(-i / sr / 0.002);
      }), 1.0),
      snare: () => this._norm(this._buf(0.35, (d, sr) => {
        for (let i = 0; i < d.length; i++) {
          const t = i / sr;
          d[i] = 0.6 * (Math.sin(2 * Math.PI * 185 * t) * Math.exp(-t / 0.05) + 0.5 * Math.sin(2 * Math.PI * 330 * t) * Math.exp(-t / 0.03));
        }
        const w = new Float32Array(d.length); for (let i = 0; i < w.length; i++) w[i] = noise();
        hp(w); for (let i = 0; i < d.length; i++) d[i] += w[i] * Math.exp(-i / sr / 0.11);
      }), 0.85),
      hat: () => this._norm(this._buf(0.12, (d, sr) => {
        for (let i = 0; i < d.length; i++) d[i] = noise(); hp(d); hp(d);
        for (let i = 0; i < d.length; i++) d[i] *= Math.exp(-i / sr / 0.022);
      }), 0.35),
      hat_open: () => this._norm(this._buf(0.7, (d, sr) => {
        for (let i = 0; i < d.length; i++) d[i] = noise(); hp(d); hp(d);
        for (let i = 0; i < d.length; i++) d[i] *= Math.exp(-i / sr / 0.2);
      }), 0.35),
      crash: () => this._norm(this._buf(2.2, (d, sr) => {
        for (let i = 0; i < d.length; i++) d[i] = noise(); hp(d);
        for (let i = 0; i < d.length; i++) d[i] *= Math.exp(-i / sr / 0.7);
        modes(d, sr, 24, 400, 6000, 0.9, 0.05);
      }), 0.5),
      ride: () => this._norm(this._buf(2.2, (d, sr) => {
        // "ping" del palillo sobre un lavado de modos metálicos (sin zumbido de ondas cuadradas)
        modes(d, sr, 40, 420, 11000, 1.4, 0.06);
        for (let i = 0; i < d.length; i++) d[i] += 0.5 * noise() * Math.exp(-i / sr / 0.012);
      }), 0.3),
      ride_bell: () => this._norm(this._buf(2.0, (d, sr) => {
        const base = 760 + 40 * R();
        [[1, 1, 1.2], [2.02, 0.6, 0.9], [2.93, 0.45, 0.7], [4.16, 0.3, 0.5], [5.43, 0.2, 0.35]].forEach(([r, a, tau]) => {
          const w = 2 * Math.PI * base * r / sr;
          for (let i = 0; i < d.length; i++) d[i] += a * Math.sin(w * i) * Math.exp(-i / sr / tau);
        });
        for (let i = 0; i < d.length; i++) d[i] += 0.3 * noise() * Math.exp(-i / sr / 0.008);
      }), 0.28),
    };
    const tom = (f) => () => this._norm(this._buf(0.7, (d, sr) => {
      sweep(d, sr, f * 1.6, f, 0.04, 0.28);
      for (let i = 0; i < d.length; i++) d[i] += noise() * 0.3 * Math.exp(-i / sr / 0.006);
    }), 0.8);
    variants.kick2 = variants.kick;
    variants.tom_hi = tom(170);
    variants.tom_mid = tom(130);
    variants.tom_floor = tom(95);
    for (const [piece, make] of Object.entries(variants)) {
      const n = piece.startsWith("ride") || piece === "crash" ? 2 : 3;
      this.drumBufs[piece] = Array.from({ length: n }, make);
    }
  }

  hit(piece, t, dest, vel = 1) {
    const list = this.drumBufs[piece] || this.drumBufs.hat;
    const src = this.ctx.createBufferSource();
    src.buffer = list[Math.floor(this.rng() * list.length)];
    const g = this.ctx.createGain();
    g.gain.value = SOUNDS.drums.level * vel;
    src.connect(g).connect(dest);
    src.start(t);
    return { release() {} };
  }

  click(t, accent, dest) {
    const o = this.ctx.createOscillator(), g = this.ctx.createGain();
    o.frequency.value = accent ? 1600 : 1000;
    g.gain.setValueAtTime(0.25, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.03);
    o.connect(g).connect(dest);
    o.start(t); o.stop(t + 0.04);
  }
}

// ---- WAV (16 bits, estéreo) --------------------------------------------------------------
function audioBufferToWav(buf) {
  const ch = Math.min(2, buf.numberOfChannels), n = buf.length, sr = buf.sampleRate;
  const out = new DataView(new ArrayBuffer(44 + n * ch * 2));
  const str = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); out.setUint32(4, 36 + n * ch * 2, true); str(8, "WAVE"); str(12, "fmt ");
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, ch, true);
  out.setUint32(24, sr, true); out.setUint32(28, sr * ch * 2, true); out.setUint16(32, ch * 2, true);
  out.setUint16(34, 16, true); str(36, "data"); out.setUint32(40, n * ch * 2, true);
  const data = [];
  for (let c = 0; c < ch; c++) data.push(buf.getChannelData(c));
  let o = 44;
  for (let i = 0; i < n; i++) for (let c = 0; c < ch; c++) {
    const v = Math.max(-1, Math.min(1, data[c][i]));
    out.setInt16(o, v < 0 ? v * 32768 : v * 32767, true); o += 2;
  }
  return new Blob([out], { type: "audio/wav" });
}
