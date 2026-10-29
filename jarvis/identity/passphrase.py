"""La parola d'ordine: riconosciuta solo come frase a sé, mai salvata in chiaro, tolta da ogni testo.

Regole decise dall'utente il 7/10: Jarvis non la chiede mai (nessuno deve sapere che esiste); se qualcuno dice come
frase a sé "la parola d'ordine è ..." ed è giusta, quella voce vale come l'utente. Il testo detto non deve finire nei
registri, nelle trascrizioni salvate né al cervello: si sostituisce prima, già nelle trascrizioni parziali.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata

_INTRO = r"(?:la\s+)?(?:mia\s+)?parola\s+d\W?\s*ordine"
# frase a sé: al massimo "Jarvis," davanti, poi "la parola d'ordine è ..." e nient'altro
STATEMENT = re.compile(rf"^\W*(?:(?:jarvis|j\w{{3,6}})\W+)?{_INTRO}\s*(?:è|e'|e|era|:)\s*[:,]?\s*(?P<pw>.+?)\W*$",
                       re.I)
MENTION = re.compile(rf"{_INTRO}\b.*", re.I | re.S)
HIDDEN = "[riservata]"


def normalize(text: str) -> str:
    """Come la trascrizione può scriverla: minuscole, senza accenti né punteggiatura, spazi singoli."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", t))


def extract(text: str) -> str | None:
    """La parola d'ordine, se la frase è proprio "la parola d'ordine è ..."; altrimenti None."""
    m = STATEMENT.match(text.strip())
    if not m:
        return None
    pw = normalize(m.group("pw"))
    return pw or None


def redact(text: str) -> str:
    """Tutto ciò che segue "parola d'ordine" sparisce (anche in una frase qualsiasi o in una trascrizione parziale)."""
    return MENTION.sub(lambda m: m.group(0)[: m.group(0).lower().find("ordine") + len("ordine")] + " " + HIDDEN, text)


def make_hash(pw: str, salt: bytes | None = None) -> tuple[str, str]:
    """(sale, impronta) in esadecimale. scrypt: lento apposta, contro chi prova a indovinarla."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(normalize(pw).encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return salt.hex(), digest.hex()


def check(pw: str, salt_hex: str, hash_hex: str) -> bool:
    _, digest = make_hash(pw, bytes.fromhex(salt_hex))
    return hmac.compare_digest(digest, hash_hex)
