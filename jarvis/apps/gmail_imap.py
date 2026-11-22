"""Gmail con una password per le app: IMAP e SMTP di Google, senza progetto su Google Cloud.

Il modo veloce di collegare Gmail: chi installa crea una password per le app (serve la verifica in due passaggi)
e la incolla nella finestra di Jarvis. `ImapSession` risponde alle stesse chiamate dell'API REST di Gmail che usa
jarvis/apps/gmail.py, così tutte le azioni restano una sola volta:

- gli id sono quelli dell'API: X-GM-MSGID e X-GM-THRID di Gmail in esadecimale;
- le ricerche usano la sintassi di Gmail (X-GM-RAW) in «Tutti i messaggi», o nel cestino e nello spam se la
  ricerca li nomina (come fa l'API, che di base non guarda lì);
- le etichette sono le cartelle IMAP (quelle di sistema si riconoscono dai segni \\All, \\Trash, \\Junk..., perché i
  nomi cambiano con la lingua dell'account); letta e stella sono \\Seen e \\Flagged;
- leggere non segna mai come letta (BODY.PEEK); cestino e spam sono spostamenti (MOVE), mai cancellazioni;
- si invia con SMTP: Gmail mette da solo la copia negli inviati e la conversazione dalle intestazioni.

Filtri e risposta automatica con IMAP non esistono: si dice chiaramente che servono il collegamento completo.
"""

from __future__ import annotations

import base64
import imaplib
import json
import re
import smtplib
import threading
import time
from collections.abc import Callable
from email import message_from_bytes, policy
from email.message import EmailMessage, Message
from email.utils import formatdate, make_msgid
from typing import Any

IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
APP_PASSWORDS = "https://myaccount.google.com/apppasswords"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT_S = 30
NO_SETTINGS = ("con la password per le app Gmail non permette filtri né risposta automatica: servono il collegamento "
               "completo con un progetto Google (azione guida)")
# le cartelle speciali di Gmail, dal segno che hanno nell'elenco IMAP (RFC 6154)
SPECIAL = {"\\all": "all", "\\trash": "trash", "\\junk": "spam", "\\sent": "sent", "\\drafts": "drafts",
           "\\flagged": "starred", "\\important": "important"}
# le etichette di sistema dell'API e la cartella IMAP che le contiene
SYSTEM_FOLDER = {"INBOX": "inbox", "STARRED": "starred", "IMPORTANT": "important", "SPAM": "spam", "TRASH": "trash",
                 "SENT": "sent", "DRAFT": "drafts"}
# le etichette di Gmail viste da IMAP (X-GM-LABELS) e il loro nome nell'API
GM_LABEL = {"\\Inbox": "INBOX", "\\Important": "IMPORTANT", "\\Sent": "SENT", "\\Starred": "STARRED",
            "\\Draft": "DRAFT"}


class AuthFailed(Exception):
    """Google non accetta indirizzo e password per le app (sbagliati, revocati, password dell'account normale)."""


class NotFound(Exception):
    pass


class SendFailed(Exception):
    """SMTP ha rifiutato o non ha risposto: la mail non si rimanda da sola (potrebbe partire due volte)."""


def is_auth_error(e: Exception) -> bool:
    text = str(e).lower()
    return any(w in text for w in ("authenticationfailed", "invalid credentials", "application-specific",
                                   "username and password not accepted", "badcredentials"))


def auth_reason(e: Exception) -> str:
    text = str(e)
    if "application-specific" in text.lower():
        return "Google chiede una password per le app, non la password dell'account"
    if "imap" in text.lower() and "disabled" in text.lower():
        return "su questo account l'accesso IMAP è spento (negli account di lavoro lo decide l'amministratore)"
    return "Google non accetta indirizzo e password per le app"


# ---------------------------------------------------------------------- nomi delle cartelle (UTF-7 modificato)


def mutf7_encode(text: str) -> str:
    """I nomi delle cartelle IMAP quando il server non accetta UTF-8 (RFC 3501, 5.1.3)."""
    out, buf = [], []

    def flush():
        if buf:
            raw = "".join(buf).encode("utf-16-be")
            out.append("&" + base64.b64encode(raw).decode().rstrip("=").replace("/", ",") + "-")
            buf.clear()

    for ch in text:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def mutf7_decode(text: str) -> str:
    def one(m: re.Match) -> str:
        chunk = m.group(1)
        if not chunk:
            return "&"
        b64 = chunk.replace(",", "/")
        return base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be")

    return re.sub(r"&([A-Za-z0-9+,]*)-", one, text)


def quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return re.sub(r"\\(.)", r"\1", raw[1:-1])
    return raw


# ---------------------------------------------------------------------- risposte del server


_LIST = re.compile(r'\((?P<flags>[^)]*)\) (?P<delim>"(?:[^"\\]|\\.)*"|NIL) (?P<name>.+)$')
_LABELS = re.compile(r'X-GM-LABELS \(((?:"(?:[^"\\]|\\.)*"|[^()"])*)\)')
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([^\s"]+)')


def parse_list(data: list) -> list[tuple[set[str], str]]:
    """Le righe di LIST: (segni in minuscolo, nome com'è sul server)."""
    out = []
    for item in data:
        if item is None:
            continue
        if isinstance(item, tuple):  # nome lungo spedito come literal
            head, name = item[0].decode("utf-8", "replace"), item[1].decode("utf-8", "replace")
            m = _LIST.match(re.sub(r"\{\d+\}$", '""', head))
            if m:
                out.append(({f.lower() for f in m.group("flags").split()}, name))
            continue
        m = _LIST.match(item.decode("utf-8", "replace"))
        if m:
            out.append(({f.lower() for f in m.group("flags").split()}, _unquote(m.group("name"))))
    return out


def parse_fetch(data: list) -> list[tuple[dict, bytes | None]]:
    """Le risposte di FETCH: per ogni mail le voci (uid, id, conversazione, etichette, segni) e il testo, se chiesto."""
    groups: list[list] = []
    for item in data:
        if isinstance(item, tuple):
            groups.append([item[0], item[1]])
        elif isinstance(item, bytes):
            if re.match(rb"\d+ \(", item):
                groups.append([item, None])
            elif groups:
                groups[-1][0] += item
    out = []
    for meta_b, body in groups:
        meta = meta_b.decode("utf-8", "replace")
        info: dict[str, Any] = {}
        for key, name in (("UID", "uid"), ("X-GM-MSGID", "msgid"), ("X-GM-THRID", "thrid")):
            m = re.search(rf"\b{key} (\d+)", meta)
            if m:
                info[name] = int(m.group(1))
        m = re.search(r"\bFLAGS \(([^)]*)\)", meta)
        info["flags"] = set(m.group(1).split()) if m else set()
        m = _LABELS.search(meta)
        info["labels"] = [re.sub(r"\\(.)", r"\1", q) if q or not a else a
                          for q, a in _TOKEN.findall(m.group(1))] if m else []
        out.append((info, body))
    return out


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def payload_of(msg: Message, path: str = "r") -> dict:
    """Una parte della mail come la descrive l'API di Gmail (testi in `data`, allegati con un `attachmentId`)."""
    part: dict[str, Any] = {"partId": path, "mimeType": msg.get_content_type(), "filename": msg.get_filename() or "",
                            "headers": [{"name": k, "value": str(v)} for k, v in msg.items()]}
    if msg.is_multipart():
        part["body"] = {"size": 0}
        part["parts"] = [payload_of(sub, f"{path}.{i}") for i, sub in enumerate(msg.get_payload())]
    else:
        data = msg.get_payload(decode=True) or b""
        part["body"] = ({"attachmentId": path, "size": len(data)} if part["filename"]
                        else {"data": b64url(data), "size": len(data)})
    return part


def part_at(msg: Message, path: str) -> Message:
    for i in path.split(".")[1:]:
        subs = msg.get_payload() if msg.is_multipart() else []
        if not i.isdigit() or int(i) >= len(subs):
            raise NotFound(path)
        msg = subs[int(i)]
    return msg


class Reply:
    """Come una risposta HTTP di requests: quello che gmail.Gmail.request legge."""

    def __init__(self, status: int, data: dict | None = None) -> None:
        self.status_code = status
        self._data = data or {}
        self.text = json.dumps(self._data) if data else ""

    def json(self) -> dict:
        return self._data


def _error(status: int, why: str) -> Reply:
    return Reply(status, {"error": {"message": why}})


# ---------------------------------------------------------------------- la sessione


class ImapSession:
    """Le chiamate dell'API di Gmail fatte con IMAP e SMTP (una connessione, riaperta da sola se cade)."""

    def __init__(self, address: str, password: str,
                 imap_factory: Callable[[], Any] = lambda: imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=TIMEOUT_S),
                 smtp_factory: Callable[[], Any] = lambda: smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_S),
                 ) -> None:
        self.address, self.password = address, password
        self.imap_factory, self.smtp_factory = imap_factory, smtp_factory
        self.imap: Any = None
        self.utf8 = False
        self.folders: dict[str, str] = {}
        self.user_labels: list[str] = []
        self.selected: str | None = None
        self.uids: dict[tuple[str, str], str] = {}  # (cartella, id della mail) -> uid in quella cartella
        self.headers: dict[str, dict] = {}  # intestazioni prese con l'elenco, lette una volta
        self.raw: dict[str, bytes] = {}  # le ultime mail intere (per gli allegati)
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ connessione

    def connect(self) -> None:
        imap = self.imap_factory()
        try:
            imap.login(self.address, self.password)
        except imaplib.IMAP4.error as e:
            if is_auth_error(e):
                raise AuthFailed(auth_reason(e)) from e
            raise  # per esempio troppe connessioni insieme: non vuol dire che la password non valga più
        self.utf8 = False
        caps = {str(c).upper() for c in getattr(imap, "capabilities", ())}
        try:  # Gmail annuncia UTF8=ACCEPT e MOVE solo dopo il login, e imaplib non le rilegge da solo
            typ, data = imap.capability()
            if typ == "OK" and data and data[-1]:
                caps |= set(data[-1].decode("ascii", "replace").upper().split())
        except imaplib.IMAP4.error:
            pass
        if "UTF8=ACCEPT" in caps:
            try:
                imap.enable("UTF8=ACCEPT")
                self.utf8 = True
            except imaplib.IMAP4.error:
                pass
        self.imap, self.selected = imap, None
        self.uids.clear()
        self._load_folders()

    def close(self) -> None:
        if self.imap is not None:
            try:
                self.imap.logout()
            except Exception:  # chiudere non deve mai far fallire niente
                pass
        self.imap = None

    def _wire(self, name: str) -> str:
        return quote(name if self.utf8 else mutf7_encode(name))

    def _name(self, raw: str) -> str:
        return raw if self.utf8 else mutf7_decode(raw)

    def _load_folders(self) -> None:
        self.folders, self.user_labels = {"inbox": "INBOX"}, []
        for flags, raw in parse_list(self._ok(self.imap.list())):
            name = self._name(raw)
            kind = next((SPECIAL[f] for f in flags if f in SPECIAL), None)
            if kind:
                self.folders.setdefault(kind, name)
            elif name.upper() != "INBOX" and "\\noselect" not in flags:
                self.user_labels.append(name)
        if "all" not in self.folders:
            raise NotFound("non trovo «Tutti i messaggi» in IMAP: in Gmail, Impostazioni, Etichette, va mostrata in "
                           "IMAP")

    def _ok(self, result: tuple) -> list:
        typ, data = result
        if typ != "OK":
            raise imaplib.IMAP4.error(f"{typ} {data!r}")
        return data

    def _select(self, kind: str) -> None:
        name = self.folders.get(kind)
        if name is None:
            raise NotFound(f"in questo account manca la cartella {kind}")
        if self.selected != kind:
            self._ok(self.imap.select(self._wire(name)))
            self.selected = kind

    # ------------------------------------------------------------------ cercare e trovare

    def _search(self, kind: str, *criteria: str) -> list[int]:
        self._select(kind)
        data = self._ok(self.imap.uid("SEARCH", *criteria))
        return sorted(int(x) for x in b" ".join(d for d in data if d).split())

    def _raw_search(self, kind: str, query: str) -> list[int]:
        if self.utf8 or query.isascii():
            return self._search(kind, "X-GM-RAW", quote(query))
        self.imap.literal = query.encode("utf-8")  # niente UTF-8 nelle stringhe: va come literal
        return self._search(kind, "CHARSET", "UTF-8", "X-GM-RAW")

    def _fetch(self, kind: str, uids: list[int] | list[str], what: str) -> list[tuple[dict, bytes | None]]:
        if not uids:
            return []
        self._select(kind)
        found = parse_fetch(self._ok(self.imap.uid("FETCH", ",".join(str(u) for u in uids),
                                                    f"(UID X-GM-MSGID X-GM-THRID X-GM-LABELS FLAGS{what})")))
        for info, _ in found:
            if "msgid" in info and "uid" in info:
                self.uids[(kind, f"{info['msgid']:x}")] = str(info["uid"])
        return found

    def _locate(self, mid: str, kinds: tuple[str, ...] = ("all", "trash", "spam")) -> tuple[str, str]:
        for kind in kinds:
            if (kind, mid) in self.uids:
                return kind, self.uids[(kind, mid)]
        if not re.fullmatch(r"[0-9a-f]{1,20}", mid):
            raise NotFound(mid)
        for kind in kinds:
            if kind not in self.folders:
                continue
            found = self._search(kind, "X-GM-MSGID", str(int(mid, 16)))
            if found:
                self.uids[(kind, mid)] = str(found[0])
                return kind, str(found[0])
        raise NotFound(mid)

    def _forget(self, mid: str) -> None:
        for key in [k for k in self.uids if k[1] == mid]:
            del self.uids[key]
        self.headers.pop(mid, None)

    def _labels_of(self, info: dict, kind: str) -> list[str]:
        ids = [] if "\\Seen" in info["flags"] else ["UNREAD"]
        if "\\Flagged" in info["flags"]:
            ids.append("STARRED")
        if "\\Draft" in info["flags"]:
            ids.append("DRAFT")
        for lb in info["labels"]:
            name = GM_LABEL.get(lb) or (None if lb.startswith("\\") else self._name(lb))
            if name and name not in ids:
                ids.append(name)
        if kind in ("trash", "spam"):
            ids.append(kind.upper())
        return ids

    def _message(self, kind: str, info: dict, body: bytes | None, fmt: str) -> dict:
        mid = f"{info.get('msgid', 0):x}"
        out: dict[str, Any] = {"id": mid, "threadId": f"{info.get('thrid', 0):x}",
                               "labelIds": self._labels_of(info, kind), "snippet": ""}
        if fmt != "minimal":
            msg = message_from_bytes(body or b"", policy=policy.default)
            out["payload"] = payload_of(msg)
            if fmt == "full":
                self.raw[mid] = body or b""
                while len(self.raw) > 3:
                    self.raw.pop(next(iter(self.raw)))
        return out

    def _get(self, mid: str, fmt: str, kinds: tuple[str, ...] = ("all", "trash", "spam")) -> dict:
        if fmt == "metadata" and mid in self.headers:
            return self.headers.pop(mid)
        kind, uid = self._locate(mid, kinds)
        what = {"minimal": "", "metadata": " BODY.PEEK[HEADER]"}.get(fmt, " BODY.PEEK[]")
        found = self._fetch(kind, [uid], what)
        if not found:
            self._forget(mid)
            raise NotFound(mid)
        info, body = found[0]
        return self._message(kind, info, body, "metadata" if fmt == "metadata" else "minimal" if fmt == "minimal"
                             else "full")

    def _folder_for(self, query: str) -> str:
        q = query.lower()
        if re.search(r"\bin:trash\b", q):
            return "trash"
        if re.search(r"\b(in|is):spam\b", q):
            return "spam"
        return "all"

    def _listing(self, kind: str, uids: list[int], limit: int) -> list[dict]:
        newest = sorted(uids, reverse=True)[:limit]
        by_uid = {}
        for info, body in self._fetch(kind, newest, " BODY.PEEK[HEADER]"):
            msg = self._message(kind, info, body, "metadata")
            self.headers[msg["id"]] = msg
            by_uid[info.get("uid")] = msg
        return [{"id": by_uid[u]["id"], "threadId": by_uid[u]["threadId"]} for u in newest if u in by_uid]

    # ------------------------------------------------------------------ cambiare etichette e cartelle

    def _apply(self, kind: str, uids: list[str], add: list[str], remove: list[str]) -> None:
        if not uids:
            return
        self._select(kind)
        seq = ",".join(uids)
        flag = {"UNREAD": "\\Seen", "STARRED": "\\Flagged"}

        def store(op: str, item: str, value: str) -> None:
            self._ok(self.imap.uid("STORE", seq, op + item, f"({value})"))

        for lid, on in [(x, True) for x in add] + [(x, False) for x in remove]:
            if lid == "UNREAD":  # "da leggere" è l'assenza di \Seen: il segno va al contrario
                store("-" if on else "+", "FLAGS", "\\Seen")
            elif lid in flag:
                store("+" if on else "-", "FLAGS", flag[lid])
            elif lid in ("IMPORTANT", "INBOX"):
                if lid == "INBOX" and on and kind != "all":
                    continue  # dal cestino o dallo spam si torna in arrivo spostando (sotto)
                store("+" if on else "-", "X-GM-LABELS", "\\Important" if lid == "IMPORTANT" else "\\Inbox")
            elif lid not in ("SPAM", "TRASH"):
                store("+" if on else "-", "X-GM-LABELS", self._wire(lid))
        if "SPAM" in add and kind != "spam":
            self._move(kind, seq, "spam")
        elif ("INBOX" in add or "SPAM" in remove) and kind in ("spam", "trash"):
            self._move(kind, seq, "inbox")

    def _move(self, kind: str, seq: str, to: str) -> None:
        self._select(kind)
        self._ok(self.imap.uid("MOVE", seq, self._wire(self.folders[to])))
        for key in [k for k, v in self.uids.items() if k[0] == kind and v in seq.split(",")]:
            del self.uids[key]

    def _modify(self, ids: list[str], add: list[str], remove: list[str]) -> None:
        by_kind: dict[str, list[str]] = {}
        for mid in ids:
            kind, uid = self._locate(mid)
            by_kind.setdefault(kind, []).append(uid)
            self.headers.pop(mid, None)
        for kind, uids in by_kind.items():
            for k in range(0, len(uids), 200):
                self._apply(kind, uids[k:k + 200], add, remove)

    # ------------------------------------------------------------------ inviare

    def _complete(self, msg: EmailMessage) -> EmailMessage:
        if not msg["From"]:
            msg["From"] = self.address
        if not msg["Date"]:
            msg["Date"] = formatdate(localtime=True)
        if not msg["Message-ID"]:
            msg["Message-ID"] = make_msgid(domain=self.address.rpartition("@")[2] or None)
        return msg

    def _send(self, raw: bytes) -> None:
        msg = self._complete(message_from_bytes(raw, policy=policy.default))
        try:
            smtp = self.smtp_factory()
        except OSError as e:  # anche SMTPException è un OSError
            raise SendFailed(f"il server della posta non risponde ({type(e).__name__})") from e
        try:
            try:
                smtp.login(self.address, self.password)
            except smtplib.SMTPAuthenticationError as e:
                raise AuthFailed(auth_reason(e)) from e
            smtp.send_message(msg)
        except OSError as e:
            raise SendFailed(f"la mail non è partita ({str(e)[:160] or type(e).__name__})") from e
        finally:
            try:
                smtp.quit()
            except Exception:  # la mail è già partita (o no): chiudere non cambia niente
                pass

    # ------------------------------------------------------------------ le chiamate dell'API

    def request(self, method: str, url: str, params: dict | None = None, json: dict | None = None,
                timeout: float | None = None) -> Reply:
        if not url.startswith(API):
            return Reply(200)  # la revoca di OAuth: con la password per le app non c'è niente da revocare qui
        path = url.removeprefix(API)
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.imap is None:
                        self.connect()
                    return self._route(method, path, params or {}, json or {})
                except NotFound as e:
                    return _error(404, f"non trovata ({e})")
                except SendFailed as e:  # prima di OSError: un invio non si ritenta mai da solo
                    return _error(502, str(e))
                except AuthFailed:
                    self.close()
                    raise
                except (imaplib.IMAP4.abort, OSError):  # connessione caduta o scaduta: si riapre una volta
                    self.close()
                    if attempt == 2:
                        raise
                except imaplib.IMAP4.error as e:
                    return _error(400, str(e)[:200])
        raise AssertionError("non arriva qui")

    def _route(self, method: str, path: str, params: dict, body: dict) -> Reply:
        if path.startswith("/settings/"):
            return _error(400, NO_SETTINGS)
        m = re.fullmatch(r"/messages/([0-9a-f]+)(?:/(\w+)(?:/([\w.]+))?)?", path)
        if path == "/profile":
            total = re.search(rb"MESSAGES (\d+)", b" ".join(self._ok(self.imap.status(
                self._wire(self.folders["all"]), "(MESSAGES)"))))
            return Reply(200, {"emailAddress": self.address, "messagesTotal": int(total.group(1)) if total else 0})
        if path == "/messages" and method == "GET":
            query = str(params.get("q") or "").strip()
            kind = self._folder_for(query)
            uids = self._raw_search(kind, query) if query else self._search(kind, "ALL")
            return Reply(200, {"messages": self._listing(kind, uids, int(params.get("maxResults") or 100))})
        if path == "/messages/send" and method == "POST":
            self._send(base64.urlsafe_b64decode(body["raw"] + "=" * (-len(body["raw"]) % 4)))
            return Reply(200, {"id": "", "threadId": body.get("threadId", "")})
        if path == "/messages/batchModify" and method == "POST":
            self._modify(list(body.get("ids") or []), body.get("addLabelIds") or [], body.get("removeLabelIds") or [])
            return Reply(204)
        if m and method == "GET" and not m.group(2):
            return Reply(200, self._get(m.group(1), str(params.get("format") or "full")))
        if m and method == "GET" and m.group(2) == "attachments":
            return Reply(200, self._attachment(m.group(1), m.group(3) or ""))
        if m and method == "POST" and m.group(2) == "modify":
            self._modify([m.group(1)], body.get("addLabelIds") or [], body.get("removeLabelIds") or [])
            return Reply(200, {"id": m.group(1)})
        if m and method == "POST" and m.group(2) == "trash":
            kind, uid = self._locate(m.group(1))
            if kind != "trash":
                self._move(kind, uid, "trash")
            self._forget(m.group(1))
            return Reply(200, {"id": m.group(1), "labelIds": ["TRASH"]})
        if m and method == "POST" and m.group(2) == "untrash":
            kind, uid = self._locate(m.group(1), ("trash", "all"))
            if kind == "trash":
                self._move(kind, uid, "inbox")
            self._forget(m.group(1))
            return Reply(200, {"id": m.group(1)})
        t = re.fullmatch(r"/threads/([0-9a-f]+)", path)
        if t and method == "GET":
            uids = self._search("all", "X-GM-THRID", str(int(t.group(1), 16)))
            return Reply(200, {"id": t.group(1), "messages": [self._message("all", info, body_, "full") for info, body_
                                                              in self._fetch("all", uids, " BODY.PEEK[]")]})
        if path == "/labels" and method == "GET":
            self._load_folders()
            system = [{"id": lid, "name": lid, "type": "system"} for lid, kind in SYSTEM_FOLDER.items()
                      if kind in self.folders]
            return Reply(200, {"labels": system + [{"id": n, "name": n, "type": "user"} for n in self.user_labels]})
        if path == "/labels" and method == "POST":
            name = str(body.get("name") or "").strip()
            typ, data = self.imap.create(self._wire(name))
            if typ != "OK" and b"exists" not in b" ".join(d for d in data if isinstance(d, bytes)).lower():
                raise imaplib.IMAP4.error(f"etichetta non creata: {data!r}")
            self.user_labels.append(name)
            return Reply(200, {"id": name, "name": name})
        lb = re.fullmatch(r"/labels/(.+)", path)
        if lb and method == "GET":
            lid = lb.group(1)
            name = self.folders.get(SYSTEM_FOLDER.get(lid, ""), lid)
            st = b" ".join(self._ok(self.imap.status(self._wire(name), "(MESSAGES UNSEEN)")))
            unseen, total = re.search(rb"UNSEEN (\d+)", st), re.search(rb"MESSAGES (\d+)", st)
            return Reply(200, {"id": lid, "messagesUnread": int(unseen.group(1)) if unseen else 0,
                               "messagesTotal": int(total.group(1)) if total else 0})
        if path == "/drafts" and method == "GET":
            uids = self._search("drafts", "ALL")
            return Reply(200, {"drafts": [{"id": x["id"], "message": x} for x
                                          in self._listing("drafts", uids, int(params.get("maxResults") or 100))]})
        if path == "/drafts" and method == "POST":
            raw = base64.urlsafe_b64decode(body["message"]["raw"] + "=" * (-len(body["message"]["raw"]) % 4))
            msg = self._complete(message_from_bytes(raw, policy=policy.default))
            self._ok(self.imap.append(self._wire(self.folders["drafts"]), "(\\Draft)", time.time(), msg.as_bytes()))
            return Reply(200, {"id": ""})
        if path == "/drafts/send" and method == "POST":
            did = str(body.get("id") or "")
            kind, uid = self._locate(did, ("drafts",))
            found = self._fetch(kind, [uid], " BODY.PEEK[]")
            if not found:
                raise NotFound(did)
            self._send(found[0][1] or b"")
            self._move(kind, uid, "trash")  # come l'API: la bozza mandata non resta tra le bozze (va nel cestino)
            self._forget(did)
            return Reply(200, {"id": did})
        d = re.fullmatch(r"/drafts/([0-9a-f]+)", path)
        if d and method == "GET":
            return Reply(200, {"id": d.group(1), "message": self._get(d.group(1), str(params.get("format") or "full"),
                                                                      ("drafts",))})
        return _error(400, f"con la password per le app questa richiesta non c'è ({method} {path})")

    def _attachment(self, mid: str, aid: str) -> dict:
        raw = self.raw.get(mid)
        if raw is None:
            self._get(mid, "full")
            raw = self.raw.get(mid, b"")
        part = part_at(message_from_bytes(raw, policy=policy.default), aid)
        data = part.get_payload(decode=True) or b""
        return {"data": b64url(data), "size": len(data)}


def check(address: str, password: str, imap_factory: Callable[[], Any] | None = None) -> None:
    """Prova il collegamento (login e cartelle). AuthFailed se Google rifiuta, OSError se non risponde."""
    s = ImapSession(address, password, **({"imap_factory": imap_factory} if imap_factory else {}))
    try:
        s.connect()
    finally:
        s.close()


APP_PASSWORD = re.compile(r"[a-z]{16}")


def clean_app_password(text: str) -> str:
    """La password per le app come la mostra Google ("abcd efgh ijkl mnop"): 16 lettere, spazi tolti."""
    pw = re.sub(r"\s+", "", str(text or "")).lower()
    if not APP_PASSWORD.fullmatch(pw):
        raise ValueError("la password per le app di Google ha 16 lettere (come «abcd efgh ijkl mnop»)")
    return pw
