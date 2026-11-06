// T2 Métodos de síntesis: dos modelos del golpe a un vaso de vidrio.
//   A) Síntesis aditiva: un oscilador sinusoidal por modo, cada uno con su envolvente exponencial.
//   B) Resonadores modales (sustractiva/física): un golpe de ruido corto excita un banco de filtros
//      pasabanda muy angostos, uno por modo. El timbre sale de los filtros, no de osciladores.
// Ambos leen la misma tabla MODOS (modos.js), medida en la grabación. Ninguno usa la grabación como audio.
// Funcionan igual en tiempo real (AudioContext) y en render (OfflineAudioContext).

const LN1000 = Math.log(1000); // 60 dB = factor 1000 en amplitud

// ---------- Parámetros ----------
// Cada modelo declara sus parámetros: [nombre, mín, máx, unidad, valor por defecto, efecto].
const PARAMS_ADITIVA = {
  f0:        { nombre: 'Frecuencia del modo principal', min: 200, max: 8000, unidad: 'Hz', def: MODOS.fRef, log: true,
               efecto: 'Transpone todos los modos (mantiene sus proporciones).' },
  decay:     { nombre: 'Decaimiento', min: 0.1, max: 10, unidad: '× T60 medido', def: 1, log: true,
               efecto: 'Multiplica el T60 de cada modo: más largo = más campana, más corto = más madera.' },
  brillo:    { nombre: 'Brillo', min: -24, max: 12, unidad: 'dB/octava', def: 0,
               efecto: 'Inclina el espectro: sube o baja los modos agudos respecto del principal.' },
  estira:    { nombre: 'Estiramiento', min: 0.5, max: 1.5, unidad: 'exponente', def: 1,
               efecto: 'Cambia las proporciones entre modos (razón^k): 1 = vaso medido, otros = otro objeto.' },
  ataque:    { nombre: 'Ataque', min: 0, max: 200, unidad: 'ms', def: 0.5,
               efecto: 'Tiempo de subida de cada parcial: 0 = golpe seco, 50+ = sonido "frotado".' },
};

const PARAMS_MODAL = {
  f0:        { nombre: 'Frecuencia del modo principal', min: 200, max: 8000, unidad: 'Hz', def: MODOS.fRef, log: true,
               efecto: 'Transpone el banco de resonadores (centro de cada filtro).' },
  decay:     { nombre: 'Decaimiento', min: 0.1, max: 10, unidad: '× T60 medido', def: 1, log: true,
               efecto: 'Escala el Q de cada filtro (Q = π·f·T60/ln 1000): Q alto = resonancia larga y angosta.' },
  dureza:    { nombre: 'Dureza del golpe', min: 0, max: 1, unidad: '0–1', def: 0.8,
               efecto: 'Duración y filtro del golpe de ruido: duro = corto y brillante (excita modos agudos).' },
  brillo:    { nombre: 'Brillo', min: -24, max: 12, unidad: 'dB/octava', def: 0,
               efecto: 'Ganancia de cada resonador según su frecuencia (igual que en la aditiva).' },
  golpe:     { nombre: 'Golpe directo', min: 0, max: 1, unidad: '0–1', def: 0.15,
               efecto: 'Cuánto del ruido de excitación se oye sin pasar por los resonadores (el "clic").' },
};

function defaults(P) { const o = {}; for (const k in P) o[k] = P[k].def; return o; }

// Frecuencia, nivel y T60 de cada modo para unos parámetros dados (común a ambos modelos).
function modosPara(p) {
  return MODOS.modos.map(m => {
    const r = m.f / MODOS.fRef;
    const f = p.f0 * Math.pow(r, p.estira ?? 1);
    const oct = Math.log2(r);
    const amp = Math.pow(10, (m.a + p.brillo * oct) / 20);
    return { f, amp, t60: m.t60 * p.decay };
  });
}

// ---------- A) Aditiva ----------
// Cada golpe crea sus osciladores y los apaga cuando se extinguieron.
function golpeAditiva(ctx, dest, t, p, vel = 1) {
  const att = Math.max(p.ataque, 0.05) / 1000;
  for (const m of modosPara(p)) {
    if (m.f > ctx.sampleRate * 0.45) continue; // sobre Nyquist no se puede
    const o = ctx.createOscillator(); o.frequency.value = m.f;
    const g = ctx.createGain(); g.gain.value = 0;
    const tau = m.t60 / LN1000; // constante de tiempo exponencial
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(m.amp * vel * 0.25, t + att);
    g.gain.setTargetAtTime(0, t + att, tau);
    o.connect(g).connect(dest);
    o.start(t); o.stop(t + att + m.t60 * 1.2 + 0.05);
  }
}

// ---------- B) Resonadores modales ----------
// El banco vive mientras viva el modelo; cada golpe solo inyecta un pulso de ruido.
class Modal {
  constructor(ctx, dest, p) {
    this.ctx = ctx; this.out = ctx.createGain(); this.out.connect(dest);
    this.bank = ctx.createGain();           // entrada del banco
    // Fuente casi silenciosa siempre conectada: sin ella el navegador puede dar por terminados los filtros
    // cuando acaba el pulso, y cortaría las resonancias largas (Q alto) antes de tiempo.
    const vivo = ctx.createConstantSource(); vivo.offset.value = 1e-6; // continua: el pasabanda la anula
    vivo.connect(this.bank); vivo.start();
    this.directo = ctx.createGain();        // golpe sin resonar
    this.directo.connect(this.out);
    this.filtros = [];
    for (let i = 0; i < MODOS.modos.length; i++) {
      const bp = ctx.createBiquadFilter(); bp.type = 'bandpass';
      const g = ctx.createGain();
      this.bank.connect(bp).connect(g).connect(this.out);
      this.filtros.push({ bp, g });
    }
    this.set(p);
  }
  set(p, t = this.ctx.currentTime) {
    this.p = { ...p };
    const ms = modosPara({ ...p, estira: 1 });
    this.filtros.forEach((F, i) => {
      const m = ms[i];
      if (m.f > this.ctx.sampleRate * 0.45) { F.g.gain.setValueAtTime(0, t); return; }
      const Q = Math.PI * m.f * m.t60 / LN1000;
      // El pasabanda de Web Audio tiene ganancia 1 en el centro; la amplitud que deja un impulso
      // es ~2·alfa (alfa = sen(w0)/2Q). Se compensa para que cada modo salga con el nivel medido.
      const w0 = 2 * Math.PI * m.f / this.ctx.sampleRate;
      const alfa = Math.sin(w0) / (2 * Q);
      F.bp.frequency.setValueAtTime(m.f, t);
      F.bp.Q.setValueAtTime(Q, t);
      F.g.gain.setValueAtTime(m.amp / (2 * alfa), t);
    });
    this.directo.gain.setValueAtTime(p.golpe, t);
  }
  // Pulso de ruido: dureza 1 = 0,15 ms y casi sin filtrar; dureza 0 = 6 ms y pasabajos en 1 kHz.
  golpear(t, vel = 1, dureza = this.p.dureza) {
    const ctx = this.ctx, sr = ctx.sampleRate;
    const dur = 0.006 * Math.pow(0.15 / 6, dureza);
    const n = Math.max(2, Math.ceil(dur * sr * 5));
    const buf = ctx.createBuffer(1, n, sr), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.exp(-i / (dur * sr));
    const src = ctx.createBufferSource(); src.buffer = buf;
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass';
    lp.frequency.value = 1000 * Math.pow(18, dureza); lp.Q.value = 0.5;
    // Un pulso más corto tiene menos energía: se escala para que el nivel no dependa de la dureza.
    const g = ctx.createGain(); g.gain.value = vel * 1.5 / Math.sqrt(dur * sr);
    src.connect(lp).connect(g);
    g.connect(this.bank); g.connect(this.directo);
    src.start(t);
  }
}

// ---------- Render offline (comparación y Parte 3) ----------
async function renderGolpe(modelo, p, dur = 1.5, sr = 48000) {
  const ctx = new OfflineAudioContext(1, Math.ceil(dur * sr), sr);
  if (modelo === 'aditiva') golpeAditiva(ctx, ctx.destination, 0.01, p);
  else new Modal(ctx, ctx.destination, p).golpear(0.01);
  return ctx.startRendering();
}

// ---------- WAV (PCM 24 bit) ----------
function wavBlob(buffer) {
  const nc = buffer.numberOfChannels, n = buffer.length, sr = buffer.sampleRate, bps = 3;
  const ab = new ArrayBuffer(44 + n * nc * bps), v = new DataView(ab);
  const s = (o, t) => { for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); };
  s(0, 'RIFF'); v.setUint32(4, 36 + n * nc * bps, true); s(8, 'WAVE'); s(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, nc, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * nc * bps, true); v.setUint16(32, nc * bps, true);
  v.setUint16(34, 24, true); s(36, 'data'); v.setUint32(40, n * nc * bps, true);
  const ch = []; for (let c = 0; c < nc; c++) ch.push(buffer.getChannelData ? buffer.getChannelData(c) : buffer[c]);
  let o = 44;
  for (let i = 0; i < n; i++) for (let c = 0; c < nc; c++) {
    const x = Math.max(-1, Math.min(1, ch[c][i])) * 8388607 | 0;
    v.setUint8(o, x & 255); v.setUint8(o + 1, (x >> 8) & 255); v.setUint8(o + 2, (x >> 16) & 255); o += 3;
  }
  return new Blob([ab], { type: 'audio/wav' });
}

function descargar(blob, nombre) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = nombre; a.click();
}
