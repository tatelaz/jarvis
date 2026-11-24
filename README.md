# Jarvis

Jarvis è un assistente vocale per Windows. Lo chiami per nome, gli parli normalmente e ti risponde a voce, in
italiano o in inglese. Può usare il PC al posto tuo: apre programmi e siti, legge e scrive su WhatsApp e Gmail, cerca
sul web, guarda un video e ti dice cosa c'è dentro, si ricorda le cose che gli dici. Nome e stile vengono dal JARVIS
dei film di Iron Man.

Orecchie, voce, memoria e riconoscimento di chi parla girano sul tuo PC. Per capire e rispondere serve un modello
linguistico, il "cervello": Claude (con il tuo abbonamento o con una chiave API) oppure un modello gratuito di
OpenCode, che non chiede nemmeno un account.

> Versione di prova. Funziona tutti i giorni sul PC su cui è nato, ma su altri PC è stato provato poco. Se qualcosa
> non va, apri una segnalazione nella pagina Issues e allega `data\logs\jarvis.log`.

## Cosa gli si può chiedere

Qualche esempio, detto come lo diresti a una persona:

- "Jarvis, che mail mi sono arrivate oggi?" e poi "rispondi che ci sono anche io".
- "Leggimi gli ultimi messaggi del gruppo del calcetto."
- "Guarda questo video di YouTube e dimmi di cosa parla."
- "Guarda lo schermo: che errore è questo?" (fa uno screenshot di ciò che vedi, solo quando glielo chiedi, e non lo
  salva; con OpenCode vede solo il titolo della finestra).
- "Ricordami domani alle nove di chiamare il dentista."
- "Ogni mattina alle otto fammi il riassunto delle notizie."
- "Apri la cartella dei download", "alza il volume", "metti in pausa la musica".
- "Parla più lentamente", "dammi del Lei", "metti la grafica cinema".

Tutto ciò che altre persone vedono (un messaggio, una mail, un file inviato) parte solo dopo che hai detto sì.

Alcune cose che fa da solo:

- Puoi interromperlo mentre parla. Capisce quando una frase non è rivolta a lui e non si confonde con la sua voce,
  con la TV o con un video che sta suonando, anche in cuffia.
- I lavori lunghi (compilare un modulo su un sito, cercare e confrontare cose) li passa a un aiutante che usa
  browser, mouse e tastiera, e intanto puoi continuare a parlargli. Quando ha finito te lo dice.
- Si ricorda le cose importanti e le ritrova per significato, non per parole uguali. A fine conversazione aggiorna
  i ricordi da solo.
- Riconosce le voci. Tu puoi fare tutto, le persone che conosce fanno quello che gli permetti, gli ospiti solo le
  cose di base e senza accesso ai tuoi dati. I permessi li controlla il programma, non il modello.
- Ogni impostazione si cambia a voce. Per cambiamenti più grossi può modificare il proprio codice: lo fa in una copia
  separata, fa girare i test e ti chiede conferma prima di usarlo.
- Se si blocca un guardiano lo riavvia, e se una versione nuova non parte torna all'ultima che funzionava. Quando
  l'abbonamento a Claude è esaurito passa da solo ai modelli gratuiti.
- Se qualcosa non va puoi segnalarlo a voce o dalla finestra: parte un rapporto tecnico ripulito dai tuoi dati, solo
  se vuoi (vedi [Segnalare un problema](#segnalare-un-problema)).

## Cosa serve

| | Provato | Note |
|---|---|---|
| Sistema | Windows 11, 64 bit | Windows 10 non ancora provato |
| Scheda video | NVIDIA con 8 GB (RTX 4060) | per ora serve una NVIDIA: voce e orecchie insieme usano circa 5,6 GB |
| Memoria | 16 GB | |
| Disco | circa 10 GB | più 5 GB se vuoi il modello di musica e suoni |
| Cervello | abbonamento Claude Pro o Max, oppure una chiave API | oppure OpenCode, gratis e senza account |
| Altro | microfono e casse o cuffie | per WhatsApp serve Chrome o Edge |

Python, Node.js, Git e OpenCode non serve installarli prima: se mancano li scarica Jarvis.

## Installazione

1. Scarica `Jarvis-Setup-<versione>.exe` dalla pagina Releases e aprilo.
2. Windows mostra "PC protetto" perché l'installatore non è firmato (sotto spiego perché): premi "Ulteriori
   informazioni" e poi "Esegui comunque".
3. Si installa per il tuo utente, senza permessi di amministratore, in `%LOCALAPPDATA%\Programs\Jarvis`. Dura circa
   un minuto: intanto prepara Python e le librerie.
4. Alla fine si apre il primo avvio: controlla il PC, ti fa scegliere il cervello, scarica i modelli (circa 5 GB da
   GitHub e Hugging Face, si riprende da dove era rimasto se la connessione cade) e prova microfono e casse.
5. Jarvis si accende e si presenta. Ti chiede come ti chiami, se dargli del tu o del Lei, impara la tua voce, ti
   aiuta a collegare le app. Puoi dire "dopo" e riprendere con "continuiamo la configurazione".

Chi preferisce i sorgenti: `git clone`, poi `uv sync` e `python -m jarvis` (serve [uv](https://docs.astral.sh/uv/)).
Porta allo stesso primo avvio.

Per parlargli chiamalo per nome ("Jarvis, ...") oppure premi Ctrl+Alt+J e parla. Ctrl+Alt+K ferma tutto. Se
preferisci scrivere, nella sua finestra basta iniziare a digitare.

## Il cervello

Si sceglie al primo avvio e si cambia quando vuoi, anche a voce.

- **Claude con l'abbonamento** (Pro o Max). Jarvis usa il login ufficiale di Claude Code: si apre il browser su
  claude.ai e accedi con il tuo account. Jarvis non vede mai la tua password. Consuma i limiti del tuo abbonamento.
- **Claude con una chiave API**. Paghi a consumo ad Anthropic. La chiave la incolli nella pagina del primo avvio, si
  controlla subito e si salva cifrata.
- **OpenCode**. Modelli gratuiti, senza account e senza chiave. Rispondono un po' peggio di Claude, ma per partire
  vanno bene. Jarvis li usa anche come riserva quando Claude non risponde.

Chi usa già Ollama può collegarlo dopo (sul PC o nel cloud).

## WhatsApp, Gmail e giochi

**WhatsApp**: di' "collega WhatsApp" e inquadra il codice QR con il telefono, come per WhatsApp Web. La prima volta
Jarvis scarica i pezzi che gli servono (qualche minuto). WhatsApp viene usato attraverso WhatsApp Web in modo non
ufficiale: se WhatsApp cambia qualcosa può smettere di funzionare finché non si aggiorna Jarvis.

**Gmail**: di' "collega Gmail". Il modo veloce è una password per le app di Google, che ti fa creare in un minuto:
serve la verifica in due passaggi attiva sull'account. Jarvis apre la pagina di Google, crei una password chiamata
"Jarvis" e la incolli nella finestra di Jarvis (mai a voce). Così legge, cerca, risponde, scrive, inoltra, mette
etichette e sposta nel cestino. Filtri e risposta automatica servono di rado e chiedono il collegamento completo, con un
progetto Google gratuito: i passaggi sono in [docs/GMAIL.md](docs/GMAIL.md).

**Giochi**: apre i giochi di Steam, Epic e Xbox per nome ("apri Rocket League").

## Cosa resta sul PC e cosa esce

L'audio non esce mai dal PC: riconoscimento della voce, sintesi, memoria e voci conosciute sono tutti in locale.
Escono:

- il testo di quello che chiedi, verso il cervello che hai scelto (Anthropic per Claude, OpenCode per i modelli
  gratuiti);
- le ricerche sul web e le cose che gli chiedi tu di fare in rete;
- una volta al giorno una domanda a GitHub per vedere se c'è una versione nuova (si spegne con "aggiornamenti
  spenti");
- al primo avvio, i download dei modelli da GitHub e Hugging Face;
- le segnalazioni agli sviluppatori, solo quando vuoi tu (vedi sotto).

### Segnalare un problema

Se qualcosa non va puoi dirlo a Jarvis ("segnala un problema: non mi ha capito quando...") o scriverlo nella
finestra, in fondo al registro, alla voce "segnala un problema". Parte un rapporto tecnico che aiuta a correggere
il difetto: versione, Windows, scheda video, gli errori del registro e le tue parole sul problema. Prima di partire
il rapporto viene ripulito: niente frasi delle conversazioni né risposte di Jarvis, niente nome tuo o della tua
cartella, email, numeri di telefono o contatti. Dalla finestra vedi il testo esatto prima di mandarlo; a voce Jarvis
ti dice cosa sta per mandare e parte solo con un tuo sì.

Dopo una caduta Jarvis ti chiede se mandare il rapporto. Puoi cambiare questa scelta a voce ("rapporti delle cadute
sempre" o "mai"). Il rapporto arriva come issue in un repository privato degli sviluppatori, attraverso un piccolo
server che non salva niente, nemmeno il tuo indirizzo IP (`servizi/segnalazioni`). Se la rete non c'è, riparte al
prossimo avvio.

Chiavi, password per le app e permessi di Gmail si salvano cifrati con Windows: li può leggere solo il tuo utente su
questo PC. I tuoi dati stanno in `%LOCALAPPDATA%\Programs\Jarvis\data`.

## La voce

Di base Jarvis parla con una di due voci incluse, prese da letture di LibriVox (dominio pubblico) e passate con un
effetto che ricorda quello dei film. Puoi crearne altre da un file audio o video ("crea una voce da questa
intervista"): Jarvis sceglie da solo 10-14 secondi di parlato pulito e li trascrive. La voce che ne esce è ispirata a
quella vera ma non è la voce vera: è un'imitazione fatta dal modello a partire da pochi secondi. Usala solo per te, e
mai per far credere che una persona abbia detto qualcosa.

## Aggiornamenti e disinstallazione

Le copie installate con l'`.exe` si aggiornano da sole dalle release di GitHub: Jarvis prende solo file con
l'impronta SHA-256 pubblicata e si aggiorna quando è fermo da un quarto d'ora. Se preferisci decidere tu, digli di
chiederti prima ("per gli aggiornamenti chiedimi"). Le copie prese con git si aggiornano con `git pull`.

Anche la copia installata è un repository git (lo diventa al primo avvio), quindi le modifiche che Jarvis fa al suo
codice su tua richiesta non si perdono con gli aggiornamenti: la versione nuova si unisce alle tue modifiche in una
copia a parte, con le prove. Se le due cose toccano gli stessi punti le sistema un sotto-agente e Jarvis ti chiede
conferma; se non si riesce resti sulla tua versione, che funziona, e lui te lo dice.

Per disinstallarlo: Impostazioni, App, App installate, Jarvis. Ti chiede se tenere dati e modelli: se dici sì, una
nuova installazione li ritrova.

## Limiti di oggi

- Serve una scheda NVIDIA: il motore della voce per ora usa solo CUDA. Sulle schede AMD e Intel non parte, e con il
  solo processore impiegherebbe circa 13 secondi a frase. Il primo avvio lo dice prima di scaricare qualsiasi cosa.
- Provato solo su Windows 11 e su pochi PC.
- Parla solo italiano e inglese.
- L'installatore non è ancora firmato (vedi [Firma del codice](#firma-del-codice-code-signing-policy)). Intanto ogni
  release ha le impronte SHA-256 dei suoi file (`SHA256SUMS.txt`), così puoi controllare che siano proprio quelli
  pubblicati.

## Sviluppo

Serve [uv](https://docs.astral.sh/uv/). Su un Windows senza il
[Visual C++ Redistributable](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist) (di solito c'è già)
va installato, o onnxruntime non si carica: l'installatore invece lo porta con sé.

```
uv sync
bash scripts/check.sh
```

`python -m jarvis` apre da solo il primo avvio se manca qualcosa di indispensabile; `python -m jarvis --setup` lo
rifà. `python scripts/primo_avvio_prova.py` mostra la pagina del primo avvio con i dati veri del PC ma senza toccare
niente (download finto, configurazione in una cartella temporanea).

`scripts/check.sh` controlla lo stile (ruff) e lancia le prove che non hanno bisogno dei motori audio, di Claude o
dell'hardware; quelle che ne hanno bisogno hanno i marcatori `engine`, `claude`, `hardware` e `desktop`.
`python scripts/mutation_check.py` inserisce di proposito dei difetti nel codice e controlla che le prove se ne
accorgano: un controllo che resta verde con un difetto dentro non protegge niente.

Ogni commit passa dalle stesse prove su GitHub Actions ([prove.yml](.github/workflows/prove.yml)). Una versione
nuova si pubblica così: versione in `pyproject.toml`, note in `docs/versioni/X.Y.Z.md`, poi il tag `vX.Y.Z`; il
resto (installatore, bundle per gli aggiornamenti, impronte, release) lo fa
[installatore.yml](.github/workflows/installatore.yml).

Per lavorare su una copia senza toccare quella che usi tutti i giorni vedi
[Copia di prova](docs/ARCHITETTURA.md#copia-di-prova). Gli altri documenti:

- [docs/ARCHITETTURA.md](docs/ARCHITETTURA.md): com'è fatto e perché.
- [docs/STATO.md](docs/STATO.md): cosa funziona, le misure, i problemi già incontrati, i prossimi passi.
- [docs/GMAIL.md](docs/GMAIL.md): come collegare Gmail.

## Firma del codice (Code signing policy)

Un certificato per firmare i programmi costa circa 200 € l'anno, che questo progetto non ha. La firma gratuita che
[SignPath Foundation](https://signpath.org/) offre ai progetti open source si chiede dopo la prima release: finché non
arriva, l'installatore non è firmato e Windows mostra "PC protetto". Se la concedono, qui comparirà: *Free code signing
provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org)*.

- L'installatore si costruisce solo su GitHub Actions, dal codice di questo repository e dal tag della versione
  ([.github/workflows/installatore.yml](.github/workflows/installatore.yml)), mai su un PC.
- Ruoli: autore, revisore (ogni modifica proposta da altri passa da una revisione) e approvatore delle firme:
  [tatelaz](https://github.com/tatelaz).
- Privacy: Jarvis non manda niente ad altri sistemi se non quello descritto in
  [Cosa resta sul PC e cosa esce](#cosa-resta-sul-pc-e-cosa-esce), che l'installatore mostra prima di installare; ciò
  che parte da solo (il controllo delle versioni nuove, i rapporti delle cadute se li vuoi automatici) si spegne a
  voce.

## Licenza

Il codice è distribuito con licenza [Apache 2.0](LICENSE). Modelli, motori e librerie hanno le loro licenze, elencate
in [LICENZE_TERZE_PARTI.md](LICENZE_TERZE_PARTI.md): alcune vietano l'uso commerciale.

Progetto indipendente, senza legami con Marvel, Disney, Anthropic, NVIDIA, Google o Meta.
