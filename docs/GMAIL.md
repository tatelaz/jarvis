# Gmail per Jarvis

Ci sono due modi. Quello veloce va bene quasi sempre; quello completo serve solo per filtri e risposta automatica.

## Modo veloce: password per le app (un minuto)

1. Serve la verifica in due passaggi sull'account Google (https://myaccount.google.com/signinoptions/twosv). Se è
   spenta, la pagina delle password per le app dice che non è disponibile.
2. Di' «Jarvis, collega Gmail». Si apre https://myaccount.google.com/apppasswords e, nella finestra di Jarvis, il
   campo Gmail. Su Google scrivi «Jarvis» come nome e premi Crea: compare una password di 16 lettere.
3. Nel campo Gmail della finestra scrivi il tuo indirizzo, incolla la password e premi Collega (mai a voce). Jarvis
   prova subito il collegamento e lo salva solo se Google risponde di sì.

Così legge, cerca, scrive, risponde, inoltra, mette etichette, prepara bozze e sposta nel cestino. Non può creare
filtri né accendere la risposta automatica: con IMAP Google non lo permette. Per scollegare: «Jarvis, scollega
Gmail»; per togliere la password anche da Google, cancellala su https://myaccount.google.com/apppasswords.

Dettagli tecnici: `jarvis/apps/gmail_imap.py` (IMAP su imap.gmail.com:993 e SMTP su smtp.gmail.com:465; la password
è cifrata con DPAPI in `data/gmail/permesso.bin`).

## Modo completo: un progetto Google tuo (~10 minuti)

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
6. Di' «Jarvis, collega Gmail completo» (se il file è già nei Download basta «collega Gmail»): si apre il browser,
   scegli il tuo account; Google avvisa che l'app non è verificata (è la tua): Avanzate, Vai a Jarvis, spunta i
   permessi e Continua.

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
chiede di ricollegare), oppure si usa il modo veloce qui sopra (niente scadenze, senza risposta automatica e filtri).
