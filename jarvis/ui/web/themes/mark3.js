"use strict";
/* Tema "Mark III": la scena. Ogni movimento ha un significato:
   - accensione: un lampo al centro, l'anello si disegna con una testa di luce, le tacche si accendono in giro;
   - ascolto: le particelle ENTRANO verso l'anello (il suono che arriva), le tacche in alto fanno da misuratore;
   - voce: l'anello si deforma con la voce, le particelle ESCONO, un bagliore pulsa;
   - ragiono: una cometa gira sull'anello, tre satelliti sull'anello interno;
   - compiti: archi in orbita che entrano volando da fuori; quando un compito finisce l'arco si chiude con un lampo;
   - riposo: tutto si raccoglie verso il centro e si spegne, come un'ottica che chiude. */
(() => {
  const INK = [243, 242, 238], VOICE = [143, 216, 255], NEED = [255, 181, 71];
  const LABEL = { attesa: "in attesa", ascolto: "ti ascolto", ragiono: "ragiono", parla: "", conferma: "serve una tua decisione",
                  riposo: "a riposo", avvio: "avvio", spento: "non collegato" };
  let api, J, U, cv, el = {}, sp = {}, parts = [], spin = 0, inner = 0, micHist = [], lastSec = -1;
  const taskVis = new Map();  // id -> { slot, a: Spring (presenza), r: Spring (raggio d'ingresso), doneAt }

  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${U.clamp(a)})`;
  const mix = (a, b, k) => [0, 1, 2].map((i) => Math.round(a[i] + (b[i] - a[i]) * k));

  function mount(stage, a) {
    api = a; J = a.J; U = a.U;
    stage.innerHTML = `
      <canvas class="scene"></canvas>
      <div class="m3-corner m3-tl"><div class="m3-mark">J.A.R.V.I.S.</div><div class="m3-brain"></div></div>
      <div class="m3-corner m3-tr"><div class="m3-time"></div><div class="m3-date"></div></div>
      <div class="m3-corner m3-bl"><div class="m3-mic"></div><div class="m3-sum"></div></div>
      <div class="m3-state"></div>
      <div class="m3-talk"><div class="m3-you"></div><div class="m3-say"></div></div>
      <div class="m3-ask"><div class="m3-ask-h"></div><div class="m3-ask-q"></div>
        <div><button class="yes">${U.t("approva")}</button><button class="no">${U.t("nega")}</button></div></div>`;
    const q = (s) => stage.querySelector(s);
    el = { cv: q("canvas"), brain: q(".m3-brain"), time: q(".m3-time"), date: q(".m3-date"), mic: q(".m3-mic"), sum: q(".m3-sum"),
           state: q(".m3-state"), talk: q(".m3-talk"), you: q(".m3-you"), say: q(".m3-say"), ask: q(".m3-ask"),
           askH: q(".m3-ask-h"), askQ: q(".m3-ask-q"), corners: stage.querySelectorAll(".m3-corner") };
    cv = el.cv;
    const answer = (ok) => { const t = api.derive.need(); if (t) api.send({ cmd: "confirm", id: t.id, approved: ok }); };
    q(".m3-ask .yes").onclick = () => answer(true);
    q(".m3-ask .no").onclick = () => answer(false);
    sp = { size: new api.Spring(0.6, 70), bright: new api.Spring(0.3, 60), focus: new api.Spring(0.8, 80),
           warm: new api.Spring(0, 60), speak: new api.Spring(0, 90), lv: new api.Spring(0, 140) };
    parts = []; taskVis.clear();
  }

  function unmount() { parts = []; taskVis.clear(); }

  // "claude-opus-5-5" -> "opus 5.5"
  const modelName = (id) => String(id || "").replace(/^claude-/, "").replace(/-(\d+)-(\d+)$/, " $1.$2").replace(/-/g, " ");

  // ---------------------------------------------------------------- testi (si toccano solo quando cambiano)
  function setText(node, key, html) { if (node._k !== key) { node._k = key; node.innerHTML = html; } }

  function texts(now, mode, t) {
    const sec = Math.floor(t);
    if (sec !== lastSec) {
      lastSec = sec;
      el.time.textContent = U.clock(); el.date.textContent = U.date();
      const awake = api.derive.awake() && mode === "attesa";
      setText(el.brain, `${J.config.brain}|${awake}|${mode}|${J.config.lang}`, `${U.esc(modelName(J.config.brain))}` +
        (awake ? ` · <span class="m3-live">${U.t("in conversazione")}</span>`
          : mode === "riposo" ? ` · ${U.t("scheda video libera")}` : ""));
      const act = api.derive.active(), need = api.derive.need();
      setText(el.sum, `${act.length}|${!!need}|${J.config.lang}`, need ? `<span class="m3-need">${U.t("un compito ti aspetta")}</span>`
        : act.length ? `${act.length} ${U.t(act.length === 1 ? "compito in corso" : "compiti in corso")}` : "");
    }
    // microfono: otto barre dagli ultimi livelli
    micHist.push(J.micS); if (micHist.length > 8) micHist.shift();
    const bars = "▁▂▃▄▅▆▇";
    el.mic.textContent = U.t("microfono") + " "
      + (J.muted ? U.t("spento") : micHist.map((v) => bars[Math.min(6, Math.round(v * 6))]).join(""));

    const label = U.t(mode === "attesa" && api.derive.awake() ? "in conversazione" : LABEL[mode] ?? "");
    if (el.state._k !== label) { el.state._k = label; el.state.textContent = label; el.state.classList.toggle("need", mode === "conferma"); }

    // sottotitoli: la tua frase sopra, la sua sotto; la parte non ancora detta resta in ombra
    const you = mode === "ascolto" ? `${U.esc(J.partial)}<span class="caret"></span>`
              : mode === "riposo" ? U.t(J.rest.listening ? "di' «Jarvis» per riaccendermi" : "premi riaccendi per riaccendermi")
              : U.esc(J.user);
    setText(el.you, you, you);
    const reply = J.reply, said = api.derive.spoken();
    let start = 0;
    if (reply.length > 280) {  // risposte lunghe: si mostra il tratto che sta dicendo
      start = Math.max(0, reply.lastIndexOf(". ", Math.max(0, said - 60)) + 2);
      if (start > said) start = 0;
    }
    const shown = reply.slice(start, start + 320), cut = U.clamp(said - start, 0, shown.length);
    const sayKey = `${start}|${cut}|${shown.length}`;
    setText(el.say, sayKey, (start ? "… " : "") + `${U.esc(shown.slice(0, cut))}<span class="un">${U.esc(shown.slice(cut))}</span>`);
    el.say.classList.toggle("old", J.replyDone && now - J.replyAt > 12000);
    el.talk.classList.toggle("hide", mode === "conferma" || mode === "spento");

    const need = api.derive.need();
    el.ask.classList.toggle("in", mode === "conferma" && !!need);
    if (need) {
      setText(el.askH, `h${need.id}`, `#${need.id} · ${U.esc(need.title)} · ti chiede`);
      setText(el.askQ, need.question, U.esc(need.question));
    }
  }

  // ---------------------------------------------------------------- particelle
  function spawn(kind, n, cx, cy, R, rT, col) {
    for (let i = 0; i < n && parts.length < 320; i++) {
      const a = Math.random() * Math.PI * 2;
      if (kind === "out") parts.push({ a, r: R * (1 + 0.04 * Math.random()), v: 55 + 90 * Math.random(), life: 0.7 + 0.6 * Math.random(), max: 1.3, s: 0.7 + Math.random() * 0.9, col });
      else parts.push({ a, r: rT + 6 + 10 * Math.random(), v: -(70 + 60 * Math.random()), stop: R * 1.02, life: 1.1, max: 1.1, s: 0.6 + Math.random() * 0.7, col });
    }
  }
  function drawParts(ctx, cx, cy, dt) {
    ctx.globalCompositeOperation = "lighter";
    for (let i = parts.length - 1; i >= 0; i--) {
      const p = parts[i];
      p.r += p.v * dt; p.v *= 1 - 0.9 * dt; p.life -= dt;
      if (p.life <= 0 || (p.stop && p.r <= p.stop)) { parts.splice(i, 1); continue; }
      const k = p.life / p.max;
      ctx.fillStyle = rgba(p.col, 0.85 * k);
      ctx.beginPath(); ctx.arc(cx + Math.cos(p.a) * p.r, cy + Math.sin(p.a) * p.r, p.s, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
  }

  // ---------------------------------------------------------------- compiti in orbita
  // titolo corto, tagliato a parola intera
  function short(title, max = 28) {
    const s = String(title || "").toUpperCase();
    if (s.length <= max) return s;
    const cut = s.slice(0, max), sp = cut.lastIndexOf(" ");
    return (sp > 12 ? cut.slice(0, sp) : cut) + "…";
  }

  function tasks(ctx, cx, cy, rO, rT, t, dt, bright, voiceCol) {
    const now = performance.now();
    const visible = [...J.tasks.values()].filter((x) => x.status === "in corso" || x.status === "in coda" || x.question ||
      (x.status === "completato" && x.finished && Date.now() / 1000 - x.finished < 5));
    const used = new Set([...taskVis.values()].map((v) => v.slot));
    for (const x of visible) if (!taskVis.has(x.id)) {
      let slot = 0; while (used.has(slot)) slot++; used.add(slot);
      taskVis.set(x.id, { slot, a: new api.Spring(0, 50).to(1), r: new api.Spring(rO + 46, 55).to(rO), doneAt: 0 });
    }
    for (const [id, v] of taskVis) {
      const x = J.tasks.get(id);
      if (!x || !visible.includes(x)) { v.a.to(0); if (v.a.v < 0.02) taskVis.delete(id); }
      v.a.step(dt); v.r.step(dt);
      const pres = U.clamp(v.a.v);
      if (pres <= 0.01 || !x) continue;
      const span = 0.46, a0 = -Math.PI / 2 + 0.55 + v.slot * 0.62, a1 = a0 + span, r = v.r.v;
      const done = x.status === "completato";
      if (done && !v.doneAt) v.doneAt = now;
      const need = !!x.question;
      const col = need ? NEED : done ? voiceCol : INK;
      ctx.lineWidth = 1;
      ctx.strokeStyle = rgba(col, (need ? 0.35 : 0.16) * pres * bright);
      ctx.beginPath(); ctx.arc(cx, cy, r, a0, a1); ctx.stroke();
      if (done) {  // completato: l'arco si riempie con un lampo e poi sfuma
        const k = U.ease((now - v.doneAt) / 700), flash = Math.max(0, 1 - (now - v.doneAt) / 1400);
        ctx.lineWidth = 2 + 4 * flash; ctx.strokeStyle = rgba(voiceCol, (0.5 + 0.5 * flash) * pres);
        ctx.beginPath(); ctx.arc(cx, cy, r, a0, a0 + span * k); ctx.stroke();
      } else {  // in corso: un tratto luminoso che fa la spola (lavoro in corso, durata ignota)
        const ph = 0.5 + 0.5 * Math.sin(t * 1.25 + id * 1.7), seg = 0.11, h0 = a0 + (span - seg) * ph;
        const pulse = need ? 0.55 + 0.45 * Math.sin(t * 3.2) : 1;
        ctx.lineWidth = need ? 2.2 : 1.6; ctx.strokeStyle = rgba(col, 0.9 * pres * pulse * bright);
        ctx.beginPath(); ctx.arc(cx, cy, r, need ? a0 : h0, need ? a1 : h0 + seg); ctx.stroke();
      }
      // etichetta fuori dalla graduazione, con una linea di richiamo come sugli strumenti veri
      const la = a1, dir = Math.cos(la) >= 0 ? 1 : -1;
      const p1 = [cx + Math.cos(la) * (r + 3), cy + Math.sin(la) * (r + 3)];
      const p2 = [cx + Math.cos(la) * (rT + 16), cy + Math.sin(la) * (rT + 16)];
      const p3 = [p2[0] + dir * 14, p2[1]];
      ctx.lineWidth = 1; ctx.strokeStyle = rgba(col, (need ? 0.5 : 0.24) * pres * bright);
      ctx.beginPath(); ctx.moveTo(...p1); ctx.lineTo(...p2); ctx.lineTo(...p3); ctx.stroke();
      ctx.fillStyle = rgba(col, (need ? 0.95 : 0.7) * pres * bright);
      ctx.beginPath(); ctx.arc(p1[0], p1[1], 1.6, 0, Math.PI * 2); ctx.fill();
      ctx.font = `500 ${Math.round(10 * (J.config.text_scale || 1))}px Bahnschrift, "Segoe UI", sans-serif`;
      if ("letterSpacing" in ctx) ctx.letterSpacing = "2px";
      ctx.textBaseline = "middle"; ctx.textAlign = dir > 0 ? "left" : "right";
      ctx.fillText(`#${id} ${short(x.title)}${need ? " · TI CHIEDE" : done ? " · FATTO" : ""}`, p3[0] + dir * 6, p3[1]);
      if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
    }
  }

  // ---------------------------------------------------------------- fotogramma
  function frame(now, dt) {
    const { ctx, w, h } = U.crisp(cv);
    const t = now / 1000, mode = api.derive.mode(), rich = U.rich();
    const tb = (now - J.bootAt) / 1000;                       // secondi dall'accensione
    const booting = mode !== "riposo" && tb < 2.6;
    const voiceCol = api.accent(VOICE);
    const speaking = mode === "parla", listening = mode === "ascolto";

    sp.size.to(mode === "riposo" ? 0.42 : mode === "spento" ? 0.7 : 1).step(dt);
    sp.bright.to(mode === "riposo" ? 0.26 : mode === "spento" ? 0.22 : 1).step(dt);
    sp.focus.to(listening ? 0.70 : 0.80).step(dt);
    sp.warm.to(mode === "conferma" ? 1 : 0).step(dt);
    sp.speak.to(speaking ? 1 : 0).step(dt);
    sp.lv.to(speaking ? J.outS : listening ? J.micS : 0).step(dt);
    const lv = U.clamp(sp.lv.v), bright = U.clamp(sp.bright.v);

    const cx = w / 2, cy = Math.round(h * (h < 560 ? 0.36 : 0.40));
    const R = Math.min(w * 0.17, h * 0.205) * sp.size.v, rT = R * 1.36, rO = R * 1.18;
    el.state.style.top = `${cy - 6}px`;
    el.talk.style.top = `${cy + Math.min(w * 0.17, h * 0.205) * 1.62}px`;
    el.ask.style.top = `${cy + Math.min(w * 0.17, h * 0.205) * 1.62 + 4}px`;

    // tappe dell'accensione
    const bFlare = booting ? U.ease(tb / 0.35) * (1 - U.ease((tb - 0.35) / 0.7)) : 0;
    const bRing = booting ? U.easeIO((tb - 0.12) / 0.95) : 1;
    const bTicks = booting ? U.clamp((tb - 0.45) / 1.0) : 1;
    const bInner = booting ? U.ease((tb - 0.85) / 0.8) : 1;
    const bText = booting ? U.ease((tb - 1.3) / 0.8) : 1;
    for (const c of el.corners) c.style.opacity = mode === "riposo" ? 0.45 : bText;
    el.state.style.opacity = bText;

    spin += dt * (mode === "ragiono" ? 0.95 : mode === "riposo" ? 0.004 : speaking ? 0.14 : 0.05);
    inner += dt * (listening ? 0.9 : mode === "ragiono" ? 0.5 : 0.18);
    ctx.clearRect(0, 0, w, h);

    // 1 · fondo: una luce appena percettibile dietro lo strumento
    if (rich) {
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rT * 1.6);
      const glowCol = speaking ? voiceCol : sp.warm.v > 0.3 ? NEED : INK;
      g.addColorStop(0, rgba(glowCol, (0.035 + 0.09 * lv * sp.speak.v + 0.03 * sp.warm.v) * bright));
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    }

    // 2 · graduazione: 180 tacche, una lunga ogni 15; in ascolto quelle in alto fanno da misuratore
    for (let i = 0; i < 180; i++) {
      const frac = i / 180, vis = U.clamp((bTicks - frac * 0.85) / 0.15);
      if (vis <= 0) continue;
      const a = frac * Math.PI * 2 - Math.PI / 2 + spin * 0.35;
      const major = i % 15 === 0;
      let len = major ? 9 : 4, al = (major ? 0.42 : 0.17) * bright;
      if (listening) { const top = Math.max(0, Math.cos(a + Math.PI / 2)) ** 3; len += 16 * J.micS * top; al += 0.5 * J.micS * top; }
      if (speaking && rich) { const wave = Math.max(0, Math.sin(i * 0.33 - t * 7)); len += 7 * lv * wave; al += 0.25 * lv * wave; }
      ctx.strokeStyle = rgba(speaking ? mix(INK, voiceCol, 0.35) : INK, al * vis);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * rT, cy + Math.sin(a) * rT);
      ctx.lineTo(cx + Math.cos(a) * (rT + len), cy + Math.sin(a) * (rT + len)); ctx.stroke();
    }

    // 3 · orbita dei compiti
    ctx.lineWidth = 1; ctx.strokeStyle = rgba(INK, 0.07 * bright * bInner);
    ctx.beginPath(); ctx.arc(cx, cy, rO, 0, Math.PI * 2); ctx.stroke();
    if (mode !== "riposo") tasks(ctx, cx, cy, rO, rT, t, dt, bright * bInner, voiceCol);

    // 4 · anello della voce
    const amp = R * 0.10 * lv;
    const ringCol = mix(mix(INK, voiceCol, sp.speak.v), NEED, sp.warm.v * 0.35);
    const ringAlpha = (0.46 + 0.06 * Math.sin(t * 0.9) + 0.48 * Math.max(lv, sp.speak.v * 0.6)) * bright;
    const path = () => {
      ctx.beginPath();
      const end = Math.PI * 2 * bRing, steps = Math.max(2, Math.round(260 * bRing));
      for (let i = 0; i <= steps; i++) {
        const a = -Math.PI / 2 + (i / steps) * end;
        const r = R + amp * (0.6 + 0.4 * U.smooth(a, t * (speaking ? 2.3 : 1.4)));
        const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      if (bRing >= 1) ctx.closePath();
    };
    if (mode === "riposo") ctx.setLineDash([1, 6]);
    ctx.lineWidth = 1.3 + 0.7 * sp.speak.v; ctx.strokeStyle = rgba(ringCol, ringAlpha);
    path(); ctx.stroke(); ctx.setLineDash([]);
    if (rich && (lv > 0.02 || booting)) {  // bagliore: lo stesso tracciato, largo e sfocato, sommato alla luce
      ctx.globalCompositeOperation = "lighter";
      ctx.shadowColor = rgba(ringCol, 0.9); ctx.shadowBlur = 18 + 22 * lv;
      ctx.lineWidth = 3 + 8 * lv; ctx.strokeStyle = rgba(ringCol, (0.10 + 0.22 * lv) * bright);
      path(); ctx.stroke();
      ctx.shadowBlur = 0; ctx.globalCompositeOperation = "source-over";
    }
    if (booting && bRing < 1) {  // la testa di luce che disegna l'anello
      const a = -Math.PI / 2 + Math.PI * 2 * bRing, hx = cx + Math.cos(a) * R, hy = cy + Math.sin(a) * R;
      const g = ctx.createRadialGradient(hx, hy, 0, hx, hy, 18);
      g.addColorStop(0, "rgba(255,255,255,.95)"); g.addColorStop(0.25, rgba(voiceCol, 0.45)); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(hx - 18, hy - 18, 36, 36);
    }

    // 5 · ragiono: una cometa sull'anello
    if (mode === "ragiono") {
      const head = t * 3.4;
      for (let k = 0; k < 34; k++) {
        const aa = head - k * 0.032;
        ctx.strokeStyle = rgba(INK, 0.95 * (1 - k / 34) ** 1.6); ctx.lineWidth = 2.2 - k * 0.04;
        ctx.beginPath(); ctx.arc(cx, cy, R, aa - 0.034, aa); ctx.stroke();
      }
      if (rich) {
        const hx = cx + Math.cos(head) * R, hy = cy + Math.sin(head) * R, g = ctx.createRadialGradient(hx, hy, 0, hx, hy, 22);
        g.addColorStop(0, "rgba(255,255,255,.7)"); g.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = g; ctx.fillRect(hx - 22, hy - 22, 44, 44);
      }
    }

    // 6 · anello interno tratteggiato (si stringe quando ascolta: mette a fuoco) e satelliti quando ragiona
    const rI = R * sp.focus.v;
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(-inner);
    ctx.setLineDash([2, 7]); ctx.lineWidth = 1; ctx.strokeStyle = rgba(INK, 0.22 * bright * bInner);
    ctx.beginPath(); ctx.arc(0, 0, rI, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
    if (mode === "ragiono") for (let k = 0; k < 3; k++) {
      const a = t * 1.7 + k * (Math.PI * 2 / 3);
      ctx.fillStyle = rgba(INK, 0.9); ctx.beginPath(); ctx.arc(Math.cos(a) * rI, Math.sin(a) * rI, 2, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();

    // 7 · particelle: entrano quando ascolta, escono quando parla
    if (rich) {
      if (speaking && lv > 0.05) spawn("out", Math.round(110 * lv * dt + Math.random()), cx, cy, R, rT, voiceCol);
      if (listening && J.micS > 0.05) spawn("in", Math.round(80 * J.micS * dt + Math.random()), cx, cy, R, rT, INK);
      drawParts(ctx, cx, cy, dt);
    } else parts = [];

    // 8 · nucleo: un punto di luce al centro mentre parla
    if (rich && sp.speak.v > 0.02) {
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.32);
      g.addColorStop(0, rgba(voiceCol, 0.16 * sp.speak.v * (0.5 + lv))); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
    }

    // 9 · lampo d'accensione
    if (bFlare > 0.01) {
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.9);
      g.addColorStop(0, `rgba(255,255,255,${0.9 * bFlare})`); g.addColorStop(0.12, rgba(voiceCol, 0.35 * bFlare)); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
      ctx.fillStyle = `rgba(255,255,255,${0.35 * bFlare})`; ctx.fillRect(cx - R * 1.8 * bFlare, cy - 0.5, R * 3.6 * bFlare, 1);
    }

    texts(now, mode, t);
  }

  window.JarvisThemes.mark3 = { mount, unmount, frame };
})();
