/* Primo avvio di Jarvis: la pagina. Parla con jarvis/setup/server.py; i testi sono qui, in italiano e in inglese
   (di base la lingua di Windows, poi quella scelta in alto a destra, che vale anche per Jarvis). */
"use strict";

const T = {
  it: {
    close: "Chiudi (riprende da qui la prossima volta)",
    step_hello: "Benvenuto", step_pc: "Il tuo PC", step_brain: "Cervello", step_dl: "Download", step_test: "Prova",
    step_done: "Pronto",
    test_t: "Mi senti? Ti sento?", test_s: "Di' qualcosa: la barra del microfono deve muoversi. Poi ascolta la mia voce.",
    test_mic: "Microfono", test_out: "Casse", test_heard: "Ti sento", test_play: "Ascolta",
    test_default: "Predefinito di Windows",
    e_casse_non_disponibili: "Non riesco a usare queste casse: scegline altre o controlla che siano collegate.",
    e_dispositivo_sconosciuto: "Questo dispositivo non c'è più: scegline un altro.",
    e_mic: "Non riesco ad aprire il microfono: forse lo sta usando un altro programma.",
    hello_t: "Ciao. Sono Jarvis.", hello_s: "Pochi minuti per prepararmi, poi il resto me lo dici a voce.",
    hello_go: "Iniziamo",
    pc_t: "Guardo il tuo PC", pc_s: "Scelgo i modelli giusti per la tua scheda video e la tua memoria.",
    pc_gpu: "Scheda video", pc_ram: "Memoria", pc_disk: "Spazio libero", pc_audio: "Audio", pc_net: "Connessione",
    pc_wait: "controllo…", no_gpu: "nessuna scheda NVIDIA", free_need: "{free} liberi · servono {need}",
    nothing_to_get: "{free} liberi · niente da scaricare", mics: "{m} microfono · {s} uscite",
    mics_n: "{m} microfoni · {s} uscite", net_ok: "raggiungibile", net_no: "assente",
    p_completo: "Profilo completo", p_completo_s: "Voce e orecchie sulla scheda video: rispondo in circa due secondi.",
    p_leggero: "Profilo leggero", p_leggero_s: "Una voce più piccola, per stare nella memoria della scheda video.",
    p_processore: "Solo processore", p_processore_s: "Senza una scheda NVIDIA la mia voce per ora non parte.",
    p_blocked: "Così non posso continuare",
    n_driver_vecchio: "Il driver NVIDIA manca o è troppo vecchio: aggiornalo dal sito di NVIDIA e riprova.",
    n_da_misurare: "Questa scheda non è ancora stata provata su un PC vero: potrei essere più lento.",
    n_poca_memoria_video: "La scheda video ha troppa poca memoria per la mia voce.",
    n_scheda_non_nvidia: "Le schede AMD e Intel non sono ancora supportate.",
    n_voce_lenta: "Sul solo processore la mia voce sarebbe molto lenta.",
    n_niente_microfono: "Non trovo un microfono: potrai scrivermi, o collegarne uno dopo.",
    n_niente_casse: "Non trovo casse o cuffie: collegale prima di parlarmi.",
    b_voce_solo_nvidia: "Per ora mi serve una scheda video NVIDIA.",
    b_poca_ram: "Servono almeno 8 GB di memoria.",
    b_poco_spazio: "Serve più spazio libero sul disco: almeno {need}.",
    b_niente_rete: "Non raggiungo i siti da cui scaricare: controlla la connessione.",
    brain_t: "Chi pensa per me", brain_s: "Puoi cambiarlo quando vuoi, anche a voce.",
    b_best: "Consigliato", b_claude: "Claude, con il tuo abbonamento",
    b_claude_s: "Pro o Max. Accedi su claude.ai dal browser: la password non passa da qui.",
    b_free: "OpenCode, gratis", b_free_s: "Nessun account. Modelli gratuiti, meno precisi di Claude.",
    c_checking: "Controllo l'accesso…",
    c_install: "Installa Claude Code", c_installing: "Installazione nella finestra aperta…",
    c_login: "Accedi con Claude", c_wait: "Completa l'accesso nel browser…", c_ok: "Collegato",
    h_claude_later: "Puoi accedere anche dopo: intanto rispondo con i modelli gratuiti di OpenCode.",
    h_free: "Le domande passano dai server dei modelli gratuiti di OpenCode.",
    dl_t: "Scarico ciò che serve", dl_s: "Puoi chiudere la finestra: la prossima volta riprendo da dove ero arrivato.",
    dl_engine: "Motore audio", dl_ears: "Orecchie", dl_voice: "Voce", dl_speakers: "Chi parla", dl_memory: "Memoria",
    dl_turns: "Turni di parola", dl_done: "Fatto", dl_all: "Tutto scaricato",
    dl_left: "{left} rimanenti", dl_speed: "{speed}/s", dl_eta_min: "circa {n} min", dl_eta_s: "meno di un minuto",
    dl_err: "Il download si è fermato.", dl_err_retry: "Riprova: riparto da dove ero arrivato.",
    dl_err_perm: "Un file non è quello atteso o non c'è più: serve un aggiornamento di Jarvis.",
    done_t: "Sono pronto.", done_s: "Da adesso chiamami per nome: “Jarvis, …”. Il resto lo sistemiamo parlando.",
    done_name: "Come ti chiami?", done_auto: "Avvia con Windows", done_go: "Inizia",
    e_voce_mancante: "Manca ancora la mia voce di base: arriverà con un aggiornamento. Per ora non posso partire.",
    e_download_incompleto: "Il download non è finito.", e_controllo_pc: "Prima serve il controllo del PC.",
    e_net: "Non riesco a parlare con il programma di installazione. Riprova.",
    bye_t: "A tra poco.",
    back: "Indietro", next: "Avanti", again: "Riprova", other_lang: "English",
  },
  en: {
    close: "Close (I'll pick up from here next time)",
    step_hello: "Welcome", step_pc: "Your PC", step_brain: "Brain", step_dl: "Download", step_test: "Test",
    step_done: "Ready",
    test_t: "Can you hear me?", test_s: "Say something: the microphone bar should move. Then listen to my voice.",
    test_mic: "Microphone", test_out: "Speakers", test_heard: "I hear you", test_play: "Listen",
    test_default: "Windows default",
    e_casse_non_disponibili: "I can't use these speakers: pick others or check they're plugged in.",
    e_dispositivo_sconosciuto: "This device is gone: pick another one.",
    e_mic: "I can't open the microphone: another program may be using it.",
    hello_t: "Hello. I'm Jarvis.", hello_s: "A few minutes to get me ready. The rest you can tell me out loud.",
    hello_go: "Let's begin",
    pc_t: "Looking at your PC", pc_s: "I pick the right models for your graphics card and memory.",
    pc_gpu: "Graphics", pc_ram: "Memory", pc_disk: "Free space", pc_audio: "Audio", pc_net: "Connection",
    pc_wait: "checking…", no_gpu: "no NVIDIA card", free_need: "{free} free · {need} needed",
    nothing_to_get: "{free} free · nothing to download", mics: "{m} microphone · {s} outputs",
    mics_n: "{m} microphones · {s} outputs", net_ok: "reachable", net_no: "offline",
    p_completo: "Full profile", p_completo_s: "Voice and hearing on the graphics card: I answer in about two seconds.",
    p_leggero: "Light profile", p_leggero_s: "A smaller voice, to fit in the graphics card's memory.",
    p_processore: "Processor only", p_processore_s: "Without an NVIDIA card my voice can't run yet.",
    p_blocked: "I can't go on like this",
    n_driver_vecchio: "The NVIDIA driver is missing or too old: update it from NVIDIA's website and try again.",
    n_da_misurare: "This card hasn't been tested on a real PC yet: I might be slower.",
    n_poca_memoria_video: "The graphics card has too little memory for my voice.",
    n_scheda_non_nvidia: "AMD and Intel cards aren't supported yet.",
    n_voce_lenta: "On the processor alone my voice would be very slow.",
    n_niente_microfono: "I can't find a microphone: you can type to me, or plug one in later.",
    n_niente_casse: "I can't find speakers or headphones: plug them in before talking to me.",
    b_voce_solo_nvidia: "For now I need an NVIDIA graphics card.",
    b_poca_ram: "At least 8 GB of memory is needed.",
    b_poco_spazio: "More free disk space is needed: at least {need}.",
    b_niente_rete: "I can't reach the download sites: check your connection.",
    brain_t: "Who does the thinking", brain_s: "You can change it any time, even by voice.",
    b_best: "Recommended", b_claude: "Claude, with your subscription",
    b_claude_s: "Pro or Max. Sign in on claude.ai in your browser: your password never passes through here.",
    b_free: "OpenCode, free", b_free_s: "No account. Free models, less accurate than Claude.",
    c_checking: "Checking your sign-in…",
    c_install: "Install Claude Code", c_installing: "Installing in the window that opened…",
    c_login: "Sign in with Claude", c_wait: "Finish signing in in your browser…", c_ok: "Connected",
    h_claude_later: "You can sign in later: meanwhile I'll answer with OpenCode's free models.",
    h_free: "Your questions go through the servers of OpenCode's free models.",
    dl_t: "Downloading what I need", dl_s: "You can close this window: next time I'll pick up where I left off.",
    dl_engine: "Audio engine", dl_ears: "Hearing", dl_voice: "Voice", dl_speakers: "Who's speaking", dl_memory: "Memory",
    dl_turns: "Turn taking", dl_done: "Done", dl_all: "All downloaded",
    dl_left: "{left} left", dl_speed: "{speed}/s", dl_eta_min: "about {n} min", dl_eta_s: "under a minute",
    dl_err: "The download stopped.", dl_err_retry: "Try again: I'll pick up where I left off.",
    dl_err_perm: "A file isn't the expected one or is gone: Jarvis needs an update.",
    done_t: "I'm ready.", done_s: "From now on, call me by name: “Jarvis, …”. We'll sort out the rest by talking.",
    done_name: "What's your name?", done_auto: "Start with Windows", done_go: "Start",
    e_voce_mancante: "My default voice is still missing: it will come with an update. I can't start yet.",
    e_download_incompleto: "The download isn't finished.", e_controllo_pc: "The PC check comes first.",
    e_net: "I can't reach the installer. Try again.",
    bye_t: "See you in a moment.",
    back: "Back", next: "Next", again: "Try again", other_lang: "Italiano",
  },
};

const STEPS = ["hello", "pc", "brain", "dl", "test", "done"];
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
let lang = "it";
let current = 0;
let pcData = null, brainData = null, dlData = null, choice = "claude";
// controlli già avviati sul benvenuto: la prima volta i programmi partono a freddo (e l'antivirus li guarda),
// misurato 8 s nella finestra vera contro meno di 1 s a caldo; così quando si arriva al passo sono già pronti
let pcEarly = null, brainEarly = null;
let claudeWait = "";  // "", "install" o "login": si aspetta che finisca nella finestra a parte
let timer = 0;

const t = (key, vars = {}) => (T[lang][key] ?? key).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
const num = (x) => new Intl.NumberFormat(lang === "it" ? "it-IT" : "en-US", { maximumFractionDigits: 1 }).format(x);
const size = (bytes) => bytes >= 2 ** 30 * 0.95 ? `${num(bytes / 2 ** 30)} GB` : `${Math.max(0, Math.round(bytes / 2 ** 20))} MB`;
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

async function api(path, body) {
  const opt = body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let r;
  try {
    r = await fetch(path, opt);
  } catch {
    throw Object.assign(new Error("net"), { code: "net" });
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || r.status), { code: data.error || "net" });
  return data;
}

function stopTimer() {
  clearTimeout(timer);
  timer = 0;
}

// --- lingua ---

function applyLanguage() {
  document.documentElement.lang = lang;
  $$("[data-t]").forEach((el) => { el.textContent = t(el.dataset.t); });
  $$("[data-t-title]").forEach((el) => { el.title = t(el.dataset.tTitle); el.setAttribute("aria-label", el.title); });
  $("#lang").textContent = t("other_lang");
  $("#steps").innerHTML = STEPS.map((s) => `<span data-dot="${s}">${t("step_" + s)}</span>`).join("");
  markSteps();
  if (pcData) renderPc(false);
  renderBrain();
  if (dlData) renderDownload();
}

// --- passi ---

function markSteps() {
  $$("#steps [data-dot]").forEach((el, i) => {
    el.classList.toggle("on", i === current);
    el.classList.toggle("past", i < current);
  });
}

function go(i) {
  stopTimer();
  current = i;
  const name = STEPS[i] ?? "bye";
  document.body.dataset.current = name;
  $$("[data-step]").forEach((el) => {
    const idx = STEPS.indexOf(el.dataset.step);
    el.classList.toggle("on", el.dataset.step === name);
    el.classList.toggle("past", idx >= 0 && idx < i);
  });
  markSteps();
  if (name !== "test") api("/api/mic/stop", {}).catch(() => {});  // il microfono resta aperto solo nella prova
  const enter = { pc: enterPc, brain: enterBrain, dl: enterDownload, test: enterTest, done: enterDone }[name];
  if (enter) enter();
  const first = $(`[data-step="${name}"] .btn.primary:not(:disabled)`);
  if (first) setTimeout(() => first.focus({ preventScroll: true }), reduced ? 0 : 650);
}

// --- il PC ---

async function enterPc() {
  if (pcData) return renderPc(false);
  $("#pc-again").hidden = true;
  $$("#rows .row").forEach((r) => { r.className = "row"; $(".v", r).textContent = t("pc_wait"); });
  $("#profile").classList.remove("on");
  $('[data-step="pc"] [data-next]').disabled = true;
  try {
    const early = pcEarly;
    pcEarly = null;  // solo la prima volta: "Riprova" deve rifare il controllo davvero
    pcData = await (early || api("/api/pc"));
  } catch (e) {
    $("#profile").className = "profile on blocked";
    $("#profile b").textContent = t("e_net");
    $("#profile span").textContent = "";
    $("#profile .notes").innerHTML = "";
    $("#pc-again").hidden = false;
    return;
  }
  renderPc(true);
}

function renderPc(animate) {
  const { facts, profile } = pcData;
  const bad = new Set(profile.blockers), notes = new Set(profile.notes);
  const gpu = facts.gpu ? `${facts.gpu.name} · ${num(facts.gpu.vram_mb / 1024)} GB` : t("no_gpu");
  const disk = profile.download_mb ? t("free_need", { free: size(facts.free_mb * 2 ** 20), need: size(profile.need_mb * 2 ** 20) })
    : t("nothing_to_get", { free: size(facts.free_mb * 2 ** 20) });
  const audio = t(facts.mics === 1 ? "mics" : "mics_n", { m: facts.mics, s: facts.speakers });
  const rows = {
    gpu: [gpu, bad.has("voce_solo_nvidia") ? "bad" : notes.has("da_misurare") ? "warn" : "ok"],
    ram: [`${Math.round(facts.ram_mb / 1024)} GB`, bad.has("poca_ram") ? "bad" : "ok"],  // 15,9 GB è un PC da 16
    disk: [disk, bad.has("poco_spazio") ? "bad" : "ok"],
    audio: [audio, notes.has("niente_microfono") || notes.has("niente_casse") ? "warn" : "ok"],
    net: [t(facts.online ? "net_ok" : "net_no"), bad.has("niente_rete") ? "bad" : facts.online ? "ok" : "warn"],
  };
  const els = $$("#rows .row");
  els.forEach((el, i) => {
    const [value, state] = rows[el.dataset.row];
    $(".v", el).textContent = value;
    $(".v", el).title = value;
    const show = () => { el.className = `row ${state}`; };
    if (animate && !reduced) { el.className = "row"; setTimeout(show, 260 * (i + 1)); } else show();
  });
  const box = $("#profile");
  box.className = "profile" + (profile.ok ? "" : " blocked");
  $("b", box).textContent = profile.ok ? t("p_" + profile.key) : t("p_blocked");
  $("span", box).textContent = t(`p_${profile.key}_s`);
  $(".notes", box).innerHTML = "";
  for (const b of profile.blockers) addNote(t("b_" + b, { need: size(profile.need_mb * 2 ** 20) }), true);
  for (const n of profile.notes) if (!(n === "voce_lenta" && bad.has("voce_solo_nvidia"))) addNote(t("n_" + n), false);
  const reveal = () => box.classList.add("on");
  if (animate && !reduced) setTimeout(reveal, 260 * (els.length + 1.5)); else reveal();
  $('[data-step="pc"] [data-next]').disabled = !profile.ok;
  $("#pc-again").hidden = profile.ok;
}

function addNote(text, problem) {
  const li = document.createElement("li");
  li.textContent = text;
  if (problem) li.className = "problem";
  $("#profile .notes").appendChild(li);
}

// --- il cervello ---

async function enterBrain() {
  renderBrain();
  await refreshBrain();
}

async function refreshBrain() {
  try {
    const early = brainEarly;
    brainEarly = null;
    brainData = await (early || api("/api/brain"));
  } catch {
    brainData = null;
  }
  if (claudeWait === "install" && brainData?.claude.installed) claudeWait = "";
  if (claudeWait === "login" && brainData?.claude.logged_in) claudeWait = "";
  renderBrain();
  // si richiede finché si aspetta l'installazione o l'accesso, e anche se la risposta non è arrivata (riprova)
  if ((claudeWait || !brainData) && STEPS[current] === "brain") {
    timer = setTimeout(refreshBrain, claudeWait === "install" || !brainData ? 3000 : 2000);
  }
}

function renderBrain() {
  $$(".choice").forEach((el) => el.setAttribute("aria-checked", String(el.dataset.choice === choice)));
  const btn = $("#claude-action");
  const c = brainData?.claude;
  let label = t("c_login"), state = "";
  if (!brainData) [label, state] = [t("c_checking"), "wait"];
  if (claudeWait === "install") [label, state] = [t("c_installing"), "wait"];
  else if (claudeWait === "login") [label, state] = [t("c_wait"), "wait"];
  else if (c && !c.installed) label = t("c_install");
  else if (c?.logged_in) [label, state] = [c.plan ? `${t("c_ok")} · ${c.plan[0].toUpperCase()}${c.plan.slice(1)}` : t("c_ok"), "ok"];
  btn.textContent = label;
  btn.dataset.state = state;
  btn.disabled = state !== "" || !brainData;
  $("#brain-hint").textContent = choice === "opencode" ? t("h_free") : c && !c.logged_in ? t("h_claude_later") : "";
}

async function claudeAction(ev) {
  ev.stopPropagation();
  choice = "claude";
  const c = brainData?.claude;
  if (!c || claudeWait || c.logged_in) return renderBrain();
  claudeWait = c.installed ? "login" : "install";
  renderBrain();
  try {
    await api(c.installed ? "/api/brain/login" : "/api/brain/install", {});
  } catch {
    claudeWait = "";
  }
  stopTimer();
  refreshBrain();
}

async function brainNext() {
  try {
    await api("/api/brain", { provider: choice });
  } catch {
    $("#brain-hint").textContent = t("e_net");
    return;
  }
  go(current + 1);
}

// --- il download ---

async function enterDownload() {
  try {
    dlData = dlData && ["running", "done"].includes(dlData.state) ? await api("/api/download") : await api("/api/download", {});
  } catch (e) {
    if (e.code === "controllo_pc" || `b_${e.code}` in T[lang]) return go(STEPS.indexOf("pc"));  // PC da ricontrollare
    showDownloadError(t(e.code === "net" ? "e_net" : "e_" + e.code));
    return;
  }
  renderDownload();
  pollDownload();
}

async function pollDownload() {
  if (STEPS[current] !== "dl") return;
  if (dlData?.state === "running") {
    timer = setTimeout(async () => {
      try { dlData = await api("/api/download"); } catch { /* il prossimo giro riprova */ }
      renderDownload();
      pollDownload();
    }, 500);
  }
}

function renderDownload() {
  const box = $("#dls");
  if (box.childElementCount !== dlData.groups.length) {
    box.innerHTML = dlData.groups.map((g) =>
      `<div class="dl" data-group="${g.key}"><span class="n"></span><span class="sz"></span><i class="track"></i></div>`).join("");
  }
  for (const g of dlData.groups) {
    const el = $(`[data-group="${g.key}"]`, box);
    const done = g.total > 0 && g.done >= g.total;
    el.classList.toggle("done", done);
    el.style.setProperty("--p", g.total ? Math.min(1, g.done / g.total) : 1);
    $(".n", el).textContent = t("dl_" + g.key);
    $(".sz", el).textContent = done ? t("dl_done") : `${size(g.done)} / ${size(g.total)}`;
  }
  const p = dlData.total ? dlData.done / dlData.total : 0;
  document.documentElement.style.setProperty("--progress", dlData.state === "done" ? 1 : p);
  const left = dlData.total - dlData.done;
  let total = "";
  if (dlData.state === "done") total = t("dl_all");
  else if (dlData.state === "running" && dlData.total) {
    total = t("dl_left", { left: size(left) });
    if (dlData.speed > 0) {
      const secs = left / dlData.speed;
      total += ` · ${t("dl_speed", { speed: size(dlData.speed) })} · ` +
        (secs < 60 ? t("dl_eta_s") : t("dl_eta_min", { n: Math.ceil(secs / 60) }));
    }
  }
  $("#total").textContent = total;
  if (dlData.state === "error") showDownloadError(`${t("dl_err")} ${t(dlData.permanent ? "dl_err_perm" : "dl_err_retry")}`, dlData.error);
  else { $("#dl-error").hidden = true; $("#dl-again").hidden = true; }
  $('[data-step="dl"] [data-next]').disabled = dlData.state !== "done";
}

function showDownloadError(text, detail = "") {
  const el = $("#dl-error");
  el.textContent = text;
  el.title = detail;
  el.hidden = false;
  $("#dl-again").hidden = false;
}

// --- prova di microfono e casse ---

let audioData = null;

function fillSelect(sel, names, chosen) {
  sel.innerHTML = "";
  for (const [value, label] of [["", t("test_default")], ...names.map((n) => [n, n])]) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = label;
    o.selected = value === chosen;
    sel.appendChild(o);
  }
}

async function enterTest() {
  $("#test-error").hidden = true;
  $("#mic-meter").classList.remove("ok");
  try {
    audioData = await api("/api/audio");
  } catch (e) {
    return showTestError(t(e.code === "net" ? "e_net" : "e_" + e.code));
  }
  fillSelect($("#mic-select"), audioData.inputs, audioData.input);
  fillSelect($("#out-select"), audioData.outputs, audioData.output);
  startMic();
}

async function startMic() {
  try {
    const m = await api("/api/mic", {});
    if (m.error) return showTestError(t("e_mic"));
  } catch {
    return showTestError(t("e_net"));
  }
  pollMic();
}

function pollMic() {
  if (STEPS[current] !== "test") return;
  timer = setTimeout(async () => {
    let m = null;
    try { m = await api("/api/mic"); } catch { /* il prossimo giro riprova */ }
    if (m) {
      $("#mic-meter").style.setProperty("--level", m.level);
      if (m.heard) $("#mic-meter").classList.add("ok");
      if (!m.running) return;  // chiuso da solo dopo un po': si riapre cambiando microfono
    }
    pollMic();
  }, 100);
}

async function chooseDevices() {
  $("#test-error").hidden = true;
  try {
    await api("/api/audio", { input: $("#mic-select").value, output: $("#out-select").value });
  } catch (e) {
    return showTestError(t(e.code === "net" ? "e_net" : "e_" + e.code));
  }
  stopTimer();
  $("#mic-meter").classList.remove("ok");
  startMic();
}

async function playTest() {
  $("#test-error").hidden = true;
  let r;
  try {
    r = await api("/api/play", {});
  } catch (e) {
    return showTestError(t(e.code === "net" ? "e_net" : "e_" + e.code));
  }
  const line = $("#test-line");
  line.textContent = r.line;
  line.classList.remove("show");
  void line.offsetWidth;  // la stessa animazione può ripartire
  line.classList.add("show");
  const out = $("#out-meter"), start = performance.now(), ms = r.seconds * 1000;
  const wave = (now) => {  // le casse non si misurano: la barra segue la frase mentre suona
    const k = (now - start) / ms;
    out.style.setProperty("--level", k >= 1 ? 0 : (0.55 + 0.35 * Math.sin(now / 90)) * Math.sin(Math.PI * k) ** 0.3);
    if (k < 1) requestAnimationFrame(wave);
  };
  requestAnimationFrame(wave);
}

function showTestError(text) {
  const el = $("#test-error");
  el.textContent = text;
  el.hidden = false;
}

// --- pronto ---

function enterDone() {
  $("#done-error").hidden = true;
}

async function finish() {
  const btn = $("#go");
  btn.disabled = true;
  try {
    const r = await api("/api/finish", { name: $("#name").value, autostart: $("#autostart").getAttribute("aria-checked") === "true" });
    if (r.ok) return go(STEPS.length);
    showDoneError(t("e_" + r.problem));
  } catch (e) {
    showDoneError(t(e.code === "net" ? "e_net" : "e_" + e.code));
  } finally {
    btn.disabled = false;
  }
}

function showDoneError(text) {
  const el = $("#done-error");
  el.textContent = text;
  el.hidden = false;
}

// --- avvio ---

async function main() {
  let start = { lang: "it", name: "", autostart: true, provider: "claude" };
  try { start = await api("/api/start"); } catch { /* si parte coi valori di base */ }
  const windows = (navigator.language || "it").toLowerCase();
  lang = start.lang === "en" ? "en" : windows.startsWith("it") ? "it" : "en";
  choice = start.provider;
  $("#name").value = start.name || "";
  api("/api/lang", { lang }).catch(() => {});
  applyLanguage();
  pcEarly = api("/api/pc");
  brainEarly = api("/api/brain");
  pcEarly.catch(() => {});  // gli errori li gestisce il passo, quando arriva
  brainEarly.catch(() => {});

  $("#lang").onclick = () => {
    lang = lang === "it" ? "en" : "it";
    api("/api/lang", { lang }).catch(() => {});
    applyLanguage();
  };
  $("#close").onclick = () => { api("/api/quit", {}).catch(() => {}); };
  $$("[data-prev]").forEach((b) => { b.onclick = () => go(Math.max(0, current - 1)); });
  $('[data-step="hello"] [data-next]').onclick = () => go(1);
  $('[data-step="pc"] [data-next]').onclick = () => go(2);
  $('[data-step="brain"] [data-next]').onclick = brainNext;
  $('[data-step="dl"] [data-next]').onclick = () => go(4);
  $('[data-step="test"] [data-next]').onclick = () => go(5);
  $("#mic-select").onchange = chooseDevices;
  $("#out-select").onchange = chooseDevices;
  $("#play").onclick = playTest;
  $("#pc-again").onclick = () => { pcData = null; enterPc(); };
  $("#dl-again").onclick = async () => {
    $("#dl-again").hidden = true;
    try { dlData = await api("/api/download", {}); } catch (e) { return showDownloadError(t(e.code === "net" ? "e_net" : "e_" + e.code)); }
    renderDownload();
    pollDownload();
  };
  $$(".choice").forEach((el) => {
    const pick = () => { choice = el.dataset.choice; renderBrain(); };
    el.onclick = pick;
    el.onkeydown = (ev) => { if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); pick(); } };
  });
  $("#claude-action").onclick = claudeAction;
  $("#autostart").onclick = (ev) => {
    const el = ev.currentTarget;
    el.setAttribute("aria-checked", String(el.getAttribute("aria-checked") !== "true"));
  };
  $("#go").onclick = finish;
  $("#name").onkeydown = (ev) => { if (ev.key === "Enter") finish(); };
  go(0);
}

main();
