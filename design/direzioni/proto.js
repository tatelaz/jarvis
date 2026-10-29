"use strict";
// Parte comune dei prototipi delle direzioni grafiche.
// Nessuna conversazione va avanti da sola: si sceglie uno stato (pulsanti in basso o tasti 1-6) e lo si guarda.

const P = {
  state: "attesa", level: 0, levelS: 0, typed: 0, since: performance.now(),
  text: {
    user: "A che ora parte il mio volo domani?",
    partial: "A che ora parte il mio vo",
    jarvis: "Alle sette e quaranta da Linate, signore: terminal due, uscita B12. Con il traffico di adesso le " +
            "conviene partire entro le sei e cinque.",
    task: { id: 3, title: "Rapporto vendite di settembre", short: "Rapporto vendite", pct: 0.64,
            step: "crea il grafico per regione" },
    ask: { id: 2, title: "Pulizia della cartella Download", short: "Pulizia Download",
           q: "Sposto nel cestino 214 file vecchi della cartella Download (3,1 GB)?" },
  },
};
const STATES = [["attesa", "Attesa"], ["ascolto", "Ascolto"], ["ragiono", "Ragiono"], ["parla", "Parlo"],
                ["conferma", "Ti chiede"], ["riposo", "Riposo"]];

// inviluppo sillabico di una voce: 0..1, con micro-variazioni
function voice(t) {
  return Math.abs(Math.sin(t * 7.3)) * (0.55 + 0.45 * Math.abs(Math.sin(t * 1.7))) * (0.85 + 0.15 * Math.sin(t * 23));
}
// rumore liscio (somma di seni) per deformare forme in modo organico ma deterministico
function smooth(x, t) {
  return 0.5 * Math.sin(x * 3 + t * 1.9) + 0.3 * Math.sin(x * 7 - t * 2.7) + 0.2 * Math.sin(x * 13 + t * 4.1);
}
function clock() {
  const d = new Date();
  return d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
}
function dateLine() {
  const d = new Date();
  return d.toLocaleDateString("it-IT", { weekday: "short", day: "2-digit", month: "short" }).replace(/\./g, "");
}

function proto(onFrame, onState) {
  const bar = document.createElement("nav");
  bar.className = "proto-bar";
  bar.innerHTML = `<span class="proto-tag">prototipo · stato</span>` +
    STATES.map(([k, l], i) => `<button data-s="${k}"><kbd>${i + 1}</kbd>${l}</button>`).join("");
  document.body.appendChild(bar);
  const set = (s) => {
    P.state = s; P.since = performance.now(); P.typed = 0;
    bar.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.s === s));
    onState && onState(s);
  };
  bar.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) set(b.dataset.s); });
  addEventListener("keydown", (e) => { const i = +e.key - 1; if (i >= 0 && i < STATES.length) set(STATES[i][0]); });

  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    const t = now / 1000;
    const target = P.state === "ascolto" || P.state === "parla" ? voice(t) : 0;
    P.level = target;
    P.levelS += (target - P.levelS) * Math.min(1, dt * 14);
    if (P.state === "parla") P.typed = Math.min(P.text.jarvis.length, P.typed + dt * 17);  // ~ritmo della voce
    onFrame(now, dt);
    requestAnimationFrame(frame);
  }
  set(new URLSearchParams(location.search).get("stato") || "attesa");
  requestAnimationFrame(frame);
}

// canvas nitido su schermi ad alta densità
function crisp(canvas) {
  const r = canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  if (canvas.width !== Math.round(r.width * dpr) || canvas.height !== Math.round(r.height * dpr)) {
    canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: r.width, h: r.height };
}
