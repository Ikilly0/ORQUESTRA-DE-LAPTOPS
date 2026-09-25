// Orden / Desorden: estación web. Cada laptop abre esta página, elige su rol y toca con el teclado.
"use strict";

const D = window.ORDER_DATA;
const NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const noteName = (m) => NOTE[m % 12] + (Math.floor(m / 12) - 1);
const INTERVAL = { 0: "R", 1: "b9", 2: "9", 3: "b3", 4: "3", 5: "4", 6: "b5", 7: "5", 8: "#5", 9: "6", 10: "7", 11: "7M" };
const PIECE = { kick: "Bombo", kick2: "Bombo 2", snare: "Caja", tom_hi: "Tom", tom_mid: "Tom medio", tom_floor: "Tom de piso",
  hat: "Hi-hat", hat_open: "Hi-hat abierto", ride: "Ride", ride_bell: "Campana", crash: "Crash" };
const RHYTHM = ["Bajo", "Batería: tambores", "Batería: platillos"];  // siguen leyendo en la sección III

// ---- la obra: secciones -----------------------------------------------------------------
const EM = { name: "Em", pcs: [4, 7, 11], root: 4 };
const SECTIONS = [
  { id: "I", name: "I. Desorden (entrada)", mode: "improv", clock: false, chord: EM,
    text: "Sin pulso. Tus teclas son las notas de Mi menor en tu registro. Notas largas, pocas, escuchando. El director abre y cierra con cartas." },
  { id: "II", name: "II. Orden A (compases 1-128)", mode: "read", from: 1, to: 128,
    text: "Lee tu pista: toca la tecla cuando la nota toca la línea. Escucha a la batería y al bajo." },
  { id: "III", name: "III. Cobra (compases 129-160, x2)", mode: "improv", from: 129, to: 160, repeat: 2,
    text: "Bajo y batería siguen leyendo. El resto improvisa: tus teclas cambian solas a las notas del acorde de cada compás. Obedece las cartas del director." },
  { id: "IV", name: "IV. Orden B (compases 161-230)", mode: "read", from: 161, to: 230,
    text: "Lee tu pista hasta el final. En el compás 230 todos sostienen." },
  { id: "V", name: "V. Desorden (coda)", mode: "improv", clock: false, chord: EM,
    text: "Sin pulso, cada vez menos notas. CORTE termina la obra." },
];
const CARDS = [
  { key: "1", name: "TUTTI", text: "Todos tocan." },
  { key: "2", name: "SOLO", text: "Solo la estación indicada. El resto calla." },
  { key: "3", name: "DÚO", text: "Las dos estaciones indicadas: pregunta y respuesta de un compás." },
  { key: "4", name: "XP", text: "Intercambio: los que tocan paran y los que callaban entran." },
  { key: "5", name: "ECO", text: "Imita el ritmo de la estación indicada, con tus propias notas." },
  { key: "6", name: "CAPAS", text: "Entrada de a uno, cada 2 compases, de grave a agudo." },
  { key: "7", name: "CORTE", text: "Silencio total en el 1 del próximo compás." },
  { key: "8", name: "ORDEN", text: "Volver a leer: pasamos a la sección siguiente en el próximo 1." },
];

// ---- tiempo de la canción -------------------------------------------------------------------
const barStart = (b) => D.bars[Math.max(0, Math.min(D.bars.length - 1, b - 1))];
function barAt(sec) {  // compás (1..) que suena en sec
  let lo = 0, hi = D.bars.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (D.bars[mid] <= sec) lo = mid; else hi = mid - 1; }
  return lo + 1;
}
function chordAt(sec) {
  let i = 0;
  while (i + 1 < D.chords.length && D.chords[i + 1].t <= sec + 1e-6) i++;
  return i;
}
function lowerBound(arr, x, key) {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (key(arr[mid]) < x) lo = mid + 1; else hi = mid; }
  return lo;
}

// Teclas en modo improvisación: las notas del acorde dentro del registro del rol, de grave a agudo.
function roleRange(role) {
  const ps = role.ev.map((e) => e[2]).filter((p) => typeof p === "number");
  if (!ps.length) return [48, 72];
  const sorted = ps.slice().sort((a, b) => a - b);
  return [sorted[Math.floor(sorted.length * 0.05)], sorted[Math.floor(sorted.length * 0.95)]];
}
function improvNotes(role, chord) {
  const n = role.slots.length;
  let [lo, hi] = roleRange(role);
  const tones = [];
  for (let m = lo; m <= 108 && tones.length < n; m++) {
    if (m > hi + 12 && tones.length >= 7) break;
    if (chord.pcs.includes(m % 12)) tones.push(m);
  }
  return tones;
}

// ---- estación --------------------------------------------------------------------------------
class Station {
  constructor() {
    this.ctx = null;
    this.synth = null;
    this.roleIdx = 0;
    this.director = false;
    this.section = SECTIONS[1];
    this.bpm = D.bpm;
    this.timeline = null;  // lista de segmentos {t, s0, s1, speed} mientras corre
    this.held = new Map(); // tecla -> {voice, idx}
    this.lit = new Set();
    this.card = null;
    this.cardTarget = "";
    this.nudge = 0;
    this.bandVoices = [];
    this.rhythmFree = false;
    this.buildUI();
    this.bindKeys();
    requestAnimationFrame(() => this.draw());
    setInterval(() => this.schedule(), 25);
  }

  get role() { return D.roles[this.roleIdx]; }

  ensureAudio() {
    if (this.ctx) return;
    this.ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: "interactive" });
    this.synth = new Synth(this.ctx, Date.now() & 0xffff);
    this.myOut = this.ctx.createGain();
    this.bandOut = this.ctx.createGain();
    this.clickOut = this.ctx.createGain();
    this.myOut.connect(this.synth.out);
    this.bandOut.connect(this.synth.out);
    this.clickOut.connect(this.synth.out);
    this.applyVolumes();
  }

  applyVolumes() {
    if (!this.ctx) return;
    this.myOut.gain.value = +this.$("vol").value / 100;
    this.bandOut.gain.value = this.$("fondo").checked ? +this.$("fondoVol").value / 100 : 0;
    this.clickOut.gain.value = this.$("click").checked ? 1 : 0;
  }

  $(id) { return document.getElementById(id); }

  // ---- interfaz ----
  buildUI() {
    const roleSel = this.$("role");
    D.roles.forEach((r, i) => roleSel.add(new Option(r.name, i)));
    roleSel.add(new Option("Director (cartas)", -1));
    roleSel.onchange = () => {
      const v = +roleSel.value;
      this.director = v < 0;
      if (!this.director) this.roleIdx = v;
      this.releaseAll();
      this.updateInfo();
    };
    const secSel = this.$("section");
    SECTIONS.forEach((s, i) => secSel.add(new Option(s.name, i)));
    secSel.value = 1;
    secSel.onchange = () => { this.stop(); this.section = SECTIONS[+secSel.value]; this.rhythmFree = false; this.updateInfo(); };
    this.$("bpm").value = Math.round(D.bpm);
    this.$("bpm").onchange = () => { this.bpm = Math.max(60, Math.min(220, +this.$("bpm").value || D.bpm)); };
    this.$("start").onclick = () => this.toggle();
    ["vol", "fondo", "fondoVol", "click"].forEach((id) => this.$(id).addEventListener("input", () => this.applyVolumes()));
    this.$("render").onclick = () => renderDemo(this.$("renderStatus"));
    const cards = this.$("cards");
    CARDS.forEach((c) => {
      const b = document.createElement("button");
      b.textContent = `${c.key} ${c.name}`;
      b.onclick = () => this.setCard(c);
      cards.appendChild(b);
    });
    const tgt = this.$("target");
    tgt.add(new Option("(todas)", ""));
    D.roles.forEach((r) => tgt.add(new Option(r.name, r.name)));
    tgt.onchange = () => { this.cardTarget = tgt.value; };
    this.updateInfo();
  }

  setCard(c) { this.card = c; }

  updateInfo() {
    const s = this.section;
    this.$("sectionText").textContent = s.text;
    this.$("directorPanel").style.display = this.director ? "" : "none";
    const r = this.role;
    this.$("roleInfo").textContent = this.director ? "Director: muestra cartas con 1-8 (o los botones) y da la entrada."
      : `${r.name}: ${(SOUNDS[r.sound] || {}).label || r.sound}. Teclas ${r.slots.map((s) => s.k.toUpperCase()).join(" ")}.`;
  }

  bindKeys() {
    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      if (e.code === "Enter") { e.preventDefault(); this.toggle(); return; }
      if (e.code === "Tab") { e.preventDefault(); this.rhythmFree = !this.rhythmFree; return; }
      if (e.code === "ArrowLeft" || e.code === "ArrowRight") {  // corrige el reloj +-30 ms si me desfaso
        this.nudge += e.code === "ArrowLeft" ? -0.03 : 0.03;
        if (this.timeline) this.timeline.forEach((sg) => { sg.t += e.code === "ArrowLeft" ? 0.03 : -0.03; });
        return;
      }
      if (this.director && /^Digit[1-8]$/.test(e.code)) { this.setCard(CARDS[+e.code.slice(5) - 1]); return; }
      if (e.repeat) return;
      const k = keyOf(e.code);
      if (!k || this.director) return;
      const idx = this.role.slots.findIndex((s) => s.k === k);
      if (idx < 0) return;
      e.preventDefault();
      this.ensureAudio();
      this.press(k, idx);
    });
    window.addEventListener("keyup", (e) => {
      const k = keyOf(e.code);
      const h = k && this.held.get(k);
      if (!h) return;
      h.voice.release(this.ctx.currentTime);
      this.held.delete(k);
      this.lit.delete(h.idx);
    });
  }

  // En la sección III el bajo y la batería siguen leyendo; con Tab pasan a improvisar (y vuelven)
  currentMode() {
    if (this.section.id === "III" && !this.director && RHYTHM.includes(this.role.name))
      return this.rhythmFree ? "improv" : "read";
    return this.section.mode;
  }

  songNow() {
    if (!this.timeline || !this.ctx) return null;
    const ct = this.ctx.currentTime;
    for (const sg of this.timeline) {
      if (ct < sg.t) return sg.count ? null : sg.s0;
      const end = sg.t + (sg.s1 - sg.s0) / sg.speed;
      if (ct < end) return sg.count ? null : sg.s0 + (ct - sg.t) * sg.speed;
    }
    return undefined;  // terminó
  }

  currentChord() {
    if (this.section.chord) return this.section.chord;
    const now = this.songNow();
    const sec = now == null ? barStart(this.section.from || 1) : now;
    return D.chords[chordAt(sec)];
  }

  slotNote(idx) {
    const r = this.role, s = r.slots[idx];
    if (r.sound === "drums") return s.p;
    if (this.currentMode() === "improv") return improvNotes(r, this.currentChord())[idx];
    return s.m;
  }

  press(k, idx) {
    const note = this.slotNote(idx);
    if (note === undefined || note === null) return;
    const voice = this.synth.note(this.role.sound, note, this.ctx.currentTime, this.myOut);
    this.held.set(k, { voice, idx });
    this.lit.add(idx);
  }

  releaseAll() {
    this.held.forEach((h) => h.voice.release(this.ctx.currentTime));
    this.held.clear();
    this.lit.clear();
  }

  // ---- transporte ----
  toggle() {
    this.ensureAudio();
    if (this.ctx.state === "suspended") this.ctx.resume();
    if (this.timeline) { this.stop(); return; }
    const s = this.section;
    if (!s.clock && s.clock !== undefined) { this.$("start").textContent = "Sin pulso: toca libre"; return; }
    const speed = this.bpm / D.bpm, t = this.ctx.currentTime + 0.1;
    const beat = 60 / this.bpm;
    const count = { count: true, t, s0: 0, s1: D.meter * beat, speed: 1 };
    this.timeline = [count];
    let tt = t + D.meter * beat;
    for (let r = 0; r < (s.repeat || 1); r++) {
      const sg = { t: tt, s0: barStart(s.from), s1: barStart(s.to + 1), speed };
      this.timeline.push(sg);
      tt += (sg.s1 - sg.s0) / speed;
    }
    for (let i = 0; i < D.meter; i++) this.synth.click(t + i * beat, i === 0, this.synth.out);  // cuenta siempre audible
    this.ptrs = null;
    this.$("start").textContent = "■ Detener (Enter)";
  }

  stop() {
    this.timeline = null;
    this.ptrs = null;
    if (this.bandVoices) this.bandVoices.forEach((v) => v.release(this.ctx.currentTime));
    this.bandVoices = [];
    this.$("start").textContent = "▶ Empezar (Enter)";
  }

  // agenda las notas del "fondo" (las otras pistas, para ensayar solo) y el clic, 0.2 s adelante
  schedule() {
    if (!this.timeline || !this.ctx) return;
    const now = this.ctx.currentTime, horizon = now + 0.2;
    if (this.songNow() === undefined) { this.stop(); return; }
    if (!this.ptrs) this.ptrs = this.timeline.map(() => ({ notes: D.roles.map(() => 0), beat: 0, done: false }));
    this.timeline.forEach((sg, si) => {
      if (sg.count) return;
      const p = this.ptrs[si];
      if (p.done || horizon < sg.t) return;
      const toCtx = (sec) => sg.t + (sec - sg.s0) / sg.speed;
      if (p.beat === 0) p.beat = lowerBound(D.beats, sg.s0 - 1e-6, (x) => x);
      while (p.beat < D.beats.length && D.beats[p.beat] < sg.s1 && toCtx(D.beats[p.beat]) < horizon) {
        const bsec = D.beats[p.beat];
        const accent = Math.abs(barStart(barAt(bsec)) - bsec) < 1e-3;
        if (toCtx(bsec) >= now - 0.01) this.synth.click(toCtx(bsec), accent, this.clickOut);
        p.beat++;
      }
      if (this.$("fondo").checked) {
        D.roles.forEach((r, ri) => {
          if (!this.director && ri === this.roleIdx) return;
          if (this.section.mode === "improv" && !RHYTHM.includes(r.name)) return;
          const ev = r.ev;
          if (p.notes[ri] === 0) p.notes[ri] = lowerBound(ev, sg.s0 - 1e-6, (e) => e[0]);
          while (p.notes[ri] < ev.length && ev[p.notes[ri]][0] < sg.s1 && toCtx(ev[p.notes[ri]][0]) < horizon) {
            const [on, off, pitch] = ev[p.notes[ri]];
            if (toCtx(on) >= now - 0.01) {
              const dur = Math.max(0.05, (Math.min(off, sg.s1) - on) / sg.speed);
              this.bandVoices.push(this.synth.note(r.sound, pitch, toCtx(on), this.bandOut, dur));
            }
            p.notes[ri]++;
          }
        });
        if (this.bandVoices.length > 200) this.bandVoices = this.bandVoices.slice(-100);
      }
      if (toCtx(sg.s1) < horizon) p.done = true;
    });
  }

  // ---- dibujo ----
  draw() {
    requestAnimationFrame(() => this.draw());
    const cv = this.$("hw"), g = cv.getContext("2d");
    const W = cv.clientWidth, H = cv.clientHeight;
    if (cv.width !== Math.round(W * devicePixelRatio) || cv.height !== Math.round(H * devicePixelRatio)) { cv.width = Math.round(W * devicePixelRatio); cv.height = Math.round(H * devicePixelRatio); }
    g.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    g.fillStyle = "#15161a"; g.fillRect(0, 0, W, H);
    const now = this.ctx ? this.songNow() : null;

    // barra de estado: compás, acorde, carta
    const ch = this.currentChord(), next = now != null && now !== undefined && !this.section.chord ? D.chords[chordAt(now) + 1] : null;
    const bar = now != null && now !== undefined && !this.section.chord ? barAt(now) : null;
    let status = this.section.chord ? "Sin pulso" : (now === null && this.timeline ? "Cuenta..." : bar ? `Compás ${bar}` : `Desde compás ${this.section.from}`);
    const modeTxt = this.director ? "" : (this.currentMode() === "read" ? "   ·   LEER" : "   ·   IMPROVISAR");
    const tabTxt = this.section.id === "III" && !this.director && RHYTHM.includes(this.role.name) ? " (Tab cambia)" : "";
    this.$("status").textContent = `${status}   ·   Acorde: ${ch ? ch.name : "-"}${next ? "   →   " + next.name : ""}${modeTxt}${tabTxt}`;

    if (this.director) return this.drawCard(g, W, H);
    const r = this.role, n = r.slots.length, laneW = W / n, keysH = 86, yHit = H - keysH;
    const improv = this.currentMode() === "improv" && r.sound !== "drums";
    const tones = improv ? improvNotes(r, ch) : null;
    // notas que caen (modo lectura)
    if (!improv && now != null && now !== undefined) {
      const ahead = 3.0 * (this.bpm / D.bpm), pps = yHit / ahead;
      const lane = new Map();
      r.slots.forEach((s, i) => lane.set(r.sound === "drums" ? s.p : s.m, i));
      const nearest = (p) => {
        if (lane.has(p)) return lane.get(p);
        if (r.sound === "drums") return null;
        let best = null, bd = 99;
        r.slots.forEach((s, i) => { const d = Math.abs(s.m - p); if (d < bd) { bd = d; best = i; } });
        return best;
      };
      const ev = r.ev, i0 = lowerBound(ev, now - 2, (e) => e[0]);
      g.fillStyle = r.color;
      for (let i = i0; i < ev.length && ev[i][0] < now + ahead; i++) {
        let [on, off, p] = ev[i];
        if (r.sound === "drums") off = on + 0.09;
        if (off < now) continue;
        const li = nearest(p);
        if (li == null) continue;
        const yOn = yHit - (on - now) * pps, yOff = Math.min(yOn - 8, yHit - (off - now) * pps + 2);
        g.fillStyle = on <= now && now < off ? "#ffffff" : r.color;
        g.fillRect(li * laneW + 3, Math.max(0, yOff), laneW - 6, Math.min(yHit, yOn) - Math.max(0, yOff));
      }
    } else if (improv) {
      g.fillStyle = "#9a9ca5"; g.font = "15px system-ui"; g.textAlign = "center";
      g.fillText("Improvisa: cada tecla es una nota del acorde actual", W / 2, 30);
    }
    // teclado
    for (let i = 0; i < n; i++) {
      const s = r.slots[i], x = i * laneW, on = this.lit.has(i);
      g.strokeStyle = "#2a2c33"; g.beginPath(); g.moveTo(x, 0); g.lineTo(x, yHit); g.stroke();
      g.fillStyle = on ? r.color : "#2e3038";
      g.fillRect(x + 2, yHit + 4, laneW - 4, keysH - 8);
      g.fillStyle = "#e8e8ea"; g.textAlign = "center"; g.font = "bold 17px system-ui";
      g.fillText(s.k.toUpperCase(), x + laneW / 2, yHit + 34);
      g.font = "12px system-ui"; g.fillStyle = on ? "#fff" : "#9a9ca5";
      let sub = r.sound === "drums" ? PIECE[s.p] || s.p : noteName(s.m);
      if (improv) sub = tones[i] !== undefined ? `${noteName(tones[i])} ${INTERVAL[(tones[i] - ch.root + 120) % 12]}` : "";
      g.fillText(sub, x + laneW / 2, yHit + 62);
    }
    g.fillStyle = "#f0f0f0"; g.fillRect(0, yHit - 1, W, 3);
  }

  drawCard(g, W, H) {
    g.textAlign = "center";
    if (!this.card) {
      g.fillStyle = "#9a9ca5"; g.font = "20px system-ui";
      g.fillText("Elige una carta (teclas 1-8). Proyecta esta pantalla o levanta la carta impresa.", W / 2, H / 2);
      return;
    }
    g.fillStyle = "#f0c040"; g.font = `bold ${Math.min(160, W / 6)}px system-ui`;
    g.fillText(this.card.name, W / 2, H * 0.45);
    g.fillStyle = "#e8e8ea"; g.font = "28px system-ui";
    if (this.cardTarget) g.fillText(this.cardTarget, W / 2, H * 0.62);
    g.fillStyle = "#9a9ca5"; g.font = "20px system-ui";
    g.fillText(this.card.text, W / 2, H * 0.75);
  }
}

function keyOf(code) {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  return null;
}

// ---- render de demostración (mismo sintetizador, sin tiempo real) -----------------------------
// Recorre la obra en miniatura (~70 s): Desorden -> Orden A (c. 33-48) -> Cobra (c. 129-152, con cartas)
// -> CORTE -> Orden B (c. 161-176). Las partes improvisadas las toca un "intérprete simulado" con semilla fija,
// para que el render sea reproducible; en concierto las toca una persona.
const RENDER_PLAN = [
  { kind: "free", dur: 10 },
  { kind: "read", from: 33, to: 48 },
  { kind: "cobra", from: 129, to: 152, cards: [
    { bar: 129, card: "TUTTI", who: ["Guitarra rítmica", "Guitarra 2 + solista", "Piano", "Orquesta de cuerdas", "Coro"] },
    { bar: 137, card: "SOLO", who: ["Guitarra 2 + solista"] },
    { bar: 145, card: "XP", who: ["Piano", "Orquesta de cuerdas", "Coro", "Violín"] },
  ] },
  { kind: "gap", dur: 0.7 },  // CORTE: silencio total
  { kind: "read", from: 161, to: 176 },
];

async function renderDemo(statusEl, bpm = D.bpm) {
  const SR = 48000, speed = bpm / D.bpm;
  let total = 1.5;
  RENDER_PLAN.forEach((p) => { total += p.dur || (barStart(p.to + 1) - barStart(p.from)) / speed; });
  if (statusEl) statusEl.textContent = `Calculando ${total.toFixed(0)} s de audio...`;
  const ctx = new OfflineAudioContext(2, Math.ceil(total * SR), SR);
  const syn = new Synth(ctx, 2026);
  const rng = makeRng(7);
  const bus = ctx.createGain(); bus.connect(syn.out);
  const role = (name) => D.roles.find((r) => r.name === name);
  const log = [];
  let t = 0.3;

  const readRoles = (names, from, to, t0) => {
    const s0 = barStart(from), s1 = barStart(to + 1);
    D.roles.forEach((r) => {
      if (names && !names.includes(r.name)) return;
      for (const [on, off, p] of r.ev) {
        if (on < s0 || on >= s1) continue;
        syn.note(r.sound, p, t0 + (on - s0) / speed, bus, Math.max(0.05, (Math.min(off, s1) - on) / speed));
      }
    });
    return (s1 - s0) / speed;
  };
  // intérprete simulado: camina por las teclas (notas del acorde) con el ritmo típico de su instrumento
  const improviser = (r, fromSec, toSec, t0, chordFn, density) => {
    let k = Math.floor(rng() * 4) + 2;
    const sustained = ["string_ensemble", "strings", "vocal", "tremolo_strings"].includes(r.sound);
    const step = (sustained ? D.meter : r.sound === "guitar" ? 0.5 : 1) * 60 / bpm;
    for (let tt = 0; t0 + tt < t0 + (toSec - fromSec) / speed - 0.05; tt += step) {
      if (rng() > density) continue;
      const sec = fromSec + tt * speed, tones = improvNotes(r, chordFn(sec));
      if (!tones.length) continue;
      k = Math.max(0, Math.min(tones.length - 1, k + Math.round((rng() - 0.5) * 3)));
      syn.note(r.sound, tones[k], t0 + tt, bus, step * (sustained ? 0.98 : 0.8), 0.8 + 0.2 * rng());
    }
  };

  for (const p of RENDER_PLAN) {
    if (p.kind === "free") {
      log.push(`${t.toFixed(1)} s  I. Desorden (Em, sin pulso)`);
      ["Coro", "Orquesta de cuerdas", "Piano", "Cuerdas trémolo 2"].forEach((name, i) => {
        const r = role(name);
        let tt = 0.4 + i * 1.3;
        while (tt < p.dur - 1.5) {
          const tones = improvNotes(r, EM), m = tones[Math.floor(rng() * Math.min(6, tones.length))];
          const dur = r.sound === "piano" ? 1.2 : 2 + rng() * 2.5;
          syn.note(r.sound, m, t + tt, bus, Math.min(dur, p.dur - tt), 0.6 + 0.3 * rng());
          tt += 1.2 + rng() * 2.2;
        }
      });
      t += p.dur;
    } else if (p.kind === "read") {
      log.push(`${t.toFixed(1)} s  Orden: lectura de los compases ${p.from}-${p.to}, 12 estaciones`);
      t += readRoles(null, p.from, p.to, t);
    } else if (p.kind === "cobra") {
      log.push(`${t.toFixed(1)} s  III. Cobra: compases ${p.from}-${p.to}; bajo y batería leen, el resto improvisa`);
      readRoles(RHYTHM, p.from, p.to, t);
      p.cards.forEach((c, i) => {
        const endBar = i + 1 < p.cards.length ? p.cards[i + 1].bar : p.to + 1;
        const s0 = barStart(c.bar), s1 = barStart(endBar), t0 = t + (s0 - barStart(p.from)) / speed;
        log.push(`${t0.toFixed(1)} s     carta ${c.card}: ${c.who.join(", ")}`);
        c.who.forEach((name) => improviser(role(name), s0, s1, t0, (sec) => D.chords[chordAt(sec)],
          c.card === "SOLO" ? 0.85 : 0.55));
      });
      t += (barStart(p.to + 1) - barStart(p.from)) / speed;
    } else if (p.kind === "gap") {
      log.push(`${t.toFixed(1)} s  carta CORTE: silencio`);
      t += p.dur;
    }
  }
  const buf = await ctx.startRendering();
  const blob = audioBufferToWav(buf);
  if (statusEl) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "orden-desorden-render.wav";
    a.textContent = "Descargar render (WAV)";
    statusEl.textContent = `Listo: ${buf.duration.toFixed(1)} s. `;
    statusEl.appendChild(a);
    const au = document.createElement("audio"); au.controls = true; au.src = a.href;
    statusEl.appendChild(au);
    a.click();
  }
  window.RENDER_LOG = log;
  return { buf, blob, log };
}

window.addEventListener("DOMContentLoaded", () => { window.station = new Station(); });
