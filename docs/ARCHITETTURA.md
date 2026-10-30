# Jarvis — architettura

Documento di riferimento. Ogni scelta qui sotto è stata presa dopo una verifica (ricerca o misura):
le misure, le trappole e lo stato attuale stanno in `docs/STATO.md`, i banchi di prova in `scripts/bench/`.

## Obiettivo

Un assistente vocale che si comporta come JARVIS: lo chiami, ti risponde in meno di un secondo,
fa davvero le cose (PC, browser, file, ricerche, codice), lavora in background mentre continui a
parlargli, puoi interromperlo in qualsiasi momento, ricorda, e si ripara da solo. Gratuito: voce
tutta in locale, cervello a scelta tra Claude (abbonamento, via Claude Agent SDK) e modelli locali.

## Mappa dei processi

```
            ┌────────────────────────── guardiano (jarvis.supervisor) ───────────────────────────┐
            │ avvia, sorveglia, riavvia con backoff, rollback dopo un auto-aggiornamento fallito │
            └──────┬───────────────────────────┬───────────────────────────────┬─────────────────┘
                   │                           │                               │
          ┌────────▼─────────┐        ┌────────▼────────┐             ┌────────▼─────────┐
          │  core (asyncio)  │◄──ws──►│  finestra UI    │             │ audiocpp_server  │
          │  jarvis.core     │        │  pywebview      │             │ STT + TTS (CUDA) │
          └──┬───┬───┬───┬───┘        └─────────────────┘             └────────▲─────────┘
             │   │   │   └── sotto-agenti: sessioni Claude Agent SDK / loop locale │ HTTP
             │   │   └────── cervello frontale: Claude SDK o server OpenAI-compatibile (Bonsai/Ollama)
             │   └────────── memoria: SQLite + FTS5 + embedding locali
             └────────────── audio: microfono → AEC → VAD → turni → STT ; TTS → altoparlanti
```

Il core non muore mai per colpa di un componente: ogni servizio esterno è un processo figlio con
health check, riavvio automatico e un ripiego (fallback) dichiarato.

## Pipeline vocale

Ingresso, a blocchi di 10 ms a 16 kHz mono:

1. **Cattura** WASAPI (sounddevice), ricampionamento con soxr.
2. **AEC** WebRTC APM (livekit `AudioProcessingModule`): cancella dall'ingresso tutto ciò che esce
   dalle cuffie o dalle casse. Riferimento = la cattura loopback dell'uscita (voce di Jarvis, video,
   musica: `audio/loopback.py`, dal 26/10), oppure i campioni che scriviamo noi se l'uscita di Jarvis
   è un'altra. Anche soppressione rumore e filtro passa-alto.
3. **VAD** Silero (ONNX, CPU, < 1 ms per blocco da 32 ms) con isteresi.
4. **Fine turno**: silenzio minimo + Smart Turn v3.2 (classificatore audio, sa se una pausa è "sto
   pensando" o "ho finito"). Così non ti taglia a metà frase e non aspetta inutilmente.
5. **STT**: parakeet-ultra (rifinitura di Parakeet TDT v3, WER 2,12 % su FLEURS italiano) su
   audio.cpp, sulla scheda video: trascrive l'intera frase a fine turno in 30-75 ms, e ogni 450 ms
   mentre parli per il testo in diretta. Lo streaming vero (Nemotron 3.5) era stato misurato e
   scartato: più errori (7,4 %) senza vantaggi di velocità.

Uscita:

1. Il cervello produce testo in streaming → **segmentatore di frasi** (prima frase anticipata).
2. **Normalizzatore per la voce**: niente markdown, numeri/sigle/unità leggibili, URL e codice
   esclusi dal parlato (vanno sullo schermo).
3. **TTS** su audio.cpp con la voce di Jarvis, una frase alla volta in pipeline (la frase n+1 si
   sintetizza mentre suona la n).
4. **Riproduzione** con volume dinamico (ducking) e taglio istantaneo.

### Interruzione (barge-in)

Mentre Jarvis parla, il VAD gira sull'audio già ripulito dall'eco:
- inizio voce → il volume di Jarvis scende subito (ducking, ~30 ms);
- se entro ~350 ms lo STT conferma parole vere e diverse da quello che Jarvis sta dicendo →
  stop immediato di voce e generazione, si passa ad ascoltare;
- altrimenti (tosse, rumore, eco residua) il volume torna su e Jarvis continua.
Nella cronologia resta quello che Jarvis ha **davvero** detto prima di essere interrotto, così il
cervello sa a che punto l'hai fermato. Comandi brevissimi ("stop", "basta", "zitto") fermano la voce
senza passare dal modello.

## Cervello

Due livelli, come un capo con la sua squadra.

**Frontale (Jarvis)**: risponde a voce, decide, coordina. Deve essere veloce. Ha strumenti rapidi
(ora, meteo, app, volume, appunti, ricerca veloce, memoria, schermo) e strumenti di regia:
`avvia_compito`, `messaggio_a_compito`, `annulla_compito`, `stato_compiti`, `mostra_a_schermo`.
Le cose lunghe (codice, ricerche approfondite, navigazione, operazioni a più passi) le delega.

**Sotto-agenti usa e getta**: ognuno è una sessione indipendente con strumenti completi (shell,
file, web) più tre "mani" (`jarvis/tasks/hands.py`): il browser dell'utente con i suoi accessi
(Claude in Chrome), un browser usa e getta isolato (Playwright MCP) e mouse e tastiera sulle app di
Windows (Windows-MCP, un compito alla volta). Lavora in background e riferisce: avanzamento,
domande, richieste di conferma, risultato. Puoi parlargli attraverso Jarvis mentre lavorano
("aggiungi anche...", "lascia stare la parte X"): il messaggio entra nella loro sessione al passo
successivo.

Fornitori, intercambiabili da impostazioni o a voce:
- **Claude** via Claude Agent SDK (usa il login di Claude Code: abbonamento, nessuna API key).
- **OpenCode Zen**, modelli gratis, via un `opencode serve` di Jarvis (vedi sotto).
- **Locale** via qualunque server OpenAI-compatibile: Bonsai 2 27B (fork llama.cpp PrismML),
  Ollama, LM Studio, llama.cpp. Loop agentico nostro con gli stessi strumenti.

Ripieghi (`jarvis/brain/router.py`): Opus → Haiku → OpenCode (gratis) → Ollama cloud → locale. Un cervello che non
risponde va in pausa fino all'ora di ripristino nota (o un minuto); se il limite è dell'account si saltano tutti i
cervelli di quel fornitore (tutti i Claude, o tutti i modelli gratis di OpenCode per al più un'ora). Un
cervello spento nelle impostazioni si salta senza pausa. Chi subentra riceve un riassunto degli ultimi scambi;
appena possibile si torna al principale. Jarvis lo dice a voce, e non resta mai in silenzio su un errore.

### OpenCode: il ripiego gratuito (dal 28/10)

I modelli gratis di Zen funzionano solo attraverso OpenCode (documentazione di Zen), quindi Jarvis non chiama
l'API di Zen: accende un OpenCode suo e gli parla con l'API HTTP del server (la stessa di `opencode run --attach`).

```
 cervello a voce ──┐                                ┌── sessione "Jarvis" (agente jarvis: solo strumenti jarvis_*)
 sotto-agenti ─────┼─ HTTP + /global/event ─► opencode serve ──┼── una sessione per compito (agente jarvis-lavoro)
 esame, custode ───┘   (127.0.0.1, parola d'ordine)   │        └── esame / custode (sessioni usa e getta)
                                                       └─ MCP remoto ─► ponte di Jarvis (127.0.0.1, gettone)
                                                                         jarvis_* (strumenti a voce), lavoro_*, esame_*
```

- `brain/opencode.py`: processo isolato (`data/opencode`, cartelle XDG proprie, `--pure`, nessuna variabile
  `OPENCODE_*` ereditata, solo il fornitore `opencode`, permessi completi in ogni agente), client con un solo
  flusso di eventi smistato per sessione, `OpenCodeBrain` (parole dai pezzi `message.part.delta`, mai la domanda
  né il ragionamento, strumenti come ToolStart/ToolCall/ToolResult, interruzione con `/abort`, stato `retry` del
  limite gratuito = fornitore in pausa, modello sparito o sovraccarico = il successivo, sessione sparita = una nuova).
- `brain/mcp_bridge.py`: server MCP minimo (streamable HTTP, risposte JSON) che espone le `ToolRegistry` di Jarvis;
  così il cervello su OpenCode usa gli stessi strumenti, con gli stessi permessi delle voci.
- `brain/zen.py`: catalogo dei modelli gratis (da `GET /provider`), note sull'uso dei dati per l'addestramento,
  classifica (promossi per prima parola, poi mai esaminati, poi bocciati; scelta fissa possibile), esame breve.
- `tasks/opencode_worker.py`: `OpenCodeWorker` (permessi "ask" → `SafetyPolicy`, mani, conferma a voce;
  `lavoro_chiedi_conferma` vale APPROVATA solo dopo un sì vero), `FallbackWorker` (Claude Code di norma, OpenCode
  solo con Claude esaurito, anche a metà compito), `ClaudeQuota`.
- `opencode_kit.py`: tutto legato alle impostazioni `brain.opencode*`, rilette a ogni uso.

Strumenti (`jarvis/brain/tools.py`): definiti una volta e adattati a ogni fornitore. Un risultato può contenere
immagini (`ToolOutput`: testi e `Image` nell'ordine di lettura): a Claude arrivano come blocchi immagine MCP
(`image` + base64 + `mimeType`, formato dell'SDK installato); ai cervelli OpenAI-compatibili il testo con una
nota sulle immagini che non vedono. Il cervello frontale alza `MAX_MCP_OUTPUT_TOKENS`, altrimenti Claude Code
toglierebbe le immagini oltre metà del tetto.

Contesto a ogni turno (fuori dal system prompt, così la cache del prompt regge): compiti attivi
con stato, cosa stava dicendo Jarvis se interrotto, eventi recenti, finestra attiva, ora, ricordi
pertinenti. È questo che gli permette di capire "a cosa ti riferisci".

## Memoria

SQLite in `data/jarvis.db` (`jarvis/memory/store.py`):
- **conversazioni**: ogni frase, cercabile per parole (FTS5) e per significato;
- **fatti** sull'utente (preferenze, persone, progetti, abitudini, decisioni), cercabili allo stesso modo.

Significato: EmbeddingGemma 2 (`memory/embed.py`, Google 6/10/2026, Apache 2.0), solo la parte di testo, variante
q4 su processore: 26 ms a frase, niente VRAM. Le somiglianze assolute sono tutte alte, quindi conta quanto un
ricordo *spicca* rispetto a venti "ricordi di fondo" su altre persone (soglia 0,02 per il contesto di ogni turno,
0 quando Jarvis cerca apposta). I vettori delle frasi nuove si calcolano in sottofondo.

Scrittura: esplicita (`ricorda`, "ricordati che...") e automatica: il **custode** (`memory/keeper.py`), dopo
3 minuti di silenzio, dà a un cervello usa e getta (Claude Haiku o il cloud gratuito, poi chiuso) le frasi nuove e i
ricordi che c'entrano, numerati; riceve operazioni in JSON (aggiungi / aggiorna / elimina) e il codice le controlla:
modifiche solo ai ricordi mostrati, mai segreti (password, PIN, IBAN, parole d'ordine), al massimo 12 per volta; se
il cervello non risponde riprova senza perdere frasi. Ogni cambio compare nel registro della finestra.
Lettura: profilo essenziale nel prompt di sistema + ricordi pertinenti nel contesto di ogni turno + `cerca_memoria`.
`dimentica` cancella solo se la richiesta è chiara, altrimenti fa chiedere quale ricordo. Le prove con il
dispositivo simulato usano un database a parte (`data/prove/memoria_prove.db`).

## Chi parla

`jarvis/identity/`: a ogni frase l'impronta vocale (ReDimNet2+, lab260 2026, Apache 2.0, convertito in ONNX da
`scripts/explore/export_redimnet.py`, ~50 ms per secondo di voce sul processore) si calcola mentre si trascrive.
Scelto con `scripts/explore/speaker_arena.py` (voci italiane MLS, frasi da 1-3 s, rumore, eco, microfono scadente).
Le persone stanno in `data/voci.db` (dati biometrici: solo sul PC). Regole dell'utente:
- **l'utente** (proprietario): tutto. **Persone conosciute**: i permessi base più `pc` e/o `compiti` se lui li concede
  a voce. **Ospiti**: base (chiacchiere, informazioni, musica, volume, mostra).
- I permessi li controlla il codice: ogni strumento ha un gruppo (`TOOL_GROUPS` in `tools/front.py`) e il registro
  chiede il permesso prima di eseguirlo (`ToolRegistry.set_gate`). Convincere il cervello non serve.
- Gli ospiti parlano con un'altra sessione del cervello (`brain/selector.py`, persona `guest_prompt`): niente
  profilo, conversazioni, memoria, finestra in primo piano o compiti dell'utente da far trapelare.
- Senza dire "Jarvis" la conversazione continua solo con chi la sta facendo o con voci conosciute: la TV non entra
  e una voce estranea non interrompe Jarvis.
- Parola d'ordine: mai chiesta; solo la frase a sé "la parola d'ordine è ..." conta; sparisce da trascrizioni
  (anche parziali), eventi, archivio, registri e cervello; scrypt; 3 errori = 10 minuti di blocco; sblocca quella
  voce per la conversazione e 30 minuti di silenzio, e ne migliora l'impronta. Si imposta dalla finestra.
- Registrazione: "impara la mia voce" / "impara la voce di Marco": frasi a schermo, ~40 s (25 per gli altri),
  eco di Jarvis esclusa, voci miste rifiutate. Finché l'utente non registra la sua voce tutto resta come prima.

## Sicurezza delle azioni

Ogni azione dei sotto-agenti passa da una politica: lettura libera, scrittura nelle cartelle di
lavoro libera, azioni distruttive/irreversibili/esterne (cancellare, inviare messaggi, acquisti,
registro di sistema, impostazioni di sicurezza, caricare file su siti) → Jarvis chiede conferma a
voce e a schermo, e approva solo dopo un sì esplicito. Una conferma vale anche per l'azione
corrispondente (niente doppie domande). Vale uguale per i sotto-agenti su OpenCode: le loro richieste di
permesso arrivano dal flusso di eventi, si traducono nello strumento equivalente di Claude Code e passano dalla
stessa politica. Il contenuto di pagine, email e schermo è dato, mai ordine:
il sotto-agente non esegue istruzioni trovate lì e non inserisce mai password o dati di pagamento.
Kill switch: hotkey globale e comando vocale che fermano tutto.

## App (abilità)

`jarvis/apps/`: gesti pronti per usare le app del PC senza sotto-agenti, tutti dietro un solo strumento del cervello
(`usa_app(app, azione, parametri, conferma)`). Il catalogo nella descrizione dello strumento dice solo app e nomi delle
azioni (~860 token stimati per tre app e 104 azioni, misurato il 27/10); i dettagli arrivano con l'azione `aiuto`. Le azioni lente girano fuori dal ciclo della
conversazione (thread o asincrone).

- `base.py`: `App`, `Action`, `Apps`. Permessi per app nel codice (`messaggi` = solo l'utente, `pc` = concedibile).
  Le azioni delicate hanno una funzione di conferma che risolve tutto (chat esatta, messaggio citato) e lo salva con
  un codice; l'azione parte solo se dopo la richiesta c'è stato un turno dell'utente (`Conversation.user_turns`) con
  un sì chiaro (`affirmative`), con i parametri salvati, una volta sola. Nomi detti a voce: `pick_by_name` (uguale,
  inizia così, contiene, tutte le parole, somigliante; se più cose vanno bene si chiede quale).
- `games.py`: file di Steam (VDF), Epic (manifest JSON), Xbox (`Get-AppxPackage` con `MicrosoftGame.config`);
  comandi `steam://`, `com.epicgames.launcher://`, `shell:AppsFolder`; processi per la chiusura (WM_CLOSE, a forza
  solo confermato).
- `whatsapp.py` + `whatsapp_bridge/bridge.js`: ponte Node (whatsapp-web.js, Chrome senza finestra, sessione in
  `data/whatsapp`) a righe JSON su stdin/stdout, legato a Jarvis da un Job Object; `WhatsApp` accende il ponte quando
  serve e lo spegne dopo 15 minuti (`apps.whatsapp`), lo collega col QR in una finestra vera, tiene la rubrica in
  cache e avvisa dei messaggi nuovi senza mai leggerne il testo ad alta voce (`apps.whatsapp_avvisi`).
- `gmail.py`: API REST di Gmail con `google-auth` (sessione che rinnova da sola il permesso), consenso OAuth
  "App desktop" nel browser (client copiato dai Download, permesso cifrato con DPAPI in `data/gmail`), mail citate
  col numero dell'ultimo elenco, destinatari cercati per nome nelle mail scambiate; permesso revocato → si
  dimentica e si chiede di ricollegare.

## Video e audio

`jarvis/media/`: Jarvis "guarda" un video senza un modello video, perché Claude vede le immagini e legge il testo.
Un solo strumento, `guarda_video(sorgente | chat, inizio, fine, solo_audio, suoni, domanda)`.

- `fetch.py`: da dove arriva. Percorso o nome detto a voce (cercato nelle cartelle solite con `pick_by_name`,
  "l'ultimo video" = il più recente), indirizzo web scaricato con yt-dlp in una cartella temporanea (video e audio
  separati, così niente FFmpeg; H.264 per primo; firme di YouTube con deno o Node), oppure l'ultimo
  video/vocale/audio di una chat WhatsApp (`apps/whatsapp.fetch_media`, permesso `messaggi` controllato nel codice).
- `watch.py`: con PyAV. Fotogrammi esaminati a intervalli regolari (al massimo 120: solo i fotogrammi chiave se
  sono fitti, altrimenti tutti tranne i B), per ognuno una copia 64×36 in grigio e il JPEG da 768 px; scelta dei
  cambi di scena (differenza di pixel e istogramma, mai due troppo vicini) più fotogrammi sparsi dove la scena non
  cambia; al massimo 20. Audio a 16 kHz mono alle orecchie di Jarvis (`Jarvis.stt`, parakeet-ultra) a pezzi di al
  massimo 30 s tagliati nei 50 ms più silenziosi tra 18 e 30 s; il silenzio vero non si trascrive. Al cervello:
  intestazione, `[fotogramma i di n, m:ss]` + immagine, parlato con l'ora, note (limiti e da dove riprendere).
- `service.py`: lo strumento; impostazioni `media.*` rilette a ogni video.
- `omni.py`: modello locale per musica e suoni, a richiesta (`suoni=true`). `GpuLease` presta la scheda video:
  stesso lucchetto del riposo, aspetta che sia detta la frase già scritta (`ResponseSpeaker.wait_said`), scarica la
  voce, se serve sposta l'udito sul processore, e alla fine rimette tutto (anche con errori e annullamenti). Il
  modello (Audio Flamingo su audio.cpp, oppure Qwen2.5-Omni / LFM3-Omni su llama-server) descrive l'audio un minuto
  alla volta, con i fotogrammi di quel minuto se li sa vedere. Spento finché la gara (`scripts/bench/omni_race.py`)
  non sceglie.

## Programmi (dal 29/10)

`jarvis/scheduler.py`: ciò che Jarvis fa da solo a un'ora, in un giorno o a ripetizione, chiesto a voce con lo
strumento `programma` (`programmi` per l'elenco, `togli_programma` per toglierne uno). Salvati in `data/programmi.db`.

- **Cosa**: `avviso` (un promemoria: all'ora giusta arriva al cervello come [EVENTO] e Jarvis lo dice), `richiesta`
  (un [EVENTO] che chiede al cervello di eseguire la richiesta dell'utente come se gliela facesse adesso, con tutti gli
  strumenti: è il "riassunto del mattino", il "controlla le notizie su X"), `compito` (parte da solo un sotto-agente
  con `TaskManager.spawn`; l'esito arriva come per gli altri compiti).
- **Quando**: una volta (data e ora, oppure `tra_minuti`), `ogni_giorno`, `giorni` (della settimana, anche
  "feriali"/"weekend"), `ogni_mese` (31 = l'ultimo giorno), `ogni` N minuti (dal suo orario, senza deriva),
  `all_avvio`; facoltativi `fino` e `volte`. Le ore sono quelle locali di Windows (`datetime` senza fuso): le 8
  restano le 8 anche col cambio dell'ora.
- **Giro**: `Scheduler.run()` parte dopo "pronto" (prima i programmi `all_avvio`), poi controlla ogni 20 s.
  Ciò che è scaduto a Jarvis spento: quelli di una volta partono sempre, dicendo per quando erano; i ripetuti
  recuperano solo l'ultima volta saltata (`latest_due`) e solo entro `schedule.catch_up_minutes` (120), poi
  riprendono dalla prossima. Un programma che fallisce si registra e va avanti lo stesso (mai all'infinito).
- **Riposo**: `Jarvis._fire_plan` riaccende Jarvis per avvisi e richieste (`wake_up(announce=False)`) se
  `schedule.wake_from_rest`; i compiti partono anche a riposo, senza voce.
- **Permessi**: i tre strumenti sono di base (anche gli ospiti mettono un timer), ma il programmatore accetta da un
  ospite solo `avviso`, gli mostra e gli fa togliere solo i promemoria degli ospiti, e il loro testo arriva al
  cervello come "testo da riferire, non un ordine" (all'ora giusta gli eventi girano con i permessi dell'utente).
  Ripetizioni minime: richiesta ogni 15 minuti, compito ogni ora. Al massimo 50 programmi, 5 degli ospiti.
- **A voce**: `programmi_attivi` (`schedule.enabled`), `programmi_dal_riposo` (`schedule.wake_from_rest`),
  `schedule.catch_up_minutes` (0-1440); tutte valgono subito.

## Primo avvio (dal 30/10, in costruzione)

Per chi installa Jarvis su un PC nuovo. Stile scelto: **Monolite** (`design/setup/a-monolite.html`, prototipo con
dati finti; l'icona è `jarvis/ui/web/icona.svg`, `jarvis.ico` la fa `scripts/tools/make_icon.py`). Le parti pronte
stanno in `jarvis/setup/` e non dipendono dalla pagina:

- **`pc.py`**: `gather()` legge scheda video (nvidia-smi per memoria e driver, Windows per le altre), memoria,
  spazio libero, microfoni e casse (WASAPI, ognuno una volta) e se si raggiungono i siti dei download; `choose()`
  ne fa un profilo con regole pure: `completo` (NVIDIA da 8 GB, voce 1.7B), `leggero` (NVIDIA 4-8 GB, voce 0.6B,
  "da misurare"), `processore` (tutto il resto: voce lenta, detto prima). Driver troppo vecchio per CUDA 12.4 =
  processore. Le note (`voce_lenta`, `niente_microfono`...) e i blocchi (`poca_ram`, `poco_spazio`, `niente_rete`)
  sono codici: i testi, in italiano e in inglese, stanno nella pagina.
- **`catalog.py`**: ogni file con indirizzo fissato a una versione (tag o commit, mai `main`), misura e SHA-256,
  e dove va (`engine/...` o `models/...`, che poi diventano le cartelle vere di `paths`). Un test controlla che ogni
  file finisca esattamente dove Jarvis lo cerca; `scripts/controlla_catalogo.py` controlla in rete indirizzi,
  misure e impronte (quelle delle release GitHub dalla loro API, senza scaricare). Parakeet-ultra in GGUF e
  ReDimNet2+ in ONNX non esistevano già pronti: stanno nella release `modelli-1` del progetto (`MIRROR`), con
  provenienza e licenze in `LICENZE-modelli.txt`. Finché il repository è privato si scaricano solo col login.
- **`download.py`**: un file alla volta in `.part`, ripresa con `Range` dopo un'interruzione (anche a PC spento),
  da capo se il server non sa riprendere o riprende dal punto sbagliato, ritentativi con attese crescenti, subito
  fermo per 404/403/410 e per un file più grande o con un'impronta diversa (mai installato). Gli archivi del motore
  si estraggono rifiutando percorsi che escono dalla cartella e poi si cancellano. `scaricati.json` ricorda cosa è
  già controllato (finché misura e data non cambiano). Lo spazio si controlla prima di cominciare.

Da fare: la pagina vera collegata a questi pezzi, il cervello (login ufficiale di Claude Code, chiave API,
OpenCode, Ollama), la prova di microfono e casse, la voce di base, le due lingue in tutta l'app.

## Auto-riparazione e auto-miglioramento

- Supervisor: riavvio dei processi caduti con backoff, rilevamento microfono scollegato, VRAM
  satura (passaggio a configurazioni più leggere), fallback su ogni servizio.
- Diario dei fallimenti: errori degli strumenti, correzioni ("no, intendevo..."), interruzioni.
- Miglioramento: un sotto-agente lavora sul codice di Jarvis in un worktree git, esegue i test;
  se sono verdi e tu approvi, si applica e Jarvis si riavvia; se dopo il riavvio l'health check
  fallisce, rollback automatico al commit precedente.

## Audio che non fa cadere Jarvis

Il dispositivo vero è avvolto da `ResilientBackend` (`jarvis/audio/devices.py`): se non si apre o smette di
chiamare (cuffie staccate, driver che si riavvia, dispositivo occupato) Jarvis va avanti senza audio — un orologio
muto tiene il tempo reale, così la conversazione scritta continua e i sottotitoli scorrono — e un thread di
sorveglianza riprova da solo (1, 2, 3, poi ogni 5 s), rileggendo i dispositivi. Se Windows cambia il dispositivo
predefinito (chiesto a Windows con pycaw), Jarvis lo segue, salvo un dispositivo scelto per nome nelle
impostazioni. Ogni dispositivo si apre a 48 kHz con la conversione di Windows: a valle non cambia mai niente.
A ogni cambio la pipeline rimisura l'eco partendo dalla decisione ricordata per quella coppia di dispositivi,
la finestra mostra "audio non disponibile" e, dopo un'assenza lunga, Jarvis dice che l'audio è tornato.

## Finestra

Una pagina web in pywebview (`jarvis/ui/web/`), collegata al core da un websocket (`jarvis/ui/server.py`):
il core manda eventi (stato, livelli di microfono e voce, testo in arrivo, compiti, conferme), la pagina manda
comandi (testo scritto, approva/nega, riposo, stop, parla). Due grafiche, scelte e cambiate a voce:

- **Mark III** (`themes/mark3.*`): uno strumento al centro. Ghiera graduata che fa da misuratore quando ascolta,
  anello della voce deformato dal livello reale, cometa mentre ragiona, compiti in orbita con etichette fuori
  dalla ghiera, particelle che entrano (ascolto) o escono (voce), accensione in sequenza.
- **Cinema** (`themes/cinema.*`): una linea di luce calda che vibra con la voce, lampo anamorfico all'accensione,
  pulviscolo, grana e vignettatura, sottotitoli da film parola per parola, domande in corsivo.

`core.js` tiene lo stato, il protocollo e le parti comuni (comandi, campo di testo, registro, schede); un tema
è solo `mount(stage, api)` + `frame(now, dt)` + `unmount()`, quindi se ne aggiunge uno senza toccare il resto.
Il cambio di tema è una dissolvenza di 460 ms senza ricaricare. Le impostazioni `ui.theme`, `ui.accent` (nomi
di colore in italiano → esadecimale in `jarvis/ui/look.py`), `ui.text_scale`, `ui.effects` valgono subito: il
server trasforma `settings_changed` in un evento `ui_config`. Cambi più profondi passano da `migliora_jarvis`.

I sottotitoli seguono la voce, non il testo: lo speaker conta i caratteri già pronunciati (`said_chars`, dalla
frazione di audio suonata) e la pagina illumina solo quelli. I comandi dati mentre il collegamento è giù (Jarvis
che si riavvia) restano in coda e partono appena torna, tranne lo "stop" che dopo non avrebbe senso. I file della
pagina sono firmati con la data di modifica (`?v=…`) e serviti con `no-store`: mai una copia vecchia in cache.

`scripts/vetrina.py` fa la parte di Jarvis con scene fisse (`--scena ascolto|ragiono|parla|compiti|conferma|riposo`,
`--tema mark3|cinema`) per lavorare sulla grafica senza motori né cervello.

## Test

`pytest`: unità (macchina a stati del turno con audio simulato, segmentatore, normalizzatore,
memoria, politica di sicurezza, gestore compiti con cervello finto), integrazione col motore
(TTS → STT andata e ritorno con WER, latenze), scenari end-to-end con microfono virtuale
(interruzioni, eco, compiti in parallelo), la finestra vera in un Chrome senza finestra con la vetrina al posto
di Jarvis (`tests/test_ui.py`: ogni stato di entrambe le grafiche disegnato senza errori, cambio di grafica dal
vivo, sottotitoli al ritmo della voce, comandi che arrivano, riavvio senza perdere ciò che hai scritto, meno di
16 ms a fotogramma). Più un controllo che i test colgano un bug inserito apposta.

## Copia di prova

Il codice è uno solo; ciò che rende "tua" una copia sta tutto fuori dal repository: `data/` (configurazione col tuo
nome, memoria, voci, WhatsApp, registri), `models/` ed `engine/`. Per provare una versione nuova, o il primo avvio di un
PC mai visto, senza toccare la copia di tutti i giorni né duplicare i modelli:

1. una seconda cartella con lo stesso repository, per esempio `git worktree add "../Jarvis (prova)" -b prova`;
2. dalla nuova cartella, con il Python della copia principale:
   `<principale>\.venv\Scripts\python scripts\avvia_prova.py --da "<cartella della copia principale>"`.

`scripts/avvia_prova.py` fa girare la copia con `JARVIS_ENGINE_DIR` e `JARVIS_MODELS_DIR` della principale e
`JARVIS_DATA_DIR` suo (di base la sua cartella `data`, vuota: è come un PC nuovo). Si rifiuta di partire se i dati
sarebbero quelli della principale o se un altro Jarvis è acceso: le porte sono le stesse e la scheda video non basta
per due voci.

Le note personali di chi usa la copia (la storia del suo PC, le sue richieste, le sue regole per chi lo aiuta) stanno
in `privato/`, ignorata da git: `CLAUDE.md` dice di leggerla se c'è, `tests/test_privacy.py` controlla che le parole
di `privato/parole.txt` non finiscano mai nei file del repository.
