# Jarvis

Assistente vocale per Windows, in italiano o in inglese, ispirato al JARVIS dei film di Iron Man. Lo chiami per nome, gli parli
come a una persona e lui risponde con la sua voce, usa il PC al posto tuo e lavora in background mentre continui a
parlargli.

Riconoscimento della voce, sintesi, memoria e riconoscimento di chi parla girano sul tuo PC. Esce dal PC solo ciò
che serve al cervello (Claude, oppure un modello gratuito di OpenCode o di Ollama) e ciò che chiedi tu, per esempio
una ricerca sul web.

> Stato: in sviluppo. Oggi gira su un PC di sviluppo. Il primo avvio grafico c'è (controllo del PC, scelta del
> cervello, download dei modelli, voce di base); manca ancora il programma di installazione. Vedi
> [Prossimi passi](docs/STATO.md#prossimi-passi).

## Cosa sa fare

- Conversazione naturale. Si interrompe quando gli parli sopra, capisce quando una frase non è per lui, non si
  confonde con la propria voce, con un video o con la TV (cancellazione dell'eco anche su ciò che suona in cuffia).
- Usa il PC. Apre app, siti, file e cartelle, regola volume e musica, legge e scrive negli appunti; per i lavori
  lunghi affida il compito a un sotto-agente che usa browser, mouse e tastiera e riferisce quando ha finito.
- App. WhatsApp (leggere, cercare, rispondere, inviare file, gruppi, trascrivere i vocali), Gmail (leggere,
  cercare, rispondere, etichette, filtri, risposta automatica), giochi di Steam, Epic e Xbox. Tutto ciò che altri
  vedono parte solo dopo un tuo sì.
- Video e audio. Guarda un video di YouTube o del PC (fotogrammi e parlato con l'ora) e ascolta i vocali.
- Memoria. Ricorda ciò che conta e lo ritrova per significato, non per parole uguali; a conversazione finita
  aggiorna da solo i ricordi.
- Riconosce chi parla. Il proprietario può tutto, le persone conosciute ciò che concedi, gli ospiti solo le cose di
  base e niente informazioni personali. I permessi li controlla il codice, non il modello.
- Programmi. Promemoria, richieste da eseguire a un'ora ("ogni mattina alle 8 il riassunto delle notizie") e
  compiti a ripetizione.
- Si configura a voce. Ogni impostazione si cambia parlando ("parla più lentamente", "metti la grafica cinema");
  per cambiamenti più profondi modifica il proprio codice in una copia separata, con i test, e chiede conferma.
- Non si ferma. Un guardiano lo riavvia se cade e torna all'ultima versione sana; se l'audio sparisce va avanti e
  riprova da solo; quando l'abbonamento a Claude è esaurito passa a modelli gratuiti.

## Requisiti

| | Consigliato (provato) | Note |
|---|---|---|
| Sistema | Windows 11, 64 bit | Windows 10 non ancora provato |
| Scheda video | NVIDIA con 8 GB (RTX 4060) | voce e orecchie insieme occupano circa 5,6 GB; senza scheda video la voce clonata è troppo lenta |
| Memoria | 32 GB | |
| Disco | circa 10 GB, più 5 GB per il modello di musica e suoni | |
| Cervello | abbonamento Claude Pro o Max, oppure una chiave API | in alternativa OpenCode (gratis, senza account) o Ollama |
| Altro | microfono, Chrome (per WhatsApp), Node 22 | |

## Installazione

Il modo comodo è l'installatore `Jarvis-Setup-<versione>.exe` (si costruisce con `python scripts/build_installer.py`,
serve Inno Setup 6): si installa per il proprio utente, senza permessi di amministratore, prepara da solo Python e
le librerie e apre Jarvis, che parte dal primo avvio (controllo del PC, cervello, download dei modelli, prova di
microfono e casse); il resto lo chiede lui a voce. Chi preferisce i sorgenti: `git clone`, `uv sync`,
`python -m jarvis`.

L'installatore non è firmato: un certificato per firmare i programmi costa circa 200 € l'anno, che questo progetto
non ha. Per questo al primo avvio Windows mostra "PC protetto" e bisogna scegliere "Ulteriori informazioni" e poi
"Esegui comunque". Quando il repository sarà pubblico si potrà chiedere la firma gratuita di
[SignPath Foundation](https://signpath.org/), che firma i progetti open source. Nel frattempo ogni release ha
l'impronta SHA-256 dell'installatore, per controllare che sia quello pubblicato.

Le copie installate si aggiornano da sole dalle release di GitHub (solo versioni con l'impronta dell'installatore,
installate quando Jarvis è fermo; si può scegliere "chiedi" o "spenti", anche a voce). Le copie prese con `git`
si aggiornano con `git pull`.

## Sviluppo

Serve [uv](https://docs.astral.sh/uv/).

```
uv sync
bash scripts/check.sh
```

`python -m jarvis` apre da solo il primo avvio se manca qualcosa di indispensabile; `python -m jarvis --setup` lo
rifà. `python scripts/primo_avvio_prova.py` mostra la pagina del primo avvio con i dati veri del PC ma senza
toccare niente (download simulato, configurazione in una cartella temporanea).

`scripts/check.sh` controlla lo stile (ruff) e lancia le prove che non richiedono motori, Claude o hardware. Le prove
che usano i motori audio, Claude vero o mouse e tastiera hanno i loro marcatori (`engine`, `claude`, `hardware`,
`desktop`). `python scripts/mutation_check.py` inserisce di proposito dei difetti nel codice e verifica che le prove
se ne accorgano: un controllo che resta verde con un difetto dentro non protegge niente.

Per lavorare su una copia senza toccare quella che usi tutti i giorni, vedi
[Copia di prova](docs/ARCHITETTURA.md#copia-di-prova).

Documenti:

- [docs/ARCHITETTURA.md](docs/ARCHITETTURA.md): com'è fatto e perché.
- [docs/STATO.md](docs/STATO.md): cosa funziona, le misure, le trappole già incontrate, i prossimi passi.
- [docs/GMAIL.md](docs/GMAIL.md): come collegare Gmail.

## Licenza

Il codice è distribuito con licenza [Apache 2.0](LICENSE). Modelli, motori e librerie hanno le loro licenze, elencate
in [LICENZE_TERZE_PARTI.md](LICENZE_TERZE_PARTI.md): alcune vietano l'uso commerciale.

Progetto indipendente, senza legami con Marvel, Disney, Anthropic, NVIDIA, Google o Meta. WhatsApp viene usato
attraverso WhatsApp Web in modo non ufficiale.
