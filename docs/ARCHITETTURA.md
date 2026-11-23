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
  cache e avvisa dei messaggi nuovi senza mai leggerne il testo ad alta voce (`apps.whatsapp_avvisi`). Una voce
  per persona (`person_key`): le chat a due hanno un LID (`@lid`) e la rubrica i numeri (`@c.us`), quindi il ponte
  chiede a WhatsApp il numero dietro ogni LID (una volta per persona) e toglie i contatti doppi; per un id `@c.us`
  vale il numero dell'id (il campo `number` a volte è il LID).
- `gmail.py`: API REST di Gmail con `google-auth` (sessione che rinnova da sola il permesso), consenso OAuth
  "App desktop" nel browser (client copiato dai Download, permesso cifrato con DPAPI in `data/gmail`), mail citate
  col numero dell'ultimo elenco, destinatari cercati per nome nelle mail scambiate; permesso revocato → si
  dimentica e si chiede di ricollegare.
- `gmail_imap.py` (dal 22/11): il modo veloce, di base per chi non ha già il progetto Google. "Collega Gmail" apre
  myaccount.google.com/apppasswords e il campo Gmail della finestra (evento `gmail_form`); indirizzo e password per
  le app (16 lettere) arrivano col comando `gmail_app_password`, si provano con un login IMAP e solo allora si
  salvano cifrati (`{"mode": "imap", ...}` nello stesso `permesso.bin`); la password non va mai in registri, eventi
  o messaggi. `ImapSession` risponde alle stesse chiamate dell'API (stesso `request`, stesse risposte), così le
  azioni di `gmail.py` sono una sola volta: id = X-GM-MSGID e X-GM-THRID in esadecimale (come l'API), ricerche con
  X-GM-RAW in «Tutti i messaggi» (nel cestino o nello spam se la ricerca li nomina), cartelle riconosciute dai segni
  SPECIAL-USE e non dai nomi (cambiano con la lingua dell'account), letta e stella con \Seen e \Flagged, etichette
  con X-GM-LABELS, cestino e spam con MOVE (mai \Deleted né EXPUNGE), lettura con BODY.PEEK (non segna letta), invio
  con SMTP (mittente, data e Message-ID aggiunti; un invio fallito non si ritenta mai, potrebbe partire due volte),
  bozze con APPEND in \Drafts. Le capacità si rileggono dopo il login (Gmail annuncia UTF8=ACCEPT e MOVE solo
  allora); senza UTF-8 i nomi delle cartelle vanno in UTF-7 modificato e le ricerche con accenti come literal. Una
  connessione caduta si riapre una volta; "password non accettata" fa ricollegare, un server occupato no. Filtri e
  risposta automatica con IMAP non ci sono: lo si dice e si indica il modo completo (`collega` con `modo: completo`).
  Provato con un server IMAP finto che si comporta come Gmail (cartelle in italiano, uid diversi per cartella, in
  UTF-8 e in UTF-7) e un SMTP finto; il login a Gmail vero non ancora (serve una password per le app).

## Video e audio

`jarvis/media/`: Jarvis "guarda" un video senza un modello video, perché Claude vede le immagini e legge il testo.
Lo stesso vale per lo schermo: `guarda_schermo(dove)` (`screen.py`) cattura con Pillow lo schermo della finestra in
primo piano (o solo la finestra, o tutti gli schermi), con le coordinate lette in DPI per monitor (pixel veri anche
con lo zoom di Windows; la finestra dai bordi di DWM, senza l'ombra), lo riduce a `media.screen_px` e lo manda come
JPEG in un `ToolOutput`, mai su disco. Il gruppo di permessi `schermo` è solo dell'utente: uno screenshot può mostrare
chat e posta. `ToolOutput.blind` dice ai cervelli che non vedono immagini cosa riferire.
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
  si estraggono rifiutando percorsi che escono dalla cartella e poi si cancellano. Oltre al registro nei dati, nella cartella estratta resta `.jarvis-estratti.json` (le impronte degli archivi estratti lì): con dati nuovi e motore già al suo posto l'archivio non si riscarica e non si riestrae sopra (il 22/11 un primo avvio di prova con i dati vuoti e il motore di tutti i giorni l'aveva riscritto, per fortuna con la stessa versione). `scaricati.json` ricorda cosa è
  già controllato (finché misura e data non cambiano). Lo spazio si controlla prima di cominciare.
- **Strumenti** (`jarvis/toolbox.py`, pacchetti `tool-*` del catalogo): OpenCode 1.19.3 (il cervello gratuito),
  Node.js 22.19.0 (il ponte di WhatsApp) e Git per Windows 2.56.1 portatile (la bash che serve a Claude Code, git
  per l'auto-miglioramento) si scaricano in `engine/tools` solo se il PC non li ha (`missing_tools`: già scaricati,
  sul PATH, o OpenCode dove lo cerca `find_opencode`), sulla riga "Strumenti" della pagina. Git portatile è un
  archivio 7-Zip autoestraente (`kind="sfx"`): si apre eseguendolo con `-o` e `-y`, senza finestre, solo dopo il
  controllo dell'impronta. All'avvio `use_downloaded_tools` mette le cartelle scaricate in fondo al PATH (quelli del
  PC restano davanti) e, se l'unico Git è quello portatile, indica la sua bash a Claude Code
  (`CLAUDE_CODE_GIT_BASH_PATH`). Il ponte di WhatsApp non ha i suoi pacchetti nell'installatore: al primo
  "collega" Jarvis fa `npm ci` da solo (con un Chrome o un Edge sul PC, puppeteer non scarica il suo browser) e
  poi apre il QR, avvisando a voce. Se sul PC non c'è Chrome, WhatsApp usa Edge.

- **`brains.py`**: Claude Code solo con i comandi ufficiali: `claude auth status --json` (se ne tengono accesso,
  abbonamento e metodo, mai email e organizzazione), `claude auth login --claudeai` (browser su claude.ai) e
  l'installatore di Anthropic (`irm https://claude.ai/install.ps1 | iex`), entrambi in una finestra visibile.
  In alternativa una **chiave API** di Anthropic (terza scelta della pagina, campo nascosto): deve avere la forma
  `sk-ant-…`, si controlla con `GET /v1/models` (gratis) e, se Anthropic la accetta, si salva cifrata con DPAPI
  (`jarvis/brain/keys.py`, `data/claude/chiave.bin`); la pagina la dimentica subito. Come paga Claude lo decide
  `brain.claude_access` a ogni sessione nuova (`claude_cli.session_env`): con "chiave" la chiave va in
  `ANTHROPIC_API_KEY`, con "abbonamento" la variabile arriva vuota, così una chiave rimasta nel PC non fa pagare
  a consumo senza volerlo.
- **`flow.py`** (`Setup`): la procedura senza HTTP. `missing_files` dice cosa manca a Jarvis per partire;
  lingua e cervello si salvano subito (solo `claude` e `opencode`: gli altri non funzionano su un PC nuovo senza
  altro lavoro), poi controllo del PC, download con avanzamento per gruppo e velocità sugli ultimi 6 s, fine:
  nome, avvio con Windows (lo applica Jarvis con `autostart.reconcile`), modello della voce adatto al profilo e
  la voce: se quella scelta non c'è (su un PC nuovo la voce del film non c'è mai) usa quella inclusa della lingua,
  `base-it` o `base-en`. Se non trovasse nemmeno quella lo direbbe, invece di far cadere Jarvis all'avvio.
- **`server.py`**: la pagina e le richieste in JSON sulla porta della finestra; Host nostro sempre, Origin nostro e
  corpo JSON per ogni POST (un sito aperto nel browser non può comandarlo), le GET non cambiano niente.
- **`audio_check.py`**: la prova di microfono e casse senza accendere i motori. Il microfono scelto si apre per
  al massimo 30 s e misura il livello (barra da -60 a -12 dBFS, che scende piano); "ti sento" quando la voce supera
  la soglia per almeno 250 ms in tutto, così il fondo della stanza o un colpo secco non bastano. Le casse suonano
  `prova.wav` della voce inclusa della lingua: la frase di `test_line`, registrata con lo stesso motore, voce ed
  effetto di Jarvis. I dispositivi si cercano come li cerca Jarvis (WASAPI); si sceglie solo un nome che esiste.
- **`web/`**: la pagina Monolite nelle due lingue (di base quella di Windows): benvenuto, PC, cervello,
  download (l'anello in alto si riempie con l'avanzamento vero), prova di microfono e casse, pronto. Controllo del
  PC e stato di Claude partono già sul benvenuto: nella finestra vera, a freddo, hanno impiegato 8 s ciascuno
  (programmi che partono per la prima volta, antivirus), a caldo meno di 1 s. Nelle finestre piccole (la minima è
  420x560) l'anello in alto sparisce e tutto si stringe.
- **`run.py`** e `python -m jarvis`: il primo avvio parte da solo se manca un file indispensabile, oppure con
  `--setup`; chiuso prima della fine, Jarvis non parte e la volta dopo si riprende da lì. Per prima cosa, prima di importare aiohttp, `jarvis/certs.py` fa verificare i certificati HTTPS a Windows (truststore): su un Windows appena installato l'archivio delle radici è quasi vuoto e Python, che lo legge senza chiedere niente, falliva ogni download (provato in Windows Sandbox il 22/11). Senza finestra
  (`--no-window`) non si fa: esce con codice 2 e lo scrive nel registro.

Il motore della voce oggi parte solo su NVIDIA (`backend: cuda` fisso): il profilo "processore" è un blocco
dichiarato (`voce_solo_nvidia`). Da fare: la chiave API di Claude, il motore Vulkan o su processore, le due lingue
nel resto dell'app.

Provato dal vero il 21/11 come un PC nuovo, nella finestra vera (`JARVIS_DATA_DIR`, `JARVIS_ENGINE_DIR` e
`JARVIS_MODELS_DIR` su cartelle vuote, tranne i due modelli della release privata copiati a mano): `python -m jarvis`
ha aperto da solo il primo avvio, il download vero (4,7 GB da GitHub e Hugging Face, 7-8 MB/s) è finito con tutte le
impronte giuste e il motore estratto, poi Jarvis è partito con la voce `base-it` in 11,9 s e ha risposto a voce
(prima parola dopo 2,4 s). Visto in quella prova: la finestra si apre dietro le altre se a lanciarla è un processo
in secondo piano (da un collegamento o dall'installatore non succede).

### Voci incluse (dal 30/10)

`jarvis/speech/voci/`: una voce per lingua, ognuna un riferimento di 11-13 s (24 kHz mono, come la voce del film)
con il suo testo esatto, da cui Qwen3-TTS copia il timbro. Sono lettori di LibriVox, registrazioni di dominio
pubblico: si distribuiscono con il codice. `base-it` è Riccardo Fasol (*Il fu Mattia Pascal*), `base-en` Simon Evers
(*Huntingtower*); la fonte esatta (file e secondi) sta in ogni `voice.json`. Scelte a orecchio tra 11 lettori
misurati (timbro e pulizia); clonate, le orecchie di Jarvis le capiscono senza errori (0 % su sei frasi).
All'avvio, se la voce scelta non c'è (dati nuovi con i modelli già al loro posto, una cartella cancellata) Jarvis parte con quella inclusa della lingua (`engine.usable_voice`) e lo dice; la scelta resta salvata. Prima cadeva e il guardiano lo riavviava all'infinito (provato dal vero il 22/11). `engine.voice_dir` cerca prima in `data/voices` (le tue, per esempio quella del film) e poi tra le incluse; un
nome non diventa mai un percorso. A voce: `voce_di_jarvis` ("inglese", "di base", "quella del film"), rifiutata se
la voce non esiste. La lingua della sintesi segue `persona.language`.

### Voce da un file (dal 22/11)

`jarvis/speech/voice_maker.py`, strumento `crea_voce` (solo l'utente): da un file audio o video del PC (percorso o
nome cercato come per i video) fa una voce in `data/voices/<nome>`, come quelle incluse. Il pezzo di 10-14 s lo
sceglie `best_span` se non lo dice chi chiede: finestre da 50 ms con la voce (entro 25 dB dai picchi del parlato),
inizio e fine in una pausa, punteggio = quanta voce, meno le pause più lunghe di 0,8 s, meno i tratti saturati;
sotto 0,55 (il parlato pulito delle voci incluse sta a 0,85-0,89) chiede di indicare inizio e fine. Si guarda nei
primi 10 minuti. Il pezzo va a 24 kHz mono, il parlato a circa -20 dBFS con i picchi sotto 0,97 e attacco e coda
sfumati; le orecchie ne scrivono il testo (almeno quattro parole, o niente voce). Nessuna voce esistente si
sovrascrive e i nomi `base-*` restano delle incluse. La scheda dice che la voce è **ispirata** a quella del file ma
non è la voce vera (`"imitation": true`, avvertenza nella descrizione) e Jarvis lo dice ogni volta; effetto del film
di base, o voce pulita. Provata dal vero il 22/11 sul capitolo di LibriVox da cui viene `base-it`: pezzo scelto da
solo in 5,6 s (11,2 s, testo capito giusto), poi Qwen3-TTS ha parlato con la voce nuova e le orecchie hanno riletto
la frase senza errori.

## Installatore (dal 21/11)

`installer/jarvis.iss` (Inno Setup 6) e `scripts/build_installer.py`, che mette insieme il codice dell'ultimo commit
(`git archive`: niente dati, modelli o cartelle private; dal 23/11 anche `design/`, perché la copia installata deve
coincidere con il commit), `uv`, il runtime di Visual C++ e le immagini della procedura in stile Monolite (disegnate
da Chrome dall'icona), poi compila `build/Jarvis-Setup-<versione>.exe` (20 MB). Il runtime di Visual C++
(`msvcp140.dll` e famiglia, in `tools\vcrt`) lo chiedono onnxruntime e soxr: Windows di suo non l'ha, arriva con il
Visual C++ Redistributable, che quasi sempre qualche altro programma ha già installato ma non sempre (in Windows
Sandbox mancava e Jarvis non sentiva niente, 23/11). `build_installer.py` lo copia dalla cartella di sistema di chi
costruisce (o da `JARVIS_VCRT`), mai più vecchio della 14.40 (con quelli onnxruntime si pianta); `jarvis/vcrt.py`,
chiamato da `jarvis/__init__.py` prima di ogni altro import, lo carica col percorso completo solo se quello di sistema
manca o è più vecchio (quello di sistema lo aggiorna Windows Update). Così niente amministratore. L'installatore va
per utente in `%LOCALAPPDATA%\Programs\Jarvis` senza permessi di amministratore, in italiano o in inglese, grafica
scura; a fine copia prepara Python e le librerie con `uv sync --frozen --no-dev --no-cache`, senza finestre e con
Python dentro `tools\python` (si disinstalla con Jarvis); crea i collegamenti (menu Start, Desktop facoltativo) e apre
Jarvis, che parte dal primo avvio. Disinstallando chiede se tenere dati e modelli; in silenzio li tiene sempre.
Provato il 21/11: installazione silenziosa in 34-47 s, librerie a posto, disinstallazione pulita. Per le prove usare
`/TASKS=""`: senza, il collegamento sul Desktop sostituisce un "Jarvis.lnk" che c'è già. Non è firmato (un certificato
costa circa 200 € l'anno): Windows mostra "PC protetto"; da repository pubblico si può chiedere la firma gratuita di
SignPath Foundation.

## Aggiornamenti (dal 22/11)

`jarvis/updates.py`, solo per le copie installate (senza `.git`; chi sviluppa usa git). Una volta al giorno chiede
a GitHub l'elenco delle release (non "l'ultima": anche `modelli-1` è una release) e prende la più nuova con tag
`vX.Y.Z` sopra la versione di `pyproject.toml`, non bozza né anteprima, con l'asset `Jarvis-Setup-X.Y.Z.exe`
scaricabile dalle release del progetto e la sua impronta SHA-256 (il campo `digest` di GitHub): senza impronta non
si installa niente. L'installatore si scarica con lo scaricatore del primo avvio (`engine/aggiornamenti`, ripresa,
controllo dell'impronta). Poi `system.updates`: "automatici" (di base) installa quando Jarvis è fermo da 15 minuti
e non è occupato (`busy_now`: riposo, conversazione, compiti in corso), "chiedi" lo dice una volta e aspetta lo
strumento `aggiornamenti` (azione installa), "spenti" non chiede nemmeno a GitHub. Dopo un errore riprova dopo 3 ore.

Per installare Jarvis scrive `data/state/aggiornamento.json`, lancia un PowerShell nascosto e staccato
(`CREATE_NO_WINDOW` + gruppo nuovo + uscita dal job; non `DETACHED_PROCESS`, che senza console lo fa morire subito)
e si spegne con il codice 0, così anche il guardiano si ferma. Il PowerShell aspetta che Jarvis e il guardiano
siano chiusi, lancia l'installatore con `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=...` (registro in
`data/logs/aggiornamento.log`; l'installatore riusa la cartella e le scelte di prima) e riaccende Jarvis come il
collegamento. Alla ripartenza Jarvis dice com'è andata (versione nuova o "non è riuscito"), una volta sola.
Finché il repository è privato GitHub risponde 404: nessun aggiornamento e nessun errore.

Dal 23/11 la copia installata è anche un repository git (`jarvis/localrepo.py`): l'installatore porta tutti i file
del commit (anche `design/`, o la copia risulterebbe sempre modificata) e `jarvis.bundle`, la storia fino a quella
versione (~3,5 MB). Al primo avvio con Git disponibile `ensure_repo` crea il repository al commit della release
(`core.autocrlf false`, autore "Jarvis", indice dal commit: i file ci sono già) e cancella il bundle; reinstallando
sopra una copia con modifiche sue le mette in un ramo `mie-modifiche/...` e lo dice. Così auto-miglioramento e
ritorno alla versione sana del guardiano (che ora aggiunge al PATH il Git portatile) valgono anche per chi installa.
Le copie installate si riconoscono da `tools/uv.exe` (le mette l'installatore), non più dall'assenza di `.git`.

Se la release ha anche `Jarvis-X.Y.Z.bundle` (con impronta) e la copia è un repository, l'aggiornamento non usa
l'installatore: `plan_update` dice "dentro" (già incluso), "avanti" (nessuna modifica locale: il processo a parte, a
Jarvis chiuso, fa `git merge --ff-only`, poi `uv sync --frozen` con o senza strumenti di prova come prima, e riaccende)
oppure "unione". In quel caso parte un compito `aggiornamento` (`UpdateMergeWorker`): ramo `aggiornamento/...` in
una copia separata, `git merge` della versione; se si incastra da solo e le prove passano si applica senza domande,
altrimenti un sotto-agente risolve i conflitti tenendo le due cose, Jarvis rifà le prove e chiede conferma; mai
applicato con le prove rosse, conflitti aperti o la cartella principale sporca, e se le prove sul codice principale
falliscono si torna al commit di prima. Entrato il codice nuovo (la versione sul disco supera quella che gira),
l'aggiornatore riavvia quando Jarvis è fermo. Pytest e ruff non sono nell'installatore: `ensure_test_tools` li
scarica (uv sync con il gruppo dev) la prima volta che servono.

Per pubblicare una versione: alzare `version` in `pyproject.toml` (es. 0.2.0), fare il commit, costruire
l'installatore (`python scripts/build_installer.py`, che prepara anche il bundle) e creare la release con il tag e i
due file: `gh release create v0.2.0 build/Jarvis-Setup-0.2.0.exe build/Jarvis-0.2.0.bundle`. GitHub calcola da solo
le impronte; i nomi devono restare `Jarvis-Setup-<versione>.exe` e `Jarvis-<versione>.bundle`, o le copie installate
non li prendono.

Provato il 22/11: il processo a parte con un installatore e un Jarvis finti, in un percorso con spazi e apostrofo,
aspetta la chiusura, installa, riaccende dopo la fine dell'installatore, e finisce anche dopo l'uscita di chi l'ha
lanciato; l'installatore vero lanciato sopra una copia installata (come fa l'aggiornamento) finisce in 5 s nella
stessa cartella, tiene i dati e le librerie.

## Presentazione a voce (dal 21/11)

Dopo il primo avvio grafico (`data/setup/fatto.json`) il resto della configurazione si fa parlando: alla prima
accensione il cervello riceve un [EVENTO] (`jarvis/setup/welcome.py`, `Welcome.start_event`) che gli chiede di
presentarsi e di guidare i passi uno alla volta: nome e come rivolgersi, tu o Lei (non in inglese), la voce
dell'utente (impara_voce), le app (WhatsApp, Gmail, giochi), le abitudini da ricordare (solo dopo un sì). Lo
strumento `presentazione` (solo per l'utente, gruppo sistema) dice i passi rimasti e li segna fatti o saltati; finiti
tutti, la presentazione è finita da sola. "Dopo" la rimanda alla prossima accensione (allora si chiede se continuare),
"continuiamo la configurazione" la riprende, "rifacciamo la configurazione" la ricomincia. La parola d'ordine non è un
passo: si imposta dalla finestra e non si chiede né si dice mai a voce. Una copia installata a mano (senza il primo
avvio grafico) non la propone.

## Lingue (dal 21/11)

Jarvis parla italiano o inglese: `persona.language` ("it" o "en"), scelto al primo avvio (di base la lingua di
Windows) o a voce ("parla in inglese": scorciatoia `lingua`, vale dal riavvio). Quasi tutto ciò che dice lo scrive il
cervello, quindi la parte principale è la persona: le istruzioni restano in italiano, ma in inglese gli si chiede di
parlare SEMPRE inglese britannico e gli esempi di frasi sono quelli inglesi ("Right away.", "Let me check.");
"signore" diventa "sir" (`lang.address`), agli ospiti niente "sir".

`jarvis/lang.py` tiene il resto: le poche frasi fisse dette senza cervello (frase pronta per gli strumenti lenti,
cambio di cervello, cervello che non risponde), le frasi tenute pronte nella cache della voce, le parole di stop
(valgono in entrambe le lingue: "basta" e "enough" fermano sempre Jarvis). `speakable(text, lang)` legge numeri, ore,
date, unità e sigle all'inglese (4,892 / 3.14 / 8:30 / 21/11/2026 / $5 / GPU); la voce sintetizza con la lingua
della conversazione. In inglese il filtro delle frasi di servizio (`NarrationFilter`) è spento: lì "Let me check the
weather." è la risposta giusta. Cambiando lingua, una voce inclusa passa a quella della lingua nuova
(`follow_language`: base-it ⇄ base-en); una voce tua resta. Le orecchie riconoscono la lingua da sole.

La finestra segue la stessa lingua: `ui_config` porta `lang`, i testi si scrivono in italiano e `core.js` li traduce
(`U.t`, dizionario `EN`): pulsanti, stati delle due grafiche, registro, conferme, data e ora; i testi fissi della pagina
sono marcati `data-i18n*`. Cambiando lingua la grafica si rimonta, così anche i suoi testi fissi cambiano subito.

## Auto-riparazione e auto-miglioramento

- Supervisor: riavvio dei processi caduti con backoff, rilevamento microfono scollegato, VRAM
  satura (passaggio a configurazioni più leggere), fallback su ogni servizio.
- Diario dei fallimenti: errori degli strumenti, correzioni ("no, intendevo..."), interruzioni.
- Miglioramento: un sotto-agente lavora sul codice di Jarvis in un worktree git, esegue i test;
  se sono verdi e tu approvi, si applica e Jarvis si riavvia; se dopo il riavvio l'health check
  fallisce, rollback automatico al commit precedente. Dal 23/11 anche nelle copie installate (diventate repository,
  `jarvis/localrepo.py`; pytest e ruff si scaricano la prima volta), e le migliorie sopravvivono agli aggiornamenti:
  la versione nuova si unisce (`UpdateMergeWorker`).
- Prove rosse per com'è fatto il PC: dal 23/11 una prova rossa nella copia si riprova, da sola, nel codice di prima
  (la cartella principale, ancora com'era); se è rossa anche lì la fa fallire il PC (modelli non scaricati, Claude
  Code che non c'è), non la modifica, e non ferma niente. Ogni prova rossa nuova, un lint rosso o un tempo scaduto
  fermano tutto come prima; Jarvis lo dice ("1 prova è rossa anche senza"). Trovato in Windows Sandbox: 8 prove di
  questo tipo avrebbero fermato per sempre migliorie e aggiornamenti con unione su quel PC. Le prove che dipendono
  dal PC si saltano quando manca quello che serve, ma su un altro PC ce ne può essere una che nessuno ha previsto.

## Segnalazioni agli sviluppatori (dal 23/11)

`jarvis/feedback.py` prepara un rapporto tecnico e lo manda a `servizi/segnalazioni`, un Cloudflare Worker che lo
scrive come issue in un repository GitHub privato (la chiave di GitHub sta solo lì, come segreto). Chi usa Jarvis
non ha bisogno di nessun account.

- **Cosa parte:** versione e commit, Windows, Python, memoria, scheda video, cervello, lingua; le righe del registro
  che servono (avvisi ed errori di tutti con il loro traceback, le righe normali solo dei registri tecnici:
  guardiano, salute, motore, aggiornamenti, primo avvio...); le firme degli errori ripetuti nel diario (titolo
  generico e quante volte, mai le correzioni); la descrizione dell'utente.
- **Cosa non parte:** dai registri che possono portare testo della conversazione (cervello, voce, memoria, app, ui,
  librerie esterne) arrivano solo livello, nome e punti del codice: né il messaggio né il testo dell'eccezione né le
  righe di codice. Ogni riga passa poi da `scrub()`: segreti (`log.redact`), la cartella di Jarvis (`<jarvis>`), la
  cartella utente, il nome dell'utente e di Windows, id di WhatsApp, email, numeri di telefono e testo fra
  virgolette con spazi. I percorsi nei traceback diventano relativi (`jarvis/audio/vad.py`, `site-packages/...`):
  le cartelle dicono come l'utente organizza il PC. Provato sul registro vero di tutti i giorni contando soltanto:
  0 nomi, 0 parole di `privato/parole.txt` (prima della correzione dei percorsi ne passavano 6, tutte dal nome
  della cartella del progetto), 0 email.
- **Quando:** a voce è l'app `segnalazioni` (azioni `manda` e `anteprima`; gruppo "sistema", solo l'utente).
  `manda` chiede sempre il sì (`Action.always_confirm`), anche con le conferme delle azioni delicate spente: parte
  esattamente il rapporto letto all'utente. Dalla finestra (registro → "segnala un problema") si vede il testo
  esatto e si manda solo quello, una volta. Dopo una caduta `system.crash_reports` ("rapporti_cadute" a voce): chiedi
  (di base: l'avviso della ripresa dice al cervello di chiamare `manda` con tipo caduta, così la domanda è una sola),
  sempre (parte da solo e lo dice) o mai.
- **Senza rete** resta in `data/segnalazioni` e riparte al prossimo avvio. Senza `system.feedback_url` (vuoto finché
  il server non è in funzione) l'app dice che le segnalazioni non sono ancora attive.
- **Il server** (`logica.js`, provato con Node in `worker.test.mjs`): solo POST su `/segnala`, JSON fatto bene,
  massimo 70 KB, limiti di Cloudflare per PC e in tutto se il piano li ha; non salva niente (l'IP serve solo a
  contare); rende innocui `@nome` e `#12`. Come metterlo in funzione: `servizi/segnalazioni/README.md`. Le
  segnalazioni si leggono con `gh issue list --repo <proprietario>/jarvis-segnalazioni`.

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

Una pagina web in pywebview (`jarvis/ui/web/`), collegata al core da un websocket (`jarvis/ui/server.py`). Il motore è sempre WebView2: pywebview lo cerca solo nella voce di Edge Update del registro e senza passerebbe di nascosto a Internet Explorer (pagina bianca, visto in Windows Sandbox il 22/11); `jarvis/ui/window.py` allora gli dà la cartella della versione più nuova (`WEBVIEW2_RUNTIME_PATH`), e se WebView2 non c'è proprio apre la pagina nel browser e resta acceso al posto della finestra.

Il core e la pagina:
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
per due voci. Il primo avvio grafico (`--setup`) va provato con `JARVIS_ENGINE_DIR` e `JARVIS_MODELS_DIR` su cartelle
vuote, non su quelle della copia principale: il 22/11 un primo avvio così ha riestratto il motore sopra quello di
tutti i giorni (stessa versione; ora gli archivi già estratti si riconoscono dal segno nella loro cartella).

Le note personali di chi usa la copia (la storia del suo PC, le sue richieste, le sue regole per chi lo aiuta) stanno
in `privato/`, ignorata da git: `CLAUDE.md` dice di leggerla se c'è, `tests/test_privacy.py` controlla che le parole
di `privato/parole.txt` non finiscano mai nei file del repository.
