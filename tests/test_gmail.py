"""Gmail (jarvis/apps/gmail.py): collegamento, permesso cifrato, ogni azione contro un Gmail finto che risponde come
l'API REST vera. Niente parte davvero: le mail "inviate" si decodificano e si controllano qui.
"""

import asyncio
import base64
import email
import json
import time
from email import policy

import pytest
from google.auth.exceptions import RefreshError

from jarvis.apps import gmail as G
from jarvis.apps.base import Apps

NOW = time.mktime((2026, 10, 27, 15, 0, 0, 0, 0, -1))
TODAY = "Tue, 27 Oct 2026 14:32:00 +0100"
YESTERDAY = "Mon, 26 Oct 2026 09:10:00 +0100"


def hm(header: str) -> str:
    """L'ora come la mostra Jarvis: nel fuso del PC (qui l'ora italiana, su GitHub UTC: lì "14:32" diventava 13:32)."""
    from email.utils import parsedate_to_datetime

    return parsedate_to_datetime(header).astimezone().strftime("%H:%M")


H_TODAY, H_YESTERDAY = hm(TODAY), hm(YESTERDAY)
ME = "andrea@gmail.com"


def b64(text: str | bytes, enc="utf-8") -> str:
    raw = text.encode(enc) if isinstance(text, str) else text
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def headers(**h):
    return [{"name": k.rstrip("_").replace("_", "-").title() if k != "message_id" else "Message-ID", "value": v}
            for k, v in h.items()]


def plain(text, enc="utf-8"):
    return {"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": f"text/plain; charset={enc}"}],
            "body": {"data": b64(text, enc)}}


MAILS = {
    "a1": {"threadId": "t1", "labelIds": ["INBOX", "UNREAD"], "snippet": "La riunione è spostata",
           "payload": {"mimeType": "multipart/mixed", "headers": headers(
               from_='"Luca Rossi" <luca@rossi.it>', to=ME, cc="giulia@verdi.it", subject="Riunione",
               date=TODAY, message_id="<m1@rossi.it>"),
               "parts": [plain("Ciao Andrea,\nla riunione è spostata a giovedì.\nLuca"),
                         {"mimeType": "application/pdf", "filename": "verbale.pdf",
                          "body": {"attachmentId": "att1", "size": 250_000}}]}},
    "a2": {"threadId": "t2", "labelIds": ["INBOX"], "snippet": "Il tuo ordine",
           "payload": {"mimeType": "text/html", "headers": headers(
               from_="Amazon <spedizioni@amazon.it>", to=ME, subject="Il tuo ordine è stato spedito", date=YESTERDAY,
               message_id="<m2@amazon.it>"),
               "body": {"data": b64("<html><head><style>p{}</style></head><body><p>Ciao&nbsp;Andrea,</p>"
                                    "<p>il tuo ordine &egrave; <b>in viaggio</b>.<br>Arriva domani.</p>"
                                    "<script>x()</script></body></html>")}}},
    "a3": {"threadId": "t1", "labelIds": ["SENT"], "snippet": "ok",
           "payload": {"mimeType": "text/plain", "headers": headers(
               from_=ME, to="Luca Rossi <luca@rossi.it>", subject="Re: Riunione", date=TODAY,
               message_id="<m3@gmail.com>"), "body": {"data": b64("Va bene, a giovedì.")}}},
    "a4": {"threadId": "t4", "labelIds": ["INBOX", "UNREAD"], "snippet": "vecchio",
           "payload": {"mimeType": "text/plain", "body": {"data": b64("Caffè è pronto", "latin-1")}, "headers": [
               *headers(from_="Luca Bianchi <lollo@bianchi.com>", to=ME, subject="Calcetto", date=YESTERDAY),
               {"name": "Content-Type", "value": "text/plain; charset=ISO-8859-1"}]}},
}


class Response:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = "" if data is None else json.dumps(data)

    def json(self):
        return self._data


class FakeGmail:
    """L'API REST di Gmail in memoria, con le query di questi test."""

    def __init__(self):
        self.mails = json.loads(json.dumps(MAILS))
        self.sent: list[tuple[email.message.EmailMessage, str | None]] = []
        self.calls: list[tuple[str, str, dict | None]] = []
        self.labels = [{"id": "INBOX", "name": "INBOX", "type": "system"}, {"id": "UNREAD", "name": "UNREAD"},
                       {"id": "STARRED", "name": "STARRED"}, {"id": "Label_1", "name": "Lavoro"}]
        self.drafts = {"d1": {"to": "giulia@verdi.it", "subject": "Preventivo"}}
        self.filters = [{"id": "f1", "criteria": {"from": "amazon"}, "action": {"addLabelIds": ["Label_1"],
                                                                                "removeLabelIds": ["INBOX"]}}]
        self.queries = {"is:unread in:inbox": ["a1", "a4"], "in:inbox": ["a1"], "from:amazon": ["a2"],
                        "from:(luca) OR to:(luca) OR cc:(luca)": ["a1", "a3", "a4"],
                        "from:(giulia) OR to:(giulia) OR cc:(giulia)": ["a1"], "riunione": ["a1", "a3"]}
        self.fail: tuple[int, str] | None = None
        self.raise_: Exception | None = None

    def request(self, method, url, params=None, json=None, timeout=None):
        if self.raise_:
            raise self.raise_
        if self.fail:
            return Response(self.fail[0], {"error": {"message": self.fail[1]}})
        path = url.removeprefix(G.API)
        self.calls.append((method, path, json))
        if url == G.REVOKE:
            return Response(200, None)
        parts = path.strip("/").split("/")
        if path == "/profile":
            return Response(200, {"emailAddress": ME, "messagesTotal": 1234})
        if path == "/messages" and method == "GET":
            ids = self.queries.get(params["q"], [])[: params.get("maxResults", 100)]
            return Response(200, {"messages": [{"id": i, "threadId": self.mails[i]["threadId"]} for i in ids]})
        if parts[0] == "messages" and len(parts) == 2 and method == "GET":
            return Response(200, {"id": parts[1], **self.mails[parts[1]]})
        if parts[0] == "messages" and len(parts) == 4 and parts[2] == "attachments":
            return Response(200, {"data": b64(b"%PDF-finto"), "size": 10})
        if path == "/messages/send":
            msg = email.message_from_bytes(base64.urlsafe_b64decode(json["raw"]), policy=policy.default)
            self.sent.append((msg, json.get("threadId")))
            return Response(200, {"id": "nuovo"})
        if parts[0] == "messages" and len(parts) == 3 and parts[2] == "modify":
            labels = self.mails[parts[1]]["labelIds"]
            labels[:] = [x for x in labels if x not in json["removeLabelIds"]] + json["addLabelIds"]
            return Response(200, {})
        if parts[0] == "messages" and len(parts) == 3 and parts[2] in ("trash", "untrash"):
            return Response(200, {})
        if path == "/messages/batchModify":
            return Response(204, None)
        if path == "/threads/t1":
            return Response(200, {"messages": [{"id": i, **self.mails[i]} for i in ("a1", "a3")]})
        if path == "/labels" and method == "GET":
            return Response(200, {"labels": self.labels})
        if path == "/labels" and method == "POST":
            self.labels.append({"id": f"Label_{len(self.labels)}", "name": json["name"]})
            return Response(200, self.labels[-1])
        if parts[0] == "labels":
            return Response(200, {"messagesUnread": 3 if parts[1] in ("INBOX", "Label_1") else 0})
        if path == "/drafts" and method == "GET":
            return Response(200, {"drafts": [{"id": d} for d in self.drafts]})
        if path == "/drafts" and method == "POST":
            msg = email.message_from_bytes(base64.urlsafe_b64decode(json["message"]["raw"]), policy=policy.default)
            self.drafts[f"d{len(self.drafts) + 1}"] = {"to": msg["To"], "subject": msg["Subject"]}
            return Response(200, {})
        if parts[0] == "drafts" and len(parts) == 2 and parts[1] != "send":
            d = self.drafts[parts[1]]
            return Response(200, {"message": {"payload": {"headers": headers(to=d["to"], subject=d["subject"])}}})
        if path == "/drafts/send":
            return Response(200, {})
        if path == "/settings/vacation":
            return Response(200, {})
        if path == "/settings/filters" and method == "GET":
            return Response(200, {"filter": self.filters})
        if path.startswith("/settings/filters"):
            return Response(200, {} if method == "POST" else None)
        raise AssertionError(f"chiamata inattesa: {method} {path}")


def lock(data: bytes) -> bytes:  # al posto di DPAPI: reversibile ma mai in chiaro
    return b"CIFRATO" + data[::-1]


def unlock(blob: bytes) -> bytes:
    assert blob.startswith(b"CIFRATO")
    return blob[7:][::-1]


@pytest.fixture
def gm(tmp_path):
    api = FakeGmail()
    notes, consents = [], []

    def consent(client, scopes):
        consents.append((client, scopes))
        return {"refresh_token": "rt-segreto", "client_id": "x", "client_secret": "y", "token": "at"}

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    opened = []
    g = G.Gmail(tmp_path / "gmail", downloads, notify=notes.append, consent_flow=consent,
                session_factory=lambda info: api, protect_fn=lock, unprotect_fn=unlock,
                open_url=opened.append)  # mai il browser vero
    turns = {"n": 0, "text": ""}
    apps = Apps([G.build(g, now=lambda: NOW)], turns=lambda: turns["n"], last_text=lambda: turns["text"])

    class Ctx:
        pass

    c = Ctx()
    c.api, c.g, c.apps, c.notes, c.consents, c.downloads, c.tmp = api, g, apps, notes, consents, downloads, tmp_path
    c.opened = opened

    def say(text):
        turns["n"] += 1
        turns["text"] = text

    c.say = say
    return c


def link(c):
    c.g.dir.mkdir(parents=True, exist_ok=True)
    c.g.token.write_bytes(lock(json.dumps({"refresh_token": "rt"}).encode()))


def code_of(out):
    assert out.startswith("DA CONFERMARE (codice "), out
    return out.split("codice ")[1].split(")")[0]


async def confirmed(c, action, params):
    ask = await c.apps.use("gmail", action, params)
    c.say("sì, mandala")
    return ask, await c.apps.use("gmail", action, {}, code_of(ask))


# ------------------------------------------------------------------ collegamento


async def test_without_the_google_project_it_takes_the_quick_way(gm):
    out = await gm.apps.use("gmail", "collega")
    assert out.startswith("Ho aperto la pagina di Google delle password per le app") and gm.opened == [
        "https://myaccount.google.com/apppasswords"]
    out = await gm.apps.use("gmail", "collega", {"modo": "completo"})
    assert out.startswith("Errore: manca il progetto Google dell'utente") and "azione guida" in out
    guide = await gm.apps.use("gmail", "guida")
    assert "Pubblica app" in guide and "7 giorni" in guide and "App desktop" in guide
    gm.g.waiting_password = False
    assert await gm.apps.use("gmail", "leggi", {"mail": "1"}) == (
        "Errore: Gmail non è pronta: non è collegata: usa l'azione collega (una volta sola)")


async def test_a_web_client_is_refused_a_desktop_one_is_found_in_downloads(gm):
    web = gm.downloads / "client_secret_123-web.apps.googleusercontent.com.json"
    web.write_text(json.dumps({"web": {"client_id": "w"}}), encoding="utf-8")
    assert "non è di tipo «App desktop»" in await gm.apps.use("gmail", "collega")
    desk = gm.downloads / "client_secret_456-desk.apps.googleusercontent.com.json"
    desk.write_text(json.dumps({"installed": {"client_id": "d"}}), encoding="utf-8")
    out = await gm.apps.use("gmail", "collega")
    assert out.startswith("Si è aperto il browser con la pagina di Google")
    await asyncio.wait_for(gm.g.linking, 5)
    assert json.loads(gm.g.client.read_text(encoding="utf-8")) == {"installed": {"client_id": "d"}}
    assert gm.consents == [(gm.g.client, G.SCOPES)]
    raw = gm.g.token.read_bytes()
    assert b"rt-segreto" not in raw and json.loads(unlock(raw))["refresh_token"] == "rt-segreto"  # mai in chiaro
    assert gm.notes == [f"Gmail è collegata ({ME}). Dillo in una frase."]
    assert await gm.apps.use("gmail", "stato") == f"Gmail collegata: {ME}, 1234 mail in tutto"
    assert await gm.apps.use("gmail", "collega") == "Gmail è già collegata (modo completo)"
    assert gm.opened == []  # con il progetto Google il modo veloce non si apre


@pytest.mark.parametrize("result", [RuntimeError("consenso negato"), {"token": "solo-accesso"}])
async def test_a_failed_consent_is_reported(gm, result):
    gm.g.dir.mkdir(parents=True)
    gm.g.client.write_text(json.dumps({"installed": {}}), encoding="utf-8")

    def consent(client, scopes):
        if isinstance(result, Exception):
            raise result
        return result

    gm.g.consent_flow = consent
    await gm.apps.use("gmail", "collega")
    await asyncio.wait_for(gm.g.linking, 5)
    assert gm.notes[0].startswith("Il collegamento di Gmail non è riuscito") and not gm.g.linked()


async def test_unlink_revokes_and_forgets(gm):
    link(gm)
    ask, out = await confirmed(gm, "scollega", {})
    assert "scollegare Gmail" in ask and out.startswith("Gmail scollegata")
    assert ("POST", G.REVOKE, None) in gm.api.calls and not gm.g.linked()


async def test_a_revoked_permission_asks_to_link_again_and_errors_are_clear(gm):
    link(gm)
    gm.api.fail = (403, "Gmail API has not been used in project 123")
    assert await gm.apps.use("gmail", "non_lette") == (
        "Errore: Gmail ha rifiutato la richiesta (403): Gmail API has not been used in project 123")
    gm.api.fail, gm.api.raise_ = None, RefreshError("invalid_grant")
    assert "va ricollegata con collega" in await gm.apps.use("gmail", "non_lette")
    assert not gm.g.linked()


async def test_mail_is_only_for_the_owner(gm):
    link(gm)
    gm.apps.may = lambda group: "Errore: permesso negato." if group == "messaggi" else None
    assert await gm.apps.use("gmail", "non_lette") == "Errore: permesso negato." and gm.api.calls == []


# ------------------------------------------------------------------ leggere


async def test_unread_list_then_read_by_number(gm):
    link(gm)
    assert await gm.apps.use("gmail", "non_lette") == (
        f"1. da Luca Rossi: «Riunione» ({H_TODAY}) [da leggere]\n"
        f"2. da Luca Bianchi: «Calcetto» (ieri {H_YESTERDAY}) [da leggere]")
    out = await gm.apps.use("gmail", "leggi", {"mail": "1"})
    assert out.startswith('Da: "Luca Rossi" <luca@rossi.it>\nA: andrea@gmail.com\nCc: giulia@verdi.it\n'
                          f"Data: {H_TODAY}\nOggetto: Riunione\nAllegati: verbale.pdf (244 KB)\n\nCiao Andrea,")
    assert "spostata a giovedì" in out
    assert "Caffè è pronto" in await gm.apps.use("gmail", "leggi", {"mail": "2"})  # charset latin-1
    assert not any(p.endswith("/modify") for _m, p, _b in gm.api.calls)  # leggere non segna come letta
    assert await gm.apps.use("gmail", "cerca", {"cosa": "from:amazon"}) == (
        f"1. da Amazon: «Il tuo ordine è stato spedito» (ieri {H_YESTERDAY})")
    html = await gm.apps.use("gmail", "leggi", {"mail": "1"})  # "1" ora è la mail di Amazon (ultimo elenco)
    assert html.endswith("Ciao Andrea,\n\nil tuo ordine è in viaggio.\nArriva domani.")
    assert await gm.apps.use("gmail", "cerca", {"cosa": "zzz"}) == "nessuna mail per «zzz»"
    assert "non trovo la mail «zzz»" in await gm.apps.use("gmail", "leggi", {"mail": "zzz"})


async def test_conversation_labels_attachments(gm):
    link(gm)
    conv = await gm.apps.use("gmail", "conversazione", {"mail": "riunione"})
    assert conv == (f"[{H_TODAY}] Luca Rossi: Ciao Andrea, la riunione è spostata a giovedì. Luca\n"
                    f"[{H_TODAY}] {ME}: Va bene, a giovedì.")
    assert await gm.apps.use("gmail", "etichette") == "Lavoro (3 da leggere), posta in arrivo (3 da leggere), speciali"
    out = await gm.apps.use("gmail", "allegati", {"mail": "riunione"})
    saved = gm.downloads / "Gmail" / "verbale.pdf"
    assert out == f"salvati: {saved}" and saved.read_bytes() == b"%PDF-finto"
    await gm.apps.use("gmail", "allegati", {"mail": "riunione"})
    assert (gm.downloads / "Gmail" / "verbale (2).pdf").exists()  # mai sovrascrivere


# ------------------------------------------------------------------ scrivere: sempre con conferma


async def test_send_finds_the_address_by_name_and_asks_first(gm):
    link(gm)
    f = gm.tmp / "preventivo.pdf"
    f.write_bytes(b"pdf")
    ask, out = await confirmed(gm, "invia", {"a": "giulia", "oggetto": "Preventivo", "testo": "Ecco il preventivo.",
                                             "allegati": str(f)})
    assert ask.startswith("DA CONFERMARE") and "inviare una mail a giulia@verdi.it, oggetto «Preventivo», testo: " \
        "«Ecco il preventivo.», allegati: preventivo.pdf" in ask
    assert out == "mail inviata a giulia@verdi.it"
    msg, thread = gm.api.sent[-1]
    assert (msg["To"], msg["Subject"], thread) == ("giulia@verdi.it", "Preventivo", None)
    att = [p for p in msg.iter_attachments()]
    assert [a.get_filename() for a in att] == ["preventivo.pdf"] and att[0].get_content() == b"pdf"
    assert "più indirizzi corrispondono a 'luca'" in await gm.apps.use(
        "gmail", "invia", {"a": "luca", "oggetto": "x", "testo": "y"})
    assert "l'allegato" in await gm.apps.use("gmail", "invia", {"a": "a@b.it", "oggetto": "x", "testo": "y",
                                                                 "allegati": "C:/manca.pdf"})
    assert len(gm.api.sent) == 1


async def test_reply_and_reply_all_stay_in_the_thread(gm):
    link(gm)
    await gm.apps.use("gmail", "non_lette")
    ask, out = await confirmed(gm, "rispondi", {"mail": "1", "testo": "Perfetto, ci sarò."})
    assert "rispondere a \"Luca Rossi\" <luca@rossi.it> («Re: Riunione»): «Perfetto, ci sarò.»" in ask
    assert out == "risposto a Luca Rossi"
    msg, thread = gm.api.sent[-1]
    assert thread == "t1" and msg["Subject"] == "Re: Riunione" and msg["In-Reply-To"] == "<m1@rossi.it>"
    body = msg.get_body().get_content()
    assert body.startswith(f"Perfetto, ci sarò.\n\nIl giorno {H_TODAY}") and "> la riunione è spostata" in body
    assert msg["Cc"] is None
    ask, _ = await confirmed(gm, "rispondi", {"mail": "riunione", "testo": "ok", "tutti": "sì"})
    assert "e in copia giulia@verdi.it" in ask and ME not in ask.split("copia")[1]
    assert gm.api.sent[-1][0]["Cc"] == "giulia@verdi.it"


async def test_forward_keeps_the_attachments(gm):
    link(gm)
    ask, out = await confirmed(gm, "inoltra", {"mail": "riunione", "a": "giulia", "testo": "Guarda qui"})
    assert out == "inoltrata a giulia@verdi.it"
    assert "inoltrare la mail di Luca Rossi «Riunione» a giulia@verdi.it con il messaggio «Guarda qui»" in ask
    msg, _ = gm.api.sent[-1]
    assert msg["Subject"] == "Fwd: Riunione" and msg["To"] == "giulia@verdi.it"
    assert "---------- Messaggio inoltrato ----------" in msg.get_body().get_content()
    assert [a.get_filename() for a in msg.iter_attachments()] == ["verbale.pdf"]


async def test_drafts(gm):
    link(gm)
    assert await gm.apps.use("gmail", "bozza", {"a": "a@b.it", "oggetto": "Idea", "testo": "..."}) == (
        "bozza salvata per a@b.it")  # una bozza non parte: niente conferma
    assert await gm.apps.use("gmail", "bozze") == "1. a giulia@verdi.it: «Preventivo»\n2. a a@b.it: «Idea»"
    ask, out = await confirmed(gm, "invia_bozza", {"numero": "1"})
    assert "inviare la bozza a giulia@verdi.it «Preventivo»" in ask and out == "bozza inviata"
    assert ("POST", "/drafts/send", {"id": "d1"}) in gm.api.calls
    assert "numero di bozza sconosciuto" in await gm.apps.use("gmail", "invia_bozza", {"numero": "9"})


# ------------------------------------------------------------------ ordinare, cestino, regole


async def test_tidy_actions(gm):
    link(gm)
    await gm.apps.use("gmail", "non_lette")
    for action, add, remove in (("archivia", [], ["INBOX"]), ("segna_letta", [], ["UNREAD"]),
                                ("speciale", ["STARRED"], []), ("spam", ["SPAM"], ["INBOX"])):
        await gm.apps.use("gmail", action, {"mail": "1"})
        assert gm.api.calls[-1] == ("POST", "/messages/a1/modify", {"addLabelIds": add, "removeLabelIds": remove})
    assert await gm.apps.use("gmail", "etichetta", {"mail": "1", "nome": "lavoro"}) == "etichetta lavoro messa"
    assert gm.api.calls[-1][2] == {"addLabelIds": ["Label_1"], "removeLabelIds": []}
    await gm.apps.use("gmail", "etichetta", {"mail": "1", "nome": "Fatture 2026"})  # non c'è: si crea
    assert any(p == "/labels" and m == "POST" for m, p, _ in gm.api.calls)
    await gm.apps.use("gmail", "etichetta", {"mail": "1", "nome": "importanti", "togli": "sì"})
    assert gm.api.calls[-1][2] == {"addLabelIds": [], "removeLabelIds": ["IMPORTANT"]}
    ask, out = await confirmed(gm, "cestina", {"mail": "1"})
    assert "spostare nel cestino la mail di Luca Rossi «Riunione»" in ask and out == "spostata nel cestino"
    assert ("POST", "/messages/a1/trash", {}) in gm.api.calls
    assert await gm.apps.use("gmail", "ripristina", {"mail": "1"}) == "ripresa dal cestino"
    assert not any("delete" in p.lower() for _m, p, _b in gm.api.calls)  # mai cancellazioni definitive


async def test_bulk_asks_with_the_count(gm):
    link(gm)
    ask, out = await confirmed(gm, "in_blocco", {"cerca": "is:unread in:inbox", "azione": "letta"})
    assert "segnare come lette 2 mail (ricerca «is:unread in:inbox»)" in ask and out == "fatto su 2 mail"
    assert gm.api.calls[-1] == ("POST", "/messages/batchModify", {"ids": ["a1", "a4"], "addLabelIds": [],
                                                                  "removeLabelIds": ["UNREAD"]})
    ask, out = await confirmed(gm, "in_blocco", {"cerca": "is:unread in:inbox", "azione": "cestina"})
    assert [p for m, p, _ in gm.api.calls[-2:]] == ["/messages/a1/trash", "/messages/a4/trash"]
    assert "azione in blocco sconosciuta" in await gm.apps.use("gmail", "in_blocco", {"cerca": "x", "azione": "brucia"})
    assert "nessuna mail per «zzz»" in await gm.apps.use("gmail", "in_blocco", {"cerca": "zzz", "azione": "letta"})


async def test_auto_reply_and_filters(gm):
    link(gm)
    ask, out = await confirmed(gm, "risposta_automatica", {"testo": "Sono in ferie", "fino_al": "31/10/2026"})
    assert "attivare la risposta automatica fino al 31/10/2026, a chiunque scriva: «Sono in ferie»" in ask
    body = gm.api.calls[-1][2]
    assert body["enableAutoReply"] is True and body["responseBodyPlainText"] == "Sono in ferie"
    assert body["endTime"] == int(time.mktime((2026, 10, 31, 23, 59, 59, 0, 0, -1)) * 1000)
    assert await gm.apps.use("gmail", "togli_risposta_automatica") == "risposta automatica spenta"
    assert await gm.apps.use("gmail", "filtri") == "1. se from amazon: etichetta Lavoro, togli posta in arrivo"
    ask, out = await confirmed(gm, "crea_filtro", {"da": "newsletter@x.it", "archivia": "sì", "letta": "sì"})
    assert "per le mail con da «newsletter@x.it» → salta la posta in arrivo, segna letta (vale anche in futuro)" in ask
    assert gm.api.calls[-1][2] == {"criteria": {"from": "newsletter@x.it"},
                                   "action": {"addLabelIds": [], "removeLabelIds": ["INBOX", "UNREAD"]}}
    assert "serve almeno una condizione" in await gm.apps.use("gmail", "crea_filtro", {"etichetta": "x"})
    ask, out = await confirmed(gm, "elimina_filtro", {"numero": "1"})
    assert out == "regola eliminata" and gm.api.calls[-1][:2] == ("DELETE", "/settings/filters/f1")


def test_text_helpers():
    assert G.html_text("<p>a&amp;b</p><div>c</div><style>x</style>") == "a&b\n\nc"
    assert G.who('"Mario Rossi" <m@r.it>') == "Mario Rossi" and G.who("m@r.it") == "m@r.it"
    assert G.when("Sat, 24 Oct 2026 10:00:00 +0200", NOW) == "24/10/2026" and G.when("boh", NOW) == ""
    assert G.size_text(250_000) == "244 KB" and G.size_text(3_000_000) == "2,9 MB"
