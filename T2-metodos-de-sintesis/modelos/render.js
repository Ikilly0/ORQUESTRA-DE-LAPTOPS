// Parte 3: render "más allá del original". Recorre cada modelo cambiando un parámetro a la vez.
// Cada sección queda anotada con su marca de tiempo en el registro (render/render-log.txt).
// Para cambiar el recorrido basta editar SECCIONES: t = inicio (s), modelo, cambios respecto del
// valor por defecto, y los golpes: [tiempo relativo (s), velocidad, {cambios para ese golpe}].

function serie(n, paso, f) { return Array.from({ length: n }, (_, i) => [i * paso, 1, f(i, n)]); }
const lin = (a, b, i, n) => a + (b - a) * i / Math.max(1, n - 1);
const exp = (a, b, i, n) => a * Math.pow(b / a, i / Math.max(1, n - 1));

const SECCIONES = [
  { t: 0,  modelo: 'aditiva', titulo: 'Aditiva: el vaso tal como se midió', golpes: serie(3, 1.2, () => ({})) },
  { t: 4,  modelo: 'aditiva', titulo: 'Aditiva: decaimiento ×1 → ×8', golpes: serie(4, 1.6, (i, n) => ({ decay: exp(1, 8, i, n) })) },
  { t: 12, modelo: 'aditiva', titulo: 'Aditiva: estiramiento 0,6 → 1,4 (decaimiento ×3)', golpes: serie(6, 0.9, (i, n) => ({ estira: lin(0.6, 1.4, i, n), decay: 3 })) },
  { t: 18, modelo: 'aditiva', titulo: 'Aditiva: brillo −18 → +9 dB/oct', golpes: serie(5, 0.8, (i, n) => ({ brillo: lin(-18, 9, i, n), decay: 2 })) },
  { t: 23, modelo: 'aditiva', titulo: 'Aditiva: ataque 0 → 150 ms', golpes: serie(4, 1.2, (i, n) => ({ ataque: lin(0, 150, i, n), decay: 3 })) },
  { t: 28.5, modelo: 'aditiva', titulo: 'Aditiva: vaso transpuesto a 330–660 Hz (melodía)',
    golpes: [0, 0.4, 0.8, 1.2, 1.8, 2.2, 2.6, 3.2].map((d, i) => [d, 1, { f0: 330 * Math.pow(2, [0, 3, 7, 12, 10, 7, 3, 0][i] / 12), decay: 4 }]) },
  { t: 34, modelo: 'modal', titulo: 'Modal: el vaso tal como se midió', golpes: serie(3, 1.2, () => ({})) },
  { t: 38, modelo: 'modal', titulo: 'Modal: dureza 0 → 1', golpes: serie(6, 0.9, (i, n) => ({ dureza: lin(0, 1, i, n) })) },
  { t: 44, modelo: 'modal', titulo: 'Modal: decaimiento ×0,2 → ×10 (Q de los filtros)', golpes: serie(4, 1.8, (i, n) => ({ decay: exp(0.2, 10, i, n) })) },
  { t: 52, modelo: 'modal', titulo: 'Modal: golpe directo 0 → 1', golpes: serie(5, 0.8, (i, n) => ({ golpe: lin(0, 1, i, n) })) },
  { t: 57, modelo: 'modal', titulo: 'Modal: redoble acelerando sobre resonadores largos (decaimiento ×6)',
    golpes: Array.from({ length: 40 }, (_, i) => [3.2 * Math.pow(i / 39, 0.6), 0.3 + 0.7 * i / 39, { decay: 6, dureza: 0.5 }]) },
  { t: 63, modelo: 'modal', titulo: 'Modal: vaso transpuesto a 220 Hz, golpe blando (decaimiento ×8)',
    golpes: serie(3, 1.5, (i) => ({ f0: 220 * [1, 1.5, 2][i], decay: 8, dureza: 0.2 })) },
];
const DURACION_RENDER = 72;

function fmt(s) { const m = Math.floor(s / 60); return `${m}:${(s - 60 * m).toFixed(1).padStart(4, '0')}`; }

async function renderParte3(sr = 48000) {
  const ctx = new OfflineAudioContext(1, DURACION_RENDER * sr, sr);
  const master = ctx.createGain(); master.gain.value = 0.5; master.connect(ctx.destination);
  const log = [];
  for (const S of SECCIONES) {
    log.push(`${fmt(S.t)}  ${S.titulo}`);
    // El modelo modal usa un banco propio por sección (sus filtros siguen sonando entre golpes).
    const modal = S.modelo === 'modal' ? new Modal(ctx, master, defaults(PARAMS_MODAL)) : null;
    for (const [dt, vel, cambios] of S.golpes) {
      const t = S.t + dt;
      if (S.modelo === 'aditiva') golpeAditiva(ctx, master, t, { ...defaults(PARAMS_ADITIVA), ...cambios }, vel);
      else { const p = { ...defaults(PARAMS_MODAL), ...cambios }; modal.set(p, t); modal.golpear(t, vel); }
    }
  }
  const buf = await ctx.startRendering();
  // Normaliza el pico a −1 dBFS
  const d = buf.getChannelData(0); let pk = 0; for (const x of d) pk = Math.max(pk, Math.abs(x));
  const k = 0.891 / (pk || 1); for (let i = 0; i < d.length; i++) d[i] *= k;
  return { buf, blob: wavBlob(buf), log };
}
