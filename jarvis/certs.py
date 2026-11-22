"""HTTPS su un Windows appena installato: i certificati li verifica Windows, non l'elenco che Python legge.

Su un PC nuovo l'archivio dei certificati radice di Windows è quasi vuoto: Windows scarica una radice solo quando un
suo programma gliela chiede. Python (OpenSSL) legge l'archivio così com'è e non chiede niente, quindi ogni HTTPS
fallisce con "unable to get local issuer certificate": download del primo avvio, aggiornamenti, Gmail, ricerche.
Provato in Windows Sandbox il 22/11: con i certificati di base falliscono github.com, huggingface.co,
registry.npmjs.org e google.com; con truststore passano tutti (e passano anche le reti aziendali con certificati
propri, dove un elenco fisso come certifi fallirebbe).
"""

from __future__ import annotations

import logging

log = logging.getLogger("jarvis.certs")


def trust_windows() -> bool:
    """Da qui in poi ogni contesto SSL di Python (aiohttp, urllib, requests, imaplib, smtplib) verifica con Windows."""
    try:
        import truststore
    except ImportError:  # senza truststore resta la verifica di base: va bene dove le radici ci sono già
        log.warning("truststore manca: HTTPS con i certificati di base di Python")
        return False
    truststore.inject_into_ssl()
    return True
