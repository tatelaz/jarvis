"""Segnalazioni agli sviluppatori (jarvis/feedback.py): cosa parte, cosa non parte mai, come parte.

Nomi, numeri e frasi qui sotto sono inventati.
"""

import asyncio
import json
from pathlib import Path

import pytest

from jarvis import feedback as F
from jarvis.apps.base import Apps

ROOT = Path(r"C:\Users\Matteo\Desktop\Progetti di Matteo\Jarvis")
NAMES = ["Matteo", "mrossi"]

LOG = r"""2026-11-23 10:00:00,001 DEBUG   jarvis.audio: blocco da 10 ms
2026-11-23 10:00:01,002 INFO    jarvis.conversation: sentito: domani porto Giulia al mare
2026-11-23 10:00:02,003 INFO    jarvis.engine: motore orecchie pronto su 127.0.0.1:8999 (pid 4242)
2026-11-23 10:00:03,004 WARNING jarvis.conversation: eco ignorata: "ciao Giulia come stai"
2026-11-23 10:00:04,005 ERROR   jarvis.brain: risposta fallita per "scrivi a Giulia che arrivo"
Traceback (most recent call last):
  File "C:\Users\Matteo\Desktop\Progetti di Matteo\Jarvis\jarvis\brain\claude.py", line 120, in reply
    raise BrainUnavailable(f"testo: {prompt}")
jarvis.brain.base.BrainUnavailable: testo: scrivi a Giulia che arrivo, il suo numero è 333 1234567
2026-11-23 10:00:05,006 ERROR   jarvis.health: modello mancante in C:\Users\Matteo\Desktop\Progetti di Matteo\Jarvis\models\x.gguf
Traceback (most recent call last):
  File "C:\Users\Matteo\Desktop\Progetti di Matteo\Jarvis\jarvis\health.py", line 88, in check
    path = model_path(key)
  File "C:\Users\Matteo\AppData\Roaming\uv\python\cpython-3.12.9\Lib\pathlib.py", line 10, in exists
    return os.stat(self)
FileNotFoundError: [WinError 2] manca C:\Users\Matteo\Desktop\Progetti di Matteo\Jarvis\models\x.gguf
2026-11-23 10:00:06,007 WARNING aiohttp.client: richiesta a https://mail.example.com?u=matteo.rossi@example.com
2026-11-23 10:00:07,008 INFO    jarvis.whatsapp: nuovo messaggio da 393331234567@c.us
2026-11-23 10:00:08,009 ERROR   jarvis.supervisor: Jarvis caduto (codice 1): chiave sk-abcdefghijklmnop1234 in mrossi
"""


def lines():
    return F.log_lines(LOG.splitlines(), NAMES)


def test_a_line_loses_names_folders_contacts_numbers_and_sentences():
    s = F.scrub(r'Matteo ha scritto a giulia.b@example.com e al +39 333 123 4567 da C:\Users\mrossi\Desktop: '
                r'"porto il cane dal veterinario" (393331234567@c.us) chiave sk-abcdefghijklmnop1234', NAMES, ROOT)
    assert "Matteo" not in s and "mrossi" not in s and "@example.com" not in s and "333" not in s
    assert "veterinario" not in s and "@c.us" not in s and "abcdefghijklmnop" not in s
    assert "[NOME]" in s and "[EMAIL]" in s and "[NUMERO]" in s and '"[TESTO]"' in s and "[CONTATTO]" in s
    assert r"C:\Users\[UTENTE]\Desktop" in s
    kept = F.scrub("2026-11-23 10:00:02,003 versione 0.1.0 pid 4242 porta 8999 'jarvis.audio' codice=1", NAMES, ROOT)
    assert kept == "2026-11-23 10:00:02,003 versione 0.1.0 pid 4242 porta 8999 'jarvis.audio' codice=1"  # tecnico
    assert F.scrub(str(ROOT / "data" / "logs"), NAMES, ROOT) == r"<jarvis>\data\logs"  # anche il nome delle cartelle


def test_paths_say_where_in_the_code_not_where_on_the_pc():
    assert F.short_path(str(ROOT / "jarvis" / "audio" / "vad.py"), ROOT) == "jarvis/audio/vad.py"
    assert F.short_path(r"C:\Users\Matteo\.venv\Lib\site-packages\aiohttp\client.py", ROOT) == \
        "site-packages/aiohttp/client.py"
    assert F.short_path(r"C:\Users\Matteo\AppData\Roaming\uv\python\cpython\Lib\pathlib.py", ROOT) == \
        "python/Lib/pathlib.py"
    assert F.short_path(r"D:\Foto di famiglia\script.py", ROOT) == "…/script.py"


def test_only_what_helps_and_never_the_conversation(monkeypatch):
    monkeypatch.setattr(F.paths, "ROOT", ROOT)
    out = lines()
    text = "\n".join(out)
    assert "blocco da 10 ms" not in text  # niente DEBUG
    assert "domani porto" not in text and "Giulia" not in text  # le frasi non arrivano mai, né come INFO né negli errori
    assert "motore orecchie pronto su 127.0.0.1:8999 (pid 4242)" in text  # le righe tecniche sì
    assert f"WARNING jarvis.conversation: {F.REMOVED}" in text  # che c'è stato un avviso, e dove
    assert f"jarvis.brain.base.BrainUnavailable: {F.REMOVED}" in text  # il tipo d'errore sì, il suo testo no
    assert 'File "jarvis/brain/claude.py", line 120, in reply' in text  # e il punto del codice
    assert "raise BrainUnavailable" not in text  # dei registri personali nemmeno la riga di codice
    assert "    path = model_path(key)" in text  # dei registri tecnici sì: è il codice di Jarvis
    assert 'File "python/Lib/pathlib.py", line 10, in exists' in text
    assert "FileNotFoundError: [WinError 2] manca <jarvis>" in text and "Progetti" not in text
    assert f"WARNING aiohttp.client: {F.REMOVED}" in text and "matteo.rossi" not in text  # librerie esterne: niente
    assert "393331234567" not in text and "nuovo messaggio" not in text  # INFO di WhatsApp: fuori
    assert "INFO jarvis.conversation" not in text and "INFO jarvis.whatsapp" not in text  # nemmeno la riga vuota
    assert "sk-[REDATTO]" in text and "mrossi" not in text and "Matteo" not in text


def test_the_title_says_what_broke_and_where(monkeypatch):
    monkeypatch.setattr(F.paths, "ROOT", ROOT)
    assert F.where_it_broke(lines()) == "FileNotFoundError in jarvis/health.py:88"
    assert F.where_it_broke(["2026-11-23 10:00:00,000 INFO jarvis.engine: ok"]) == ""


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_a_crash_report_from_the_guardian(tmp_path, monkeypatch):
    monkeypatch.setattr(F.paths, "ROOT", ROOT)
    crash = write(tmp_path / "20261123_100009.txt", "motivo: crash\ncodice d'uscita: 1\nora: Mon Nov 23 10:00:09 "
                  "2026\n\n--- ultime righe del registro ---\n" + LOG)
    cfg = type("C", (), {"persona": type("P", (), {"user_name": "Matteo"})()})()
    r = F.build("caduta", "si è chiuso mentre parlavo con Giulia, scrivetemi a m.r@example.com", cfg=cfg,
                crash_file=crash, machine_lines=["- Jarvis 0.1.0"], diary_lines=["Jarvis cade (3 volte in 30 giorni)"],
                version="0.1.0")
    assert r.kind == "caduta" and r.title == "Caduta: FileNotFoundError in jarvis/health.py:88"
    assert "- motivo: crash" in r.text and "- codice d'uscita: 1" in r.text
    assert "si è chiuso mentre parlavo con Giulia, scrivetemi a [EMAIL]" in r.text  # le sue parole, senza email
    assert "- Jarvis 0.1.0" in r.text and "Jarvis cade (3 volte in 30 giorni)" in r.text
    assert "Matteo" not in r.text and "domani porto" not in r.text
    assert r.payload() == {"tipo": "caduta", "titolo": r.title, "testo": r.text, "versione": "0.1.0"}


def test_a_long_log_keeps_the_newest_lines(tmp_path):
    log = write(tmp_path / "jarvis.log", "\n".join(f"2026-11-23 10:{i // 60:02d}:{i % 60:02d},000 ERROR   "
                                                   f"jarvis.health: errore numero {i:05d}" for i in range(4000)))
    r = F.build("segnalazione", "va lento", log_file=log, machine_lines=[])
    assert len(r.text) <= F.MAX_TEXT and "errore numero 03999" in r.text and "errore numero 00000" not in r.text
    assert r.title == "Segnalazione: va lento" and r.text.rstrip().endswith("```")


def test_the_diary_gives_titles_and_counts_never_the_corrections():
    from jarvis.improve.diary import Proposal

    class Diary:
        def candidates(self, now, limit):
            return [Proposal("strumento:apri:timeout", "lo strumento «apri» non riesce", 4, ["apri giulia.txt"]),
                    Proposal("correzione:risposta", "mi correggi spesso", 5, ["no, intendevo Giulia"])]

    class Broken:
        def candidates(self, now, limit):
            raise RuntimeError("database chiuso")

    assert F.diary_summary(Diary()) == ["lo strumento «apri» non riesce (4 volte in 30 giorni)"]
    assert F.diary_summary(Broken()) == [] and F.diary_summary(None) == []


REPORT = F.Report("segnalazione", "Segnalazione: va lento", "testo", "0.1.0")


async def test_sending_says_the_issue_number_and_every_way_it_can_fail():
    got = []

    async def ok(url, data):
        got.append((url, data))
        return 201, json.dumps({"ok": True, "numero": 12})

    assert await F.send(REPORT, "https://segnalazioni.example.workers.dev/segnala", ok) == "12"
    assert got == [("https://segnalazioni.example.workers.dev/segnala", REPORT.payload())]

    async def refused(url, data):
        return 429, "troppe"

    async def offline(url, data):
        raise OSError("rete assente")

    for post, why in ((refused, "429"), (offline, "non raggiungibile")):
        with pytest.raises(F.SendError, match=why):
            await F.send(REPORT, "https://x.example", post)
    with pytest.raises(F.SendError, match="ancora attive"):
        await F.send(REPORT, "", ok)


async def test_what_could_not_leave_waits_and_leaves_later(tmp_path):
    box = F.Outbox(tmp_path / "segnalazioni")
    box.put(REPORT)
    box.put(F.Report("caduta", "Caduta: x", "t2", "0.1.0"))
    (tmp_path / "segnalazioni" / "rovinata.json").write_text("{non json", encoding="utf-8")

    async def offline(url, data):
        raise OSError("rete assente")

    assert await box.flush("https://x.example", offline) == 0
    assert len(box.waiting()) == 2  # la rovinata non partirà mai: via; le altre aspettano
    sent = []

    async def ok(url, data):
        sent.append(data["titolo"])
        return 200, "{}"

    assert await box.flush("https://x.example", ok) == 2 and box.waiting() == []
    assert sorted(sent) == ["Caduta: x", "Segnalazione: va lento"]


class Turns:
    def __init__(self):
        self.n, self.text = 0, ""

    def say(self, text):
        self.n += 1
        self.text = text


def app_with(made, sent, ready=lambda: None):
    def make(kind, description):
        r = F.Report(kind, f"{kind}: {description}", f"rapporto {len(made)}", "0.1.0")
        made.append(r)
        return r

    async def deliver(report):
        sent.append(report)
        return "segnalazione #7 mandata"

    t = Turns()
    apps = Apps([F.build_app(make, deliver, ready)], turns=lambda: t.n, last_text=lambda: t.text,
                confirm=lambda: False)  # conferme delle azioni delicate spente: per le segnalazioni non vale
    return apps, t


async def test_sending_always_asks_and_sends_exactly_what_was_read():
    made, sent = [], []
    apps, t = app_with(made, sent)
    t.say("segnala che non mi ha capito")
    out = await apps.use("segnalazioni", "manda", {"descrizione": "non mi ha capito"})
    assert out.startswith("DA CONFERMARE") and "«segnalazione: non mi ha capito»" in out and sent == []
    code = out.split("codice ")[1].split(")")[0]
    t.say("sì, mandala")
    assert await apps.use("segnalazioni", "manda", {"descrizione": "altro"}, code) == "segnalazione #7 mandata"
    assert sent == [made[0]] and len(made) == 1  # quello letto all'utente, non uno rifatto dopo


async def test_a_no_sends_nothing_and_the_preview_never_sends():
    made, sent = [], []
    apps, t = app_with(made, sent)
    t.say("segnala una caduta")
    code = (await apps.use("segnalazioni", "manda", {"tipo": "caduta"})).split("codice ")[1].split(")")[0]
    t.say("no, lascia stare")
    assert "non è un sì chiaro" in await apps.use("segnalazioni", "manda", {"tipo": "caduta"}, code)
    assert await apps.use("segnalazioni", "anteprima", {"tipo": "caduta"}) == "rapporto 1"
    assert sent == []
    assert "tipo va scritto" in await apps.use("segnalazioni", "manda", {"tipo": "lamentela"})


async def test_not_active_yet_is_said():
    apps, _ = app_with([], [], ready=lambda: "le segnalazioni non sono ancora attive in questa versione")
    assert "non sono ancora attive" in await apps.use("segnalazioni", "manda", {})


def test_only_the_owner_sends_reports():
    from jarvis.identity.guard import OWNER_ONLY

    app = F.build_app(lambda k, d: REPORT, lambda r: asyncio.sleep(0), lambda: None)
    assert app.group in OWNER_ONLY  # i rapporti hanno il registro: un ospite non li manda


# ---------------------------------------------------------------------- Jarvis: dopo una caduta e quando manda


def jarvis_like(tmp_path, mode="chiedi", url="https://segnalazioni.example.workers.dev/segnala"):
    from types import SimpleNamespace

    from jarvis.config import Config

    cfg = Config()
    cfg.system.crash_reports, cfg.system.feedback_url = mode, url
    bg = []

    async def send_crash():
        pass

    return SimpleNamespace(store=SimpleNamespace(config=cfg), diary=None, _send_crash_report=send_crash,
                           _background=lambda c: (bg.append(c), c.close())), bg


@pytest.mark.parametrize("mode", ["chiedi", "sempre", "mai"])
def test_after_a_crash_ask_send_or_nothing(tmp_path, monkeypatch, mode):
    from jarvis.app import Jarvis

    monkeypatch.setattr(F, "latest_crash", lambda logs=None: tmp_path / "20261123_100009.txt")
    j, bg = jarvis_like(tmp_path, mode)
    said = Jarvis.after_crash(j)
    if mode == "chiedi":
        assert "app segnalazioni, azione manda e tipo caduta" in said and "solo con un sì" in said and bg == []
    elif mode == "sempre":
        assert "parte da solo" in said and len(bg) == 1  # mandato in sottofondo, e lo si dice
    else:
        assert said == "" and bg == []


def test_after_a_crash_nothing_when_not_active_or_no_report(tmp_path, monkeypatch):
    from jarvis.app import Jarvis

    monkeypatch.setattr(F, "latest_crash", lambda logs=None: tmp_path / "r.txt")
    j, bg = jarvis_like(tmp_path, "sempre", url="")
    assert Jarvis.after_crash(j) == "" and bg == []
    monkeypatch.setattr(F, "latest_crash", lambda logs=None: None)
    j, bg = jarvis_like(tmp_path, "sempre")
    assert Jarvis.after_crash(j) == "" and bg == []


async def test_delivering_says_the_number_or_keeps_it_for_later(tmp_path, monkeypatch):
    from jarvis.app import Jarvis

    box = F.Outbox(tmp_path / "segnalazioni")
    monkeypatch.setattr(F, "Outbox", lambda: box)

    async def ok(report, url, post=None):
        return "12"

    async def offline(report, url, post=None):
        raise F.SendError("server non raggiungibile (OSError)")

    j, _ = jarvis_like(tmp_path)
    monkeypatch.setattr(F, "send", ok)
    assert await Jarvis.deliver_report(j, REPORT) == ("Segnalazione mandata agli sviluppatori (numero 12): la "
                                                     "guarderanno per correggere il problema.")
    monkeypatch.setattr(F, "send", offline)
    said = await Jarvis.deliver_report(j, REPORT)
    assert "riprovo al prossimo avvio" in said and len(box.waiting()) == 1
    j, _ = jarvis_like(tmp_path, url="")
    assert (await Jarvis.deliver_report(j, REPORT)).startswith("Non l'ho mandata") and len(box.waiting()) == 1


def test_the_crash_setting_can_be_changed_by_voice():
    from jarvis.config import is_live
    from jarvis.tools.front import SETTABLE

    key, convert = SETTABLE["rapporti_cadute"]
    assert key == "system.crash_reports" and is_live(key)
    assert [convert(v) for v in ("chiedimi", "da solo", "mai", "Sempre")] == ["chiedi", "sempre", "mai", "sempre"]
    with pytest.raises(ValueError):
        convert("a volte")


async def test_the_window_sends_only_what_was_shown_and_only_once():
    from types import SimpleNamespace

    from jarvis.core.bus import Bus
    from jarvis.ui.server import UiServer

    made, sent = [], []

    def make(kind, text):
        made.append(F.Report(kind, f"{kind}: {text}", f"testo {len(made)}", "0.1.0"))
        return made[-1]

    async def deliver(report):
        sent.append(report)
        return "Segnalazione mandata agli sviluppatori (numero 3): la guarderanno per correggere il problema."

    bus = Bus()
    q = bus.subscribe(50)
    srv = UiServer(SimpleNamespace(bus=bus, conv=None, _reports_ready=lambda: None, make_report=make,
                                   deliver_report=deliver), port=0)
    await srv._command({"cmd": "report_send"})  # senza aver guardato: non parte niente
    await srv._command({"cmd": "report_preview", "text": "si blocca", "crash": True})
    await srv._command({"cmd": "report_send"})
    await srv._command({"cmd": "report_send"})  # una volta sola: per rimandarla la si rivede
    notices = [q.get_nowait() for _ in range(q.qsize())]
    assert [(n["kind"], n["info"]["ok"]) for n in notices] == [("report", False), ("report_preview", True),
                                                                 ("report", True), ("report", False)]
    assert notices[1]["info"]["text"] == "testo 0" and sent == [made[0]] and made[0].kind == "caduta"
    srv.app._reports_ready = lambda: "le segnalazioni non sono ancora attive in questa versione"
    await srv._command({"cmd": "report_preview", "text": "x", "crash": False})
    assert q.get_nowait()["info"] == {"ok": False, "message": "Le segnalazioni non sono ancora attive in questa versione."}
    assert len(made) == 1  # senza server niente rapporto
