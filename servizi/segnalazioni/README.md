# Server delle segnalazioni

Un Cloudflare Worker che riceve le segnalazioni di Jarvis (`jarvis/feedback.py`) e le scrive come issue in un
repository GitHub **privato**. Chi usa Jarvis non ha bisogno di nessun account: la chiave di GitHub sta solo qui.

- Il rapporto arriva già ripulito da Jarvis: niente frasi delle conversazioni, nomi, email, numeri, contatti.
- Il server non salva niente. L'indirizzo IP serve solo a contare le richieste e non finisce nella issue.
- `@nome` e `#12` nel testo si rendono innocui, così GitHub non avvisa nessuno e non collega altre issue.
- Limiti: 70 KB a segnalazione, e (se il piano Cloudflare ha il limite di richieste) 3 al minuto per PC e 30 al
  minuto in tutto. Il piano gratuito di Workers regge 100 000 richieste al giorno.

`logica.js` fa tutto; `worker.js` la passa a Cloudflare. Le prove (`worker.test.mjs`, senza rete) girano con
`node --test servizi/segnalazioni/worker.test.mjs` e con la suite di Jarvis (`tests/test_feedback_server.py`).

## Metterlo in funzione

Servono un account GitHub e un account Cloudflare (gratuito). I passi con le credenziali li fa chi possiede gli
account.

1. **Repository privato** su GitHub per le segnalazioni, per esempio `jarvis-segnalazioni`. Il nome va in
   `GITHUB_REPO` dentro `wrangler.toml`.
2. **Chiave di GitHub a grana fine** (Settings → Developer settings → Fine-grained tokens): accesso solo a quel
   repository, permesso *Issues: Read and write*, nient'altro. Quando scade, le segnalazioni tornano indietro
   (Jarvis le tiene e le rimanda) finché non se ne mette una nuova.
3. Da questa cartella, con Node:
   ```
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put GITHUB_TOKEN
   ```
   Il primo apre il browser per l'accesso a Cloudflare; il terzo chiede la chiave, da incollare lì (non va mai
   scritta in un file). `deploy` stampa l'indirizzo, del tipo `https://jarvis-segnalazioni.<nome>.workers.dev`.
4. L'indirizzo con `/segnala` in fondo va in `system.feedback_url` (`jarvis/config.py`): da quella versione le
   segnalazioni sono attive per tutti.

Se `wrangler deploy` rifiuta i limiti di richieste (`[[ratelimits]]`), si tolgono da `wrangler.toml`: il server va
avanti anche senza (sul piano gratuito, il 24/11, li ha accettati).

Se le segnalazioni tornano indietro con "GitHub ha risposto 403: Resource not accessible by personal access token",
alla chiave manca *Issues: Read and write* su quel repository: si corregge modificando la chiave su GitHub, senza
ricaricarla su Cloudflare. Chi prova il server a mano deve dare un User-Agent: Cloudflare rifiuta quello di urllib
(403, errore 1010).

## Leggere le segnalazioni

Sono issue del repository privato, con l'etichetta `caduta` o `segnalazione` e quella della versione. Con il
terminale: `gh issue list --repo <proprietario>/jarvis-segnalazioni`.
