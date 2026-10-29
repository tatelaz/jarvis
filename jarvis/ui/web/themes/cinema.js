"use strict";
/* Tema "Cinema": la scena.
   - accensione: un punto di luce si apre in una striscia anamorfica e diventa la linea; il titolo JARVIS appare
     e svanisce come all'inizio di un film;
   - voce: la linea vibra, un bagliore caldo e una striscia orizzontale pulsano con la voce, le parole emergono
     dalla sfocatura mentre le dice;
   - ascolto: la linea trema con la tua voce, la tua frase si scrive sopra;
   - ragiono: un punto di luce corre lungo la linea;
   - riposo: la linea si raccoglie in un punto che si raffredda, come la lampada di un proiettore;
   - sempre: grana di pellicola, vignetta e un po' di pulviscolo nel fascio (solo con gli effetti pieni). */
(() => {
  const WARM = [244, 230, 207], INK = [244, 239, 230], NEED = [227, 178, 106];
  let api, J, U, cv, el = {}, sp = {}, grain = null, dust = [], lastSec = -1, words = [], wordsKey = "";

  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${U.clamp(a)})`;

  function mount(stage, a) {
    api = a; J = a.J; U = a.U;
    stage.innerHTML = `
      <canvas class="scene"></canvas>
      <div class="ci-top"><span>Jarvis</span><span class="ci-time"></span></div>
      <div class="ci-title">JARVIS</div>
      <div class="ci-cap"></div>
      <div class="ci-sub"></div>
      <div class="ci-credits"></div>`;
    const q = (s) => stage.querySelector(s);
    el = { cv: q("canvas"), top: q(".ci-top"), time: q(".ci-time"), title: q(".ci-title"), cap: q(".ci-cap"),
           sub: q(".ci-sub"), credits: q(".ci-credits") };
    cv = el.cv;
    el.sub.addEventListener("click", (e) => {
      const b = e.target.closest("button"); const t = api.derive.need();
      if (b && t) api.send({ cmd: "confirm", id: t.id, approved: b.classList.contains("yes") });
    });
    sp = { len: new api.Spring(0, 40), bright: new api.Spring(0.3, 50), speak: new api.Spring(0, 80), lv: new api.Spring(0, 140),
           dot: new api.Spring(0, 60) };
    dust = Array.from({ length: 46 }, () => ({ x: Math.random(), y: Math.random(), z: 0.3 + Math.random() * 0.7,
                                               vx: (Math.random() - 0.5) * 0.004, vy: (Math.random() - 0.5) * 0.003 }));
    words = []; wordsKey = "";
  }
  function unmount() { dust = []; }

  function makeGrain() {
    const g = document.createElement("canvas"); g.width = g.height = 256;
    const c = g.getContext("2d"), img = c.createImageData(256, 256);
    for (let i = 0; i < img.data.length; i += 4) { const v = Math.random() * 255; img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255; }
    c.putImageData(img, 0, 0);
    return g;
  }
  function setText(node, key, html) { if (node._k !== key) { node._k = key; node.innerHTML = html; } }

  // ---------------------------------------------------------------- testi
  function credits() {
    const rows = [...J.tasks.values()].filter((x) => x.status === "in corso" || x.status === "in coda" || x.question ||
      (x.status === "completato" && x.finished && Date.now() / 1000 - x.finished < 6)).sort((a, b) => a.id - b.id).slice(-4);
    return rows.map((x) => {
      const tag = x.question ? "ti chiede" : x.status === "completato" ? "fatto" : x.status;
      const line = `${String(x.id).padStart(2, "0")}  ${String(x.title || "").toLowerCase().slice(0, 38)} `.padEnd(46, ".") + ` ${tag}`;
      return x.question ? `<span class="need">${U.esc(line)}</span>` : x.status === "completato" ? `<span class="done">${U.esc(line)}</span>` : U.esc(line);
    }).join("\n");
  }

  function texts(now, mode, t, booting, tb) {
    const sec = Math.floor(t);
    if (sec !== lastSec) { lastSec = sec; el.time.textContent = U.clock(); setText(el.credits, "c" + credits(), credits()); }
    el.top.style.opacity = mode === "riposo" ? 0.4 : booting ? U.ease((tb - 1.6) / 0.8) : 1;
    // titolo di testa: compare e svanisce durante l'accensione
    el.title.style.opacity = booting ? Math.min(U.ease((tb - 0.9) / 0.6), 1 - U.ease((tb - 2.2) / 0.7)) : 0;
    el.title.style.letterSpacing = `${1.2 + 0.25 * U.clamp(tb / 3)}em`;

    const need = api.derive.need();
    const cap = mode === "ascolto" ? `${U.esc(J.partial)}<span class="caret"></span>`
              : mode === "conferma" && need ? `<span class="need">${U.esc(need.title)} · ti chiede</span>`
              : mode === "riposo" || mode === "spento" ? "" : U.esc(J.user);
    setText(el.cap, cap, cap);

    // sottotitoli
    if (mode === "conferma" && need) {
      setText(el.sub, "ask" + need.id + need.question, `<div class="ci-ask">${U.esc(need.question)}<div class="opts">` +
        `<button class="yes">sì, procedi</button><button class="no">no</button></div></div>`);
      el.sub.className = "ci-sub";
      return;
    }
    if (mode === "riposo") {
      setText(el.sub, "rest", J.rest.listening ? "di' «Jarvis» per riaccendermi" : "premi riaccendi per riaccendermi");
      el.sub.className = "ci-sub hint"; return;
    }
    if (mode === "spento") { setText(el.sub, "off", "collegamento con Jarvis perso"); el.sub.className = "ci-sub hint"; return; }
    el.sub.classList.remove("hint");
    const reply = J.reply;
    let start = 0;
    if (reply.length > 240) { const said = api.derive.spoken(); start = Math.max(0, reply.lastIndexOf(". ", Math.max(0, said - 40)) + 2); }
    const shown = reply.slice(start, start + 260);
    const key = `${start}|${shown}`;
    if (key !== wordsKey) {  // parole come elementi separati: ognuna emerge dalla sfocatura quando viene detta
      wordsKey = key; el.sub._k = key;
      const parts = shown.split(/(\s+)/);
      let pos = start;
      el.sub.innerHTML = parts.map((p) => { const s = `<span class="w wait" data-end="${pos + p.length}">${U.esc(p)}</span>`; pos += p.length; return s; }).join("");
      words = [...el.sub.querySelectorAll(".w")];
    }
    const said = api.derive.spoken();
    for (const wnode of words) {
      const show = +wnode.dataset.end <= said + 1 || (J.replyDone && J.said < 0);
      if (show === wnode.classList.contains("wait")) wnode.classList.toggle("wait", !show);
    }
    el.sub.classList.toggle("old", J.replyDone && now - J.replyAt > 12000);
  }

  // ---------------------------------------------------------------- fotogramma
  function frame(now, dt) {
    const { ctx, w, h } = U.crisp(cv);
    if (!grain) grain = makeGrain();
    const t = now / 1000, mode = api.derive.mode(), rich = U.rich();
    const tb = (now - J.bootAt) / 1000, booting = mode !== "riposo" && tb < 3.2;
    const light = api.accent(WARM);
    const speaking = mode === "parla", listening = mode === "ascolto";

    sp.len.to(mode === "riposo" ? 0 : mode === "spento" ? 0.25 : 1).step(dt);
    sp.bright.to(mode === "riposo" ? 0.35 : mode === "spento" ? 0.2 : 1).step(dt);
    sp.speak.to(speaking ? 1 : 0).step(dt);
    sp.lv.to(speaking ? J.outS : listening ? J.micS : 0).step(dt);
    sp.dot.to(mode === "riposo" ? 1 : 0).step(dt);
    const lv = U.clamp(sp.lv.v), bright = U.clamp(sp.bright.v);

    const y0 = Math.round(h * 0.42), span = w * 0.64;
    el.cap.style.top = `${y0 - 58}px`; el.title.style.top = `${y0 - 64}px`; el.sub.style.top = `${y0 + 34}px`;

    // accensione: prima un punto, poi la striscia che si apre, poi la linea
    const bPoint = booting ? U.ease(tb / 0.4) : 1;
    const bOpen = booting ? U.easeIO((tb - 0.35) / 1.1) : 1;
    const len = span * bOpen * U.clamp(sp.len.v);
    const x0 = w / 2 - len / 2, x1 = w / 2 + len / 2;
    ctx.clearRect(0, 0, w, h);

    // pulviscolo nel fascio di luce
    if (rich) {
      for (const d of dust) {
        d.x = (d.x + d.vx * dt + 1) % 1; d.y = (d.y + d.vy * dt + 1) % 1;
        const px = d.x * w, py = d.y * h, near = Math.exp(-((py - y0) ** 2) / (2 * (h * 0.22) ** 2));
        ctx.fillStyle = rgba(INK, (0.05 + 0.18 * near * (0.6 + 0.4 * lv)) * d.z * bright);
        ctx.beginPath(); ctx.arc(px, py, 0.6 + d.z * 0.9, 0, Math.PI * 2); ctx.fill();
      }
    }

    // la linea
    if (len > 2) {
      const base = mode === "attesa" ? 0.32 + 0.1 * Math.sin(t * 0.8) : mode === "ragiono" ? 0.2 : mode === "conferma" ? 0.45 : 0.8;
      const col = speaking ? light : mode === "conferma" ? NEED : INK;
      const draw = () => {
        ctx.beginPath();
        for (let x = x0; x <= x1; x += 1.5) {
          const u = (x - x0) / Math.max(1, len), fade = Math.sin(Math.PI * u) ** 1.4;
          let y = 0;
          if (speaking || listening) y = lv * fade * h * 0.065 * (0.6 * Math.sin(u * 46 + t * 17) + 0.4 * Math.sin(u * 19 - t * 9)) * (0.75 + 0.25 * U.smooth(u * 5, t));
          x === x0 ? ctx.moveTo(x, y0 - y) : ctx.lineTo(x, y0 - y);
        }
      };
      ctx.lineWidth = 1 + 0.5 * sp.speak.v; ctx.strokeStyle = rgba(col, base * bright); draw(); ctx.stroke();
      if (rich && (sp.speak.v > 0.02 || booting)) {  // bagliore caldo sommato alla luce
        ctx.globalCompositeOperation = "lighter";
        ctx.shadowColor = rgba(light, 0.9); ctx.shadowBlur = 12 + 22 * lv;
        ctx.lineWidth = 2 + 5 * lv; ctx.strokeStyle = rgba(light, (0.08 + 0.28 * lv) * Math.max(sp.speak.v, booting ? 0.6 : 0));
        draw(); ctx.stroke(); ctx.shadowBlur = 0; ctx.globalCompositeOperation = "source-over";
      }
      // le estremità sfumano nel nero
      for (const [xa, xb] of [[x0 - 2, x0 + len * 0.14], [x1 + 2, x1 - len * 0.14]]) {
        const g = ctx.createLinearGradient(xa, 0, xb, 0);
        g.addColorStop(0, "rgba(5,5,5,1)"); g.addColorStop(1, "rgba(5,5,5,0)");
        ctx.fillStyle = g; ctx.fillRect(Math.min(xa, xb), y0 - 60, Math.abs(xb - xa), 120);
      }
    }

    // striscia anamorfica: all'accensione e quando parla
    const streak = (booting ? Math.max(0, 1 - Math.abs(tb - 0.9) / 0.9) : 0) + (rich ? 0.55 * lv * sp.speak.v : 0);
    if (streak > 0.01) {
      ctx.globalCompositeOperation = "lighter";
      const sw = w * (0.35 + 0.5 * streak), g = ctx.createLinearGradient(w / 2 - sw, 0, w / 2 + sw, 0);
      g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(0.5, rgba(light, 0.35 * streak)); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(w / 2 - sw, y0 - 1.5, sw * 2, 3);
      const c = ctx.createRadialGradient(w / 2, y0, 0, w / 2, y0, 60);
      c.addColorStop(0, rgba([255, 250, 240], 0.5 * streak)); c.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = c; ctx.fillRect(w / 2 - 60, y0 - 60, 120, 120);
      ctx.globalCompositeOperation = "source-over";
    }

    // il punto: all'accensione nasce da qui, a riposo resta qui e respira
    const dotA = (booting ? bPoint * (1 - bOpen * 0.8) : 0) + sp.dot.v * (0.25 + 0.2 * Math.sin(t * 1.1));
    if (dotA > 0.01) {
      const g = ctx.createRadialGradient(w / 2, y0, 0, w / 2, y0, 16);
      g.addColorStop(0, rgba([255, 252, 245], dotA)); g.addColorStop(0.3, rgba(light, dotA * 0.4)); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(w / 2 - 16, y0 - 16, 32, 32);
    }

    // ragiono: un punto di luce che corre lungo la linea
    if (mode === "ragiono") {
      const px = w / 2 + Math.sin(t * 1.8) * len * 0.38, g = ctx.createRadialGradient(px, y0, 0, px, y0, 28);
      g.addColorStop(0, "rgba(255,246,232,.95)"); g.addColorStop(0.2, rgba(light, 0.3)); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(px - 28, y0 - 28, 56, 56);
      if (rich) { ctx.fillStyle = rgba(light, 0.25); ctx.fillRect(px - 70, y0 - 0.5, 140, 1); }
    }

    // pellicola: grana e vignetta
    if (rich) {
      ctx.globalAlpha = 0.034;
      const ox = Math.floor(Math.random() * 256), oy = Math.floor(Math.random() * 256);
      for (let x = -ox; x < w; x += 256) for (let y = -oy; y < h; y += 256) ctx.drawImage(grain, x, y);
      ctx.globalAlpha = 1;
    }
    const v = ctx.createRadialGradient(w / 2, h * 0.45, Math.min(w, h) * 0.22, w / 2, h * 0.45, Math.max(w, h) * 0.78);
    v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, "rgba(0,0,0,.72)");
    ctx.fillStyle = v; ctx.fillRect(0, 0, w, h);

    texts(now, mode, t, booting, tb);
  }

  window.JarvisThemes.cinema = { mount, unmount, frame };
})();
