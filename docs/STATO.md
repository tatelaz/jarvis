# Stato del progetto Jarvis

Aggiornato al 24 novembre 2026. Cosa funziona, con quali misure, e le trappole già incontrate: da leggere prima
di lavorare sul progetto.

## Funziona (verificato con test automatici)

| Area | Cosa | Test |
|---|---|---|
| Orecchie | **parakeet-ultra** (Moondream, rifinitura di Parakeet TDT v3, dal 26/10) su audio.cpp 0.9.2: WER 2,12 % su 60 frasi FLEURS italiane contro 3,34 % del v3, stessa velocità, niente parole inventate su silenzio e rumore; il v3 resta come `speech.stt_model = parakeet-v3`. **Sulla scheda video** dal 26/10 sera: 30-75 ms per frasi da 2-20 s (sul processore 0,2-1,7 s), la voce rallenta solo del 3-4 %; a riposo passa sul processore, se all'avvio la memoria video non basta resta sul processore | `scripts/bench/asr_compare.py`, `ears_gpu_bench.py`, `test_health.py` |
| Turni di parola | Silero VAD + Smart Turn v3.2 (parità numerica col riferimento), pre-roll, pause | `test_listener.py`, `test_audio_models.py` |
| Eco | WebRTC AEC3: −37/−41 dB; si spegne da solo in cuffia (misura l'accoppiamento) | `test_pipeline_sim.py` |
| Voce | voci incluse: **Riccardo Fasol** (italiano) e **Simon Evers** (inglese), letture di LibriVox in dominio pubblico, clonate con Qwen3-TTS 1.7B e passate con l'effetto "film" V4; una voce nuova si fa da un file (`crea_voce`, sotto). La voce del film usata su questo PC (separazione, restauro Sidon, clone) non è nel progetto: solo per uso personale | `test_voicefx.py`, `test_voices_base.py` |
| Testo → voce | segmentazione in streaming (la prima frase già in cache esce da sola e suona subito), lettura italiana di numeri, orari, date, unità; cache, ritentativi, ripiego, riscaldamento vero | `test_text.py`, `test_voice_output.py` |
| Conversazione | chiamata per nome, finestra di ascolto, interruzione con riduzione del volume, stop, filtro dell'eco, eventi in quiete, mai silenzio sugli errori | `test_conversation.py` |
| Cervello | Claude Opus 5.5 sforzo basso (~0,8 s alla prima parola) via Agent SDK isolato; locale OpenAI-compatibile. **Dal 22/11 Claude anche con una chiave API di Anthropic** (a consumo, `brain.claude_access` = abbonamento o chiave, a voce `accesso_claude`): la chiave si scrive nel primo avvio, si controlla chiedendo ad Anthropic l'elenco dei modelli (gratis) e si salva cifrata con DPAPI in `data/claude`; arriva a Claude Code solo nella variabile della sessione, e con l'abbonamento la variabile arriva vuota (provato con `claude auth status`). Senza accesso (login mancante, chiave sbagliata) Claude si mette in pausa per 5 minuti e risponde il cervello dopo: provato dal vero con una chiave inventata, rifiutata e riconosciuta in 2,0 s. **Non provato con una chiave vera** Dal 23/11 anche il login scaduto nella forma nuova ("Failed to authenticate: this login reached its time limit", codice `authentication_failed`) passa a OpenCode e Jarvis dice che il login va rifatto; prima rispondeva "problema nel formulare la risposta" (visto sul Jarvis vero). | `test_brain_claude.py`, `test_brain_local.py`, `test_claude_access.py` |
| Ripieghi | instradatore Opus → Haiku → **OpenCode Zen, modelli gratis** (dal 28/10, vedi sotto) → Ollama cloud `gpt-oss:120b-cloud` (gratis, 0 VRAM, 0,9 s alla prima parola, usa gli strumenti) → Bonsai locale; "cervello" a voce sceglie chi va in testa (claude / opencode / cloud / locale), a caldo; un cervello spento nelle impostazioni si salta senza pausa; pausa fino all'ora di ripristino, limite dell'account = salta tutti i Claude, ritorno automatico al principale, riassunto a chi subentra, avviso a voce ("ho esaurito l'uso di Claude fino alle 20:20") | `test_router.py` |
| Compiti | sotto-agenti Claude Code in background, messaggi a metà lavoro, conferme a voce senza doppioni, coda | `test_tasks.py`, `test_safety.py` |
| OpenCode (gratis) | **dal 28/10**, quando l'uso di Claude è esaurito. Zen dice che i modelli gratis funzionano **solo attraverso OpenCode**: Jarvis accende un `opencode serve` suo (`jarvis/brain/opencode.py`), isolato in `data/opencode` (cartelle XDG proprie, `--pure`, niente configurazione di progetto né CLAUDE.md, solo il fornitore `opencode`, permessi scritti per intero, 127.0.0.1 con parola d'ordine nuova a ogni avvio), acceso alla prima richiesta (~1,5 s) e spento dopo 30 minuti di inutilizzo. **A voce**: una sessione OpenCode, gli strumenti di Jarvis passano da un ponte MCP sul PC (`brain/mcp_bridge.py`, gettone segreto, solo 127.0.0.1); modello sparito, sovraccarico o zitto per 20 s → il successivo; **limite gratuito** = tutto il fornitore in pausa (si riprova al più dopo un'ora) e si passa a Ollama. **Provato dal vivo il 28/10 sera** (`scripts/opencode_live.py`, senza chiave): server acceso in 2,6 s, 8 modelli gratis letti, esame vero su 2 (lo strumento finto chiamato da OpenCode attraverso il ponte: `nemotron-3.5-super-vl-free` promosso, prima parola 1,1 s; `flint-40b-free` bocciato, non riportava il dato), compito vero con le richieste di permesso vere (file nella cartella scritto, file fuori fermato dalla conferma e non creato, comando innocuo eseguito, riepilogo in italiano), nessun processo rimasto acceso; Jarvis a voce su `beam-free`: strumento chiamato attraverso il ponte ("Sono le 21:37."), prima parola 2,2 s, interruzione ferma in 1,4 s senza parole dopo lo stop. Al secondo esame `nemotron-3.5-super-vl-free` è rimasto zitto 45 s: da qui il limite di 20 s alla prima parola e la regola che un intoppo non cancella una promozione. **Sotto-agenti**: di norma sempre Claude Code; `FallbackWorker` manda il compito a OpenCode solo se Claude è esaurito (anche a metà, con l'avviso di non rifare invii e acquisti); ogni richiesta di permesso di OpenCode passa dalla stessa `SafetyPolicy`, dalle stesse regole delle mani e dalla stessa conferma a voce (mai un sì da soli); una sessione per compito, in parallelo. **Modello**: elenco da OpenCode (costo zero, strumenti, non deprecati), esame breve ogni 72 ore o quando arriva un modello nuovo (italiano, strumento finto "meteo", prima parola), solo in quiete e mai a spese delle risposte; privacy annotata per modello (`brain/zen.py`), "senza addestramento" li esclude. A voce: cervello opencode, opencode sì/no, modello_gratis, privacy_modelli_gratis, compiti_su_opencode, `modelli_gratis` (classifica ed esame). Chiave facoltativa: `data/opencode/chiave.txt` (cifrata con DPAPI al primo avvio) o `OPENCODE_API_KEY`; mai nei registri | `test_opencode.py`, `test_opencode_worker.py` (contro un finto `opencode serve` costruito sulla specifica OpenAPI vera) |
| Mani dei sotto-agenti | browser dell'utente (Claude in Chrome, `--chrome`), browser usa e getta (Playwright MCP 0.0.83, isolato e invisibile), desktop (Windows-MCP 0.8.5, solo strumenti d'interfaccia, un compito alla volta) | `test_hands.py`; dal vivo `test_hands_live.py` (marcatori claude, desktop) |
| Memoria | SQLite + FTS5 + significato (EmbeddingGemma 2 q4 sul processore, 26 ms a frase, 14/15 domande italiane senza parole in comune); conversazioni passate cercabili per significato; custode automatico a conversazione finita (Haiku o cloud gratuito, provato dal vivo con entrambi: aggiorna, aggiunge, ignora richieste del momento, TV e password); dimentica con prudenza (chiede quale se ambiguo); doppioni veri fusi, fatti diversi mai | `test_memory.py`, `test_memory_keeper.py` |
| Voce da un file | **dal 22/11** strumento `crea_voce`: una voce nuova da un file audio o video (pezzo di 10-14 s di parlato pulito scelto da solo o indicato, testo dalle orecchie, scheda in `data/voices`); la scheda e Jarvis dicono sempre che è ispirata alla voce vera ma non è la voce vera, solo per uso personale. Provata dal vero sul capitolo di LibriVox di `base-it` (pezzo e testo in 5,6 s) e con Qwen3-TTS che parla con la voce nuova, riletta senza errori dalle orecchie. Il 22/11 anche con un Jarvis acceso (scrivendogli, dati di prova): "crea una voce da questo file e chiamala lettore" → voce fatta da 11 s del file, testo giusto, avvertenza detta a voce e scritta nella scheda. **Non provata parlando al microfono** | `test_voice_maker.py` |
| Voci | chi parla dall'impronta vocale (ReDimNet2+ in ONNX, scelto con una gara su voci italiane); l'utente tutto, persone conosciute base + permessi concessi a voce, ospiti base con una sessione del cervello separata e niente contesto privato; permessi controllati nel codice; la TV e le voci estranee non entrano nella conversazione né zittiscono Jarvis; parola d'ordine mai chiesta e mai scritta da nessuna parte; registrazione a voce («impara la mia voce»). **Attivo dal 26/10**: voce dell'utente registrata (12 frasi, coerenza 0,81), provato dal vivo con un ospite | `test_identity.py`, `test_conversation_voices.py`, `test_ui.py` |
| Strumenti | apri (app, siti, cartelle, file), volume, musica, appunti, finestra attiva, blocca schermo, memoria, compiti: tutti con test, e uno strumento nuovo senza test fa fallire la suite | `test_everyday_tools.py` |
| App (abilità) | **dal 27/10**: un solo strumento `usa_app` con il catalogo corto (~860 token stimati per tre app e 104 azioni, dentro la cache del prompt, contro ~20.000 a passo di un sotto-agente), dettagli con l'azione `aiuto`, tutto in background. Permessi nel codice (messaggi e posta solo dell'utente, gruppo `messaggi`); azioni delicate in sospeso con un codice, partono solo dopo un sì chiaro detto in un turno successivo, con i parametri salvati, una volta sola, un no annulla. **Giochi** (`jarvis/apps/games.py`): Steam (più librerie, stati, ore e ultima partita dell'account collegato o di quello usato più di recente), Epic, Xbox; avvia, chiudi (gentile, a forza solo confermato), installa, disinstalla, verifica, Store, sezioni e stato di Steam, aggiornamenti, recenti, spazio. **WhatsApp nascosto** (`jarvis/apps/whatsapp.py` + ponte Node `whatsapp_bridge/`, whatsapp-web.js dal commit `064a3d5` del 28/09/2026 su Chrome senza finestra): 42 azioni (leggere, non letti, cercare, inviare testi e file, rispondere citando, reagire, inoltrare, correggere, cancellare, archiviare, silenziare, fissare, gruppi, profilo, stato, scaricare allegati, trascrivere i vocali con le orecchie di Jarvis); collegamento col QR in una finestra di Chrome, poi invisibile; su richiesta (si spegne dopo 15 minuti) o sempre acceso con avvisi (solo chi scrive, mai il testo). Ponte vero provato il 27/10: QR in 3,1 s, 387 MB di RAM reale prima del collegamento, nessun processo orfano allo stop; collegato dall'utente il 27/10: pronto in 3,5 s, 129 chat, ricerca, info e lettura provate. **Gmail** (`jarvis/apps/gmail.py`, API REST ufficiale con `google-auth`): 31 azioni (non lette, cerca con la sintassi di Gmail, leggi senza segnare come letta, conversazione, allegati, invia/rispondi/rispondi a tutti/inoltra con allegati, bozze, archivia, stella, spam, etichette, cestino e ripristino, azioni in blocco fino a 500 mail, risposta automatica, filtri); destinatari anche per nome (cercati nelle mail scambiate); permessi `gmail.modify` + `gmail.settings.basic`, mai cancellazioni definitive; permesso cifrato con DPAPI; guida con l'azione `guida` e in `docs/GMAIL.md`. **Dal 22/11 il modo veloce** (`jarvis/apps/gmail_imap.py`, di base per chi non ha il progetto Google): password per le app incollata nel campo Gmail della finestra, IMAP e SMTP di Google che rispondono come l'API, quindi le stesse azioni tranne filtri e risposta automatica; provato con un server IMAP finto fedele a Gmail (in UTF-8 e in UTF-7), non ancora con un account vero **23/11**: con i LID di WhatsApp (101 chat a due su 103) la stessa persona era la chat @lid e il contatto @c.us (dato pure due volte), e Jarvis chiedeva "quale Bro?" tra tre "Bro": il ponte dice il numero dietro ogni LID e Jarvis tiene una voce per persona (nomi con più voci da 46 a 0 sui dati veri). | `test_apps.py`, `test_whatsapp.py`, `test_gmail.py`, `test_gmail_imap.py`, `test_settings_tools.py` |
| Video e audio | **dal 28/10**: strumento `guarda_video` (gruppo `pc`; i video di WhatsApp chiedono in più il permesso `messaggi`, nel codice). Da un file del PC (percorso, nome detto a voce cercato in Desktop, Download, Download\WhatsApp, Video, Musica, Documenti, oppure "l'ultimo video/audio"), da un indirizzo (YouTube e gli altri siti di yt-dlp, scaricati in una cartella temporanea poi cancellata) o l'ultimo video/vocale/audio di una chat WhatsApp. Al cervello arrivano fino a 20 **fotogrammi dei cambi di scena** come immagini (768 px, ognuno con l'ora) e il **parlato trascritto dalle orecchie di Jarvis** a pezzi di 30 s tagliati nelle pause, con l'ora; `inizio`/`fine` per i video lunghi, `solo_audio`. I risultati degli strumenti ora possono contenere immagini (`ToolOutput`): Claude le vede, Ollama cloud e Bonsai ricevono il testo e una nota "N immagini non mostrate". Impostazioni `media.*` a voce (fotogrammi, lato, minuti, caratteri, siti, modello dei suoni). Modello locale per musica e suoni (`suoni=true`) preparato ma **spento** finché la gara non sceglie (sotto) | `test_media.py`, `test_media_av.py` (video veri sintetici, con PyAV), `test_omni.py`, `test_speaker.py` |
| Guardare lo schermo | **dal 23/11** strumento `guarda_schermo` (`jarvis/media/screen.py`): uno screenshot di ciò che l'utente vede, solo quando lo chiede ("cosa vedi?", "leggimi questo errore"): lo schermo della finestra in primo piano (di base), solo la finestra o tutti gli schermi; coordinate in pixel veri anche con lo zoom di Windows; barra delle applicazioni e desktop non contano come finestra; lato lungo 1568 px in JPEG (~230 KB, ~1.800 token), mai salvato. Solo l'utente (gruppo `schermo`, mai concedibile). A voce `vedere_schermo` e `risoluzione_schermo`. Ai cervelli senza immagini (OpenCode, Ollama) arriva il titolo della finestra e la nota che l'immagine non l'hanno vista. Provato dal vero il 23/11 solo nelle misure (1920x1080 → 1568x882); **non ancora chiesto a voce a Jarvis** | `test_screen.py` |
| Auto-riparazione | guardiano esterno (`python -m jarvis.supervisor`, usato da `Avvia Jarvis.bat`): riavvio dopo una caduta con rapporto in `data/logs/cadute/`, blocco riconosciuto dal battito, 3 cadute in 10 min con codice nuovo → ultima versione sana da una cartella a parte (git worktree, file di lavoro intatti), si arrende invece di girare all'infinito; autodiagnosi all'avvio (modelli, motore, Claude, VRAM, microfono) detta a voce; avviso di ripresa | `test_supervisor.py`, `test_health.py` |
| Audio | non fa mai cadere Jarvis: dispositivo che non si apre o smette di rispondere → si va avanti senza audio (la conversazione scritta continua, avviso nella finestra) e si riprova da soli (1, 2, 3, poi ogni 5 s); segue il dispositivo predefinito di Windows (cuffie collegate); sempre 48 kHz con la conversione di Windows; l'eco si rimisura sui dispositivi nuovi; dopo un'assenza lunga Jarvis dice che l'audio è tornato. Provato sul PC vero il 7/10 | `test_audio_resilience.py` (2 test sul dispositivo vero, marcatore hardware) |
| Riposo | pulsante «riposo» nella finestra o "riposo" a voce: scarica i modelli (4,76 GB di VRAM liberati, misurato con `scripts/rest_live.py`), le orecchie restano sul processore; risveglio col nome, Ctrl+Alt+J o il pulsante «riaccendi» in ~12 s, la frase che l'ha svegliato non si perde, gli avvisi dei compiti aspettano | `test_conversation.py`, `test_health.py` |
| Finestra | due grafiche, **Mark III** (strumento al centro) e **Cinema** (linea di luce, sottotitoli da film), con accensione animata, scene per ogni stato, bagliore e particelle; a voce: "metti la grafica cinema/mark III", "colore rosso", "testo più grande", "effetti sobri", valgono subito senza ricaricare (dissolvenza); sottotitoli al ritmo della voce vera; comandi scritti durante un riavvio non persi; < 16 ms a fotogramma | `test_ui.py` (Chrome vero senza finestra), `test_look.py` |
| Auto-miglioramento | `migliora_jarvis`: copia separata (git worktree), sotto-agente con i test, controlli rifatti da Jarvis col bash di Git, conferma dell'utente, unione con ritorno al commit di prima se rompe; prova dal vivo `scripts/self_improve_live.py` (risponde sempre no). **Spontaneo** (26/10, `jarvis/improve/diary.py`, `data/diario.db`): diario silenzioso di cadute, strumenti e compiti falliti, errori di cervello e udito, correzioni dell'utente subito dopo una risposta (limiti d'uso e rete esclusi); solo gli errori ripetuti ≥3 volte in 30 giorni diventano proposte, al massimo 3 per riepilogo, al massimo un riepilogo a settimana (`system.proposals`: mai/settimana/giorno, a voce) e solo con l'utente presente, conversazione finita, niente compiti, non a riposo; "hai proposte?" → `proposte`; `rispondi_proposta`: sì = `migliora_jarvis` con gli esempi del diario, no = mai più **Dal 23/11 anche nelle copie installate** (repository dal bundle; pytest e ruff scaricati la prima volta). | `test_self_improve.py`, `test_improve.py` |
| Programmi | **dal 29/10** (`jarvis/scheduler.py`, strumenti `programma`, `programmi`, `togli_programma`): promemoria, richieste che il cervello esegue all'ora giusta con tutti i suoi strumenti (riassunto del mattino, notizie su un tema, controlli) e compiti che partono da soli; una volta, ogni giorno, certi giorni, ogni mese, ogni N minuti, a ogni accensione, con fine e numero di volte. Jarvis spento: quelli di una volta partono in ritardo e lo dicono, i ripetuti recuperano solo l'ultima volta entro 2 ore. Ospiti solo promemoria (i loro, testo mai eseguito come ordine). A riposo un promemoria riaccende Jarvis (`programmi_dal_riposo` a voce). **Non ancora provato a voce col Jarvis vero** | `test_scheduler.py` |
| Installatore | `.exe` con Inno Setup (circa 19 MB): per utente e senza amministratore, italiano e inglese; prima di installare mostra cosa resta sul PC e cosa esce; prepara Python e le librerie di `uv.lock` dentro la cartella di Jarvis, porta il runtime di Visual C++ (`tools/vcrt`, `jarvis/vcrt.py`) e la storia git (`jarvis.bundle`), crea i collegamenti e apre il primo avvio; la disinstallazione chiede se tenere i dati. **Dal 24/11 si costruisce solo su GitHub Actions** dal tag `vX.Y.Z` (installatore, bundle, impronte e release; `prove.yml` fa la suite a ogni commit, verde): **release v0.1.0 e v0.1.1 del 24/11**. **Il 24/11 sul PC vero** (utente senza amministratore) l'installatore della 0.1.0 si è fermato su "Python e le librerie (codice 2)": uv 0.13.9, preso da solo su GitHub Actions, dentro Inno Setup non riusciva a creare la junction della versione di Python in `tools\python` (errore 448 con RedirectionGuard, un altro errore senza); la 0.10.0, quella di `uv.lock`, sì in tutti e due i casi (provato con un installatore ridotto che fa solo `uv sync` del progetto vero). Nella 0.1.1 uv è fissato alla 0.10.0 e "Avvia Jarvis" si offre solo con Python pronto. La Sandbox non lo vedeva: lì l'utente è amministratore. Subito dopo, il primo avvio della prova si è chiuso da solo mentre scaricava: il guardiano concede 180 s prima del primo battito e il battito partiva solo con Jarvis acceso, cioè dopo il primo avvio; dopo 3 minuti lo credeva bloccato e alla terza volta si arrendeva (le prove del primo avvio erano state fatte senza guardiano, e nella Sandbox il primo avvio si ferma subito per la scheda video). Nella 0.1.1 un battito di avvio parte subito sotto il guardiano e lascia il posto a quello di Jarvis acceso (`early_heartbeat` in `jarvis/__main__.py`; provato con il guardiano vero e un avvio che dura il triplo dell'attesa). Provato in silenzio su questo PC (21/11) e **da zero in Windows Sandbox** (22-24/11, Windows pulito senza Python, Git, Node né scheda video): installazione in 21 s, strumenti scaricati (OpenCode, Node.js, Git) e funzionanti, ponte di WhatsApp pronto, primo avvio che dice che senza NVIDIA non si va avanti; l'installatore fatto su GitHub carica il runtime di Visual C++ e la voce, e la copia diventa identica al commit. La Sandbox ha trovato quattro difetti veri, corretti: HTTPS senza certificati radice (`jarvis/certs.py`), la finestra passata a Internet Explorer (`jarvis/ui/window.py`), il benvenuto fuori dal cerchio in inglese, il runtime di Visual C++ mancante (onnxruntime e soxr non si caricavano). **Non firmato**: la firma gratuita di SignPath Foundation è stata chiesta il 24/11, in attesa di risposta (README, "Firma del codice"). **Non provato su un PC vero diverso da questo** | `test_installer.py`, `test_workflows.py`, `test_certs.py`, `test_window_engine.py`, `test_vcrt.py` |
| Aggiornamenti | le copie installate si aggiornano dalle release di GitHub (`jarvis/updates.py`, `system.updates`: automatici, chiedi o spenti, anche a voce; strumento `aggiornamenti`): solo versioni più nuove con le impronte SHA-256 pubblicate, installate a Jarvis fermo (non a riposo, niente compiti in corso) da un processo a parte che lo riaccende; alla ripartenza dice com'è andata. **Dal 23/11 la copia installata è un repository git** (`jarvis/localrepo.py`, dal bundle dell'installatore) e l'aggiornamento unisce invece di sovrascrivere: senza modifiche locali va avanti con git (`merge --ff-only`); con modifiche dell'auto-miglioramento le unisce in una copia a parte con le prove, chiama un sotto-agente e chiede conferma se si toccano, mai applicato con prove rosse (una prova rossa anche nel codice di prima, per com'è fatto il PC, non conta). Provato da zero in Windows Sandbox il 23/11: 0.2.0 senza modifiche in 3 s, poi una miglioria e una 0.3.0 nello stesso file unite da sole in 6 minuti con le prove verdi. La release vera v0.1.0 (24/11) l'aggiornatore la legge giusta (da una versione più vecchia sceglie installatore e bundle con le loro impronte; dalla 0.1.0 niente). **Non provati**: un aggiornamento da una release vera alla successiva (servirà la 0.1.1) e il caso con conflitti, che chiama Claude (solo nelle prove automatiche) | `test_updates.py`, `test_localrepo.py`, `test_self_improve.py` |
| Segnalazioni | **dal 23/11** a voce (app `segnalazioni`, sempre dopo un sì), dalla finestra (si vede il testo esatto) e dopo una caduta (`rapporti_cadute`: chiedi, sempre, mai): rapporto tecnico ripulito (`jarvis/feedback.py`: dai registri della conversazione niente messaggi; nomi, cartelle, email, numeri, contatti e frasi tolti) verso un Cloudflare Worker (`servizi/segnalazioni`) che lo scrive come issue in un repository privato; senza rete riparte al prossimo avvio. Provato sul registro vero contando soltanto: 0 nomi, 0 parole private, 0 email. **Server in funzione dal 24/11** (`jarvis-segnalazioni.jarvis-assistente-it.workers.dev`, repository privato `jarvis-segnalazioni`): una segnalazione vera, fatta dal codice di Jarvis con il registro di tutti i giorni, è arrivata come issue #1 con le etichette, 0 parole private nel testo arrivato. Due intoppi trovati così e sistemati: Cloudflare rifiuta la firma di urllib (403, errore 1010: Jarvis si presenta con un nome suo) e una chiave senza *Issues: Read and write* dava 403 senza spiegare (ora il motivo di GitHub arriva fino a Jarvis). Provato sul Jarvis acceso il 24/11: chiesto per scritto, Jarvis ha detto cosa conteneva il rapporto e ha chiesto il sì, poi l'ha mandato (issue #2, 0 parole private); l'anteprima della finestra vera, dal suo canale, con 0 parole private. **Non provati** a voce parlata né il giro dopo una caduta vera | `test_feedback.py`, `test_feedback_server.py`, `test_ui.py` |
| Presentazione a voce | **dal 21/11** dopo il primo avvio Jarvis si presenta e guida a voce nome, tu o Lei, la tua voce, le app e le abitudini, un passo alla volta (strumento `presentazione`: stato, fatto, salta, rimanda, fine, ricomincia); si riprende a ogni accensione finché non è finita. Provata dal vero il 21/11 scrivendo a un Jarvis appena installato: si è presentato da solo, ha salvato nome, "signore" e Lei segnando i passi, ha rimandato il resto su richiesta. Il 22/11 di nuovo scrivendo, con OpenCode: nome, tu, voce rimandata, app; corretto l'attacco "Certamente, signore." prima di presentarsi (a un [EVENTO] niente frase di cortesia). Quel giorno i modelli gratuiti di OpenCode non rispondevano ("Model is unavailable") e Jarvis è passato da solo a Claude. **Non provata parlando al microfono, né i passi voce e abitudini** | `test_welcome.py`, `test_lang.py` |
| Lingue | **dal 21/11** italiano o inglese (`persona.language`, a voce "parla in inglese"): la persona chiede al cervello di parlare sempre inglese britannico con esempi inglesi e "sir"; frasi fisse, frasi pronte, parole di stop ("enough", "hold on") e lettura di numeri, ore, date e unità in inglese; la voce inclusa segue la lingua. Finestra in inglese anche lei (pulsanti, stati, registro, conferme), cambia dal vivo. Provato dal vero il 21/11 scrivendo a un Jarvis in inglese con la voce base-en: risposta in inglese con "sir" e numeri da leggere, prima parola dopo 3,7 s, congedo in inglese. **Non provato parlando al microfono in inglese**. **Dal 24/11** anche il README (`README.en.md`, si passa dall'uno all'altro in cima) e i messaggi che la finestra mostra così come sono (Gmail, parola d'ordine, segnalazioni: `lang.in_language`) | `test_lang.py`, `test_readme.py`, `test_ui.py` (Chrome vero: nessuna parola italiana visibile in nessuno stato), `test_look.py` |
| Primo avvio | **dal 30/10** (`jarvis/setup/`): controllo del PC con profilo (provato su questo PC: "completo", RTX 4060, 4,3 GB da scaricare su un PC nuovo), catalogo con versioni fissate e impronte (16 file su 16 controllati in rete; parakeet-ultra e ReDimNet2+ nella release `modelli-1`, pubblica dal 24/11: i 20 file del catalogo controllati da fuori, senza login, tutti a posto), download con ripresa, ritentativi, controllo dell'impronta ed estrazione sicura del motore; icona nuova in tutte le misure. **Pagina vera dal 30/10** (Monolite, italiano e inglese): benvenuto, PC, cervello (login ufficiale di Claude Code, o OpenCode), download, pronto; parte da sola con `python -m jarvis` se manca un file indispensabile, o con `--setup`. Provata in Chrome con dati inventati (tutto il percorso, due lingue, misura normale e minima) e con i dati veri di questo PC e lo stato vero di Claude Code (`scripts/primo_avvio_prova.py`, download simulato). **Voci di base dal 30/10**: Riccardo Fasol (italiano) e Simon Evers (inglese), lettori di LibriVox in dominio pubblico, clonate e capite al 100 % dalle orecchie; un PC nuovo usa quella della sua lingua. **Provato dal vero il 21/11** nella finestra vera come un PC nuovo (cartelle vuote): download vero di 4,7 GB, Jarvis partito con la voce di base e risposta a voce in 2,4 s. Prova di microfono e casse dal 21/11 (barra del microfono, frase di prova con la voce di Jarvis, scelta dei dispositivi). **Strumenti dal 22/11**: OpenCode, Node.js e Git portatile scaricati in `engine/tools` solo se mancano (provato dal vero il 22/11 in una cartella a parte: 157 MB scaricati ed estratti in 27 s, `git`, `node`, `opencode` e la bash partono, `npm ci` del ponte di WhatsApp col Node scaricato in 18 s senza un Chrome in più); il ponte di WhatsApp si prepara da solo al primo "collega" (**non provato dal vero il collegamento dopo la preparazione automatica**). **Non provato su un PC diverso da questo** | `test_setup_pc.py`, `test_setup_catalog.py`, `test_setup_download.py`, `test_setup_flow.py`, `test_setup_page.py`, `test_setup_audio.py`, `test_voices_base.py`, `test_icon.py`, `test_toolbox.py` |
| Fine a fine | voce simulata → Jarvis → voce: ~2,2 s dalla fine della domanda | `test_e2e.py` (marcatori engine, claude) |

Accensione: da sola all'accesso a Windows (avvio automatico), oppure collegamento **Jarvis** sul Desktop (stesso
comando, senza console; se Jarvis è già acceso apre la sua finestra; si ricrea con `scripts/tools/make_shortcut.py`),
oppure `Avvia Jarvis.bat` (con la console, per vedere cosa succede). La finestra senza bordi si sposta trascinandola
dalla fascia in alto (dal 26/10; prova sulla finestra vera: `python scripts/window_drag_live.py`).

Controlli: `bash scripts/check.sh` (lint + test rapidi), `bash scripts/commit.sh "msg"` (committa solo se verde),
`python scripts/mutation_check.py` (i test colgono i bug inseriti apposta: 570 mutazioni; le 5 della 0.1.1 (uv provato, "Avvia Jarvis" solo con Python pronto, battito durante il primo avvio) colte il 24/11; le 5 delle due lingue (README, messaggi della finestra) colte il 24/11; le 5 della pubblicazione (informativa, GitHub Actions) colte il 24/11; le 26 delle segnalazioni colte il 23/11; le 7 delle prove rosse per com'è fatto il PC colte il 23/11; le 9 del runtime di Visual C++ portato da Jarvis e del registro dell'aggiornamento in UTF-8 colte il 23/11; le 15 della copia installata come repository git colte il 23/11; le 3 di WhatsApp e le 8 dello schermo colte il 23/11; le 5 dell'accesso a Claude scaduto colte il 23/11; le 17 di Gmail con la password per le app e le 14 dei difetti trovati nella Sandbox e nella prova (certificati, finestra, cerchio, testi, voce di riserva, download, persona) colte il 22/11; dal 22/11 `test_mutation_list.py` controlla che ognuna trovi ancora il suo testo, dopo che 7 erano rimaste indietro e sono state riportate sul codice di oggi; le 14 della voce da un file, le 16 degli aggiornamenti, le 12 della chiave API di Claude e le 17 degli strumenti e del ponte di WhatsApp colte il 22/11, le 73 del primo avvio, dell'installatore, della presentazione, delle voci, dell'inglese e dell'icona colte tra il 30/10 e il 21/11, le 13 dei programmi il 29/10 in una copia separata; le 211 del giro completo del 27/10 tutte colte, quelle nuove di video/audio e di OpenCode verificate dai sotto-agenti il 28/10; con una parola filtra, es. `whatsapp:`).
Prova dal vivo di OpenCode (modelli gratis veri, niente Claude, consuma un po' dell'uso gratuito del giorno):
`python scripts/opencode_live.py` (Jarvis può restare acceso: usa una cartella sua).
Prova dal vivo del ripiego (una risposta di Claude vero, poi Claude esaurito simulato → OpenCode vero, poi il primo
modello gratis "non disponibile nel paese" simulato → il successivo senza pausa, ed esame): `python
scripts/ripiego_live.py` (cartella sua; tutta verde il 20/11).
Prove con Claude vero (consumano abbonamento): `pytest -m "engine and claude" tests/test_e2e.py` (11 giri di fila
verdi dopo il riscaldamento dei motori; ogni giro salva un rapporto completo in `data/logs/e2e/`) e
`pytest -m claude tests/test_hands_live.py` (il test del desktop usa mouse e tastiera: marcatore `desktop`).

## VRAM (RTX 4060, 8 GB) — misurato il 5/10 con `scripts/bench/vram_bench.py` e `tts_arena_bench.py`

| Pezzo | VRAM | Note |
|---|---|---|
| Desktop di Windows | ~0,5-0,8 GB | |
| Orecchie (parakeet-ultra) | 1,2 GB sulla GPU (dal 26/10; voce + orecchie al lavoro 5,6 GB) | il 6/10 con la 0.9.0 rallentavano la voce di 2× ed erano state spostate sul processore; con la 0.9.2 +3-4 % (`scripts/bench/ears_gpu_bench.py`). A riposo sul processore: la scheda video si libera tutta (5,0 GB, `scripts/rest_live.py`) |
| Voce (Qwen3-TTS 1.7B, GPU) | 3,9 GB con audio.cpp 0.9.2 (5,0 con la 0.9.0) | stessa voce campione per campione e stessi tempi, caricamento 5,9 s invece di 13,4 (`scripts/bench/tts_engine_bench.py`, 26/10). `perf_mode=flash_attention` scartato: cambia l'audio. Su CPU 12,8 s a frase (inutilizzabile) |
| Bonsai 2 27B | ~7 GB | non può convivere con la voce: niente modalità "tutto locale" su questa scheda |

Opzioni di sessione di qwen3_tts (trovate provando, le sconosciute vengono rifiutate): `talker_graph_arena_mb`,
`talker_weight_type`, `weight_type`, `conv_weight_type`. L'arena e i grafi CUDA (`GGML_CUDA_DISABLE_GRAPHS`) non cambiano la VRAM.
Ritmo grezzo di Qwen3: 18-26 caratteri al secondo (media 21,4; il JARVIS del film nella scena del volo ~21).
`pace()` porta ogni frase a 19 car/s × velocità (tolleranza ±6 %) con WSOLA (`jarvis/audio/stretch.py`).

Contesa della GPU (`scripts/bench/contention_bench.py`, secondi di sintesi ogni 100 caratteri):
voce da sola 2,32 · voce + orecchie su GPU che trascrivono 4,93 (picco 8,2 s per una frase) · orecchie su CPU 2,58.
Nella prova completa con orecchie su GPU la sintesi era 2-4× più lenta delle misure a motore libero: era
questa la causa dei buchi a metà frase che sentiva l'utente.

## Avvio

Parte da solo all'accesso a Windows (`system.autostart`, voce in HKCU\...\Run → `avvia_jarvis.pyw`, senza console).
A mano: `Avvia Jarvis.bat` (guardiano + console di diagnosi); se è già acceso apre solo la sua finestra.
A voce: "spegniti", "riavviati", e ogni impostazione ("impostazioni" per l'elenco).
**Le prove complete (`scripts/e2e.py`, `tests/test_e2e.py`) richiedono Jarvis spento**: stesse porte e la VRAM non
basta per due voci; la prova si rifiuta di partire con un messaggio chiaro. Senza guardiano: `.venv\Scripts\python -m jarvis --console`.
Registri: `data/logs/jarvis.log` (Jarvis), `data/logs/guardiano.log` (guardiano), `data/logs/cadute/` (rapporti).
Stato per il guardiano: `data/state/` (battito, ultima versione sana, nota di ripresa).

## Modelli Claude (misurati il 26/10 con `scripts/bench/claude_models_bench.py`)

Persona e strumenti veri di Jarvis, strumenti finti (niente azioni sul PC), 6 domande per modello:
| Modello | Prima parola (mediana) | Note |
|---|---|---|
| Opus 5.5 sforzo low (**cervello frontale**) | ~1,45 s | il migliore, strumenti sempre giusti |
| Sonnet 5.5 low | ~2,0 s | scartato: conto sbagliato e un carattere cinese in una frase |
| Haiku 5.5 low (**ripiego e custode**, `brain.fast_claude_model`) | ~1,25 s | buono, ~75% meno caro di Haiku 4.5 |
| Haiku 5.5 senza ragionamento | ~1,3 s | non più rapido, qualche errore |
| Haiku 4.5 senza ragionamento | ~0,7 s | il più rapido, meno capace (resta impostabile) |
Sotto-agenti: Opus 5.5 sforzo high. Un messaggio isolato di Jarvis pesa 6-16 mila token, quasi tutti in cache.

## Video e audio (28/10) — misure e scelta del modello dei suoni

**Dipendenze nuove** (nel pyproject; nel venv principale vanno installate con `uv sync`): `av` 19.0.2 (PyAV, FFmpeg
dentro il pacchetto: niente programmi esterni) e `yt-dlp[default]` 2026.10.26 (con `yt-dlp-ejs`; YouTube vuole un
interprete JavaScript: sul PC c'è deno, altrimenti Node v22 del ponte WhatsApp). yt-dlp va **tenuto aggiornato**:
YouTube cambia spesso. Senza i pacchetti lo strumento risponde con un errore chiaro e i test con PyAV si saltano.

Costo misurato con `scripts/bench/media_cost.py` (niente orecchie né scheda video: il parlato è stimato dai
sottotitoli automatici di YouTube, il tempo delle orecchie dalle misure del 26/10):

| Video | Scaricamento | Fotogrammi | Audio | Orecchie (stima) | Token per Claude |
|---|---|---|---|---|---|
| YouTube 10:28 (Geopop, 480p H.264) | 11,7-15 s | 1,75 s (solo fotogrammi chiave) | 2,4 s | ~3,4 s sulla scheda video (~157 s sul processore) | ~11.500: 8.860 di 20 immagini 768×432 + ~2.650 di trascrizione |
| stesso video in VP9 | 16,9 s | 6,5 s (VP9 decodifica tutto) | 2,5 s | idem | idem |
| file 1080p30 di 60 s, fotogrammi chiave ogni 10 s | — | 2,0 s (decodifica tutti, senza i B) | 0,1 s | ~0,3 s | ~4.550 (10 immagini) |
| YouTube 0:19 ("Me at the zoo", 240p) | 4,5 s | 0,4 s | 0,05 s | ~0,1 s | ~500 (4 immagini 320×240) |

Un'immagine costa ~1 token ogni 750 pixel: 768×432 = 443 token; 20 fotogrammi ≈ 8.900 token (dentro la sessione
restano in cache per i turni dopo). Fotogrammi e audio vanno in parallelo; la trascrizione segue l'audio.
Video di 1 ora (il massimo per volta, `media.max_minutes`): ~20 s di trascrizione con l'udito sulla scheda video,
~15 minuti se l'udito è rimasto sul processore (scheda video piena all'avvio).

**Modello dei suoni (fase 2, preparato, non ancora scaricato né provato).** Claude vede già i fotogrammi e legge
il parlato: al modello locale serve soprattutto l'orecchio per musica, suoni e rumori. Candidati che stanno negli
8 GB (con la voce scaricata), cercati il 28/10:
- **Audio Flamingo Next** (NVIDIA, aprile 2026, 8B, `audio-cpp/Audio-Flamingo-GGUF`, Q4_K **5,46 GB**): il più
  forte sull'audio tra i modelli aperti piccoli (secondo gli articoli già AF3 superava Qwen2.5-Omni 7B su MMAU e
  MMAR; numeri da verificare nella gara); gira su **audio.cpp, lo
  stesso motore delle orecchie** (famiglia `audio_flamingo`, opzione `instruct`), quindi niente runtime nuovo; solo
  audio, risponde in inglese; licenza NVIDIA non commerciale (uso privato sì). Vuole anche l'udito sul processore
  durante il lavoro (come il riposo). **È la scelta di partenza.**
- **Qwen2.5-Omni 7B** (`ggml-org/Qwen2.5-Omni-7B-GGUF`, Q4_K_M 4,68 GB + proiettore 1,55 GB = **6,23 GB**) su
  llama.cpp (supporto stabile dal 2025): audio e immagini insieme, MMAR ~56; più vecchio, stretto in 8 GB.
- **LFM3-Omni 2B** (Liquid AI, `ggml-org/LFM3-Omni-2B-GGUF`, Q8_0 1,85 GB + proiettore 0,54 GB = **2,39 GB**) su
  llama.cpp (supporto entrato il 27/10/2026, PR #31257; video non ancora, ma i fotogrammi li mandiamo come
  immagini): batte Qwen2.5-Omni 7B sui test audio+video insieme (Daily-Omni 70 contro 61) ma è più debole
  sull'audio da solo (MMAR 51,4); l'udito resta sulla scheda video, si carica in fretta.
- Scartati: Gemma 4 E4B (audio quasi solo parlato: OmniBench 20,7, MMAR 49), MiniCPM-o 4.5 (int4 11 GB, solo un
  fork di llama.cpp), Qwen3-Omni e Nemotron 3 Nano Omni 30B-A3B (16-19 GB).
Tutto in `jarvis/media/omni.py`: `GpuLease` (stesso lucchetto del riposo; aspetta che sia detta la frase già scritta,
scarica la voce, sposta l'udito se serve, e rimette tutto a posto anche con errori, annullamenti e due "stop" di
fila; la voce si riprova 3 volte), motori `AudioCppBackend` e `LlamaServerBackend` (llama-server in
`engine/llamacpp`, porta 8794), descrizione a finestre di un minuto con i fotogrammi di quel minuto.

**Gara di misura, fatta il 29/10 con Jarvis spento** (`scripts/bench/omni_race.py`, 15 clip da fonti libere in
`data/prove/omni_clips`, rigenerabili con `scripts/bench/omni_clips.py`, fonti e licenze in `FONTI.md`):

| Modello | Suoni | Musica | Video | Totale | Caricamento | Picco scheda video | 15 clip |
|---|---|---|---|---|---|---|---|
| **Audio Flamingo Next** (vince) | **8/8** | 6/8 | 0/3 (è cieco) | **14/19** | 11,9 s | 7,1 GB | 23 s |
| LFM3-Omni 2B | 4/8 | 6/8 | 2/3 | 12/19 | 4,1 s | 4,4 GB | 9 s |
| Qwen2.5-Omni 7B | 2/8 (inventa musica su pioggia e sirena) | 8/8 | 2/3 | 12/19 | 8,9 s | 7,7 GB | 35 s |

Audio Flamingo è l'unico che riconosce i suoni; le immagini le vede già Claude. Impostato `media.omni:
audio-flamingo-next`; i perdenti e llama.cpp sono stati cancellati (riscaricabili con `--scarica`). Correzioni nate
dalla gara: su audio.cpp la domanda va nel campo `prompt` (`instruct` è ignorato e il modello trascriveva invece di
rispondere); llama-server con un solo slot e contesto 8192 (con 16384 e più slot Qwen usciva dalla scheda: 1,6
token/s). Giro completo provato coi motori veri senza avviare Jarvis (`scripts/omni_live.py`): 28,5 s sul video
di cucina, picco 6,65 GB, voce e udito tornati sulla scheda video (frase sintetizzata e ritrascritta in 45 ms).
Da migliorare: i video quadrati o 4:3 costano di più a fotogramma (fino a 787 token contro 443 del 16:9): va
limitata anche l'area dei fotogrammi.

## Trappole già incontrate (non rifarle)

- **Claude Code toglie le immagini dai risultati MCP troppo grandi** (letto nel codice della CLI 2.1.310): stima
  1600 token a immagine e, oltre metà di `MAX_MCP_OUTPUT_TOKENS` (25.000 di base), conta i token con una chiamata
  in più e poi rimpicciolisce o toglie le immagini in eccesso. 20 fotogrammi = 32.000 stimati: il cervello frontale
  ora parte con `MAX_MCP_OUTPUT_TOKENS=100000` (`brain/claude.py`).
- **Il VP9 di YouTube non salta i fotogrammi non chiave** (`skip_frame` ignorato): 6,5 s invece di 1,75 s su 10
  minuti. yt-dlp ora sceglie H.264 (`avc1`) per primo, AV1 per ultimo.
- yt-dlp: con un percorso completo in `outtmpl` l'opzione `paths` è ignorata (avviso); i sottotitoli a volte danno
  429 (Too Many Requests): non servono a Jarvis, solo alla misura. Video e audio si scaricano separati (formato con
  la virgola): unirli richiederebbe FFmpeg.
- I video del telefono hanno la rotazione nei metadati: `frame.rotation` di PyAV è in senso antiorario e
  `PIL.rotate(rotation)` dà lo stesso orientamento di FFmpeg (verificato con un file ruotato da `ffmpeg
  -display_rotation 90`).
- Una copertina dentro un mp3/m4a è una "traccia video" di un fotogramma (`disposition` attached_pic): non è un video.
- Mentre la voce è scaricata per il modello dei suoni, ciò che Jarvis dovesse dire (un avviso di un compito)
  non si sente: la frase va persa a voce (resta nella finestra). Il prestito dura il tempo del modello.
- **OpenCode Zen, modelli gratis (28/10)**: la documentazione dice "Free-tier chat models require OpenCode; use paid
  models or Go from other clients": chiamare Zen direttamente (l'endpoint compatibile OpenAI è
  `https://opencode.ai/inference/openai/v1/chat/completions`, non `/zen/v1`) va bene solo per i modelli a pagamento.
  Per questo il ripiego passa da un `opencode serve` di Jarvis. Senza chiave OpenCode mostra lo stesso il fornitore
  `opencode` collegato (chiave "public") con i soli modelli gratis.
- **Il limite gratuito non è un errore**: la richiesta resta appesa e la sessione va in stato `retry` con
  `action.reason = "free_tier_limit"` e `next` = mezzanotte UTC (in millisecondi); OpenCode riproverebbe fino ad
  allora. Il 28/10 alle 16:20 il limite era esaurito per l'uso anonimo da questo PC, su tutti i modelli gratis
  (anche da un'installazione appena creata: non dipende dalla cartella dei dati); alle 20:04 funzionava di nuovo,
  ben prima della mezzanotte annunciata. Per questo Jarvis mette OpenCode in pausa al più per un'ora.
- **OpenCode legge sempre `%USERPROFILE%\.opencode\opencode.json`** (cartella "globale" nella home), anche con
  `OPENCODE_DISABLE_PROJECT_CONFIG=1` e cartelle XDG spostate: i permessi degli agenti di Jarvis sono scritti per
  intero, chiave per chiave, e gli strumenti spenti per nome (il jolly `"*": false` insieme a `"jarvis_*": true` non
  ha una precedenza documentata); ciò che sfugge chiede il permesso e Jarvis lo rifiuta.
- `opencode serve --port 0` non sceglie una porta libera: usa la 4096. Jarvis sceglie la porta da sé.
- `opencode.cmd`/`.ps1` di npm avviano `opencode.exe`: chiudere il .cmd lascia vivo il server. Si avvia l'.exe.
- Il `python.exe` del venv è un lanciatore (il processo vero è un figlio): spegnendo un processo si chiudono anche i
  figli (psutil), altrimenti restano orfani (visto nei test con il finto server).

- **Nei gruppi WhatsApp gli autori arrivano come LID** (`66039618482205@lid`), spesso senza nome, e i propri
  messaggi anche con il dispositivo attaccato (`…:15@lid`): il ponte ricava il LID dell'utente all'avvio, confronta
  senza il dispositivo e risolve i nomi (contatto, poi numero, altrimenti "un numero nascosto", mai il codice).
  Le risposte che citano un messaggio dicono a chi e cosa (27/10: "tu: bell' audio" senza contesto).
- **Il cervello a volte scrive una frase di servizio in inglese prima di uno strumento** ("Let me check the
  status."), che veniva pronunciata e attaccata alla frase dopo: `jarvis/core/narration.py` la trattiene e la
  scarta solo se subito dopo parte uno strumento (l'inglese chiesto apposta resta).
- **whatsapp-web.js 1.34.7 di npm (aprile) non arriva più a "pronto"** con il WhatsApp Web di ottobre 2026: il QR
  funziona, poi si ferma (WhatsApp ha rinominato `id._serialized` in `id.$1` a settembre). Corretto sul ramo
  principale: il ponte usa l'archivio del commit `064a3d5` (impronta sha512 nel lockfile). Se si rompe di nuovo:
  nel registro ci sono i passi del collegamento (QR, QR accettato, caricamento, pronto) e si aggiorna al commit
  più recente.
- **La cartella `data/whatsapp/session` la crea Chrome appena parte**, prima del QR: "collegato" è il file
  `data/whatsapp/collegato`, scritto solo quando WhatsApp è pronto.
- **L'app di WhatsApp per Windows è WhatsApp Web in una finestra (WebView2)**: da minimizzata si legge tutta per nome
  (UI Automation), ma non agisce (il clic simulato viene rifiutato) e Windows non la lascia aprire fuori dallo
  schermo. Per il background serve il WhatsApp nascosto (provato il 27/10 sulla chat con sé stessi, senza inviare).
- **Il QR di WhatsApp Web cambia ogni ~20 secondi** e la finestra di Jarvis non mostra immagini (per sicurezza): per
  collegare si apre una finestra vera di Chrome con WhatsApp Web, che il QR lo aggiorna da sola.
- **Un figlio Python su Windows legge stdin con la codifica di sistema** (non UTF-8): il ponte vero (Node) è UTF-8,
  quello finto dei test si riconfigura (`tests/fixtures/fake_wa_bridge.py`).
- **Il 25/10/2026 finisce l'ora legale**: nei test "3 giorni fa alle 15" non è `ora - 3 * 86400` (cade alle 16).
- **`loginusers.vdf` di Steam non ha più `MostRecent`**: l'ultimo account usato è quello col `timestamp` più alto;
  quello collegato adesso è in `HKCU\Software\Valve\Steam\ActiveProcess\ActiveUser` (dalla shell di Claude Code il
  registro è isolato e dice 0: dentro Jarvis è quello vero).
- **Wallpaper Engine si installa da Steam come un gioco ma resta sempre acceso**: non conta come "gioco in corso"
  (altrimenti "chiudi il gioco" chiuderebbe lo sfondo).

- **La sandbox di Codex (OpenAI) etichetta le cartelle a "integrità bassa"** (gruppo `CodexSandboxUsers` nei
  permessi): l'8/10 ha toccato le cartelle del Desktop dalla A a "Jarvis". Da lì tutto ciò che parte da quelle
  cartelle gira a integrità bassa: niente registro (avvio automatico), niente `~/.claude` (Claude Code non salva le
  credenziali rinnovate e l'accesso si rompe: "OAuth session expired and could not be refreshed"), il bash di Git
  non parte (0xC0000022). Controllo: `icacls "<cartella>"` → "Livello obbligatorio basso". Rimedio (lo fa l'utente):
  `icacls "<cartella>" /setintegritylevel "(OI)(CI)M" /T /C /Q`, poi rifare `/login` in Claude Code.
- **Mai provare Claude Code dal terminale con le impostazioni normali**: carica plugin, skill e prompt di Claude
  Code (26/10: 79.000 token per rispondere "ok"). Il percorso di Jarvis (`ClaudeBrain`: `setting_sources=[]`,
  `strict_mcp_config`, connettori claude.ai spenti) per la stessa risposta ne usa 435.
- Claude Code (CLI npm usata da Jarvis) aggiornato il 26/10 da 2.1.282 a 2.1.310: sparito l'avviso
  `unrecognized_model` per `claude-haiku-5-5`. Isolamento verificato dopo l'aggiornamento: 612 token in ingresso per
  "ok" senza strumenti, 1974 con WebSearch e WebFetch (le loro descrizioni); mai i 79.000 di una CLI non isolata.
- Le "App di avvio" di Windows spengono l'avvio automatico senza toccare la voce in Run
  (`StartupApproved\Run`, primo byte dispari = spento): Jarvis ora lo legge e lo riaccende quando glielo si chiede.
  All'avvio, se la voce in Run c'è, decide quell'interruttore (in entrambi i sensi) e Jarvis allinea l'impostazione
  **senza togliere la voce**: il 26/10 l'aveva tolta e Jarvis spariva dall'elenco delle App di avvio.
- **Da una dimostrazione dal vivo (26/10 pomeriggio)**, corretti con test: dopo `nessuna_risposta` Jarvis diceva
  ad alta voce "Resto in silenzio: parlava con un amico" (ora il testo del turno non si pronuncia e la conversazione
  si chiude); a riposo lo risvegliava la finestra d'ascolto rimasta aperta (ora solo il nome, il pulsante o
  Ctrl+Alt+J); 21 s di silenzio durante una ricerca sul web (ora "Controllo subito." già pronto, all'istante).
  Velocità misurata: prima voce 3-5 s nei turni normali; la trascrizione sul processore costava ~0,25 s per ogni
  secondo di frase (0,3 s sotto i 3 s, 2,7 s oltre i 10 s): **risolto la sera stessa portando l'udito sulla
  scheda video** (30-75 ms). Dopo ogni spostamento l'udito va riscaldato prima dello scambio (a freddo la prima
  frase costava 3,8 s).
- **Sicurezza (revisione esterna del 27/10, punti verificati e corretti)**: il canale `/ws` della finestra accettava
  chiunque (testo che vale come l'utente, conferme, parola d'ordine): ora solo Host 127.0.0.1/localhost e Origin della
  finestra o assente (`tests/test_ui_security.py`, finestra vera: `scripts/window_guard_live.py`). La politica dei
  sotto-agenti aveva buchi (`rm -r`, `Remove-Item -r`, `shutil.rmtree` da `python -c`, script scritti e poi
  eseguiti, clic "Acquista" nel browser): chiusi, ma resta una rete in più e non una garanzia; i clic con mouse e
  tastiera sul desktop non dicono cosa colpiscono. Prima la sicurezza, poi le funzioni nuove.
- **Comet 155 blocca clic, screenshot e script di Claude in Chrome** ("Host access is restricted by policy", su
  tutti i siti; dal suo aggiornamento del 20-22/10). Navigare e leggere funziona. Non è un criterio di Windows né
  un'impostazione dell'estensione (permessi completi): è Comet, segnalazione aperta anthropics/claude-code#103981.
  Rimedi: Chrome (sul PC c'è la 155.0.8059.85, non toccata dal problema), oppure Comet avviato con
  `--disable-features=ExtensionDebuggerStrictPolicyRestrictions` (provato solo su macOS, sparisce con Chromium 160;
  lo decide l'utente). Dal 27/10 gli assistenti lo sanno e passano a mouse e tastiera o al browser usa e getta.
- **Mai prove pesanti col Jarvis vero acceso** (26/10): un confronto con due Parakeet (~5,4 GB di memoria prenotata)
  mentre Jarvis girava ha esaurito la memoria virtuale del PC (32 GB, ~20 già presi da altri programmi): sono caduti
  senza messaggio prima le orecchie e poi Jarvis (il guardiano l'ha riavviato). Prima spegnere Jarvis.
- **audio.cpp aggiornato alla 0.9.2-drama3-base-small** (26/10): la 0.9.0 resta in `engine/audiocpp-0.9.0` (basta
  rinominare le cartelle per tornare indietro). Le arene ridotte di Parakeet (`PARAKEET_SESSION`) valgono uguali:
  2,7 GB di commit per le orecchie con entrambe (`scripts/bench/ears_memory_bench.py`).
- parakeet-ultra non ha un GGUF ufficiale: convertito da `moondream/parakeet-ultra` (safetensors) con
  `audiocpp_gguf.exe --family parakeet_tdt --type q8_0`, aggiungendo processor/tokenizer/generation_config del
  Parakeet v3 di NVIDIA (config e tokenizer identici bit per bit). Il pacchetto ufficiale del v3 tiene alcune parti
  a precisione più alta (873 contro 716 MB): ultra vince lo stesso.
- **Dai test dell'utente del 26/10** (tutti corretti, con test):
  - la sessione dell'utente non sapeva niente degli ospiti e Jarvis inventava ("anche sotto la vecchietta ho sentito
    l'utente", ma quella voce era stata trattata da ospite): ora il contesto dell'utente riassume gli scambi con gli ospiti;
  - a registrazione della voce finita, le frasi lette ancora per 20 s si ignorano (prima diventavano richieste);
  - il modello del significato si carica subito dopo "pronto": al primo turno la CPU piena spezzava la frase;
  - col riferimento di sistema la misura dell'eco può solo accendere la cancellazione (mentre l'utente parla sopra un
    video la misura crollava a 0,010 e la spegneva);
  - un sotto-agente aveva premuto Ctrl+W sulla finestra di Jarvis (Windows aveva rifiutato il cambio di finestra):
    ora i tasti senza clic verso quella finestra sono rifiutati nel codice;
  - con l'avvio automatico (pythonw) ogni sessione di Claude apriva una finestra di Terminale "claude": il guardiano
    ora lancia Jarvis con python.exe e una console nascosta, che i programmi figli ereditano.
- **Dopo un'interruzione Jarvis rispondeva una domanda in ritardo** (visto dal vivo il 26/10, presente da sempre):
  la sessione dell'SDK ha un'unica coda di messaggi e il turno interrotto lasciava lì il suo risultato
  (`error_during_execution`). Il turno dopo lo riceveva ("Ho avuto un problema… Può ripetere?") e da lì ogni
  risposta slittava di uno. Ora `ClaudeBrain` legge gli avanzi prima della domanda nuova (o riparte la sessione).
  I test di allora usavano un client finto che ricominciava da capo a ogni domanda: non poteva vederlo.
- **Le cuffie dell'utente rientrano nel microfono** (26/10, YouTube a cuffie chiuse: correlazione uscita→microfono
  0,64, il video entra attenuato di soli 26 dB e Parakeet lo trascrive). La cancellazione d'eco ora usa come
  riferimento tutto ciò che suona sull'uscita (`audio/loopback.py`, cattura loopback WASAPI con soundcard; misurato
  sul PC: −25/−38 dB sul video), solo in memoria. Impostazione `audio.echo_reference` ("system"/"jarvis"), a voce.
  soundcard va importato nel thread della cattura **prima** di inizializzare COM (altrimenti "Error 0x100000001")
  e non apre il microfono di questo PC (formato non "extensible"): per il microfono resta sounddevice.
- **La shell di Claude Code (in questa app) vede un registro isolato**: le scritture in HKCU fatte da lì non arrivano
  al registro vero e le letture mostrano la copia isolata (26/10: la shell vedeva Run presente e interruttore acceso,
  il registro vero aveva Run assente e interruttore spento). Lo stato reale si legge da un processo esterno, avviato
  con `Invoke-CimMethod Win32_Process Create` (come si avvia Jarvis), che scrive il risultato in un file.

- **WASAPI vuole COM pronto nel thread che apre i flussi.** Se sounddevice (cioè PortAudio) viene caricato per la
  prima volta in un altro thread (l'autodiagnosi gira in `asyncio.to_thread`), ogni apertura dal thread principale
  fallisce con "Unanticipated host error … WDM-KS" (il testo WDM-KS è un residuo, l'errore vero è WASAPI). Era la
  causa della caduta del 6/10. `ResilientBackend` riavvia PortAudio nel proprio thread prima di aprire.
- **`.venv\Scripts\pythonw.exe` è un lanciatore**: Jarvis gira in un processo figlio con un altro pid. Il guardiano
  riconosceva il battito dal pid e uccideva un Jarvis sano ogni 3 minuti (7/10): ora usa `JARVIS_RUN_ID`.
  Una lettura fallita del file del battito non deve mai far contare l'età dall'avvio.
- sounddevice 0.5.6 (l'ultima) usa `data.shape = ...` a ogni blocco audio, deprecato da NumPy 2.5: sostituito in
  `devices._fix_sounddevice` (con un NumPy futuro l'audio si sarebbe zittito del tutto).
- EmbeddingGemma 2 int8 (`model_quantized`) è 8 volte più lento della q4 sul processore (209 ms contro 26 a
  frase) a parità di qualità: quantizza al volo a ogni moltiplicazione. Le somiglianze sono tutte alte (0,66-0,70
  anche tra frasi che non c'entrano): la soglia è relativa a "ricordi di fondo" (`memory/store.py`).
- Le prove fine a fine scrivevano nella memoria vera (~150 frasi "quanto fa 17 per 23?" del 5/10, tolte il 7/10):
  ora con un dispositivo simulato Jarvis usa `data/prove/memoria_prove.db`.
- Somiglianza del testo per i doppioni: "gatto di nome Briciola" e "gatto di nome Tigre" sono simili all'81% e il
  secondo cancellava il primo. Ora contano le parole importanti (cifre comprese).
- La scheda HuggingFace di ReDimNet2+ è sbagliata: il modello vuole il suo frontend interno (TFMelBanks, 72
  bande, non 64) e `return_2d_output=True`. La conversione corretta è `scripts/explore/export_redimnet.py`.
- La funzione "scrivi per parlare" spostava ogni tasto nel campo della chat, anche mentre si scriveva in un altro
  campo: la parola d'ordine sarebbe finita come messaggio a Jarvis. Ora si sposta solo se nessun campo è attivo.
- Un Jarvis avviato dal terminale di Claude muore con la sessione di Claude (successo il 7/10 alle 14:59, senza
  registri). Per avviarlo da qui: `Invoke-CimMethod Win32_Process Create` (processo figlio di Windows, non di Claude).
- pycaw dà le casse predefinite già avvolte (con `FriendlyName`) ma il microfono come puntatore COM grezzo.

- audio.cpp prenota molta memoria virtuale: arene di Parakeet ridotte (`parakeet_tdt.*` col prefisso, altrimenti ignorate).
  Un solo TTS caricato alla volta (`max_loaded_models=1`), altrimenti la VRAM sfora in RAM.
- Il campo per il voice design via server è `instructions`; Qwen3 vuole la lingua "Italian", non "it".
- Claude Agent SDK: serve `claude.exe` nativo (il .cmd di npm viene rifiutato); senza isolamento eredita 399 strumenti
  dei connettori claude.ai (~282k token) → "Prompt is too long".
- Windows: `asyncio.sleep` sotto ~15,6 ms ritorna subito (risoluzione del timer). Per lo stesso motivo un'attesa
  con scadenza può finire qualche millisecondo **prima** della scadenza: per sapere quale limite è scattato si
  decide prima di aspettare, non rileggendo l'orologio dopo (28/10, cervello su OpenCode).
- La cancellazione d'eco attenua la voce dell'utente mentre Jarvis parla: per questo l'interruzione deve fermarlo entro ~400 ms.
- Limite dell'abbonamento: il risultato dice "You've hit your session limit · resets 8:20pm" e prima arriva un
  `RateLimitEvent` con `status="rejected"` e `resets_at`. Vale per tutti i modelli Claude (Haiku compreso).
  La parola in mezzo cambia ("5-hour limit", "weekly limit"): il 20/11 "5-hour" non era riconosciuto e Jarvis
  restava su Claude con una risposta vuota. Ora vale ogni "hit your … limit" e, senza `RateLimitEvent`, l'ora di
  "resets 1:40pm" (orologio del PC).
- **OpenCode, "This model is not available in your country"** (403): è un divieto del singolo modello, non
  dell'account. Prima metteva in pausa tutto OpenCode per 10 minuti e fermava l'esame; ora il modello è bocciato,
  escluso dalla classifica (anche dopo un riavvio, finché un esame non lo ritrova) e si passa al successivo.
- **Gli ultimi eventi di una risposta di OpenCode arrivano anche dopo la risposta finale**: con due domande di fila
  la frase vecchia veniva ridetta all'inizio del giro nuovo (e un cambio di modello saltava). I messaggi dei giri
  finiti ora si ignorano (`TurnState.past`).
- L'SDK legge i messaggi con un buffer di 1 MB: un'istantanea del desktop (albero UI + immagine) lo supera →
  `max_buffer_size` a 64 MB nei sotto-agenti.
- Screenshot di Windows-MCP all'80 % (1536 px): oltre ~1568 px l'API rimpicciolisce di nascosto e le coordinate sballano.
- Windows-MCP vuole Python ≥ 3.13 (il venv di Jarvis è 3.12): sta in `engine/windows-mcp/.venv`, telemetria spenta.
  PowerShell, FileSystem, Registry e Process esclusi (sfuggirebbero alla politica di sicurezza).
- Playwright MCP ha `browser_run_code_unsafe` (codice arbitrario sul PC): bloccato. I caricamenti di file su siti chiedono sempre conferma.
- `claude --chrome` funziona anche con `strict_mcp_config`: il server `claude-in-chrome` è "dinamico".
- Il motore carica i modelli al primo uso: scaldarlo con una frase presa dalla cache non carica niente e la prima
  risposta vera pagava ~5,5 s. `VoiceOutput.prime()` fa una sintesi vera saltando la cache. Prima voce: 7,9 s → 0,86 s.
- Dopo la prima frase ("Subito.") la successiva arriva in ~1-1,5 s: è la velocità di Qwen3-TTS (niente streaming
  nel motore); un riscaldamento più lungo non cambia nulla (misurato).
- `subprocess.run(["bash", ...])` da Python apre il bash di WSL (Windows cerca in System32 prima del PATH): niente
  variabili d'ambiente, percorsi Linux. Usare il percorso completo del bash di Git (`self_improve.git_bash()`).
- Due processi CUDA sulla stessa scheda (orecchie e voce) si rallentano a vicenda in modo pesante su Windows:
  le orecchie stanno sul processore (`speech.ears_device`).
- Il motore ignora `speed` per Qwen3: il ritmo si fa in Python (`VoiceOutput.pace`). Il tempo di sintesi si
  stima con `synth_scale · (a + b · caratteri)` e la scala impara dalle sintesi vere.
- La finestra (WebView2) teneva in cache i vecchi `.js`: dopo una modifica girava codice vecchio. Ora la pagina
  firma i file con la data di modifica (`?v=…`) e tutto è servito con `no-store`.
- Con la finestra nascosta `requestAnimationFrame` si ferma: per verifiche e test `JarvisCore.advance(secondi)`
  fa avanzare le animazioni fotogramma per fotogramma.
- Vetrina e Chrome nello stesso ciclo asyncio dei test lasciavano socket aperti (avvisi = errori): la vetrina gira
  in un processo a parte, come il Jarvis vero.
- La vetrina in modalità dimostrazione ripeteva le domande all'infinito: ora la dimostrazione gira una volta sola,
  per il resto scene fisse.
- Ollama cloud: `glm-5.1` e `deepseek-v4-flash` ritirati il 25/09/2026, `deepseek-v4-pro` solo a pagamento;
  `gpt-oss:120b-cloud` funziona col piano gratuito (a volte storpia un numero detto a parole, il risultato è giusto).

## Prossimi passi

1. **Pubblicato il 24/11** (repository pubblico, release v0.1.0; provato da fuori senza login: pagina, README,
   release, download dell'installatore, aggiornatore e i 20 file del primo avvio). Firma gratuita chiesta a
   SignPath Foundation il 24/11 (progetto nuovo, senza statistiche d'uso: possono anche dire di riprovare più avanti).
   Se approvano: la firma va aggiunta a `installatore.yml`, la frase "Free code signing provided by SignPath.io,
   certificate by SignPath Foundation" nei due README e nelle note delle release.
2. Provare l'aggiornamento vero dalla 0.1.0 alla 0.1.1 con la copia installata da GitHub su questo PC (il 24/11, con le librerie preparate a mano dopo il difetto dell'installatore), e un'installazione da zero della 0.1.1.
3. Le prove a voce parlata che mancano: presentazione (passi voce e abitudini), guardare lo schermo, segnalazioni,
   programmi, inglese al microfono; il giro dopo una caduta vera; Gmail con un account vero nel modo veloce.
4. Un PC senza NVIDIA (AMD e Intel con Vulkan, o solo processore): oggi il primo avvio si ferma; serve una voce
   veloce di riserva (la voce clonata sul processore impiega 12,8 s a frase). Windows 10 non è mai stato provato.
5. Abilità del browser (aprire un sito anche in incognito, far partire un video, schermo intero, chiudere): oggi
   passano dai sotto-agenti e quattro gesti così sono costati ~1,3 milioni di token.
6. Più persone che parlano: chi si inserisce in una conversazione già aperta dovrebbe dire "Jarvis" (oggi una frase
   senza nome di una voce conosciuta vale come rivolta a lui); il contesto di proprietario e ospiti è diviso in due
   sessioni del cervello.
7. Video: limitare anche l'area dei fotogrammi (quadrati e 4:3 costano fino a 787 token a fotogramma contro 443).
8. Ricordi da foto e screenshot (codificatore immagini di EmbeddingGemma 2), se serve.
9. Dipendenze del ponte WhatsApp segnalate da npm: `glob@10` vecchio e `fluent-ffmpeg` non più mantenuto (dentro
   whatsapp-web.js).
