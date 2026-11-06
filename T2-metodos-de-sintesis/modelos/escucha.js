// Parte 4: el modelo modal tocado por el vaso en vivo.
// Cadena: micrófono → seguidor de envolvente + detector de ataques (AudioWorklet)
//                    → centroide espectral (AnalyserNode, FFT 2048)
//         → modo "Disparo": cada ataque golpea el banco con velocidad = envolvente y dureza = centroide
//         → modo "Continuo": ruido × envolvente (muestra a muestra) entra al banco; centroide → pasabajos
// Grabación: entrada (canal izquierdo) y salida (canal derecho) en un WAV estéreo.

const ESCUCHA_WORKLET = `
class Escucha extends AudioWorkletProcessor {
  static get parameterDescriptors() { return [
    { name: 'ataqueMs', defaultValue: 2 }, { name: 'liberacionMs', defaultValue: 80 },
    { name: 'umbralDb', defaultValue: -45 }, { name: 'modo', defaultValue: 0 } ]; }
  constructor() { super(); this.env = 0; this.lento = 0; this.arm = true; this.refr = 0; this.n = 0; this.pico = 0; }
  process(inputs, outputs, P) {
    const x = inputs[0][0], y = outputs[0][0];
    const a = Math.exp(-1 / (P.ataqueMs[0] * sampleRate / 1000));
    const r = Math.exp(-1 / (P.liberacionMs[0] * sampleRate / 1000));
    const rl = Math.exp(-1 / (0.3 * sampleRate));        // envolvente lenta (300 ms) para comparar
    const umbral = Math.pow(10, P.umbralDb[0] / 20);
    for (let i = 0; i < y.length; i++) {
      const v = x ? Math.abs(x[i]) : 0;
      this.env = v > this.env ? a * this.env + (1 - a) * v : r * this.env + (1 - r) * v;
      this.lento = rl * this.lento + (1 - rl) * this.env;
      // Ataque: la envolvente rápida supera el umbral y duplica (+6 dB) a la lenta.
      if (this.refr > 0) this.refr--;
      if (this.arm && this.refr === 0 && this.env > umbral && this.env > 2 * this.lento) {
        this.arm = false; this.pico = this.env; this.n = Math.round(0.005 * sampleRate);
      }
      if (!this.arm && this.n > 0) {               // espera 5 ms para medir el pico del ataque
        this.pico = Math.max(this.pico, this.env);
        if (--this.n === 0) { this.port.postMessage({ tipo: 'ataque', nivel: this.pico, t: currentTime });
                              this.refr = Math.round(0.06 * sampleRate); }
      }
      if (!this.arm && this.n === 0 && this.env < 1.2 * this.lento) this.arm = true;
      y[i] = P.modo[0] > 0.5 ? (Math.random() * 2 - 1) * this.env * 4 : 0;   // excitación continua
    }
    if ((currentFrame & 2047) < 128) this.port.postMessage({ tipo: 'env', nivel: this.env });
    return true;
  }
}
registerProcessor('escucha', Escucha);

class Grabador extends AudioWorkletProcessor {
  constructor() { super(); this.on = false; this.port.onmessage = e => this.on = e.data; }
  process(inputs) {
    const i = inputs[0];
    if (this.on && i.length) this.port.postMessage([i[0].slice(0), (i[1] || i[0]).slice(0)]);
    return true;
  }
}
registerProcessor('grabador', Grabador);
`;

class CadenaEscucha {
  constructor(ctx, modal, onEstado) {
    this.ctx = ctx; this.modal = modal; this.onEstado = onEstado || (() => {});
    this.modo = 'disparo'; this.trozos = [];
    this.ajustes = { ataqueMs: 2, liberacionMs: 80, umbralDb: -45, centroMin: 1500, centroMax: 7000 };
  }
  async iniciar() {
    const ctx = this.ctx;
    // data: en vez de blob: para que funcione también abriendo el archivo directo (file://).
    const url = 'data:application/javascript;base64,' + btoa(unescape(encodeURIComponent(ESCUCHA_WORKLET)));
    await ctx.audioWorklet.addModule(url);
    // Sin cancelación de eco ni control de ganancia: el seguidor necesita la amplitud real.
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: {
      echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    this.mic = ctx.createMediaStreamSource(this.stream);
    this.det = new AudioWorkletNode(ctx, 'escucha', { outputChannelCount: [1] });
    this.an = ctx.createAnalyser(); this.an.fftSize = 2048; this.an.smoothingTimeConstant = 0;
    this.espectro = new Float32Array(this.an.frequencyBinCount);
    this.mic.connect(this.det); this.mic.connect(this.an);
    // Modo continuo: ruido × envolvente → pasabajos (centroide) → banco de resonadores
    this.lp = ctx.createBiquadFilter(); this.lp.type = 'lowpass'; this.lp.frequency.value = 8000;
    this.cont = ctx.createGain(); this.cont.gain.value = 0;
    this.det.connect(this.lp).connect(this.cont).connect(this.modal.bank);
    // Grabación: L = micrófono, R = salida del modelo
    this.merge = ctx.createChannelMerger(2);
    this.mic.connect(this.merge, 0, 0); this.modal.out.connect(this.merge, 0, 1);
    this.rec = new AudioWorkletNode(ctx, 'grabador', { numberOfInputs: 1, channelCount: 2, channelCountMode: 'explicit' });
    this.merge.connect(this.rec);
    this.rec.port.onmessage = e => this.trozos.push(e.data);
    this.det.port.onmessage = e => this.mensaje(e.data);
    this.aplicar();
  }
  aplicar() {
    const P = this.det.parameters, a = this.ajustes;
    P.get('ataqueMs').value = a.ataqueMs; P.get('liberacionMs').value = a.liberacionMs;
    P.get('umbralDb').value = a.umbralDb; P.get('modo').value = this.modo === 'continuo' ? 1 : 0;
    this.cont.gain.value = this.modo === 'continuo' ? 1 : 0;
  }
  centroide() {
    this.an.getFloatFrequencyData(this.espectro);
    const df = this.ctx.sampleRate / this.an.fftSize; let num = 0, den = 0;
    for (let k = 1; k < this.espectro.length; k++) { const m = Math.pow(10, this.espectro[k] / 20); num += k * df * m; den += m; }
    return den > 0 ? num / den : 0;
  }
  durezaDe(c) {
    const a = this.ajustes;
    return Math.min(1, Math.max(0, Math.log(c / a.centroMin) / Math.log(a.centroMax / a.centroMin)));
  }
  mensaje(d) {
    const c = this.centroide();
    if (d.tipo === 'ataque') {
      const dur = this.durezaDe(c);
      if (this.modo === 'disparo') this.modal.golpear(this.ctx.currentTime, Math.min(4, d.nivel * 8), dur);
      this.onEstado({ ataque: true, nivel: d.nivel, centroide: c, dureza: dur });
    } else {
      if (this.modo === 'continuo') this.lp.frequency.setTargetAtTime(Math.max(300, c * 1.5), this.ctx.currentTime, 0.02);
      this.onEstado({ nivel: d.nivel, centroide: c });
    }
  }
  grabar(on) {
    if (on) this.trozos = [];
    this.rec.port.postMessage(on);
    if (!on) {
      const n = this.trozos.reduce((s, t) => s + t[0].length, 0);
      const L = new Float32Array(n), R = new Float32Array(n); let o = 0;
      for (const t of this.trozos) { L.set(t[0], o); R.set(t[1], o); o += t[0].length; }
      return wavBlob({ numberOfChannels: 2, length: n, sampleRate: this.ctx.sampleRate, getChannelData: c => c ? R : L });
    }
  }
}
