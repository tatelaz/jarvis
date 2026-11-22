"""Gmail in due modi: veloce con una password per le app (IMAP e SMTP), completo con l'API ufficiale di Google.

- Veloce (di base): "Jarvis, collega Gmail" apre la pagina di Google delle password per le app (serve la verifica in
  due passaggi) e il campo Gmail nella finestra di Jarvis, dove l'utente incolla indirizzo e password (mai a voce).
  Jarvis prova il collegamento e lo salva cifrato. Le chiamate passano da jarvis/apps/gmail_imap.py, che risponde come
  l'API: le azioni sotto sono le stesse. Filtri e risposta automatica no (IMAP non li ha).
- Completo (se c'è già il progetto Google o lo si chiede): progetto gratuito su Google Cloud, API Gmail attivata, app
  "Jarvis" pubblicata (in prova il permesso scadrebbe dopo 7 giorni), client OAuth "App desktop" scaricato. Jarvis
  trova da solo il file client_secret_*.json nei Download e lo copia in data/gmail/client.json. "Jarvis, collega
  Gmail": si apre il browser, Google chiede il consenso, l'utente preme Consenti (e la prima volta "Avanzate, vai a
  Jarvis", perché l'app non è verificata: è sua). Permessi chiesti: gmail.modify (leggere, scrivere, inviare,
  etichette, cestino; MAI cancellazioni definitive) e gmail.settings.basic (risposta automatica, filtri).
- In tutti e due i modi il permesso è cifrato con DPAPI di Windows: lo legge solo il suo utente su questo PC.
- Ogni cosa che parte verso altri, cancella o resta come regola chiede conferma (jarvis/apps/base.py); la posta è
  solo dell'utente.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import shutil
import time
import webbrowser
from collections.abc import Callable
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from . import gmail_imap
from .base import App, AppError, norm, pick_by_name

SCOPES = ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.settings.basic"]
API = "https://gmail.googleapis.com/gmail/v1/users/me"
REVOKE = "https://oauth2.googleapis.com/revoke"
SYSTEM_LABELS = {"INBOX": "posta in arrivo", "UNREAD": "da leggere", "STARRED": "speciali", "IMPORTANT": "importanti",
                 "SENT": "inviati", "DRAFT": "bozze", "SPAM": "spam", "TRASH": "cestino",
                 "CATEGORY_PROMOTIONS": "promozioni", "CATEGORY_SOCIAL": "social", "CATEGORY_UPDATES": "aggiornamenti",
                 "CATEGORY_FORUMS": "forum", "CATEGORY_PERSONAL": "principale"}

GUIDE = """Collegare Gmail a Jarvis

Il modo veloce (un minuto, gratis): una password per le app di Google.
1. Serve la verifica in due passaggi attiva sull'account Google (https://myaccount.google.com/signinoptions/twosv).
2. Di' «Jarvis, collega Gmail»: si apre https://myaccount.google.com/apppasswords. Come nome scrivi «Jarvis» e
   premi Crea: Google mostra una password di 16 lettere.
3. Nella finestra di Jarvis, nel campo Gmail, scrivi il tuo indirizzo e incolla la password, poi Collega. Mai a voce.
Così Jarvis legge, cerca, scrive, risponde, inoltra, mette etichette e sposta nel cestino. Filtri e risposta
automatica no: servono il modo completo, qui sotto.

Il modo completo (una volta sola, ~10 minuti, gratis): un progetto Google tuo.
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
6. Di' «Jarvis, collega Gmail completo»: si apre il browser, scegli il tuo account; Google avvisa che l'app non è
   verificata (è la tua): Avanzate, Vai a Jarvis, spunta i permessi e Continua.

In tutti e due i modi Jarvis non cancella mai per sempre. Si scollega quando vuoi: «Jarvis, scollega Gmail»; da
Google si revoca su myaccount.google.com/apppasswords (modo veloce) o myaccount.google.com/permissions (completo)."""


# ---------------------------------------------------------------------- il permesso, cifrato


def protect(data: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(data, "jarvis-gmail", None, None, None, 0x1)


def unprotect(blob: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptUnprotectData(blob, None, None, None, 0x1)[1]


def consent(client: Path, scopes: list[str]) -> dict:
    """Il consenso nel browser predefinito: restituisce il permesso (con il refresh token) come dizionario."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client), scopes)
    creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True, timeout_seconds=300,
                                  authorization_prompt_message="", prompt="consent",
                                  success_message="Jarvis è collegato a Gmail: puoi chiudere questa scheda.")
    return json.loads(creds.to_json())


def google_session(info: dict) -> Any:
    if info.get("mode") == "imap":  # il modo veloce: stesse chiamate, fatte con IMAP e SMTP
        return gmail_imap.ImapSession(info["email"], info["password"])
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials

    return AuthorizedSession(Credentials.from_authorized_user_info(info, SCOPES))


def _revoked(e: Exception) -> bool:
    """Google ha tolto il permesso (revocato dall'account, password cambiata, password per le app cancellata...)."""
    return isinstance(e, gmail_imap.AuthFailed) or type(e).__name__ == "RefreshError"


# ---------------------------------------------------------------------- testo delle mail


_BLOCK = frozenset({"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table", "blockquote"})


class _Text(HTMLParser):

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.skip:
            self.skip -= 1
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = unescape("".join(p.out)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _headers(part: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in part.get("headers") or []}


def _charset(part: dict) -> str:
    m = re.search(r'charset="?([\w-]+)', _headers(part).get("content-type", ""), re.I)
    return m.group(1) if m else "utf-8"


def body_text(payload: dict) -> str:
    """Il testo della mail: la parte text/plain, altrimenti l'HTML ripulito."""
    plain, html = [], []

    def walk(p: dict) -> None:
        mime = p.get("mimeType", "")
        data = (p.get("body") or {}).get("data")
        if data and not p.get("filename"):
            try:
                text = _b64(data).decode(_charset(p), "replace")
            except LookupError:
                text = _b64(data).decode("utf-8", "replace")
            (plain if mime == "text/plain" else html if mime == "text/html" else []).append(text)
        for sub in p.get("parts") or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    return html_text("\n".join(html)) if html else ""


def attachments(payload: dict) -> list[dict]:
    out = []

    def walk(p: dict) -> None:
        body = p.get("body") or {}
        if p.get("filename") and body.get("attachmentId"):
            out.append({"name": p["filename"], "id": body["attachmentId"], "size": body.get("size", 0),
                        "mime": p.get("mimeType", "")})
        for sub in p.get("parts") or []:
            walk(sub)

    walk(payload)
    return out


def who(addr: str) -> str:
    name, mail = parseaddr(addr or "")
    return name.strip('"') or mail or "?"


def when(header_date: str, now: float) -> str:
    try:
        t = parsedate_to_datetime(header_date).astimezone()
    except (TypeError, ValueError):
        return ""
    days = (date.fromtimestamp(now) - t.date()).days
    hm = t.strftime("%H:%M")
    return hm if days == 0 else f"ieri {hm}" if days == 1 else t.strftime("%d/%m/%Y")


def size_text(n: int) -> str:
    return f"{n / 1024 ** 2:.1f}".replace(".", ",") + " MB" if n >= 1024 ** 2 else f"{max(1, round(n / 1024))} KB"


def _yes(v: Any) -> bool:
    return str(v or "").strip().lower() in ("sì", "si", "true", "1", "vero")


# ---------------------------------------------------------------------- l'account


class Gmail:
    """Collegamento, permesso cifrato, chiamate REST, l'ultimo elenco (per dire "leggi la seconda")."""

    def __init__(self, data_dir: Path, downloads: Path, notify: Callable[[str], Any] | None = None,
                 consent_flow: Callable[[Path, list[str]], dict] = consent,
                 session_factory: Callable[[dict], Any] = google_session,
                 protect_fn: Callable[[bytes], bytes] = protect, unprotect_fn: Callable[[bytes], bytes] = unprotect,
                 check_login: Callable[[str, str], None] = gmail_imap.check,
                 open_url: Callable[[str], Any] = webbrowser.open, show_form: Callable[[], Any] | None = None,
                 ) -> None:
        self.dir = data_dir
        self.downloads = downloads
        self.notify = notify
        self.consent_flow = consent_flow
        self.session_factory = session_factory
        self.protect, self.unprotect = protect_fn, unprotect_fn
        self.check_login = check_login  # il modo veloce: prova indirizzo e password per le app prima di salvarli
        self.open_url = open_url
        self.show_form = show_form  # apre il campo Gmail nella finestra di Jarvis
        self.waiting_password = False
        self.linking: asyncio.Task | None = None
        self.last: list[str] = []  # gli id dell'ultimo elenco, nell'ordine detto
        self.last_drafts: list[str] = []
        self.last_filters: list[str] = []
        self.me = ""
        self._session: Any = None
        self._labels: dict[str, str] | None = None  # nome -> id

    @property
    def client(self) -> Path:
        return self.dir / "client.json"

    @property
    def token(self) -> Path:
        return self.dir / "permesso.bin"

    def linked(self) -> bool:
        return self.token.is_file()

    def ready(self) -> str | None:
        if self.linking is not None and not self.linking.done():
            return "si sta collegando: l'utente deve premere Consenti nel browser"
        if not self.linked() and self.waiting_password:
            return ("si sta collegando: l'utente deve incollare la password per le app nel campo Gmail della finestra "
                    "(se non lo trova: azione collega)")
        if not self.linked():
            return "non è collegata: usa l'azione collega (una volta sola)"
        return None

    def find_client(self) -> Path | None:
        """Il client OAuth: quello già copiato, oppure il più recente scaricato da Google Cloud."""
        if self.client.is_file():
            return self.client
        found = sorted(self.downloads.glob("client_secret_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in found:
            try:
                ok = "installed" in json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ok = False
            if ok:
                self.dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, self.client)
                return self.client
        if found:
            raise AppError("ho trovato un client di Google nei Download ma non è di tipo «App desktop»: va ricreato "
                           "con quel tipo")
        return None

    async def link(self, mode: str = "") -> str:
        """Modo "veloce" (password per le app), "completo" (progetto Google) o, senza dirlo, completo solo se il
        progetto c'è già."""
        if self.linking is not None and not self.linking.done():
            return "il browser con il consenso di Google è già aperto: aspetto che l'utente prema Consenti"
        client = None if mode == "veloce" else self.find_client()
        if client is None and mode == "completo":
            raise AppError("manca il progetto Google dell'utente (si prepara una volta, ~10 minuti): prendi i passaggi "
                           "con l'azione guida e mostrali a schermo con mostra")
        if client is None:
            return self.ask_app_password()
        self.linking = asyncio.create_task(self._finish_link(client))
        return ("Si è aperto il browser con la pagina di Google: l'utente sceglie il suo account e preme Consenti (se "
                "Google avvisa che l'app non è verificata: Avanzate, poi Vai a Jarvis; l'app è la sua). Avviso io quando "
                "è fatto.")

    async def _finish_link(self, client: Path) -> None:
        try:
            info = await asyncio.to_thread(self.consent_flow, client, SCOPES)
            if not info.get("refresh_token"):
                raise AppError("Google non ha dato un permesso duraturo")
            self.dir.mkdir(parents=True, exist_ok=True)
            self.token.write_bytes(self.protect(json.dumps(info).encode()))
            self._session = None
            profile = await asyncio.to_thread(self.get, "/profile")
            self.me = profile.get("emailAddress", "")
            text = f"Gmail è collegata ({self.me}). Dillo in una frase."
        except Exception as e:  # anche il consenso negato o la finestra chiusa: si dice, non si tace
            text = f"Il collegamento di Gmail non è riuscito ({e}). Dillo in una frase: si può riprovare."
        if self.notify:
            self.notify(text)

    def ask_app_password(self) -> str:
        """Il modo veloce: la pagina di Google per creare la password e il campo nella finestra per incollarla."""
        self.waiting_password = True
        try:
            self.open_url(gmail_imap.APP_PASSWORDS)
        except Exception:  # senza browser l'indirizzo lo dice Jarvis
            pass
        if self.show_form:
            self.show_form()
        return ("Ho aperto la pagina di Google delle password per le app (" + gmail_imap.APP_PASSWORDS + ") e il campo "
                "Gmail nella finestra di Jarvis. L'utente scrive «Jarvis» come nome, preme Crea e incolla la password di "
                "16 lettere, con il suo indirizzo, nel campo della finestra: mai a voce. Serve la verifica in due "
                "passaggi (se Google dice che la pagina non è disponibile, va attivata prima). Avviso io quando è fatto.")

    async def link_app_password(self, address: str, password: str) -> str:
        """Dal campo della finestra: prova indirizzo e password con Google e solo allora li salva. "ok" o il motivo.
        La password non va mai in registri, eventi o messaggi."""
        address = str(address or "").strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
            return "manca l'indirizzo Gmail (per esempio nome@gmail.com)"
        try:
            pw = gmail_imap.clean_app_password(password)
        except ValueError as e:
            return str(e)
        try:
            await asyncio.to_thread(self.check_login, address, pw)
        except gmail_imap.AuthFailed as e:
            return f"{e}: controlla indirizzo e password per le app"
        except Exception as e:  # rete, Google che non risponde, account senza IMAP
            return f"Gmail non risponde ({str(e)[:120] or type(e).__name__}): riprova tra poco"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.token.write_bytes(self.protect(json.dumps({"mode": "imap", "email": address, "password": pw}).encode()))
        self._session, self.me, self.waiting_password = None, address, False
        if self.notify:
            self.notify(f"Gmail è collegata ({address}) con la password per le app. Dillo in una frase.")
        return "ok"

    def mode(self) -> str:
        """"veloce" (password per le app), "completo" (progetto Google) o "" se non è collegata."""
        if not self.linked():
            return ""
        try:
            return "veloce" if json.loads(self.unprotect(self.token.read_bytes())).get("mode") == "imap" else "completo"
        except Exception:
            return "completo"

    def unlink(self) -> str:
        quick = self.mode() == "veloce"
        if self.linked() and not quick:
            try:
                info = json.loads(self.unprotect(self.token.read_bytes()))
                self.session().request("POST", REVOKE, params={"token": info.get("refresh_token")}, timeout=15)
            except Exception:  # anche se Google non risponde, da questo PC il permesso sparisce comunque
                pass
        if self._session is not None and hasattr(self._session, "close"):
            self._session.close()
        self.token.unlink(missing_ok=True)
        self._session = None
        if quick:
            return ("Gmail scollegata: la password è cancellata da questo PC (per toglierla anche da Google: "
                    f"{gmail_imap.APP_PASSWORDS})")
        return "Gmail scollegata: permesso revocato e cancellato da questo PC"

    # ------------------------------------------------------------------ chiamate

    def session(self) -> Any:
        if self._session is None:
            if not self.linked():
                raise AppError("Gmail non è collegata (azione collega)")
            try:
                info = json.loads(self.unprotect(self.token.read_bytes()))
            except Exception as e:
                raise AppError("il permesso salvato non si legge più (è di un altro utente o PC): va ricollegata") from e
            self._session = self.session_factory(info)
        return self._session

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        url = path if path.startswith("http") else API + path
        try:
            r = self.session().request(method, url, params=params, json=body, timeout=30)
        except Exception as e:
            if _revoked(e):
                self.token.unlink(missing_ok=True)
                self._session = None
                raise AppError("Google ha tolto il permesso a Jarvis (revocato o scaduto): va ricollegata con "
                               "collega") from e
            raise AppError(f"Gmail non risponde ({type(e).__name__})") from e
        if r.status_code >= 400:
            try:
                why = r.json().get("error", {}).get("message", "")
            except ValueError:
                why = r.text[:200]
            raise AppError(f"Gmail ha rifiutato la richiesta ({r.status_code}): {why}")
        return r.json() if r.text.strip() else {}

    def get(self, path: str, **params: Any) -> dict:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, body: dict | None = None) -> dict:
        return self.request("POST", path, body=body or {})

    # ------------------------------------------------------------------ mail e etichette

    def search(self, query: str, limit: int) -> list[dict]:
        ids = [m["id"] for m in self.get("/messages", q=query, maxResults=limit).get("messages") or []]
        return [self.get(f"/messages/{i}", format="metadata", metadataHeaders=["From", "To", "Cc", "Subject", "Date"])
                for i in ids]

    def full(self, mid: str) -> dict:
        return self.get(f"/messages/{mid}", format="full")

    def labels(self, fresh: bool = False) -> dict[str, str]:
        if fresh or self._labels is None:
            self._labels = {lb["name"]: lb["id"] for lb in self.get("/labels").get("labels") or []}
        return self._labels

    def label_id(self, name: str, create: bool = False) -> str:
        names = self.labels()
        by_italian = {v: k for k, v in SYSTEM_LABELS.items()}
        key = str(name).strip()
        if key.upper() in SYSTEM_LABELS:
            return key.upper()
        if key.lower() in by_italian:
            return by_italian[key.lower()]
        try:
            return names[pick_by_name(key, list(names), lambda n: n, plural="etichette", empty="quale etichetta?",
                                      missing=f"non esiste un'etichetta '{key}'")]
        except AppError:
            if not create:
                raise
        made = self.post("/labels", {"name": key, "labelListVisibility": "labelShow", "messageListVisibility": "show"})
        self.labels(fresh=True)
        return made["id"]

    def resolve(self, ref: Any) -> str:
        """L'id della mail: numero dell'ultimo elenco, "ultima", id di Gmail, oppure una ricerca (la più recente)."""
        r = str(ref).strip()
        if r.isdigit() and 1 <= int(r) <= len(self.last):
            return self.last[int(r) - 1]
        if r.lower() in ("ultima", "l'ultima", "ultima mail", "1"):
            found = self.get("/messages", q="in:inbox", maxResults=1).get("messages") or []
        elif re.fullmatch(r"[0-9a-f]{12,20}", r):
            return r
        else:
            found = self.get("/messages", q=r, maxResults=1).get("messages") or []
        if not found:
            raise AppError(f"non trovo la mail «{r}» (fai prima cerca o non_lette e usa il numero)")
        return found[0]["id"]

    def address(self, name: str) -> str:
        """L'indirizzo di una persona: scritto, oppure trovato nelle mail scambiate con lei."""
        n = str(name).strip()
        if "@" in n:
            return n
        seen: dict[str, str] = {}
        for m in self.search(f"from:({n}) OR to:({n}) OR cc:({n})", 25):
            h = _headers(m.get("payload") or {})
            for disp, mail in getaddresses([h.get("from", ""), h.get("to", ""), h.get("cc", "")]):
                if mail and (norm(n) in norm(disp) or norm(n) in norm(mail.split("@")[0])):
                    seen.setdefault(mail.lower(), f"{disp} <{mail}>" if disp else mail)
        return pick_by_name(n, list(seen.values()), lambda x: x, plural="indirizzi", empty="a chi?",
                            missing=f"non trovo l'indirizzo di '{n}' nelle mail: serve scriverlo") \
            if len(seen) != 1 else next(iter(seen.values()))

    def addresses(self, names: Any) -> str:
        parts = [x.strip() for x in re.split(r"[,;]|\be\b", str(names or "")) if x.strip()]
        return ", ".join(self.address(p) for p in parts)

    def send_raw(self, msg: EmailMessage, thread: str | None = None) -> dict:
        body: dict[str, Any] = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        if thread:
            body["threadId"] = thread
        return self.post("/messages/send", body)


def compose(to: str, subject: str, text: str, cc: str = "", files: list[Path] | None = None,
            extra: dict[str, str] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    for k, v in (extra or {}).items():
        msg[k] = v
    msg.set_content(text)
    for f in files or []:
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        main, sub = mime.split("/", 1)
        msg.add_attachment(f.read_bytes(), maintype=main, subtype=sub, filename=f.name)
    return msg


def _files(raw: Any) -> list[Path]:
    out = []
    for x in [s.strip().strip('"') for s in re.split(r"[;\n]", str(raw or "")) if s.strip()]:
        p = Path(x).expanduser()
        if not p.is_file():
            raise AppError(f"l'allegato {p} non esiste (serve il percorso completo)")
        out.append(p)
    return out


# ---------------------------------------------------------------------- l'app


def build(g: Gmail, now: Callable[[], float] = time.time) -> App:
    app = App("gmail", "Gmail", "messaggi", ready=g.ready, aliases=("mail", "email", "posta", "e-mail"),
              notes="mail = il numero nell'ultimo elenco (cerca, non_lette), 'ultima', oppure una ricerca. cerca usa la "
                    "sintassi di Gmail: from:amazon, to:, subject:fattura, has:attachment, is:unread, is:starred, "
                    "in:sent, label:lavoro, after:2026/10/01, older_than:7d, \"frase esatta\". a/cc = indirizzi oppure "
                    "nomi (si cercano nelle mail scambiate), separati da virgole. allegati = percorsi completi separati "
                    "da ;. Le cancellazioni sono solo nel cestino (30 giorni), mai definitive.")

    def row(n: int, m: dict) -> str:
        h = _headers(m.get("payload") or {})
        unread = " [da leggere]" if "UNREAD" in (m.get("labelIds") or []) else ""
        sent = "SENT" in (m.get("labelIds") or []) and "INBOX" not in (m.get("labelIds") or [])
        person = f"a {who(h.get('to', ''))}" if sent else f"da {who(h.get('from', ''))}"
        return f"{n}. {person}: «{h.get('subject') or '(senza oggetto)'}» ({when(h.get('date', ''), now())}){unread}"

    def listing(found: list[dict]) -> str:
        g.last = [m["id"] for m in found]
        return "\n".join(row(i + 1, m) for i, m in enumerate(found))

    def brief(mid: str) -> str:
        h = _headers(g.get(f"/messages/{mid}", format="metadata", metadataHeaders=["From", "Subject"]).get("payload")
                     or {})
        return f"la mail di {who(h.get('from', ''))} «{h.get('subject') or '(senza oggetto)'}»"

    # ---------------------------------------------------------- collegamento

    @app.action("collega", "collega Gmail (una volta sola): di base con una password per le app, un minuto",
                {"modo": "? veloce (password per le app, di base) o completo (progetto Google: anche filtri e risposta "
                         "automatica)"}, needs_ready=False)
    async def collega(a):
        mode = norm(str(a.get("modo") or ""))
        if mode and mode not in ("veloce", "completo"):
            raise AppError("modo sconosciuto: veloce o completo")
        if g.linked() and not (mode and mode != g.mode()):
            return f"Gmail è già collegata (modo {g.mode()})"
        return await g.link(mode)

    @app.action("guida", "i passaggi per collegare Gmail, veloce e completo (da mostrare a schermo)", needs_ready=False)
    def guida(_a):
        return GUIDE

    @app.action("stato", "se Gmail è collegata, con quale indirizzo e in che modo", needs_ready=False)
    def stato(_a):
        if not g.linked():
            return "Gmail non è collegata (azione collega)"
        p = g.get("/profile")
        g.me = p.get("emailAddress", "")
        how = ", con la password per le app (senza filtri e risposta automatica)" if g.mode() == "veloce" else ""
        return f"Gmail collegata: {g.me}, {p.get('messagesTotal', '?')} mail in tutto{how}"

    @app.action("scollega", "revoca il permesso di Jarvis e lo cancella dal PC", needs_ready=False,
                confirm=lambda a: "scollegare Gmail da Jarvis (per riusarla andrà ridato il consenso)")
    def scollega(_a):
        return g.unlink()

    # ---------------------------------------------------------- leggere

    @app.action("non_lette", "le mail da leggere nella posta in arrivo (numerate)", {"quante": "? quante (10)"})
    def non_lette(a):
        found = g.search("is:unread in:inbox", max(1, min(int(a.get("quante") or 10), 30)))
        return listing(found) if found else "nessuna mail da leggere"

    @app.action("cerca", "cerca le mail (numerate) con la sintassi di Gmail", {"cosa": "es. from:amazon has:attachment",
                                                                                "quante": "? quante (10)"})
    def cerca(a):
        found = g.search(str(a["cosa"]), max(1, min(int(a.get("quante") or 10), 30)))
        return listing(found) if found else f"nessuna mail per «{a['cosa']}»"

    @app.action("leggi", "legge una mail intera (non la segna come letta)", {"mail": "numero dell'elenco o ricerca"})
    def leggi(a):
        m = g.full(g.resolve(a["mail"]))
        p = m.get("payload") or {}
        h = _headers(p)
        text = body_text(p) or m.get("snippet", "")
        att = attachments(p)
        lines = [f"Da: {h.get('from', '?')}", f"A: {h.get('to', '?')}"]
        if h.get("cc"):
            lines.append(f"Cc: {h['cc']}")
        lines += [f"Data: {when(h.get('date', ''), now())}", f"Oggetto: {h.get('subject') or '(senza oggetto)'}"]
        if att:
            lines.append("Allegati: " + ", ".join(f"{x['name']} ({size_text(x['size'])})" for x in att))
        cut = " […tagliata]" if len(text) > 6000 else ""
        return "\n".join(lines) + "\n\n" + text[:6000] + cut

    @app.action("conversazione", "tutta la conversazione di una mail, in breve", {"mail": "numero o ricerca"})
    def conversazione(a):
        tid = g.get(f"/messages/{g.resolve(a['mail'])}", format="minimal")["threadId"]
        msgs = g.get(f"/threads/{tid}", format="full").get("messages") or []
        out = []
        for m in msgs:
            h = _headers(m.get("payload") or {})
            text = re.sub(r"\s+", " ", body_text(m.get("payload") or {}) or m.get("snippet", ""))
            out.append(f"[{when(h.get('date', ''), now())}] {who(h.get('from', ''))}: {text[:700]}")
        return "\n".join(out) or "conversazione vuota"

    @app.action("etichette", "le etichette, con le mail da leggere")
    def etichette(_a):
        rows = []
        shown = [(SYSTEM_LABELS.get(lid, name), lid) for name, lid in g.labels(fresh=True).items()
                 if lid not in SYSTEM_LABELS or lid in ("INBOX", "STARRED", "IMPORTANT", "SPAM")]
        for said, lid in sorted(shown, key=lambda x: x[0].lower()):  # in ordine come si dicono, non come le chiama Google
            unread = g.get(f"/labels/{lid}").get("messagesUnread") or 0
            rows.append(said + (f" ({unread} da leggere)" if unread else ""))
        return ", ".join(rows)

    @app.action("allegati", "salva sul PC gli allegati di una mail", {"mail": "numero o ricerca"})
    def allegati(a):
        mid = g.resolve(a["mail"])
        att = attachments(g.full(mid).get("payload") or {})
        if not att:
            raise AppError("quella mail non ha allegati")
        g.downloads.joinpath("Gmail").mkdir(parents=True, exist_ok=True)
        saved = []
        for x in att:
            data = _b64(g.get(f"/messages/{mid}/attachments/{x['id']}")["data"])
            target = g.downloads / "Gmail" / re.sub(r'[<>:"/\\|?*]', "_", x["name"])
            i = 2
            while target.exists():
                target = target.with_name(f"{Path(x['name']).stem} ({i}){Path(x['name']).suffix}")
                i += 1
            target.write_bytes(data)
            saved.append(str(target))
        return "salvati: " + "; ".join(saved)

    # ---------------------------------------------------------- scrivere (sempre con conferma)

    def invia_conferma(a):
        a["a"] = g.addresses(a["a"])
        if a.get("cc"):
            a["cc"] = g.addresses(a["cc"])
        files = _files(a.get("allegati"))
        att = f", allegati: {', '.join(f.name for f in files)}" if files else ""
        cc = f", in copia {a['cc']}" if a.get("cc") else ""
        return f"inviare una mail a {a['a']}{cc}, oggetto «{a['oggetto']}», testo: «{a['testo']}»{att}"

    @app.action("invia", "manda una mail nuova", {"a": "destinatari", "oggetto": "l'oggetto", "testo": "il testo",
                                                 "cc": "? in copia", "allegati": "? percorsi separati da ;"},
                confirm=invia_conferma)
    def invia(a):
        to = a["a"] if "@" in str(a["a"]) else g.addresses(a["a"])
        g.send_raw(compose(to, str(a["oggetto"]), str(a["testo"]), str(a.get("cc") or ""), _files(a.get("allegati"))))
        return f"mail inviata a {to}"

    def _reply_parts(mid: str, everyone: bool) -> tuple[str, str, str, dict[str, str], str, str]:
        m = g.get(f"/messages/{mid}", format="full")
        h = _headers(m.get("payload") or {})
        me = (g.me or g.get("/profile").get("emailAddress", "")).lower()
        to = h.get("reply-to") or h.get("from", "")
        cc = ""
        if everyone:
            others = [f"{n} <{e}>" if n else e for n, e in getaddresses([h.get("to", ""), h.get("cc", "")])
                      if e and e.lower() != me and e.lower() != parseaddr(to)[1].lower()]
            cc = ", ".join(others)
        subject = h.get("subject") or ""
        subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        extra = {"In-Reply-To": h.get("message-id", ""), "References": f"{h.get('references', '')} "
                 f"{h.get('message-id', '')}".strip()}
        quoted = "\n".join("> " + x for x in body_text(m.get("payload") or {}).splitlines()[:40])
        head = f"Il giorno {when(h.get('date', ''), now())}, {h.get('from', '')} ha scritto:"
        return to, cc, subject, extra, m.get("threadId", ""), f"{head}\n{quoted}"

    def rispondi_conferma(a):
        mid = g.resolve(a["mail"])
        a["_mail"] = mid
        to, cc, subject, *_ = _reply_parts(mid, _yes(a.get("tutti")))
        copy = f" e in copia {cc}" if cc else ""
        return f"rispondere a {to}{copy} («{subject}»): «{a['testo']}»"

    @app.action("rispondi", "risponde a una mail, nella stessa conversazione",
                {"mail": "numero o ricerca", "testo": "la risposta", "tutti": "? sì per rispondere a tutti",
                 "allegati": "? percorsi separati da ;"}, confirm=rispondi_conferma)
    def rispondi(a):
        mid = a.get("_mail") or g.resolve(a["mail"])
        to, cc, subject, extra, thread, quote = _reply_parts(mid, _yes(a.get("tutti")))
        g.send_raw(compose(to, subject, f"{a['testo']}\n\n{quote}", cc, _files(a.get("allegati")), extra), thread)
        return f"risposto a {who(to)}"

    def inoltra_conferma(a):
        mid = g.resolve(a["mail"])
        a["_mail"] = mid
        a["a"] = g.addresses(a["a"])
        note = f" con il messaggio «{a['testo']}»" if a.get("testo") else ""
        return f"inoltrare {brief(mid)} a {a['a']}{note} (con gli allegati)"

    @app.action("inoltra", "inoltra una mail con i suoi allegati",
                {"mail": "numero o ricerca", "a": "destinatari", "testo": "? due righe sopra"}, confirm=inoltra_conferma)
    def inoltra(a):
        mid = a.get("_mail") or g.resolve(a["mail"])
        to = a["a"] if "@" in str(a["a"]) else g.addresses(a["a"])
        m = g.full(mid)
        p = m.get("payload") or {}
        h = _headers(p)
        text = (f"{a.get('testo') or ''}\n\n---------- Messaggio inoltrato ----------\nDa: {h.get('from', '')}\n"
                f"Data: {h.get('date', '')}\nOggetto: {h.get('subject', '')}\nA: {h.get('to', '')}\n\n{body_text(p)}")
        subject = h.get("subject") or ""
        msg = compose(to, subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}", text.strip())
        for x in attachments(p):
            data = _b64(g.get(f"/messages/{mid}/attachments/{x['id']}")["data"])
            main, _, sub = (x["mime"] or "application/octet-stream").partition("/")
            msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=x["name"])
        g.send_raw(msg)
        return f"inoltrata a {to}"

    @app.action("bozza", "prepara una bozza (non parte: la si rilegge in Gmail o con invia_bozza)",
                {"a": "destinatari", "oggetto": "l'oggetto", "testo": "il testo", "cc": "? in copia"})
    def bozza(a):
        to, cc = g.addresses(a["a"]), g.addresses(a.get("cc")) if a.get("cc") else ""
        raw = base64.urlsafe_b64encode(compose(to, str(a["oggetto"]), str(a["testo"]), cc).as_bytes()).decode()
        g.post("/drafts", {"message": {"raw": raw}})
        return f"bozza salvata per {to}"

    @app.action("bozze", "le bozze salvate (numerate)")
    def bozze(_a):
        drafts = g.get("/drafts", maxResults=15).get("drafts") or []
        if not drafts:
            return "nessuna bozza"
        g.last_drafts = [d["id"] for d in drafts]
        rows = []
        for i, d in enumerate(drafts):
            h = _headers((g.get(f"/drafts/{d['id']}", format="metadata").get("message") or {}).get("payload") or {})
            rows.append(f"{i + 1}. a {h.get('to') or '(nessuno)'}: «{h.get('subject') or '(senza oggetto)'}»")
        return "\n".join(rows)

    def bozza_di(a) -> str:
        ids = g.last_drafts
        n = str(a["numero"]).strip()
        if not n.isdigit() or not 1 <= int(n) <= len(ids):
            raise AppError("numero di bozza sconosciuto: elenca prima le bozze")
        return ids[int(n) - 1]

    def invia_bozza_conferma(a):
        did = bozza_di(a)
        a["_draft"] = did
        h = _headers((g.get(f"/drafts/{did}", format="metadata").get("message") or {}).get("payload") or {})
        return f"inviare la bozza a {h.get('to') or '(nessuno)'} «{h.get('subject') or '(senza oggetto)'}»"

    @app.action("invia_bozza", "manda una bozza salvata", {"numero": "numero dall'elenco bozze"},
                confirm=invia_bozza_conferma)
    def invia_bozza(a):
        g.post("/drafts/send", {"id": a.get("_draft") or bozza_di(a)})
        return "bozza inviata"

    # ---------------------------------------------------------- ordinare (reversibile, senza conferma)

    def modify(add: list[str] | None = None, remove: list[str] | None = None, done: str = ""):
        def run(a):
            mid = g.resolve(a["mail"])
            g.post(f"/messages/{mid}/modify", {"addLabelIds": add or [], "removeLabelIds": remove or []})
            return done
        return run

    app.action("archivia", "toglie una mail dalla posta in arrivo (resta in Tutti i messaggi)",
               {"mail": "numero o ricerca"})(modify(remove=["INBOX"], done="archiviata"))
    app.action("segna_letta", "segna una mail come letta", {"mail": "numero o ricerca"})(
        modify(remove=["UNREAD"], done="segnata come letta"))
    app.action("segna_non_letta", "segna una mail come da leggere", {"mail": "numero o ricerca"})(
        modify(add=["UNREAD"], done="segnata come da leggere"))
    app.action("speciale", "mette la stella a una mail", {"mail": "numero o ricerca"})(
        modify(add=["STARRED"], done="con la stella"))
    app.action("togli_speciale", "toglie la stella", {"mail": "numero o ricerca"})(
        modify(remove=["STARRED"], done="stella tolta"))
    app.action("spam", "segna come spam", {"mail": "numero o ricerca"})(
        modify(add=["SPAM"], remove=["INBOX"], done="spostata nello spam"))
    app.action("non_spam", "toglie dallo spam e rimette in arrivo", {"mail": "numero o ricerca"})(
        modify(add=["INBOX"], remove=["SPAM"], done="rimessa nella posta in arrivo"))

    @app.action("ripristina", "riprende una mail dal cestino", {"mail": "numero o ricerca (es. in:trash fattura)"})
    def ripristina(a):
        g.post(f"/messages/{g.resolve(a['mail'])}/untrash")
        return "ripresa dal cestino"

    @app.action("etichetta", "mette o toglie un'etichetta (la crea se manca)",
                {"mail": "numero o ricerca", "nome": "l'etichetta", "togli": "? sì per toglierla"})
    def etichetta(a):
        mid = g.resolve(a["mail"])
        off = _yes(a.get("togli"))
        lid = g.label_id(str(a["nome"]), create=not off)
        g.post(f"/messages/{mid}/modify", {"addLabelIds": [] if off else [lid], "removeLabelIds": [lid] if off else []})
        return f"etichetta {a['nome']} {'tolta' if off else 'messa'}"

    # ---------------------------------------------------------- cancellare e regole (con conferma)

    def cestina_conferma(a):
        mid = g.resolve(a["mail"])
        a["_mail"] = mid
        return f"spostare nel cestino {brief(mid)}"

    @app.action("cestina", "sposta una mail nel cestino (si riprende entro 30 giorni)", {"mail": "numero o ricerca"},
                confirm=cestina_conferma)
    def cestina(a):
        g.post(f"/messages/{a.get('_mail') or g.resolve(a['mail'])}/trash")
        return "spostata nel cestino"

    BULK = {"letta": ([], ["UNREAD"]), "non_letta": (["UNREAD"], []), "archivia": ([], ["INBOX"]),
            "speciale": (["STARRED"], []), "cestina": None, "etichetta": None}

    def blocco_conferma(a):
        kind = str(a["azione"]).strip().lower().replace(" ", "_")
        if kind not in BULK:
            raise AppError(f"azione in blocco sconosciuta: {', '.join(BULK)}")
        ids = [m["id"] for m in g.get("/messages", q=str(a["cerca"]), maxResults=500).get("messages") or []]
        if not ids:
            raise AppError(f"nessuna mail per «{a['cerca']}»")
        a["_ids"], a["azione"] = ids, kind
        what = {"letta": "segnare come lette", "non_letta": "segnare come da leggere", "archivia": "archiviare",
                "speciale": "mettere la stella a", "cestina": "spostare nel cestino",
                "etichetta": f"mettere l'etichetta {a.get('etichetta')} a"}[kind]
        return f"{what} {len(ids)} mail (ricerca «{a['cerca']}»)"

    @app.action("in_blocco", "la stessa cosa su tutte le mail di una ricerca (fino a 500): letta, non_letta, "
                "archivia, speciale, cestina, etichetta",
                {"cerca": "la ricerca", "azione": "letta, non_letta, archivia, speciale, cestina o etichetta",
                 "etichetta": "? per azione etichetta"}, confirm=blocco_conferma)
    def in_blocco(a):
        if "_ids" not in a:
            blocco_conferma(a)
        ids, kind = a["_ids"], a["azione"]
        if kind == "cestina":
            for i in ids:
                g.post(f"/messages/{i}/trash")
        else:
            add, remove = ([g.label_id(str(a.get("etichetta")), create=True)], []) if kind == "etichetta" \
                else BULK[kind]
            for k in range(0, len(ids), 1000):
                g.post("/messages/batchModify", {"ids": ids[k:k + 1000], "addLabelIds": add, "removeLabelIds": remove})
        return f"fatto su {len(ids)} mail"

    def risposta_conferma(a):
        until = f" fino al {a['fino_al']}" if a.get("fino_al") else ""
        return f"attivare la risposta automatica{until}, a chiunque scriva: «{a['testo']}»"

    def _day_ms(text: str) -> int:
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
            try:
                return int(datetime.strptime(str(text).strip(), fmt).timestamp() * 1000)
            except ValueError:
                pass
        raise AppError(f"data non capita '{text}' (es. 31/10/2026)")

    @app.action("risposta_automatica", "attiva la risposta automatica (es. ferie)",
                {"testo": "il messaggio", "oggetto": "? l'oggetto", "fino_al": "? data, es. 31/10/2026"},
                confirm=risposta_conferma)
    def risposta_automatica(a):
        body = {"enableAutoReply": True, "responseSubject": str(a.get("oggetto") or "Risposta automatica"),
                "responseBodyPlainText": str(a["testo"]), "restrictToContacts": False, "restrictToDomain": False}
        if a.get("fino_al"):
            body["endTime"] = _day_ms(a["fino_al"]) + 86_399_000
        g.request("PUT", "/settings/vacation", body=body)
        return "risposta automatica attiva"

    @app.action("togli_risposta_automatica", "spegne la risposta automatica")
    def togli_risposta_automatica(_a):
        g.request("PUT", "/settings/vacation", body={"enableAutoReply": False})
        return "risposta automatica spenta"

    @app.action("filtri", "le regole automatiche (numerate)")
    def filtri(_a):
        found = g.get("/settings/filters").get("filter") or []
        g.last_filters = [f["id"] for f in found]
        if not found:
            return "nessun filtro"
        names = {v: k for k, v in g.labels().items()}
        rows = []
        for i, f in enumerate(found):
            c, act = f.get("criteria") or {}, f.get("action") or {}
            when_ = ", ".join(f"{k} {v}" for k, v in c.items())
            does = [f"etichetta {names.get(x, SYSTEM_LABELS.get(x, x))}" for x in act.get("addLabelIds") or []]
            does += [f"togli {SYSTEM_LABELS.get(x, names.get(x, x))}" for x in act.get("removeLabelIds") or []]
            rows.append(f"{i + 1}. se {when_}: {', '.join(does) or 'niente'}")
        return "\n".join(rows)

    def filtro_conferma(a):
        if not any(a.get(k) for k in ("da", "oggetto", "contiene")):
            raise AppError("serve almeno una condizione: da, oggetto o contiene")
        cond = ", ".join(f"{k} «{a[k]}»" for k in ("da", "oggetto", "contiene") if a.get(k))
        does = [f"etichetta {a['etichetta']}" if a.get("etichetta") else "", "salta la posta in arrivo"
                if _yes(a.get("archivia")) else "", "segna letta" if _yes(a.get("letta")) else ""]
        if not any(does):
            raise AppError("serve almeno un effetto: etichetta, archivia o letta")
        return f"creare la regola: per le mail con {cond} → {', '.join(d for d in does if d)} (vale anche in futuro)"

    @app.action("crea_filtro", "crea una regola automatica per le mail in arrivo",
                {"da": "? mittente", "oggetto": "? parole nell'oggetto", "contiene": "? parole nel testo",
                 "etichetta": "? etichetta da mettere", "archivia": "? sì per saltare la posta in arrivo",
                 "letta": "? sì per segnarle lette"}, confirm=filtro_conferma)
    def crea_filtro(a):
        criteria = {k2: str(a[k1]) for k1, k2 in (("da", "from"), ("oggetto", "subject"), ("contiene", "query"))
                    if a.get(k1)}
        add = [g.label_id(str(a["etichetta"]), create=True)] if a.get("etichetta") else []
        remove = (["INBOX"] if _yes(a.get("archivia")) else []) + (["UNREAD"] if _yes(a.get("letta")) else [])
        g.post("/settings/filters", {"criteria": criteria, "action": {"addLabelIds": add, "removeLabelIds": remove}})
        return "regola creata"

    def filtro_di(a) -> str:
        ids = g.last_filters
        n = str(a["numero"]).strip()
        if not n.isdigit() or not 1 <= int(n) <= len(ids):
            raise AppError("numero di filtro sconosciuto: elenca prima i filtri")
        return ids[int(n) - 1]

    def elimina_filtro_conferma(a):
        a["_filter"] = filtro_di(a)
        return f"eliminare la regola numero {a['numero']}"

    @app.action("elimina_filtro", "elimina una regola automatica", {"numero": "numero dall'elenco filtri"},
                confirm=elimina_filtro_conferma)
    def elimina_filtro(a):
        g.request("DELETE", f"/settings/filters/{a.get('_filter') or filtro_di(a)}")
        return "regola eliminata"

    return app
