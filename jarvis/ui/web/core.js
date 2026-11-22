"use strict";
/* I testi della finestra in inglese: la chiave è il testo italiano che si vede di base. */
const EN = {
  "in attesa": "standing by", "ti ascolto": "listening", "ragiono": "thinking", "serve una tua decisione": "your decision is needed",
  "a riposo": "resting", "avvio": "starting", "non collegato": "not connected", "in conversazione": "in conversation",
  "scheda video libera": "graphics card free", "un compito ti aspetta": "a task is waiting for you",
  "compito in corso": "task running", "compiti in corso": "tasks running", "microfono": "microphone", "spento": "off",
  "di' «Jarvis» per riaccendermi": "say “Jarvis” to wake me", "premi riaccendi per riaccendermi": "press wake to wake me",
  "approva": "approve", "nega": "deny", "ti chiede": "asks you", "sì, procedi": "yes, go ahead", "no": "no", "fatto": "done",
  "in corso": "running", "in coda": "queued", "collegamento con Jarvis perso": "connection with Jarvis lost",
  "parla": "talk", "riposo": "rest", "riaccendi": "wake", "registro": "log", "ferma": "stop", "chiudi": "close",
  "Parla senza dire il nome (Ctrl+Alt+J)": "Talk without saying the name (Ctrl+Alt+J)",
  "Riposo: libera la scheda video": "Rest: free the graphics card", "Registro della conversazione": "Conversation log",
  "Ferma tutto (Ctrl+Alt+K)": "Stop everything (Ctrl+Alt+K)", "scrivi a Jarvis…": "type to Jarvis…",
  "Messaggio per Jarvis": "Message for Jarvis", "Registro": "Log", "Chiudi il registro": "Close the log",
  "trascina per spostare la finestra": "drag to move the window", "Comandi": "Controls", "Schede": "Cards",
  "voce e sicurezza": "voice and security", "nuova parola d'ordine": "new passphrase",
  "Nuova parola d'ordine": "New passphrase", "imposta": "set",
  "collegamento con Jarvis perso · riprovo…": "connection with Jarvis lost · retrying…",
  "audio non disponibile · Jarvis riprova da solo": "audio unavailable · Jarvis keeps trying",
  "audio assente": "no audio", "dispositivo non disponibile": "device unavailable", "interrotto": "interrupted",
  "riconoscimento acceso · voci": "recognition on · voices",
  "riconoscimento spento finché non registri la tua voce («Jarvis, impara la mia voce»)":
    "recognition off until you record your voice (“Jarvis, learn my voice”)",
  "parola d'ordine impostata": "passphrase set", "parola d'ordine non impostata": "passphrase not set",
  "parola d'ordine salvata": "passphrase saved", "non salvata": "not saved", "voce di": "voice of", "memoria": "memory",
  "scheda": "card", "tu": "you", "sistema": "system", "errore": "error", "ospite": "guest", "Chiudi": "Close",
  "incolla qui la password per le app (16 lettere) creata su myaccount.google.com/apppasswords":
    "paste here the app password (16 letters) created at myaccount.google.com/apppasswords",
  "indirizzo Gmail": "Gmail address", "Indirizzo Gmail": "Gmail address",
  "password per le app": "app password", "Password per le app": "App password",
  "collega": "connect", "provo il collegamento…": "trying to connect…", "Gmail collegata": "Gmail connected",
  "non collegata": "not connected",
};
/* Il motore della finestra di Jarvis.
   - Collegamento con Jarvis (WebSocket): fotografia iniziale, eventi in tempo reale, comandi.
   - Stato condiviso J: cosa sta facendo Jarvis, cosa hai detto, cosa sta dicendo e quanto ne ha già detto.
   - Parti comuni: scrivere a Jarvis, registro, schede, comandi.
   - Le scene (temi) leggono J e disegnano; si cambiano a voce ("metti la grafica cinema") senza ricaricare. */

const J = {
  state: "avvio", connected: false,
  mic: 0, out: 0, micS: 0, outS: 0, said: -1,
  partial: "", user: "", userAt: 0, reply: "", replyDone: false, replyAt: 0, interrupted: false,
  history: [], tasks: new Map(), cards: [],
  rest: { active: false, listening: true },
  awakeUntil: 0, muted: false, ptt: false,
  config: { theme: "mark3", accent: "", text_scale: 1, effects: "pieni", user: "", brain: "", lang: "it" },
  bootAt: 0,                        // l'accensione si rivede a ogni collegamento e a ogni risveglio
};
const Themes = (window.JarvisThemes = {});
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ utilità per le scene
const U = {
  esc: (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  clamp: (x, a = 0, b = 1) => Math.max(a, Math.min(b, x)),
  ease: (x) => 1 - Math.pow(1 - U.clamp(x), 3),                 // uscita morbida
  easeIO: (x) => { x = U.clamp(x); return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; },
  smooth: (x, t) => 0.5 * Math.sin(x * 3 + t * 1.9) + 0.3 * Math.sin(x * 7 - t * 2.7) + 0.2 * Math.sin(x * 13 + t * 4.1),
  clock: () => new Date().toLocaleTimeString(U.locale(), { hour: "2-digit", minute: "2-digit" }),
  date: () => new Date().toLocaleDateString(U.locale(), { weekday: "short", day: "2-digit", month: "short" }).replace(/\./g, ""),
  locale: () => (J.config.lang === "en" ? "en-GB" : "it-IT"),
  // i testi della finestra si scrivono in italiano e si traducono qui quando Jarvis parla inglese (persona.language)
  t: (s) => (J.config.lang === "en" ? EN[s] ?? s : s),
  rgb(hex) { const m = /^#?([0-9a-f]{6})$/i.exec(hex || ""); if (!m) return null; const n = parseInt(m[1], 16); return [n >> 16, (n >> 8) & 255, n & 255]; },
  rich: () => J.config.effects !== "sobri" && !matchMedia("(prefers-reduced-motion: reduce)").matches,
  // canvas nitido su schermi ad alta densità
  crisp(canvas) {
    const r = canvas.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
    const W = Math.max(1, Math.round(r.width * dpr)), H = Math.max(1, Math.round(r.height * dpr));
    if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w: r.width, h: r.height };
  },
  md(src) {  // markdown minimo e sicuro per le schede
    const blocks = []; let html = "";
    src = String(src || "").replace(/```[^\n]*\n([\s\S]*?)```/g, (_, code) => { blocks.push(code); return `\u0000${blocks.length - 1}\u0000`; });
    const inline = (s) => s.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    for (const para of U.esc(src).split(/\n{2,}/)) {
      const t = para.trim();
      if (/^\u0000\d+\u0000$/.test(t)) { html += `<pre><code>${U.esc(blocks[+t.slice(1, -1)])}</code></pre>`; continue; }
      const lines = para.split("\n");
      if (lines.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l))) html += "<ul>" + lines.map((l) => `<li>${inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/, ""))}</li>`).join("") + "</ul>";
      else html += `<p>${inline(para).replace(/\n/g, "<br>")}</p>`;
    }
    return html.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${U.esc(blocks[+i])}</code>`);
  },
};

// molla smorzata: i valori visivi non saltano mai, scivolano come oggetti fisici
class Spring {
  constructor(v = 0, k = 90) { this.v = v; this.t = v; this.vel = 0; this.k = k; }
  to(t) { this.t = t; return this; }
  step(dt) { const d = 2 * Math.sqrt(this.k); this.vel += (this.k * (this.t - this.v) - d * this.vel) * dt; this.v += this.vel * dt; return this.v; }
}

// ------------------------------------------------------------------ cose da sapere
const derive = {
  awake: () => Date.now() < J.awakeUntil,
  need: () => [...J.tasks.values()].find((t) => t.question),            // un compito che aspetta te
  active: () => [...J.tasks.values()].filter((t) => t.status === "in corso" || t.status === "in coda" || t.question),
  // la parte di risposta già pronunciata: i sottotitoli seguono la voce, non il testo
  spoken() { if (J.replyDone && J.said < 0) return J.reply.length; return J.said < 0 ? 0 : Math.min(J.reply.length, J.said); },
  mode() {  // lo stato come lo vedono le scene
    if (J.rest.active) return "riposo";
    if (!J.connected) return "spento";
    if (J.state === "avvio") return "avvio";
    if (derive.need()) return J.state === "parla" ? "parla" : "conferma";
    if (J.state === "elaborazione") return "ragiono";
    return J.state === "ascolto" ? "ascolto" : J.state === "parla" ? "parla" : "attesa";
  },
};

// ------------------------------------------------------------------ collegamento
let ws = null, retry = 0;
const outbox = [];  // comandi partiti mentre il collegamento era giù: si consegnano appena torna
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { retry = 0; while (outbox.length) ws.send(JSON.stringify(outbox.shift())); };
  ws.onmessage = (m) => handle(JSON.parse(m.data));
  ws.onclose = () => {
    if (J.connected) { J.connected = false; $("offline").hidden = false; $("noaudio").hidden = true; }
    setTimeout(connect, Math.min(4000, 400 * 2 ** retry++));
  };
}
function send(obj) {
  if (ws && ws.readyState === 1) return ws.send(JSON.stringify(obj));
  // un messaggio scritto o un "approva" non si perdono durante un ricollegamento (lo "stop" vecchio sì: non ha senso dopo)
  if (obj.cmd !== "stop" && outbox.length < 20) outbox.push(obj);
}

function addHistory(who, text, label) {
  if (!text) return;
  J.history.push({ who, text, at: Date.now(), label });
  if (J.history.length > 200) J.history.shift();
  if (!$("drawer").hidden) renderLog();
}

// audio assente (cuffie staccate, dispositivo occupato): Jarvis non può dirlo, quindi si vede qui
function showAudio(a) {
  const el = $("noaudio"), down = !!(a && a.ok === false);
  el.hidden = !down;
  el.title = down ? a.error || "" : "";
  if (!a || a.replay) return;
  if (down) addHistory("err", `${U.t("audio assente")}: ${a.error || U.t("dispositivo non disponibile")}`);
  else if (a.reason && a.reason !== "avvio") addHistory("sys", `audio: ${a.output} · ${a.input}`);
}

// voce e sicurezza: stato del riconoscimento e parola d'ordine (il testo parte e il campo si svuota subito)
function renderSecurity() {
  const id = J.identity, box = $("security");
  box.hidden = !(id && id.available);
  if (box.hidden) return;
  const names = (id.people || []).map((p) => p.name).join(", ");
  $("sec-status").textContent = (id.active ? `${U.t("riconoscimento acceso · voci")}: ${names}`
    : U.t("riconoscimento spento finché non registri la tua voce («Jarvis, impara la mia voce»)"))
    + ` · ${U.t(id.passphrase ? "parola d'ordine impostata" : "parola d'ordine non impostata")}`;
}

function handle(ev) {
  switch (ev.type) {
    case "snapshot":
      J.history = []; J.tasks.clear(); J.connected = true; $("offline").hidden = true;
      for (const h of ev.history || []) handle({ ...h, replay: true });
      for (const t of ev.tasks || []) J.tasks.set(t.id, t);
      if (ev.rest) J.rest = { active: !!ev.rest.active, listening: ev.rest.listening !== false };
      showAudio(ev.audio ? { ...ev.audio, replay: true } : null);
      J.identity = ev.identity || null; renderSecurity();
      J.state = ev.state || "in attesa";
      applyConfig(ev.config || {});
      J.reply = ""; J.user = ""; J.replyDone = false; J.bootAt = performance.now();
      break;
    case "ui_config": applyConfig(ev); break;
    case "state": J.state = ev.state; break;
    case "awake": J.awakeUntil = ev.seconds > 0 ? Date.now() + ev.seconds * 1000 : 0; break;
    case "level": J.mic = ev.mic || 0; J.out = ev.out || 0; if (ev.said !== undefined) J.said = ev.said; break;
    case "partial": J.partial = ev.text || ""; break;
    case "user":  // chi non è l'utente si vede nel registro con il suo nome (o "ospite")
      if (ev.owner === false) addHistory("guest", ev.text, ev.speaker); else addHistory("user", ev.text);
      if (!ev.replay) { J.user = ev.text; J.userAt = performance.now(); J.partial = ""; J.reply = ""; J.replyDone = false; J.said = -1; J.interrupted = false; }
      break;
    case "jarvis_delta": if (J.replyDone) { J.reply = ""; J.replyDone = false; J.said = -1; } J.reply += ev.text; J.replyAt = performance.now(); break;
    case "jarvis_done":
      addHistory("jarvis", ev.text);
      if (!ev.replay && ev.text) { J.reply = ev.text; J.replyDone = true; J.replyAt = performance.now(); }
      if (!ev.replay && !ev.text) { J.reply = ""; J.replyDone = true; }
      break;
    case "interrupted":
      J.interrupted = true; J.replyDone = true;
      J.reply = ev.spoken ? ev.spoken + " …" : ""; J.said = J.reply.length;
      addHistory("sys", U.t("interrotto"));
      break;
    case "ignored": J.partial = ""; break;
    case "error": addHistory("err", `${ev.where}: ${ev.message}`); break;
    case "task": J.tasks.set(ev.id, ev); break;
    case "show": showCard(ev.title, ev.markdown, ev.replay); break;
    case "muted": J.muted = !!ev.muted; break;
    case "gmail_form":  // "collega Gmail": si apre il campo per la password per le app
      if (!ev.replay) { toggleLog(true); $("gmail-box").open = true; $("gmail-email").focus(); }
      break;
    case "notice":
      if (ev.kind === "gmail" && !ev.replay) {
        const info = ev.info || {};
        $("gmail-msg").textContent = info.ok ? U.t("Gmail collegata") : `${U.t("non collegata")}: ${info.message || ""}`;
      }
      if (ev.kind === "passphrase" && !ev.replay) {
        $("sec-msg").textContent = ev.info && ev.info.ok ? U.t("parola d'ordine salvata")
          : `${U.t("non salvata")}: ${(ev.info || {}).message || ""}`;
        if (ev.info && ev.info.ok && J.identity) { J.identity.passphrase = true; renderSecurity(); }
      }
      break;
    case "ptt": J.ptt = !!ev.armed; break;
    case "audio": showAudio(ev); break;
    case "identity": J.identity = ev; renderSecurity(); break;
    case "enroll":
      if (!ev.replay) addHistory("sys", `${U.t("voce di")} ${ev.name}: ${ev.status} · ${ev.progress}%`);
      if (ev.status === "fatta" && J.identity) { J.identity.active = true; renderSecurity(); }
      break;
    case "memory": addHistory("sys", `${U.t("memoria")} · ${ev.op}: ${ev.text}`); break;
    case "rest":
      if (J.rest.active && !ev.active) J.bootAt = performance.now();  // risveglio: si riaccende con la sua animazione
      J.rest = { active: !!ev.active, listening: ev.listening !== false };
      $("c-rest").classList.toggle("on", J.rest.active);
      $("c-rest").textContent = U.t(J.rest.active ? "riaccendi" : "riposo");
      break;
  }
  theme && theme.event && theme.event(ev);
}

function applyConfig(c) {
  const before = J.config.lang;
  Object.assign(J.config, c);
  document.documentElement.style.setProperty("--scale", J.config.text_scale || 1);
  if (J.config.accent) document.body.style.setProperty("--accent", J.config.accent);
  else document.body.style.removeProperty("--accent");
  translatePage();
  if (before !== J.config.lang && themeName) themeName = null;  // la grafica si rimonta con i suoi testi fissi tradotti
  useTheme(J.config.theme);
}

// i testi fissi della pagina (marcati data-i18n*) nella lingua di Jarvis
function translatePage() {
  document.documentElement.lang = J.config.lang === "en" ? "en" : "it";
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = U.t(el.dataset.i18n);
  for (const el of document.querySelectorAll("[data-i18n-title]")) el.title = U.t(el.dataset.i18nTitle);
  for (const el of document.querySelectorAll("[data-i18n-ph]")) el.placeholder = U.t(el.dataset.i18nPh);
  for (const el of document.querySelectorAll("[data-i18n-aria]")) el.setAttribute("aria-label", U.t(el.dataset.i18nAria));
  $("c-rest").textContent = U.t(J.rest.active ? "riaccendi" : "riposo");
}

// ------------------------------------------------------------------ temi
let theme = null, themeName = null, switching = false, pending = null;
function useTheme(name) {
  if (!Themes[name]) name = "mark3";
  if (switching) { pending = name; return; }                // chiesto durante una dissolvenza: si applica dopo
  if (name === themeName) return;
  const stage = $("stage");
  const mount = () => {
    if (theme) theme.unmount();
    stage.innerHTML = ""; document.body.dataset.theme = name;
    theme = Themes[name]; themeName = name;
    theme.mount(stage, api);
    J.bootAt = performance.now();
    stage.classList.remove("leaving"); switching = false;
    if (pending && pending !== name) { const p = pending; pending = null; useTheme(p); }
    pending = null;
  };
  if (!theme) return mount();
  switching = true; stage.classList.add("leaving"); setTimeout(mount, 460);   // dissolvenza tra le due scene
}
// colore della voce per i canvas: quello scelto a voce, altrimenti quello del tema
function accent(fallback) { return U.rgb(J.config.accent) || fallback; }

// ------------------------------------------------------------------ parti comuni
function showCard(title, markdown, replay) {
  J.cards.push({ title, markdown });
  addHistory("sys", `${U.t("scheda")}: ${title}`);
  if (replay) return;
  const c = document.createElement("div"); c.className = "card";
  c.innerHTML = `<h3><span></span><button aria-label="${U.t("Chiudi")}">×</button></h3><div class="md"></div>`;
  c.querySelector("span").textContent = title;
  c.querySelector(".md").innerHTML = U.md(markdown);
  c.querySelector("button").onclick = () => { c.classList.add("out"); setTimeout(() => c.remove(), 340); };
  $("cards").prepend(c);
  while ($("cards").children.length > 3) $("cards").lastElementChild.remove();
}
function renderLog() {
  const who = { user: J.config.user || U.t("tu"), jarvis: "jarvis", sys: U.t("sistema"), err: U.t("errore"),
                guest: U.t("ospite") };
  $("log").innerHTML = J.history.map((h) => `<div class="entry ${h.who}"><div class="who">${U.esc(h.label || who[h.who])} · ${new Date(h.at)
    .toLocaleTimeString(U.locale(), { hour: "2-digit", minute: "2-digit" })}</div><div class="txt">${U.esc(h.text)}</div></div>`).join("");
  $("log").scrollTop = $("log").scrollHeight;
}
function toggleLog(open) {
  const d = $("drawer"); d.hidden = open === undefined ? !d.hidden : !open;
  $("c-log").classList.toggle("on", !d.hidden);
  if (!d.hidden) renderLog();
}

function wire() {
  $("c-talk").onclick = () => send({ cmd: "ptt" });
  $("c-rest").onclick = () => send({ cmd: "rest" });
  $("c-stop").onclick = () => send({ cmd: "stop" });
  $("c-log").onclick = () => toggleLog();
  $("drawer-close").onclick = () => toggleLog(false);
  const comp = $("composer"), input = $("input");
  comp.addEventListener("submit", (e) => {
    e.preventDefault();
    if (input.value.trim()) send({ cmd: "text", text: input.value.trim() });
    input.value = ""; input.blur();
  });
  input.addEventListener("blur", () => { if (!input.value) comp.classList.remove("on"); });
  $("pass-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const field = $("pass-input"), value = field.value;
    field.value = "";  // non resta nel campo né nella pagina
    if (value.trim()) send({ cmd: "passphrase", value });
  });
  $("gmail-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const field = $("gmail-pass"), value = field.value, email = $("gmail-email").value.trim();
    field.value = "";  // la password parte e sparisce dalla pagina
    if (!value.trim()) return;
    $("gmail-msg").textContent = U.t("provo il collegamento…");
    send({ cmd: "gmail_app_password", email, value });
  });
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("drawer").hidden) return toggleLog(false);
      if (document.activeElement === input) { input.value = ""; input.blur(); return; }
      return send({ cmd: "stop" });
    }
    // scrivi per parlare: basta iniziare a scrivere, il campo compare da solo
    // ...ma non mentre si scrive in un altro campo (la parola d'ordine finiva nel messaggio per Jarvis)
    const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement && document.activeElement.tagName);
    if (!typing && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
      comp.classList.add("on"); input.focus();
    }
  });
}

// ------------------------------------------------------------------ fotogramma
let last = performance.now();
function frame(now) {
  const dt = Math.max(0, Math.min(0.05, (now - last) / 1000)); last = Math.max(last, now);
  J.micS += (Math.min(1, J.mic * 9) - J.micS) * Math.min(1, dt * 12);
  J.outS += (Math.min(1, J.out * 6) - J.outS) * Math.min(1, dt * 14);
  if (theme) {
    try { theme.frame(now, dt); } catch (e) { console.error("scena:", e); }
  }
  requestAnimationFrame((t) => frame(Math.max(t, last)));
}

const api = { J, U, Spring, derive, send, accent, toggleLog };
window.JarvisCore = {
  J, U, Spring, derive, api, handle, useTheme,
  // fa avanzare le animazioni di un certo tempo, fotogramma per fotogramma (verifiche e test automatici)
  advance(seconds) {
    let t = Math.max(last, performance.now());
    for (let i = 0; i < Math.round(seconds * 60); i++) { t += 1000 / 60; frame(t); }
    return t;
  },
  start() {
    wire(); connect(); requestAnimationFrame(frame);
    // la scena aspetta la prima fotografia di Jarvis (col tema scelto): niente doppia accensione all'avvio
    setTimeout(() => { if (!theme) useTheme(J.config.theme); }, 1500);
  },
};
