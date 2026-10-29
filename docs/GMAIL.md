# Gmail per Jarvis

1. Apri https://console.cloud.google.com e accedi con il tuo account Gmail. In alto: Seleziona un progetto, poi
   Nuovo progetto, nome «Jarvis», Crea (se chiede di accettare i termini di Google Cloud: è gratis, niente carta).
2. Con il progetto Jarvis selezionato apri https://console.cloud.google.com/apis/library/gmail.googleapis.com e premi
   Abilita.
3. Apri https://console.cloud.google.com/auth/overview e premi Inizia: nome app «Jarvis», email di assistenza la
   tua; Pubblico: Esterno; dati di contatto: la tua email; accetta le norme e Crea.
4. Nel menu Pubblico premi Pubblica app e conferma. Importante: se resta «In test», Google toglie il permesso ogni
   7 giorni. La verifica di Google non serve: l'app la usi solo tu.
5. Nel menu Client: Crea client, tipo «App desktop», nome «Jarvis», Crea, poi Scarica JSON (finisce nei Download
   come client_secret_....json: Jarvis lo trova da solo).
6. Di' «Jarvis, collega Gmail»: si apre il browser, scegli il tuo account; Google avvisa che l'app non è verificata
   (è la tua): Avanzate, Vai a Jarvis, spunta i permessi e Continua.

Jarvis potrà leggere, cercare, scrivere, rispondere, inoltrare, mettere etichette e spostare nel cestino; mai
cancellare per sempre. Si revoca quando vuoi: «Jarvis, scollega Gmail» o da myaccount.google.com/permissions.

Dettagli tecnici: `jarvis/apps/gmail.py` (permessi chiesti: `gmail.modify` e `gmail.settings.basic`; il permesso è cifrato con DPAPI in `data/gmail/permesso.bin`; il client OAuth viene copiato in `data/gmail/client.json`). Le app lasciate «In test» ricevono un permesso che scade in 7 giorni (documentazione OAuth di Google), per questo l'app va pubblicata.

## Pubblicare l'app (27/10/2026)

A ottobre 2026 la console di Google tiene disattivato «Pubblica app» per le app personali senza home page e
informativa sulla privacy (segnalato da molti sul forum per sviluppatori di Google, senza risposta). Le due pagine di
Jarvis sono su GitHub Pages, nel repository pubblico `tatelaz/jarvis-app`:

- home page: https://tatelaz.github.io/jarvis-app/
- informativa sulla privacy: https://tatelaz.github.io/jarvis-app/privacy.html
- dominio autorizzato: `tatelaz.github.io`

Se nonostante questo «Pubblica app» resta disattivato: si resta in prova (il permesso scade ogni ~7 giorni e Jarvis
chiede di ricollegare), oppure si passa a IMAP con una password per le app (niente scadenze, senza risposta
automatica e filtri).
