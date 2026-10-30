/* Prototipo condiviso dalle tre direzioni del primo avvio: testi in italiano e inglese, passi della procedura,
   controllo del PC, scelta del cervello, download, prova del microfono, installatore. Ogni direzione ha il suo
   aspetto (HTML e CSS suoi) e usa gli stessi agganci: data-t, data-step, data-next, data-check, data-dl, ...
   I dati sono finti: servono a vedere tempi, stati e animazioni, non toccano il PC. */
(() => {
  "use strict";

  const T = {
    it: {
      lang: "Italiano", other: "English",
      // installatore
      inst_title: "Installa Jarvis",
      inst_sub: "Assistente vocale per Windows",
      inst_where: "Cartella", inst_path: "AppData\\Local\\Programs\\Jarvis",
      inst_space: "Ora servono circa 1,2 GB. I modelli, circa 5 GB, li scarica il primo avvio.",
      inst_desktop: "Collegamento sul desktop",
      inst_license: "Installando accetti la licenza Apache 2.0 e quelle dei componenti.",
      inst_go: "Installa", inst_cancel: "Annulla",
      inst_s1: "Copio i file", inst_s2: "Preparo Python", inst_s3: "Installo i componenti", inst_s4: "Ultimi ritocchi",
      inst_done: "Installazione completata", inst_open: "Apri Jarvis",
      // primo avvio
      step_hello: "Benvenuto", step_pc: "Il tuo PC", step_brain: "Cervello", step_voice: "Voce",
      step_dl: "Download", step_test: "Prova", step_done: "Pronto",
      hello_t: "Ciao. Sono Jarvis.",
      hello_s: "Due minuti per prepararmi, poi il resto me lo dici a voce.",
      hello_go: "Iniziamo",
      pc_t: "Guardo il tuo PC",
      pc_s: "Scelgo i modelli giusti per la tua scheda video e la tua memoria.",
      pc_os: "Sistema", pc_gpu: "Scheda video", pc_ram: "Memoria", pc_disk: "Spazio libero", pc_mic: "Microfono",
      pc_net: "Connessione",
      v_os: "Windows 11 Pro", v_gpu: "NVIDIA GeForce RTX 4060 · 8 GB", v_ram: "32 GB", v_disk: "214 GB",
      v_mic: "Microfono predefinito", v_net: "Attiva",
      pc_profile: "Profilo completo",
      pc_profile_s: "Voce e orecchie sulla scheda video: risposte in circa due secondi.",
      brain_t: "Chi pensa per me",
      brain_s: "Puoi cambiarlo quando vuoi, anche a voce.",
      b_pro: "Claude, con il tuo abbonamento", b_pro_s: "Pro o Max. Accedi su claude.ai dal browser: la password non passa da qui.",
      b_api: "Claude, con una chiave API", b_api_s: "Paghi a consumo. La chiave resta cifrata su questo PC.",
      b_free: "OpenCode, gratis", b_free_s: "Nessun account. Modelli gratuiti, meno precisi di Claude.",
      b_local: "Sul tuo PC", b_local_s: "Con Ollama, senza internet per pensare. Qui la memoria video non basta.",
      b_best: "Consigliato", b_login: "Accedi con Claude", b_wait: "Completa l'accesso nel browser…", b_ok: "Collegato",
      voice_t: "La mia voce",
      voice_s: "Una voce mia di base. Se vuoi, scarichi a parte quella del film.",
      vo_base: "Voce di Jarvis", vo_base_s: "Calma, chiara, creata per questo progetto.",
      vo_film: "Voce del film", vo_film_s: "Clonata dal doppiaggio italiano. Pacchetto a parte, solo per uso personale.",
      vo_listen: "Ascolta",
      dl_t: "Scarico ciò che serve",
      dl_s: "Puoi chiudere la finestra: riprende da dove si era fermato.",
      dl_engine: "Motore audio", dl_ears: "Orecchie", dl_voice: "Voce", dl_who: "Chi parla", dl_mem: "Memoria",
      dl_turn: "Turni di parola", dl_film: "Voce del film", dl_done: "Fatto", dl_left: "rimanenti",
      test_t: "Una prova",
      test_s: "Di' qualcosa: la barra deve muoversi. Poi ascolta come parlo.",
      test_mic: "Microfono", test_out: "Casse o cuffie", test_play: "Ascolta Jarvis",
      test_line: "Buongiorno. Tutti i sistemi sono operativi.",
      done_t: "Sono pronto.",
      done_s: "Da adesso chiamami per nome: “Jarvis, …”. Il resto lo sistemiamo parlando.",
      done_auto: "Avvia con Windows", done_go: "Inizia",
      back: "Indietro", next: "Avanti", skip: "Salta",
      // tavola
      board_icon: "Icona", board_inst: "Installazione", board_setup: "Primo avvio",
      board_play: "Riproduci", board_pause: "Pausa",
      sizes: "Misure di Windows: 256, 64, 48, 32, 24, 16 px",
    },
    en: {
      lang: "English", other: "Italiano",
      inst_title: "Install Jarvis",
      inst_sub: "Voice assistant for Windows",
      inst_where: "Folder", inst_path: "AppData\\Local\\Programs\\Jarvis",
      inst_space: "About 1.2 GB now. The models, about 5 GB, download on first launch.",
      inst_desktop: "Desktop shortcut",
      inst_license: "By installing you accept the Apache 2.0 license and those of its components.",
      inst_go: "Install", inst_cancel: "Cancel",
      inst_s1: "Copying files", inst_s2: "Setting up Python", inst_s3: "Installing components", inst_s4: "Finishing up",
      inst_done: "Installation complete", inst_open: "Open Jarvis",
      step_hello: "Welcome", step_pc: "Your PC", step_brain: "Brain", step_voice: "Voice",
      step_dl: "Download", step_test: "Test", step_done: "Ready",
      hello_t: "Hello. I'm Jarvis.",
      hello_s: "Two minutes to get me ready. The rest you can tell me out loud.",
      hello_go: "Let's begin",
      pc_t: "Looking at your PC",
      pc_s: "I pick the right models for your graphics card and memory.",
      pc_os: "System", pc_gpu: "Graphics", pc_ram: "Memory", pc_disk: "Free space", pc_mic: "Microphone",
      pc_net: "Connection",
      v_os: "Windows 11 Pro", v_gpu: "NVIDIA GeForce RTX 4060 · 8 GB", v_ram: "32 GB", v_disk: "214 GB",
      v_mic: "Default microphone", v_net: "Online",
      pc_profile: "Full profile",
      pc_profile_s: "Voice and hearing on the graphics card: replies in about two seconds.",
      brain_t: "Who does the thinking",
      brain_s: "You can change it any time, even by voice.",
      b_pro: "Claude, with your subscription", b_pro_s: "Pro or Max. Sign in on claude.ai in your browser: your password never passes through here.",
      b_api: "Claude, with an API key", b_api_s: "Pay as you go. The key stays encrypted on this PC.",
      b_free: "OpenCode, free", b_free_s: "No account. Free models, less accurate than Claude.",
      b_local: "On your PC", b_local_s: "With Ollama, no internet needed to think. Not enough video memory here.",
      b_best: "Recommended", b_login: "Sign in with Claude", b_wait: "Finish signing in in your browser…", b_ok: "Connected",
      voice_t: "My voice",
      voice_s: "A voice of my own to start with. If you like, download the film voice separately.",
      vo_base: "Jarvis voice", vo_base_s: "Calm and clear, made for this project.",
      vo_film: "Film voice", vo_film_s: "Cloned from the original film. Separate package, personal use only.",
      vo_listen: "Listen",
      dl_t: "Downloading what I need",
      dl_s: "You can close this window: it picks up where it left off.",
      dl_engine: "Audio engine", dl_ears: "Hearing", dl_voice: "Voice", dl_who: "Who's speaking", dl_mem: "Memory",
      dl_turn: "Turn taking", dl_film: "Film voice", dl_done: "Done", dl_left: "left",
      test_t: "A quick test",
      test_s: "Say something: the bar should move. Then hear how I sound.",
      test_mic: "Microphone", test_out: "Speakers or headphones", test_play: "Hear Jarvis",
      test_line: "Good morning. All systems are online.",
      done_t: "I'm ready.",
      done_s: "From now on, call me by name: “Jarvis, …”. We'll sort out the rest by talking.",
      done_auto: "Start with Windows", done_go: "Start",
      back: "Back", next: "Next", skip: "Skip",
      board_icon: "Icon", board_inst: "Install", board_setup: "First launch",
      board_play: "Play", board_pause: "Pause",
      sizes: "Windows sizes: 256, 64, 48, 32, 24, 16 px",
    },
  };

  const DOWNLOADS = [  // MB, come i file veri (docs/STATO.md)
    { key: "dl_engine", mb: 264 }, { key: "dl_ears", mb: 717 }, { key: "dl_voice", mb: 2600 },
    { key: "dl_who", mb: 309 }, { key: "dl_mem", mb: 208 }, { key: "dl_turn", mb: 31 },
    { key: "dl_film", mb: 1, film: true },
  ];
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const fmt = (lang, mb) => {
    const gb = mb >= 1000;
    const n = gb ? mb / 1000 : mb;
    const s = n.toLocaleString(lang === "it" ? "it-IT" : "en-US", { maximumFractionDigits: gb ? 1 : 0 });
    return `${s} ${gb ? "GB" : "MB"}`;
  };

  // la lingua del PC, finché non la cambi (come farà Jarvis: lingua di Windows, oppure quella che gli dici)
  let lang = "";
  try { lang = localStorage.getItem("jarvis-lang") || ""; } catch { /* anteprime senza archivio */ }
  if (!T[lang]) lang = (navigator.language || "it").toLowerCase().startsWith("it") ? "it" : "en";

  function translate() {
    document.documentElement.lang = lang;
    for (const el of $$("[data-t]")) el.textContent = T[lang][el.dataset.t] ?? el.dataset.t;
    for (const el of $$("[data-lang-switch]")) el.textContent = T[lang].other;
    $$("[data-dl]").forEach(renderDownload);
    renderTotal();
  }

  function setLang(l) {
    lang = l;
    try { localStorage.setItem("jarvis-lang", l); } catch { /* niente archivio: vale per questa pagina */ }
    translate();
  }

  // ------------------------------------------------------------------ passi del primo avvio

  const wizard = $("[data-wizard]");
  const steps = wizard ? $$("[data-step]", wizard) : [];
  let current = 0;
  const enter = {};  // funzioni chiamate quando si entra in un passo (per nome)

  function go(i) {
    if (!steps.length) return;
    i = Math.max(0, Math.min(steps.length - 1, i));
    steps.forEach((s, k) => {
      s.classList.toggle("on", k === i);
      s.classList.toggle("past", k < i);
      s.setAttribute("aria-hidden", k === i ? "false" : "true");
    });
    $$("[data-dot]").forEach((d, k) => { d.classList.toggle("on", k === i); d.classList.toggle("past", k < i); });
    wizard.style.setProperty("--progress", steps.length > 1 ? i / (steps.length - 1) : 0);
    wizard.dataset.current = steps[i].dataset.step;
    current = i;
    enter[steps[i].dataset.step]?.(steps[i]);
  }

  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-next],[data-prev],[data-goto],[data-choice],[data-login],[data-toggle],[data-lang-switch],[data-install],[data-play],[data-say]");
    if (!t) return;
    if (t.hasAttribute("data-lang-switch")) return setLang(lang === "it" ? "en" : "it");
    if (t.hasAttribute("data-next")) return go(current + 1);
    if (t.hasAttribute("data-prev")) return go(current - 1);
    if (t.hasAttribute("data-goto")) return go(Number(t.dataset.goto));
    if (t.hasAttribute("data-choice")) return choose(t);
    if (t.hasAttribute("data-login")) return login(t);
    if (t.hasAttribute("data-toggle")) return toggle(t);
    if (t.hasAttribute("data-install")) return install();
    if (t.hasAttribute("data-play")) return setAutoplay(!autoplay);
    if (t.hasAttribute("data-say")) return say(t);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") go(current + 1);
    if (e.key === "ArrowLeft") go(current - 1);
  });

  function choose(el) {
    const group = el.closest("[data-group]");
    if (el.classList.contains("disabled")) return;
    $$("[data-choice]", group).forEach((c) => {
      c.classList.toggle("on", c === el);
      c.setAttribute("aria-checked", c === el ? "true" : "false");
    });
    if (group.dataset.group === "voice") filmVoice(el.dataset.choice === "film");
  }

  function toggle(el) {
    el.classList.toggle("on");
    el.setAttribute("aria-checked", el.classList.contains("on") ? "true" : "false");
  }

  function login(btn) {
    if (btn.dataset.state) return;
    btn.dataset.state = "wait";
    btn.querySelector("[data-t]").dataset.t = "b_wait";
    translate();
    setTimeout(() => {
      btn.dataset.state = "ok";
      btn.querySelector("[data-t]").dataset.t = "b_ok";
      translate();
    }, reduced ? 200 : 2200);
  }

  // il pc: le righe si confermano una alla volta, poi il profilo
  enter.pc = (step) => {
    const rows = $$("[data-check]", step);
    rows.forEach((r) => r.classList.remove("ok"));
    $("[data-profile]", step)?.classList.remove("on");
    rows.forEach((r, k) => setTimeout(() => r.classList.add("ok"), (reduced ? 40 : 380) * (k + 1)));
    setTimeout(() => $("[data-profile]", step)?.classList.add("on"), (reduced ? 40 : 380) * (rows.length + 1.5));
  };

  // download: velocità che varia come una connessione vera, ripresa se si torna indietro e avanti
  const progress = new Map();  // chiave -> MB scaricati
  let film = false;
  let dlTimer = null;

  function filmVoice(on) {
    film = on;
    $$("[data-dl='dl_film']").forEach((el) => el.toggleAttribute("hidden", !on));
    renderTotal();
  }

  function renderDownload(el) {
    const d = DOWNLOADS.find((x) => x.key === el.dataset.dl);
    if (!d) return;
    const got = progress.get(d.key) || 0;
    const p = Math.min(1, got / d.mb);
    el.style.setProperty("--p", p);
    el.classList.toggle("done", p >= 1);
    const size = $("[data-size]", el);
    if (size) size.textContent = p >= 1 ? T[lang].dl_done : `${fmt(lang, got)} / ${fmt(lang, d.mb)}`;
  }

  function wanted() { return DOWNLOADS.filter((d) => !d.film || film); }

  function renderTotal() {
    const all = wanted();
    const total = all.reduce((s, d) => s + d.mb, 0);
    const got = all.reduce((s, d) => s + Math.min(d.mb, progress.get(d.key) || 0), 0);
    for (const el of $$("[data-total]")) {
      el.style.setProperty("--p", total ? got / total : 0);
      const txt = $("[data-total-text]", el) || el;
      const left = Math.max(0, total - got);
      txt.textContent = left > 0 ? `${fmt(lang, left)} ${T[lang].dl_left}` : T[lang].dl_done;
    }
  }

  enter.dl = () => {
    clearInterval(dlTimer);
    let t = 0;
    dlTimer = setInterval(() => {
      t += 1;
      const speed = (reduced ? 900 : 46) * (0.75 + 0.5 * Math.sin(t / 7) ** 2);  // MB per giro di 60 ms
      let budget = speed;
      for (const d of wanted()) {  // uno alla volta, come il download vero (un file per volta, ripresa per file)
        const got = progress.get(d.key) || 0;
        if (got >= d.mb) continue;
        const take = Math.min(budget, d.mb - got);
        progress.set(d.key, got + take);
        budget -= take;
        if (budget <= 0) break;
      }
      $$("[data-dl]").forEach(renderDownload);
      renderTotal();
      if (wanted().every((d) => (progress.get(d.key) || 0) >= d.mb)) clearInterval(dlTimer);
    }, 60);
  };

  // prova: il microfono si muove come una voce (sillabe, pause), l'uscita quando parla Jarvis
  let speaking = 0;
  function say(btn) {
    speaking = performance.now() + 2600;
    btn.classList.add("on");
    const line = $("[data-line]");
    if (line) { line.classList.remove("show"); void line.offsetWidth; line.classList.add("show"); }
    setTimeout(() => btn.classList.remove("on"), 2600);
  }
  function meters(now) {
    const t = now / 1000;
    const syll = Math.max(0, Math.sin(t * 7.3) * Math.sin(t * 1.9 + 1) * Math.sin(t * 0.7));
    const mic = Math.min(1, 0.04 + syll * 1.4 + Math.random() * 0.03);
    const out = now < speaking ? Math.min(1, 0.2 + Math.abs(Math.sin(t * 9.1) * Math.sin(t * 2.3)) * 0.8) : 0.02;
    for (const el of $$("[data-meter='mic']")) el.style.setProperty("--level", mic.toFixed(3));
    for (const el of $$("[data-meter='out']")) el.style.setProperty("--level", out.toFixed(3));
    for (const el of $$("[data-wave]")) el.style.setProperty("--level", (now < speaking ? out : mic * 0.4).toFixed(3));
    requestAnimationFrame(meters);
  }
  requestAnimationFrame(meters);

  // ------------------------------------------------------------------ installatore

  let installing = false;
  function install() {
    const box = $("[data-installer]");
    if (!box || installing) return;
    installing = true;
    box.dataset.state = "work";
    const keys = ["inst_s1", "inst_s2", "inst_s3", "inst_s4"];
    const line = $("[data-inst-step]", box);
    const total = reduced ? 600 : 5200;
    const t0 = performance.now();
    const tick = (now) => {
      const p = Math.min(1, (now - t0) / total);
      const eased = 1 - (1 - p) ** 2.2;
      box.style.setProperty("--p", eased.toFixed(4));
      const k = keys[Math.min(keys.length - 1, Math.floor(p * keys.length))];
      if (line && line.dataset.t !== k) { line.dataset.t = k; line.textContent = T[lang][k]; }
      if (p < 1) return requestAnimationFrame(tick);
      box.dataset.state = "done";
      installing = false;
    };
    requestAnimationFrame(tick);
  }
  function resetInstaller() {
    const box = $("[data-installer]");
    if (!box) return;
    box.dataset.state = "ready";
    box.style.setProperty("--p", 0);
  }

  // ------------------------------------------------------------------ riproduzione automatica

  let autoplay = !reduced;
  let playTimer = null;
  const HOLD = { hello: 3200, pc: 4200, brain: 4200, voice: 3600, dl: 7200, test: 4800, done: 4200 };
  function setAutoplay(on) {
    autoplay = on;
    $$("[data-play] [data-t]").forEach((el) => { el.dataset.t = on ? "board_pause" : "board_play"; });
    translate();
    clearTimeout(playTimer);
    if (on) schedule();
  }
  function schedule() {
    clearTimeout(playTimer);
    if (!autoplay || !steps.length) return;
    const name = steps[current].dataset.step;
    playTimer = setTimeout(() => {
      if (!autoplay) return;
      if (name === "brain") login($("[data-login]") || document.createElement("i"));
      if (name === "test") say($("[data-say]") || document.createElement("i"));
      if (current === steps.length - 1) {  // da capo: download e installatore ripartono da zero
        progress.clear();
        resetInstaller();
        go(0);
        install();
      } else {
        go(current + 1);
      }
      schedule();
    }, HOLD[name] || 3500);
  }
  // un clic dell'utente ferma la riproduzione: guarda lui
  document.addEventListener("pointerdown", (e) => {
    if (!e.target.closest("[data-play],[data-lang-switch]") && e.target.closest("[data-wizard],[data-installer]")) setAutoplay(false);
  });

  // ------------------------------------------------------------------ scala delle finestre finte

  function fit() {
    for (const frame of $$("[data-fit]")) {
      const w = Number(frame.dataset.fit);
      const box = frame.parentElement;
      const s = Math.min(1, box.clientWidth / w);
      frame.style.transform = `scale(${s})`;
      box.style.height = `${frame.offsetHeight * s}px`;
    }
  }
  addEventListener("resize", fit);

  translate();
  filmVoice(false);
  resetInstaller();
  fit();
  go(0);
  setAutoplay(autoplay);
  if (autoplay) setTimeout(install, 900);
})();
