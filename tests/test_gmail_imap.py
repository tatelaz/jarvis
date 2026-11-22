"""Gmail con la password per le app (jarvis/apps/gmail_imap.py): le azioni di jarvis/apps/gmail.py contro un server
IMAP finto che si comporta come quello di Gmail (cartelle in italiano, X-GM-MSGID, X-GM-LABELS, X-GM-RAW, uid
diversi in ogni cartella) e un SMTP finto. Ogni prova gira due volte: con il server che accetta UTF-8 e con quello che
vuole i nomi in UTF-7 modificato. Niente esce dal PC.
"""

import base64
import imaplib
import json
import re
import smtplib
import time
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest

from jarvis.apps import gmail as G
from jarvis.apps import gmail_imap as I
from jarvis.apps.base import Apps

NOW = time.mktime((2026, 10, 27, 15, 0, 0, 0, 0, -1))
TODAY = "Tue, 27 Oct 2026 14:32:00 +0100"
YESTERDAY = "Mon, 26 Oct 2026 09:10:00 +0100"
ME = "andrea@gmail.com"
SHOWN = "abcd efgh ijkl mnop"  # come la mostra Google
PW = "abcdefghijklmnop"

FOLDERS = [("\\HasNoChildren", "INBOX"), ("\\HasChildren \\Noselect", "[Gmail]"),
           ("\\All \\HasNoChildren", "[Gmail]/Tutti i messaggi"), ("\\HasNoChildren \\Trash", "[Gmail]/Cestino"),
           ("\\HasNoChildren \\Junk", "[Gmail]/Spam"), ("\\HasNoChildren \\Sent", "[Gmail]/Posta inviata"),
           ("\\Drafts \\HasNoChildren", "[Gmail]/Bozze"), ("\\Flagged \\HasNoChildren", "[Gmail]/Speciali"),
           ("\\HasNoChildren \\Important", "[Gmail]/Importanti"), ("\\HasNoChildren", "Lavoro"),
           ("\\HasNoChildren", "Città")]


def mail(frm, to, subject, date, text, *, cc="", charset="utf-8", html=False, attach=None, mid=""):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, to, subject, date
    if cc:
        m["Cc"] = cc
    if mid:
        m["Message-ID"] = mid
    m.set_content(text, subtype="html" if html else "plain", charset=charset)
    if attach:
        m.add_attachment(attach[1], maintype="application", subtype="pdf", filename=attach[0])
    return m.as_bytes(policy=policy.SMTP)


class Mail:
    def __init__(self, n, raw, labels=(), flags=(), thrid=None, where="all"):
        self.n, self.raw = n, raw
        self.msgid, self.thrid = 1_700_000_000_000_000_000 + n, thrid or 1_700_000_000_000_000_000 + n
        self.labels, self.flags, self.where = list(labels), set(flags), where

    @property
    def hexid(self):
        return f"{self.msgid:x}"

    def text(self):
        m = message_from_bytes(self.raw, policy=policy.default)
        body = m.get_body(("plain", "html"))
        heads = " ".join(f"{k} {v}" for k, v in m.items())
        return (heads + " " + (body.get_content() if body else "")).lower()

    def header(self, name):
        return str(message_from_bytes(self.raw, policy=policy.default)[name] or "").lower()


def tokens(inner: str) -> list[str]:
    return [re.sub(r"\\(.)", r"\1", q) if q or not a else a for q, a in I._TOKEN.findall(inner)]


class FakeServer:
    """Lo stato dell'account (le mail) e le regole del server; ogni connect() è una connessione nuova."""

    def __init__(self, utf8: bool):
        self.utf8_ok = utf8
        self.folders = list(FOLDERS)
        thread = 1_700_000_000_000_000_001
        self.mails = [
            Mail(1, mail('"Luca Rossi" <luca@rossi.it>', ME, "Riunione", TODAY,
                         "Ciao Andrea,\nla riunione è spostata a giovedì.\nLuca", cc="giulia@verdi.it",
                         attach=("verbale.pdf", b"%PDF-finto"), mid="<m1@rossi.it>"),
                 labels=["\\Inbox", "\\Important"], thrid=thread),
            Mail(2, mail("Amazon <spedizioni@amazon.it>", ME, "Il tuo ordine è stato spedito", YESTERDAY,
                         "<html><body><p>Ciao&nbsp;Andrea,</p><p>il tuo ordine &egrave; <b>in viaggio</b>.</p>"
                         "<script>x()</script></body></html>", html=True, mid="<m2@amazon.it>"),
                 labels=["\\Inbox"], flags=["\\Seen"]),
            Mail(3, mail(ME, "Luca Rossi <luca@rossi.it>", "Re: Riunione", TODAY, "Va bene, a giovedì.",
                         mid="<m3@gmail.com>"), labels=["\\Sent"], flags=["\\Seen"], thrid=thread),
            Mail(4, mail("Luca Bianchi <lollo@bianchi.com>", ME, "Calcetto", YESTERDAY, "Caffè è pronto",
                         charset="iso-8859-1"), labels=["\\Inbox", "Città"]),
        ]
        self.password = PW
        self.connections = 0
        self.commands: list[tuple] = []
        self.abort_once = False
        self.down = False
        self.login_error: str | None = None

    def connect(self):
        if self.down:
            raise OSError("rete giù")
        self.connections += 1
        return FakeImap(self)

    # ---------------------------------------------------------------- cartelle e mail

    def folder_of(self, name: str) -> int:
        for i, (_flags, n) in enumerate(self.folders):
            if n == name:
                return i
        raise KeyError(name)

    def members(self, i: int) -> list[Mail]:
        _flags, name = self.folders[i]
        rule = {
            "INBOX": lambda m: m.where == "all" and "\\Inbox" in m.labels,
            "[Gmail]/Tutti i messaggi": lambda m: m.where == "all",
            "[Gmail]/Cestino": lambda m: m.where == "trash", "[Gmail]/Spam": lambda m: m.where == "spam",
            "[Gmail]/Posta inviata": lambda m: m.where == "all" and "\\Sent" in m.labels,
            "[Gmail]/Bozze": lambda m: m.where == "all" and "\\Draft" in m.flags,
            "[Gmail]/Speciali": lambda m: m.where == "all" and "\\Flagged" in m.flags,
            "[Gmail]/Importanti": lambda m: m.where == "all" and "\\Important" in m.labels,
        }.get(name, lambda m: m.where == "all" and name in m.labels)
        return [m for m in self.mails if rule(m)]

    def uid(self, i: int, m: Mail) -> int:
        return m.n + 100 * i  # ogni cartella ha i suoi uid, come in Gmail

    def matches(self, m: Mail, query: str) -> bool:
        for alt in query.split(" OR "):
            ok = True
            for term in re.findall(r'\w+:\([^)]*\)|"[^"]*"|\S+', alt):
                key, _, val = term.partition(":")
                val = val.strip("()").lower()
                if term == "is:unread":
                    ok &= "\\Seen" not in m.flags
                elif term == "in:inbox":
                    ok &= "\\Inbox" in m.labels
                elif term in ("in:trash", "in:spam", "is:spam"):
                    pass  # lo decide la cartella scelta
                elif key in ("from", "to", "cc") and val:
                    ok &= val in m.header(key)
                elif key == "label":
                    ok &= any(val == lb.lower() for lb in m.labels)
                else:
                    ok &= term.strip('"').lower() in m.text()
            if ok:
                return True
        return False


class FakeImap:
    """Una connessione: risponde come imaplib (tipo, dati), con i literal nelle tuple."""

    def __init__(self, server: FakeServer):
        self.s = server
        self.capabilities = ("IMAP4REV1", "X-GM-EXT-1", "XLIST")  # come Gmail prima del login (provato il 22/11)
        self.logged = False
        self.utf8 = False
        self.sel: int | None = None
        self.literal = None
        self.dead = False

    def _enc(self, text: str) -> str:
        return text if self.utf8 else I.mutf7_encode(text)

    def _dec(self, text: str) -> str:
        return text if self.utf8 else I.mutf7_decode(text)

    def _check(self, *args):
        if self.dead:
            raise imaplib.IMAP4.abort("socket error: EOF")
        if self.s.abort_once:
            self.s.abort_once, self.dead = False, True
            raise imaplib.IMAP4.abort("socket error: EOF")
        for a in args:  # come imaplib: senza UTF8=ACCEPT i comandi vanno in ASCII
            if isinstance(a, str):
                a.encode("utf-8" if self.utf8 else "ascii")

    def login(self, user, password):
        self._check(user, password)
        if self.s.login_error:
            raise imaplib.IMAP4.error(self.s.login_error)
        if (user, password) != (ME, self.s.password):
            raise imaplib.IMAP4.error("b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'")
        self.logged = True
        return "OK", [b"authenticated (Success)"]

    def capability(self):  # dopo il login Gmail annuncia anche MOVE, SPECIAL-USE e UTF8=ACCEPT
        after = " MOVE SPECIAL-USE" + (" UTF8=ACCEPT" if self.s.utf8_ok else "") if self.logged else ""
        return "OK", [f"IMAP4rev1 X-GM-EXT-1 XLIST{after}".encode()]

    def enable(self, cap):
        assert cap == "UTF8=ACCEPT" and self.s.utf8_ok and self.logged
        self.utf8 = True
        return "OK", [b"ENABLED"]

    def logout(self):
        return "BYE", [b"LOGOUT"]

    def list(self):
        self._check()
        enc = "utf-8" if self.utf8 else "ascii"
        return "OK", [f'({flags}) "/" {I.quote(self._enc(name))}'.encode(enc) for flags, name in self.s.folders]

    def _folder(self, mailbox: str) -> int:
        return self.s.folder_of(self._dec(I._unquote(mailbox)))

    def select(self, mailbox):
        self._check(mailbox)
        self.sel = self._folder(mailbox)
        self.s.commands.append(("SELECT", self.s.folders[self.sel][1]))
        return "OK", [str(len(self.s.members(self.sel))).encode()]

    def status(self, mailbox, items):
        self._check(mailbox, items)
        ms = self.s.members(self._folder(mailbox))
        unseen = sum(1 for m in ms if "\\Seen" not in m.flags)
        return "OK", [f"{mailbox} (MESSAGES {len(ms)} UNSEEN {unseen})".encode()]

    def create(self, mailbox):
        self._check(mailbox)
        name = self._dec(I._unquote(mailbox))
        if any(n == name for _f, n in self.s.folders):
            return "NO", [b"[ALREADYEXISTS] Duplicate folder name (Failure)"]
        self.s.folders.append(("\\HasNoChildren", name))
        return "OK", [b"Success"]

    def append(self, mailbox, flags, date_time, message):
        self._check(mailbox, flags)
        assert self._dec(I._unquote(mailbox)) == "[Gmail]/Bozze" and flags == "(\\Draft)"
        n = len(self.s.mails) + 1
        self.s.mails.append(Mail(n, message, flags=["\\Draft", "\\Seen"]))
        return "OK", [b"[APPENDUID 1 99] (Success)"]

    def uid(self, command, *args):
        self._check(*args)
        assert self.sel is not None, "comando senza cartella scelta"
        self.s.commands.append((command, *args))
        here = self.s.members(self.sel)
        uid_of = {self.s.uid(self.sel, m): m for m in here}
        if command == "SEARCH":
            if args[0] == "ALL":
                found = list(uid_of)
            elif args[0] == "X-GM-MSGID":
                found = [u for u, m in uid_of.items() if m.msgid == int(args[1])]
            elif args[0] == "X-GM-THRID":
                found = [u for u, m in uid_of.items() if m.thrid == int(args[1])]
            elif args[0] == "X-GM-RAW":
                q = I._unquote(args[1])
                found = [u for u, m in uid_of.items() if self.s.matches(m, q)]
            elif args[:3] == ("CHARSET", "UTF-8", "X-GM-RAW"):
                q, self.literal = self.literal.decode("utf-8"), None
                found = [u for u, m in uid_of.items() if self.s.matches(m, q)]
            else:
                raise AssertionError(args)
            return "OK", [" ".join(str(u) for u in sorted(found)).encode()]
        picked = [uid_of[int(u)] for u in args[0].split(",") if int(u) in uid_of]
        if command == "FETCH":
            return "OK", self._fetch(picked, args[1])
        if command == "STORE":
            op, item = args[1][0], args[1][1:]
            values = [self._dec(v) for v in tokens(args[2].strip("()"))]
            for m in picked:
                target = m.flags if item == "FLAGS" else m.labels
                for v in values:
                    if op == "+" and v not in target:
                        target.add(v) if isinstance(target, set) else target.append(v)
                    elif op == "-" and v in target:
                        target.remove(v)
            return "OK", [b"STORED"]
        if command == "MOVE":
            dest = self._dec(I._unquote(args[1]))
            for m in picked:
                if dest == "INBOX":
                    m.where = "all"
                    if "\\Inbox" not in m.labels:
                        m.labels.append("\\Inbox")
                else:
                    m.where = {"[Gmail]/Cestino": "trash", "[Gmail]/Spam": "spam"}[dest]
            return "OK", [b"MOVED"]
        raise AssertionError(command)

    def _fetch(self, picked, items):
        enc = "utf-8" if self.utf8 else "ascii"
        out = []
        for seq, m in enumerate(sorted(picked, key=lambda x: x.n), 1):
            uid = self.s.uid(self.sel, m)
            labels = " ".join(I.quote(lb) if lb.startswith("\\") else I.quote(self._enc(lb)) for lb in m.labels)
            head = f"{seq} (X-GM-THRID {m.thrid} X-GM-MSGID {m.msgid} X-GM-LABELS ({labels}) UID {uid}"
            if "BODY[]" in items.replace("BODY.PEEK[]", ""):
                m.flags.add("\\Seen")  # leggere senza PEEK segna come letta: Jarvis non deve farlo
            body = None
            if "BODY.PEEK[]" in items or "BODY[]" in items:
                body, what = m.raw, "BODY[]"
            elif "BODY.PEEK[HEADER]" in items:
                body, what = m.raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n", "BODY[HEADER]"
            flags = " ".join(sorted(m.flags))
            if body is None:
                out.append(f"{head} FLAGS ({flags}))".encode(enc))
            else:  # come Gmail: i segni possono arrivare dopo il literal
                out.append((f"{head} {what} {{{len(body)}}}".encode(enc), body))
                out.append(f" FLAGS ({flags}))".encode(enc))
        return out


class FakeSmtp:
    def __init__(self):
        self.sent: list = []
        self.logins: list = []
        self.fail: Exception | None = None

    def connect(self):
        smtp = self

        class Conn:
            def login(self, user, password):
                smtp.logins.append(user)
                if password != PW:
                    raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

            def send_message(self, msg):
                if smtp.fail:
                    raise smtp.fail
                smtp.sent.append(msg)

            def quit(self):
                pass

        return Conn()


def lock(data: bytes) -> bytes:
    return b"CIFRATO" + data[::-1]


def unlock(blob: bytes) -> bytes:
    assert blob.startswith(b"CIFRATO")
    return blob[7:][::-1]


@pytest.fixture(params=[True, False], ids=["utf8", "utf7"])
def quick(tmp_path, request):
    server, smtp = FakeServer(utf8=request.param), FakeSmtp()
    notes, opened, forms, checks = [], [], [], []

    def session(info):
        assert info["mode"] == "imap"
        return I.ImapSession(info["email"], info["password"], imap_factory=server.connect, smtp_factory=smtp.connect)

    def check(address, password):
        checks.append(address)
        I.check(address, password, imap_factory=server.connect)

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    g = G.Gmail(tmp_path / "gmail", downloads, notify=notes.append, session_factory=session, protect_fn=lock,
                unprotect_fn=unlock, check_login=check, open_url=opened.append, show_form=lambda: forms.append(1))
    turns = {"n": 0, "text": ""}
    apps = Apps([G.build(g, now=lambda: NOW)], turns=lambda: turns["n"], last_text=lambda: turns["text"])

    class Ctx:
        pass

    c = Ctx()
    c.server, c.smtp, c.g, c.apps, c.notes, c.opened, c.forms, c.checks, c.downloads = (
        server, smtp, g, apps, notes, opened, forms, checks, downloads)

    def say(text):
        turns["n"] += 1
        turns["text"] = text

    c.say = say
    return c


async def linked(c):
    assert await c.g.link_app_password(ME, SHOWN) == "ok"
    return c


def code_of(out):
    assert out.startswith("DA CONFERMARE (codice "), out
    return out.split("codice ")[1].split(")")[0]


async def confirmed(c, action, params):
    ask = await c.apps.use("gmail", action, params)
    c.say("sì, procedi")
    return ask, await c.apps.use("gmail", action, {}, code_of(ask))


def mail_n(c, n) -> Mail:
    return next(m for m in c.server.mails if m.n == n)


# ------------------------------------------------------------------ pezzi


def test_folder_names_in_modified_utf7():
    assert I.mutf7_encode("Città") == "Citt&AOA-" and I.mutf7_encode("A&B") == "A&-B"
    for name in ("Città", "A&B", "日本 & 🎉 ok", "[Gmail]/Tutti i messaggi", "ünï/çödé"):
        assert I.mutf7_decode(I.mutf7_encode(name)) == name
        assert I.mutf7_encode(name).isascii()


def test_app_password_shape():
    assert I.clean_app_password(SHOWN) == PW and I.clean_app_password(" ABCD EFGH IJKL MNOP ") == PW
    for bad in ("", "abcd efgh ijkl", "abcd efgh ijkl mno1", "abcdefghijklmnopq", "la mia password"):
        with pytest.raises(ValueError, match="16 lettere"):
            I.clean_app_password(bad)


def test_fetch_lines_with_literal_and_flags_after_it():
    data = [(b'1 (X-GM-THRID 7 X-GM-MSGID 255 X-GM-LABELS ("\\\\Inbox" "Lavoro (vecchio)" Caff&AOg-) UID 9 '
             b"BODY[] {5}", b"ciao!"), b" FLAGS (\\Seen \\Flagged))", b"2 (X-GM-MSGID 16 UID 10 FLAGS ())"]
    (a, body_a), (b, body_b) = I.parse_fetch(data)
    assert (a["uid"], a["msgid"], a["thrid"], body_a) == (9, 255, 7, b"ciao!")
    assert a["labels"] == ["\\Inbox", "Lavoro (vecchio)", "Caff&AOg-"] and a["flags"] == {"\\Seen", "\\Flagged"}
    assert (b["uid"], b["msgid"], b["flags"], b["labels"], body_b) == (10, 16, set(), [], None)


# ------------------------------------------------------------------ collegamento


async def test_link_opens_google_and_the_window_field_and_saves_only_after_google_says_yes(quick):
    out = await quick.apps.use("gmail", "collega")
    assert quick.opened == [I.APP_PASSWORDS] and quick.forms == [1]
    assert "mai a voce" in out and "verifica in due passaggi" in out
    assert "incollare la password per le app" in await quick.apps.use("gmail", "non_lette")
    assert await quick.g.link_app_password("", SHOWN) == "manca l'indirizzo Gmail (per esempio nome@gmail.com)"
    assert "16 lettere" in await quick.g.link_app_password(ME, "la password del mio account")
    assert quick.checks == []  # una password con la forma sbagliata non arriva nemmeno a Google
    assert await quick.g.link_app_password(ME, "zzzz zzzz zzzz zzzz") == (
        "Google non accetta indirizzo e password per le app: controlla indirizzo e password per le app")
    assert not quick.g.linked() and quick.notes == []
    assert await quick.g.link_app_password(ME, SHOWN) == "ok"
    raw = quick.g.token.read_bytes()
    assert PW.encode() not in raw  # mai in chiaro sul disco
    assert json.loads(unlock(raw)) == {"mode": "imap", "email": ME, "password": PW}
    assert quick.notes == [f"Gmail è collegata ({ME}) con la password per le app. Dillo in una frase."]
    assert await quick.apps.use("gmail", "stato") == (
        f"Gmail collegata: {ME}, 4 mail in tutto, con la password per le app (senza filtri e risposta automatica)")
    assert await quick.apps.use("gmail", "collega") == "Gmail è già collegata (modo veloce)"


async def test_network_down_while_linking_is_said_and_nothing_is_saved(quick):
    quick.server.down = True
    out = await quick.g.link_app_password(ME, SHOWN)
    assert out.startswith("Gmail non risponde (rete giù)") and not quick.g.linked()


async def test_the_full_mode_still_needs_the_google_project(quick):
    out = await quick.apps.use("gmail", "collega", {"modo": "completo"})
    assert out.startswith("Errore: manca il progetto Google dell'utente") and quick.opened == []
    assert "Errore: modo sconosciuto" in await quick.apps.use("gmail", "collega", {"modo": "lento"})
    guide = await quick.apps.use("gmail", "guida")
    assert "apppasswords" in guide and "16 lettere" in guide and "Pubblica app" in guide


async def test_unlink_forgets_the_password_and_says_where_to_remove_it_at_google(quick):
    await linked(quick)
    ask, out = await confirmed(quick, "scollega", {})
    assert "scollegare Gmail" in ask and I.APP_PASSWORDS in out and not quick.g.linked()


async def test_a_removed_app_password_asks_to_link_again(quick):
    await linked(quick)
    quick.server.password = "un'altra"  # cancellata su myaccount.google.com
    assert "va ricollegata con collega" in await quick.apps.use("gmail", "non_lette")
    assert not quick.g.linked()


async def test_a_busy_server_is_not_a_wrong_password(quick):
    await linked(quick)
    quick.server.login_error = "b'[UNAVAILABLE] Too many simultaneous connections. (Failure)'"
    out = await quick.apps.use("gmail", "non_lette")
    assert out.startswith("Errore: Gmail ha rifiutato la richiesta (400)") and "Too many" in out
    assert quick.g.linked()  # la password resta: Google era solo occupato
    quick.server.login_error = None
    assert "Calcetto" in await quick.apps.use("gmail", "non_lette")


async def test_a_dropped_connection_is_reopened_once(quick):
    await linked(quick)
    await quick.apps.use("gmail", "non_lette")
    quick.server.abort_once = True
    assert "Calcetto" in await quick.apps.use("gmail", "non_lette")
    assert quick.server.connections == 3  # la prova del collegamento, la prima sessione, quella riaperta


# ------------------------------------------------------------------ leggere


async def test_unread_newest_first_then_read_without_marking_it_read(quick):
    await linked(quick)
    out = await quick.apps.use("gmail", "non_lette")
    assert out == ("1. da Luca Bianchi: «Calcetto» (ieri 09:10) [da leggere]\n"
                   "2. da Luca Rossi: «Riunione» (14:32) [da leggere]")
    read = await quick.apps.use("gmail", "leggi", {"mail": "2"})
    assert "la riunione è spostata a giovedì" in read and "Allegati: verbale.pdf (1 KB)" in read
    assert "Cc: giulia@verdi.it" in read
    assert "\\Seen" not in mail_n(quick, 1).flags  # BODY.PEEK: leggere non la segna come letta
    assert "Caffè è pronto" in await quick.apps.use("gmail", "leggi", {"mail": "1"})  # latin-1


async def test_html_mail_and_encoded_subject(quick):
    await linked(quick)
    assert "«Il tuo ordine è stato spedito»" in await quick.apps.use("gmail", "cerca", {"cosa": "from:amazon"})
    read = await quick.apps.use("gmail", "leggi", {"mail": "1"})
    assert "il tuo ordine è in viaggio" in read and "x()" not in read


async def test_search_with_accents(quick):
    await linked(quick)
    assert "Calcetto" in await quick.apps.use("gmail", "cerca", {"cosa": "caffè"})
    raw = [c for c in quick.server.commands if c[0] == "SEARCH" and "X-GM-RAW" in c]
    assert raw[-1][:3] == (("SEARCH", "X-GM-RAW", '"caffè"') if quick.server.utf8_ok else
                           ("SEARCH", "CHARSET", "UTF-8"))


async def test_thread_labels_and_attachments(quick):
    await linked(quick)
    await quick.apps.use("gmail", "cerca", {"cosa": "riunione"})
    conv = await quick.apps.use("gmail", "conversazione", {"mail": "2"})
    lines = conv.splitlines()
    assert len(lines) == 2 and "Luca Rossi: Ciao Andrea" in lines[0] and "Va bene, a giovedì." in lines[1]
    labels = await quick.apps.use("gmail", "etichette")
    assert labels == ("Città (1 da leggere), importanti (1 da leggere), Lavoro, posta in arrivo (2 da leggere), "
                      "spam, speciali")
    await quick.apps.use("gmail", "non_lette")
    saved = await quick.apps.use("gmail", "allegati", {"mail": "2"})
    assert (quick.downloads / "Gmail" / "verbale.pdf").read_bytes() == b"%PDF-finto" and "verbale.pdf" in saved


# ------------------------------------------------------------------ scrivere


async def test_send_reply_and_forward_go_through_smtp_with_the_right_headers(quick):
    await linked(quick)
    ask, out = await confirmed(quick, "invia", {"a": "luca@rossi.it", "oggetto": "Ciao", "testo": "Ci vediamo"})
    assert "inviare una mail a luca@rossi.it" in ask and out == "mail inviata a luca@rossi.it"
    sent = quick.smtp.sent[-1]
    assert sent["From"] == ME and sent["To"] == "luca@rossi.it" and sent["Message-ID"] and sent["Date"]
    await quick.apps.use("gmail", "non_lette")
    _ask, out = await confirmed(quick, "rispondi", {"mail": "2", "testo": "Perfetto"})
    reply = quick.smtp.sent[-1]
    assert out == "risposto a Luca Rossi" and reply["Subject"] == "Re: Riunione"
    assert reply["In-Reply-To"] == "<m1@rossi.it>" and reply.get_body(("plain",)).get_content().startswith("Perfetto")
    _ask, out = await confirmed(quick, "inoltra", {"mail": "2", "a": "giulia@verdi.it"})
    fwd = quick.smtp.sent[-1]
    assert fwd["Subject"] == "Fwd: Riunione" and [p.get_filename() for p in fwd.iter_attachments()] == ["verbale.pdf"]
    assert quick.smtp.logins == [ME] * 3


async def test_a_refused_send_is_said_and_never_retried(quick):
    await linked(quick)
    quick.smtp.fail = smtplib.SMTPRecipientsRefused({"x@y": (550, b"no")})
    _ask, out = await confirmed(quick, "invia", {"a": "x@y.it", "oggetto": "o", "testo": "t"})
    assert out.startswith("Errore: Gmail ha rifiutato la richiesta (502): la mail non è partita")
    assert quick.smtp.logins == [ME]  # una volta sola: un nuovo tentativo potrebbe mandarla due volte


async def test_drafts_saved_listed_and_sent(quick):
    await linked(quick)
    assert await quick.apps.use("gmail", "bozza", {"a": "giulia@verdi.it", "oggetto": "Preventivo",
                                                   "testo": "Ecco"}) == "bozza salvata per giulia@verdi.it"
    draft = quick.server.mails[-1]
    assert message_from_bytes(draft.raw, policy=policy.default)["From"] == ME
    assert await quick.apps.use("gmail", "bozze") == "1. a giulia@verdi.it: «Preventivo»"
    ask, out = await confirmed(quick, "invia_bozza", {"numero": "1"})
    assert "inviare la bozza a giulia@verdi.it «Preventivo»" in ask and out == "bozza inviata"
    assert quick.smtp.sent[-1]["Subject"] == "Preventivo" and draft.where == "trash"
    assert await quick.apps.use("gmail", "bozze") == "nessuna bozza"


# ------------------------------------------------------------------ ordinare


async def test_tidy_actions_change_flags_labels_and_folders(quick):
    await linked(quick)
    m1 = mail_n(quick, 1)
    await quick.apps.use("gmail", "non_lette")
    assert await quick.apps.use("gmail", "segna_letta", {"mail": "2"}) == "segnata come letta"
    assert "\\Seen" in m1.flags
    await quick.apps.use("gmail", "segna_non_letta", {"mail": "2"})
    assert "\\Seen" not in m1.flags
    await quick.apps.use("gmail", "speciale", {"mail": "2"})
    assert "\\Flagged" in m1.flags
    await quick.apps.use("gmail", "togli_speciale", {"mail": "2"})
    assert "\\Flagged" not in m1.flags
    await quick.apps.use("gmail", "archivia", {"mail": "2"})
    assert "\\Inbox" not in m1.labels and m1.where == "all"
    assert await quick.apps.use("gmail", "etichetta", {"mail": "2", "nome": "Progetti"}) == (
        "etichetta Progetti messa")
    assert "Progetti" in m1.labels and ("\\HasNoChildren", "Progetti") in quick.server.folders
    await quick.apps.use("gmail", "etichetta", {"mail": "2", "nome": "progetti", "togli": "sì"})
    assert "Progetti" not in m1.labels
    m2 = mail_n(quick, 2)
    await quick.apps.use("gmail", "etichetta", {"mail": "from:amazon", "nome": "città"})
    assert "Città" in m2.labels
    assert await quick.apps.use("gmail", "spam", {"mail": "from:amazon"}) == "spostata nello spam"
    assert m2.where == "spam"
    assert await quick.apps.use("gmail", "non_spam", {"mail": "in:spam amazon"}) == "rimessa nella posta in arrivo"
    assert m2.where == "all" and "\\Inbox" in m2.labels


async def test_trash_and_restore_are_moves_never_deletions(quick):
    await linked(quick)
    await quick.apps.use("gmail", "non_lette")
    ask, _out = await confirmed(quick, "cestina", {"mail": "1"})
    m4 = mail_n(quick, 4)
    assert "spostare nel cestino la mail di Luca Bianchi «Calcetto»" in ask and m4.where == "trash"
    assert "Calcetto" not in await quick.apps.use("gmail", "non_lette")
    assert await quick.apps.use("gmail", "ripristina", {"mail": "in:trash calcetto"}) == "ripresa dal cestino"
    assert m4.where == "all" and "\\Inbox" in m4.labels
    assert not [c for c in quick.server.commands if "\\Deleted" in str(c) or c[0] == "EXPUNGE"]


async def test_bulk_is_one_store_for_all_the_mails(quick):
    await linked(quick)
    ask, out = await confirmed(quick, "in_blocco", {"cerca": "is:unread in:inbox", "azione": "letta"})
    assert "segnare come lette 2 mail" in ask and out == "fatto su 2 mail"
    assert all("\\Seen" in mail_n(quick, n).flags for n in (1, 4))
    assert len([c for c in quick.server.commands if c[0] == "STORE"]) == 1


async def test_filters_and_auto_reply_say_they_need_the_full_mode(quick):
    await linked(quick)
    for out in (await quick.apps.use("gmail", "filtri"),
                (await confirmed(quick, "risposta_automatica", {"testo": "Sono in ferie"}))[1]):
        assert out == f"Errore: Gmail ha rifiutato la richiesta (400): {I.NO_SETTINGS}"


def test_the_password_never_reaches_the_log_or_the_window(quick, caplog):
    import asyncio
    import logging

    caplog.set_level(logging.DEBUG)
    asyncio.run(quick.g.link_app_password(ME, SHOWN))
    asyncio.run(quick.g.link_app_password(ME, "zzzz zzzz zzzz zzzz"))
    assert PW not in caplog.text and "zzzz" not in caplog.text
    assert all(PW not in n and "zzzz" not in n for n in quick.notes)


def test_b64url_round_trip():
    assert base64.urlsafe_b64decode(I.b64url(b"\xff\xfe\x00ok") + "==") == b"\xff\xfe\x00ok"
