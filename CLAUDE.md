# Jarvis: istruzioni per chi lavora sul progetto

Assistente vocale italiano per Windows. Prima di toccare qualunque cosa leggi `docs/STATO.md` (cosa funziona, misure,
trappole, prossimi passi) e `docs/ARCHITETTURA.md`. Per Gmail anche `docs/GMAIL.md`.

Se esiste `privato/CLAUDE.md`, leggilo e seguilo: sono le regole di chi usa questa copia (non è nel repository).

## Come lavorare
- Testi, commenti, commit e documenti in italiano.
- Rigore: test automatici, anche degli errori e dei casi limite; ogni protezione nuova ha una mutazione in
  `scripts/mutation_check.py` e va verificata; `ruff` senza avvisi; suite offline con
  `pytest -m "not engine and not claude and not hardware and not desktop" -W error`. Mai dire "fatto" senza averlo
  verificato, e dire chiaramente cosa non è stato provato.
- Git: commit piccoli e chiari. Il messaggio va scritto in un file e passato a `git commit -F`.
- Ogni funzione nuova si deve poter cambiare anche a voce: campo in `jarvis/config.py` con commento, scorciatoia o
  normalizzazione in `jarvis/tools/front.py`. Uno strumento nuovo del cervello va in `TOOL_GROUPS` (o in `BASE_TOOLS`)
  e deve avere un test (`test_every_tool_is_tested`).
- Aggiorna `docs/STATO.md` e `docs/ARCHITETTURA.md` quando cambia qualcosa di importante.
- Niente dati personali nel repository: nomi, account, percorsi della propria cartella utente, conversazioni. Il nome
  di chi usa Jarvis sta solo in `data/config.yaml` (`persona.user_name`); nei test si usano nomi inventati.
  `tests/test_privacy.py` cerca nei file del repository le parole elencate in `privato/parole.txt`, se c'è.

## Vincoli
- Mai lanciare Claude Code dal terminale con le impostazioni normali: carica plugin e connettori (decine di migliaia
  di token). Il percorso di Jarvis è isolato (`setting_sources=[]`).
- Le prove con Claude vero consumano l'abbonamento: per le prove ripetute usa il cervello `ollama-cloud`.
- Mai prove pesanti (banchi di prova, modelli grossi sulla scheda video) mentre il Jarvis vero è acceso: può esaurire
  la memoria e farlo cadere. Prima va spento.
- Messaggi ed email a nome dell'utente partono solo con la sua conferma. I suoi dati (chat, posta) si leggono solo
  quando serve; nelle sonde si stampano conteggi, mai nomi o testi.
- La parola d'ordine della voce non si chiede né si scrive mai.
- Le impostazioni di sistema e di sicurezza di Windows le cambia l'utente.

## Trappole tecniche già incontrate
- Negli heredoc di Bash le barre rovesciate si rompono (`\n` diventa un a capo vero): per le modifiche con le barre
  usa i file.
- La shell di Claude Code vede un registro di Windows isolato: lo stato vero di HKCU si legge da un processo esterno
  (`Invoke-CimMethod Win32_Process Create`). Jarvis avviato dalla sessione di Claude muore con lei: per avviarlo
  usa lo stesso metodo (`pythonw avvia_jarvis.pyw`).
- Nelle worktree non ci sono `.venv`, `engine`, `models`, `node_modules`: usa il Python del progetto principale e
  le variabili `JARVIS_PYTHON`, `JARVIS_ENGINE_DIR`, `JARVIS_MODELS_DIR` (come `scripts/check.sh`). Il giro completo
  delle mutazioni si fa in una copia separata (non tocca i file che Jarvis sta usando).
- `uv sync` toglie i pacchetti non dichiarati in `pyproject.toml`: ogni dipendenza va dichiarata (`uv add`).
- Alcuni browser basati su Chromium (Comet 155) bloccano clic e screenshot dell'estensione Claude in Chrome: si può
  solo leggere la pagina e scrivere nei campi.
- Spegnere Jarvis dall'esterno: comando `{"cmd": "text", "text": "Spegniti del tutto"}` sul websocket della sua
  finestra (`ws://127.0.0.1:8791/ws`).
