"""WhatsApp nascosto: un WhatsApp Web invisibile (Chrome senza finestra) collegato al telefono come dispositivo, guidato
dal ponte Node jarvis/apps/whatsapp_bridge (whatsapp-web.js) a righe JSON su stdin/stdout.

- Tutto in background: leggere, cercare, inviare testi e file, rispondere citando, reagire, inoltrare, modificare e
  cancellare i propri messaggi, archiviare, silenziare, fissare, gruppi, contatti, stato, scaricare gli allegati e
  trascrivere i vocali con le orecchie di Jarvis. Le chiamate restano all'app di WhatsApp (apri_app).
- Collegamento: la prima volta si apre una finestra di Chrome (o di Edge) con il QR ufficiale di WhatsApp Web (cambia ogni ~20
  secondi, un'immagine fissa non basterebbe); l'utente lo inquadra col telefono e da lì la sessione resta salvata in
  data/whatsapp e il ponte riparte invisibile.
- Si accende quando serve e si spegne dopo 15 minuti senza usarlo (~300-400 MB di RAM mentre è acceso); con
  apps.whatsapp = "sempre" resta acceso e può avvisare dei messaggi nuovi (solo chi scrive, mai il testo ad alta voce).
- Inviare e ogni cosa che altri vedono passa dalla conferma di jarvis/apps/base.py; i messaggi sono solo dell'utente.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .. import paths
from .base import App, AppError, norm, pick_by_name

log = logging.getLogger("jarvis.whatsapp")

BRIDGE_DIR = Path(__file__).with_name("whatsapp_bridge")
CREATE_NO_WINDOW = 0x08000000
# Chrome se c'è, altrimenti Edge (c'è su ogni Windows 11): WhatsApp Web gira in tutti e due
CHROME_PATHS = [*(Path(os.environ.get(v, "")) / "Google" / "Chrome" / "Application" / "chrome.exe"
                  for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")),
                *(Path(os.environ.get(v, "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
                  for v in ("PROGRAMFILES(X86)", "PROGRAMFILES"))]
NPM_TIMEOUT_S = 900.0  # npm ci del ponte: di solito un minuto o due
MODES = ("su richiesta", "sempre", "spento")

# i tipi di messaggio di WhatsApp Web, detti a voce; None = notifiche di servizio da non leggere
KINDS: dict[str, str | None] = {
    "chat": "", "image": "foto", "video": "video", "ptt": "vocale", "audio": "audio", "document": "documento",
    "sticker": "sticker", "location": "posizione", "vcard": "contatto", "multi_vcard": "contatti",
    "revoked": "messaggio eliminato", "poll_creation": "sondaggio", "call_log": "chiamata", "ciphertext":
    "messaggio non ancora arrivato del tutto", "e2e_notification": None, "notification_template": None,
    "gp2": None, "protocol": None, "notification": None, "broadcast_notification": None,
}
ME = {"me", "me stesso", "me stessa", "a me", "io", "tu", "te stesso", "messaggi a te stesso", "note"}


def find_chrome() -> Path | None:
    return next((p for p in CHROME_PATHS if p.is_file()), None)


def user_agent(chrome: Path | None) -> str | None:
    """L'User-Agent del Chrome vero (la libreria ne usa uno vecchio da Mac): la versione è il nome della cartella."""
    if chrome is None:
        return None
    versions = [d.name for d in chrome.parent.iterdir() if d.is_dir() and re.fullmatch(r"\d+(\.\d+){3}", d.name)]
    if not versions:
        return None
    major = max(versions, key=lambda v: tuple(map(int, v.split(".")))).split(".")[0]
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 "
            "Safari/537.36")


class Bridge:
    """Il processo del ponte: richieste con risposta, eventi, stato.

    Stati: spento | avvio | da collegare (mostra il QR) | pronto | scollegato (il telefono ha tolto il dispositivo)
    | errore."""

    def __init__(self, command: Callable[[bool], tuple[list[str], dict[str, str]]],
                 on_message: Callable[[dict], None] | None = None, adopt: Callable[[int], Any] | None = None) -> None:
        self.command = command  # visibile? -> (argomenti, variabili d'ambiente in più)
        self.on_message = on_message
        self.adopt = adopt  # winjob.adopt: se Jarvis cade, il ponte e il suo Chrome non restano orfani
        self.proc: asyncio.subprocess.Process | None = None
        self.state = "spento"
        self.visible = False
        self.me: dict | None = None
        self.error = ""
        self._changed = asyncio.Event()
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self._tasks: list[asyncio.Task] = []
        self._stderr: deque[str] = deque(maxlen=30)
        self._stopping = False  # chiusura voluta: la fine del processo non è un errore

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def _set(self, state: str, error: str = "") -> None:
        self.state = state
        if error:
            self.error = error
        self._changed.set()

    async def start(self, visible: bool = False) -> None:
        if self.running:
            return
        args, env = self.command(visible)
        self.visible, self.error, self.me, self._stopping = visible, "", None, False
        self._set("avvio")
        self.proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **env}, limit=64 * 1024 * 1024, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        if self.adopt is not None:
            self.adopt(self.proc.pid)
        self._tasks = [asyncio.create_task(self._read(self.proc)), asyncio.create_task(self._drain(self.proc))]

    async def wait(self, timeout_s: float, until: tuple[str, ...] = ("pronto", "da collegare", "scollegato",
                                                                         "errore", "spento")) -> str:
        """Aspetta uno stato tra quelli dati (di base: qualunque cosa che non sia "avvio")."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        while self.state not in until:
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                return self.state
            self._changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), left)
        return self.state

    async def call(self, op: str, timeout_s: float = 60.0, **args: Any) -> Any:
        if not self.running or self.proc is None or self.proc.stdin is None:
            raise AppError("WhatsApp non è acceso")
        self._next += 1
        rid = self._next
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            self.proc.stdin.write((json.dumps({"id": rid, "op": op, "args": args}, ensure_ascii=False) + "\n").encode())
            await self.proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout_s)
        except TimeoutError as e:
            raise AppError(f"WhatsApp non ha risposto in {timeout_s:g} secondi") from e
        except (ConnectionError, OSError, RuntimeError) as e:
            raise AppError("WhatsApp si è chiuso") from e
        finally:
            self._pending.pop(rid, None)

    async def stop(self) -> None:
        proc = self.proc
        if proc is None:
            return
        self._stopping = True
        if proc.returncode is None:
            with contextlib.suppress(OSError, RuntimeError):
                if proc.stdin is not None:
                    proc.stdin.close()  # il ponte chiude Chrome con garbo ed esce
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        for t in self._tasks:  # i lettori finiscono da soli (fine dell'output): niente righe perse
            try:
                await asyncio.wait_for(t, 5)
            except (TimeoutError, asyncio.CancelledError):
                pass
        self.proc = None
        self._set("spento")

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg:
                fut = self._pending.get(msg["id"])
                if fut is not None and not fut.done():
                    if msg.get("ok"):
                        fut.set_result(msg.get("result"))
                    else:
                        fut.set_exception(AppError(str(msg.get("error") or "errore sconosciuto di WhatsApp")))
            else:
                self._event(str(msg.get("event")), msg.get("data"))
        await proc.wait()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(AppError("WhatsApp si è chiuso"))
        if self.proc is proc and not self._stopping:  # chiuso da solo, non da stop()
            tail = " | ".join(list(self._stderr)[-3:])
            if self.state not in ("errore", "scollegato"):
                self._set("errore", self.error or f"il ponte si è chiuso (codice {proc.returncode}) {tail}".strip())
            self.proc = None

    async def _drain(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while line := await proc.stderr.readline():
            text = line.decode("utf-8", "replace").rstrip()
            self._stderr.append(text)
            log.debug("ponte: %s", text)

    def _event(self, kind: str, data: Any) -> None:
        # nel registro ogni passo del collegamento: il 27/10 il primo collegamento si è fermato dopo il QR senza dire
        # dove (whatsapp-web.js vecchio rispetto a WhatsApp Web), e non c'era modo di vederlo
        if kind == "qr":
            log.info("WhatsApp chiede il QR")
            self._set("da collegare")
        elif kind == "authenticated":
            log.info("WhatsApp: QR accettato, carica le chat")
            self._set("autenticato")
        elif kind == "loading":
            log.info("WhatsApp in caricamento: %s", data)
        elif kind == "ready":
            log.info("WhatsApp pronto")
            self.me = data
            self._set("pronto")
        elif kind == "auth_failure":
            log.warning("WhatsApp ha rifiutato la sessione: %s", data)
            self._set("errore", f"WhatsApp ha rifiutato la sessione: {data}")
        elif kind == "disconnected":
            log.warning("WhatsApp scollegato: %s", data)
            self._set("scollegato", f"scollegato: {data}")
        elif kind == "fatal":
            log.warning("il ponte di WhatsApp non parte: %s", data)
            self._set("errore", str(data))
        elif kind == "message" and self.on_message is not None and isinstance(data, dict):
            try:
                self.on_message(data)
            except Exception:
                log.exception("avviso di un messaggio WhatsApp fallito")


def bridge_modules() -> Path:
    """I pacchetti Node del ponte. In una copia separata (git worktree: auto-miglioramento, versione sana del guardiano)
    node_modules non c'è, git lo ignora: si usano quelli della cartella principale, che si ritrova da JARVIS_ENGINE_DIR
    (punta sempre ai motori veri, vedi tasks/self_improve.check_env e supervisor._env)."""
    local = BRIDGE_DIR / "node_modules"
    return local if local.is_dir() else paths.ENGINE_DIR.parent / "jarvis" / "apps" / "whatsapp_bridge" / "node_modules"


async def install_bridge(npm: str | None = None, chrome: Path | None = None,
                         run: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec) -> str | None:
    """I pacchetti Node del ponte (npm ci, una volta sola: nell'installatore non ci sono). None = fatto, altrimenti
    cosa non va, detto per il cervello. Se c'è già un browser, puppeteer non scarica il suo Chrome da 150 MB."""
    npm = npm or shutil.which("npm")
    if npm is None:
        return "manca Node.js (lo scarica il primo avvio, se non c'è: python -m jarvis --setup)"
    env = dict(os.environ)
    if (chrome or find_chrome()) is not None:
        env["PUPPETEER_SKIP_DOWNLOAD"] = "1"
    log.info("WhatsApp: preparo il ponte (npm ci)")
    try:
        proc = await run(npm, "ci", "--omit=dev", "--no-audit", "--no-fund", cwd=str(BRIDGE_DIR), env=env,
                         stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                         creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        return f"npm non parte ({e})"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), NPM_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"npm ci non ha finito in {NPM_TIMEOUT_S / 60:g} minuti"
    if proc.returncode != 0:
        log.warning("WhatsApp: npm ci finito con il codice %s: %s", proc.returncode,
                    " | ".join((out or b"").decode(errors="replace").strip().splitlines()[-4:]))
        return f"npm ci è finito con il codice {proc.returncode} (rete assente?)"
    return None


def bridge_command(data_dir: Path, node: str | None = None, chrome: Path | None = None
                   ) -> Callable[[bool], tuple[list[str], dict[str, str]]]:
    chrome = chrome or find_chrome()
    ua = user_agent(chrome)

    def command(visible: bool) -> tuple[list[str], dict[str, str]]:
        env = {"WA_DATA_DIR": str(data_dir), "WA_VISIBLE": "1" if visible else "0", "NODE_PATH": str(bridge_modules())}
        if chrome is not None:
            env["WA_CHROME"] = str(chrome)
        if ua:
            env["WA_USER_AGENT"] = ua
        return [node or shutil.which("node") or "node", str(BRIDGE_DIR / "bridge.js")], env

    return command


class WhatsApp:
    """Accensione su richiesta, collegamento col QR, rubrica in cache, chat cercate per nome."""

    IDLE_S = 15 * 60
    START_S = 120.0  # Chrome + WhatsApp Web + sincronizzazione: di solito 10-30 s
    LINK_S = 300.0  # quanto si aspetta che il QR venga inquadrato
    FIRST_SYNC_S = 600.0  # dopo il QR la prima sincronizzazione delle chat può essere lunga

    def __init__(self, bridge: Bridge, data_dir: Path, mode: Callable[[], str] = lambda: "su richiesta",
                 alerts: Callable[[], bool] = lambda: False, notify: Callable[[str], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 installed: Callable[[], bool] = lambda: bridge_modules().joinpath("whatsapp-web.js").is_dir(),
                 install: Callable[[], Awaitable[str | None]] = install_bridge) -> None:
        self.bridge = bridge
        self.installed = installed  # il ponte ha i suoi pacchetti Node (npm ci)
        self.install = install
        self.installing: asyncio.Task | None = None  # npm ci in corso, chiesto dal primo "collega"
        self.data_dir = data_dir
        self.mode = mode
        self.alerts = alerts
        self.notify = notify
        self.clock = clock
        self.last_used = 0.0
        self.linking: asyncio.Task | None = None
        self._starting = asyncio.Lock()  # una sola accensione alla volta; il controllo periodico non la interrompe
        self._dir: dict | None = None
        self._dir_at = 0.0
        bridge.on_message = self._incoming

    # ------------------------------------------------------------------ stato

    @property
    def mark(self) -> Path:
        """Scritto quando WhatsApp è davvero pronto. La cartella della sessione non basta: Chrome la crea appena parte,
        prima ancora del QR (il 27/10 sembrava collegato anche se non lo era)."""
        return self.data_dir / "collegato"

    def linked(self) -> bool:
        return self.mark.is_file()

    def ready(self) -> str | None:
        """Controllo leggero per ogni richiesta: None = si può provare (il ponte si accende da solo)."""
        if self.mode() == "spento":
            return "WhatsApp è spento nelle impostazioni (apps.whatsapp)"
        if not self.installed():
            if self.installing is not None and not self.installing.done():
                return "sto preparando il ponte di WhatsApp (una volta sola, un paio di minuti): poi si apre il QR"
            return "il ponte non è ancora installato: usa l'azione collega (lo prepara da solo, una volta sola)"
        if self.linking is not None and not self.linking.done():
            return "si sta collegando: l'utente deve inquadrare il QR nella finestra di WhatsApp Web"
        if not self.linked():
            return "non è collegato al telefono: usa l'azione collega (una volta sola)"
        return None

    async def ensure(self) -> None:
        async with self._starting:
            await self._ensure()

    async def _ensure(self) -> None:
        if self.bridge.state != "pronto" or not self.bridge.running:
            self.last_used = self.clock()  # sta per essere usato: il controllo dei 15 minuti non deve spegnerlo
            await self.bridge.start(visible=False)
            state = await self.bridge.wait(self.START_S)
            if state in ("da collegare", "scollegato"):
                await self.bridge.stop()
                self._forget_session()
                raise AppError("WhatsApp si è scollegato dal telefono: va ricollegato con l'azione collega")
            if state != "pronto":
                why = self.bridge.error or ("si è chiuso mentre partiva" if state == "spento"
                                            else f"ancora '{state}' dopo {self.START_S:g} secondi")
                await self.bridge.stop()
                raise AppError(f"WhatsApp non parte: {why}")
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.mark.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        self.last_used = self.clock()

    async def call(self, op: str, timeout_s: float = 60.0, **args: Any) -> Any:
        await self.ensure()
        out = await self.bridge.call(op, timeout_s, **args)
        self.last_used = self.clock()
        return out

    async def tick(self) -> None:
        """Ogni mezzo minuto (app.py): acceso sempre, spento, o spento dopo 15 minuti senza usarlo."""
        if (self.linking is not None and not self.linking.done()) or self._starting.locked():
            return  # mai a metà di un collegamento o di un'accensione (27/10: lo spegneva mentre partiva)
        mode = self.mode()
        if mode == "spento" or (mode == "su richiesta" and self.bridge.running and self.bridge.state == "pronto"
                                and self.clock() - self.last_used > self.IDLE_S):
            if self.bridge.running:
                await self.bridge.stop()
        elif mode == "sempre" and self.linked() and not self.bridge.running:
            try:
                await self.ensure()
            except AppError as e:
                log.warning("WhatsApp non si accende: %s", e)

    def _forget_session(self) -> None:
        self.mark.unlink(missing_ok=True)
        shutil.rmtree(self.data_dir / "session", ignore_errors=True)
        self._dir = None

    # ------------------------------------------------------------------ collegamento

    async def link(self) -> str:
        if not self.installed():  # la prima volta: prima i pacchetti del ponte, poi il QR (avvisa quando è pronto)
            if self.installing is None or self.installing.done():
                self.installing = asyncio.create_task(self._install_then_link())
            return ("Preparo WhatsApp: la prima volta servono un paio di minuti per installare il ponte. Poi si apre "
                    "da sola la finestra con il QR e avviso io.")
        if self.linked() and self.bridge.state == "pronto":
            return f"WhatsApp è già collegato{self._me()}"
        if self.linking is not None and not self.linking.done():
            return "la finestra con il QR è già aperta: aspetto che l'utente lo inquadri"
        await self.bridge.stop()
        await self.bridge.start(visible=True)
        state = await self.bridge.wait(self.START_S)
        if state == "pronto":  # la sessione c'era già
            await self.bridge.stop()
            await self.ensure()
            return f"WhatsApp era già collegato{self._me()}: ora lavora invisibile"
        if state != "da collegare":
            why = self.bridge.error or state
            await self.bridge.stop()
            raise AppError(f"WhatsApp Web non si apre: {why}")
        self.linking = asyncio.create_task(self._finish_link())
        return ("Si è aperta una finestra del browser con WhatsApp Web e il QR. Sul telefono: WhatsApp, Impostazioni (o i "
                "tre puntini), Dispositivi collegati, Collega un dispositivo, e inquadra il QR. Avviso io quando è fatto "
                "e la finestra si chiude da sola.")

    async def _install_then_link(self) -> None:
        problem = await self.install()
        if problem is None and not self.installed():
            problem = "npm ci è finito ma i pacchetti del ponte non ci sono"
        if problem is not None:
            log.warning("WhatsApp: ponte non installato: %s", problem)
            if self.notify:
                self.notify(f"Il ponte di WhatsApp non si è installato: {problem}. Dillo in una frase.")
            return
        try:
            said = await self.link()
        except AppError as e:
            said = f"Il ponte di WhatsApp è pronto, ma WhatsApp Web non si apre: {e}."
        if self.notify:
            self.notify(f"{said} Dillo in breve.")

    async def _finish_link(self) -> None:
        end = ("pronto", "errore", "spento", "scollegato")
        state = await self.bridge.wait(self.LINK_S, until=("autenticato", *end))  # QR inquadrato?
        if state == "autenticato":  # sì: la prima sincronizzazione ha il suo tempo
            state = await self.bridge.wait(self.FIRST_SYNC_S, until=end)
        await self.bridge.stop()
        if state != "pronto":
            why = ("il QR è stato inquadrato ma WhatsApp Web non ha finito di caricarsi" if state == "autenticato"
                   else "QR non inquadrato in tempo o finestra chiusa")
            if self.notify:
                self.notify(f"Il collegamento di WhatsApp non è riuscito ({why}). Dillo in una frase: si può riprovare.")
            return
        try:
            await self.ensure()
            if self.notify:
                self.notify(f"WhatsApp è collegato{self._me()}: d'ora in poi lavora invisibile. Dillo in una frase.")
        except AppError as e:
            if self.notify:
                self.notify(f"WhatsApp si è collegato ma non riparte invisibile: {e}. Dillo in una frase.")

    async def unlink(self) -> str:
        if self.bridge.state == "pronto":
            with contextlib.suppress(AppError):
                await self.bridge.call("logout", 30)
        await self.bridge.stop()
        self._forget_session()
        return "WhatsApp scollegato: il dispositivo è stato tolto e la sessione cancellata da questo PC"

    def _me(self) -> str:
        me = self.bridge.me or {}
        return f" (account {me.get('name')})" if me.get("name") else ""

    # ------------------------------------------------------------------ rubrica

    async def directory(self, fresh: bool = False) -> dict:
        if fresh or self._dir is None or self.clock() - self._dir_at > 60:
            self._dir = await self.call("directory", 90)
            self._dir_at = self.clock()
        return self._dir

    def changed(self) -> None:
        self._dir = None

    async def resolve(self, query: str, groups: bool | None = None) -> dict:
        """{"id", "name", "group"} della chat o del contatto: nome (anche approssimato), numero o "me stesso"."""
        q = str(query).strip()
        me = (self.bridge.me or {}).get("number")
        if norm(q) in ME and me:
            return {"id": f"{me}@c.us", "name": "te stesso (Messaggi a te stesso)", "group": False}
        digits = re.sub(r"\D", "", q)
        if len(digits) >= 8 and len(digits) >= len(re.sub(r"[\s+()./-]", "", q)) - 1:
            if len(digits) == 10 and digits.startswith("3"):  # cellulare italiano senza prefisso
                digits = "39" + digits
            cid = await self.call("number_id", number=digits)
            if not cid:
                raise AppError(f"il numero {q} non è su WhatsApp")
            return {"id": cid, "name": "+" + digits, "group": False}
        d = await self.directory()
        items, seen = [], set()
        for c in d.get("chats") or []:
            items.append({"id": c["id"], "name": c["name"], "group": bool(c.get("group"))})
            seen.add(c["id"])
        for c in d.get("contacts") or []:
            if c["id"] not in seen:
                items.append({"id": c["id"], "name": c["name"], "group": False})
        if groups is not None:
            items = [c for c in items if c["group"] == groups]
        return pick_by_name(q, items, lambda c: c["name"], plural="chat", empty="quale chat?",
                            missing=f"non trovo una chat o un contatto chiamato '{q}'")

    # ------------------------------------------------------------------ messaggi nuovi

    def _incoming(self, m: dict) -> None:
        if not self.alerts() or self.notify is None or m.get("muted"):
            return
        who = m.get("author") or m.get("chat_name") or "qualcuno"
        where = f" nel gruppo {m.get('chat_name')}" if m.get("group") else ""
        # chi scrive sì, il testo no: ad alta voce lo sentirebbe chiunque è nella stanza
        self.notify(f"Nuovo messaggio WhatsApp da {who}{where}. Dillo in una frase, senza leggerlo: lo leggi solo se "
                    "l'utente te lo chiede (usa_app whatsapp leggi).")


# ---------------------------------------------------------------------- come si dicono i messaggi


def clock_text(ts: float, now: float) -> str:
    if not ts:
        return ""
    t = datetime.fromtimestamp(ts)
    days = (date.fromtimestamp(now) - t.date()).days
    hm = t.strftime("%H:%M")
    return hm if days == 0 else f"ieri {hm}" if days == 1 else t.strftime("%d/%m %H:%M")


def seconds(n: Any) -> str:
    try:
        n = int(float(n))
    except (TypeError, ValueError):
        return ""
    return f"{n // 60}:{n % 60:02d}"


def message_line(m: dict, now: float, me: str = "tu") -> str | None:
    kind = KINDS.get(m.get("type") or "chat", m.get("type"))
    if kind is None:
        return None
    who = me if m.get("from_me") else (m.get("author") or "?")
    body = (m.get("body") or "").strip()
    if kind:
        label = kind
        if kind in ("vocale", "audio", "video") and m.get("duration"):
            label += " " + seconds(m["duration"])
        if kind == "documento" and m.get("filename"):
            label += " " + str(m["filename"])
        body = f"[{label}]" + (f" {body}" if body and kind not in ("posizione", "contatto", "contatti") else "")
    if m.get("quoted_text") is not None:  # 27/10: "tu: bell' audio" senza sapere a cosa rispondeva
        body += f" (in risposta a {m.get('quoted_author') or '?'}: «{m['quoted_text']}»)"
    return f"[{clock_text(m.get('ts') or 0, now)}] {who}: {body}"


def voice_audio(path: Path, rate: int = 16000):
    """Un vocale di WhatsApp (ogg/opus) come audio mono a 16 kHz, quello che si aspettano le orecchie."""
    import numpy as np
    import soundfile as sf
    import soxr

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    return np.asarray(soxr.resample(mono, sr, rate) if sr != rate else mono, dtype=np.float32)


async def transcribe_with(stt: Callable[[Any], Awaitable[str]], path: Path, chunk_s: float = 30.0) -> str:
    """Trascrive un vocale a pezzi di 30 secondi (le orecchie sono fatte per frasi, non per monologhi)."""
    audio = await asyncio.to_thread(voice_audio, path)
    n = int(chunk_s * 16000)
    parts = [(await stt(audio[i:i + n])).strip() for i in range(0, len(audio), n) if len(audio[i:i + n]) > 1600]
    return " ".join(x for x in parts if x)


MEDIA_KINDS = ("video", "ptt", "audio")  # ciò che Jarvis può guardare o ascoltare (jarvis/media)


async def fetch_media(wa: WhatsApp, chat: str, dest: Path, which: str = "", now: Callable[[], float] = time.time
                      ) -> tuple[Path, str]:
    """L'ultimo video, vocale o audio di una chat, scaricato in dest (cartella temporanea di chi guarda): il file e
    una riga che dice cos'è e di chi. which = 'penultimo' per quello prima."""
    if why := wa.ready():
        raise AppError(f"WhatsApp non è pronto: {why}")
    c = await wa.resolve(chat)
    data = await wa.call("messages", chat=c["id"], limit=60)
    found = [m for m in reversed(data.get("messages") or []) if m.get("type") in MEDIA_KINDS]
    if not found:
        raise AppError(f"nessun video, vocale o audio negli ultimi messaggi di {c['name']}")
    m = found[1] if str(which).strip().lower() == "penultimo" and len(found) > 1 else found[0]
    got = await wa.call("download", 300, msg=m["id"], dir=str(dest))
    who = "tuo" if m.get("from_me") else f"di {m.get('author') or c['name']}"
    when = clock_text(m.get("ts") or 0, now())
    return Path(got["file"]), f"{KINDS.get(m.get('type')) or 'file'} {who} nella chat {c['name']} ({when})"


def _yes(v: Any) -> bool:
    return str(v or "").strip().lower() in ("sì", "si", "true", "1", "vero")


# ---------------------------------------------------------------------- l'app


def build(wa: WhatsApp, open_uri: Callable[[str], Any] | None = None,
          transcribe: Callable[[Any], Awaitable[str]] | None = None, downloads: Path | None = None,
          now: Callable[[], float] = time.time) -> App:
    opener = open_uri or os.startfile
    save_dir = downloads or Path.home() / "Downloads" / "WhatsApp"
    app = App("whatsapp", "WhatsApp (nascosto, in background)", "messaggi", ready=wa.ready, aliases=("wa", "whats app"),
              notes="chat = nome di una chat o di un contatto (anche approssimato), un numero, o 'me stesso'. "
                    "messaggio = un pezzo del testo per scegliere un messaggio preciso; senza, l'ultimo adatto. "
                    "Leggere non segna come letto (segna_letta lo fa). Le chiamate: apri_app e l'utente preme "
                    "chiama. Prima del collegamento funzionano solo collega e stato.")

    async def chat_of(a: dict, groups: bool | None = None) -> dict:
        if a.get("_chat"):
            return a["_chat"]  # già risolta e confermata: si usa quella
        c = await wa.resolve(a["chat"], groups)
        a["_chat"] = c
        return c

    async def pick_message(chat_id: str, fragment: Any, mine: bool | None, what: str) -> dict:
        data = await wa.call("messages", chat=chat_id, limit=60)
        msgs = [m for m in reversed(data.get("messages") or []) if KINDS.get(m.get("type"), "") is not None]
        if mine is not None:
            msgs = [m for m in msgs if bool(m.get("from_me")) == mine]
        if fragment:
            f = norm(str(fragment))
            msgs = [m for m in msgs if f in norm((m.get("body") or "") + " " + (m.get("filename") or ""))]
        if not msgs:
            hint = f" con «{fragment}»" if fragment else ""
            raise AppError(f"non trovo {what}{hint} negli ultimi messaggi")
        return msgs[0]

    def preview(c: dict) -> str:
        last = c.get("last") or {}
        kind = KINDS.get(last.get("type") or "chat", last.get("type"))
        if not last or kind is None:
            return ""
        text = f"[{kind}] {last.get('body') or ''}".strip() if kind else (last.get("body") or "")
        return f" — {'tu: ' if last.get('from_me') else ''}{text} ({clock_text(c.get('ts') or 0, now())})"

    def quote(m: dict) -> str:
        line = message_line(m, now()) or ""
        return line if len(line) <= 160 else line[:160] + "…"

    # ---------------------------------------------------------- collegamento e stato

    @app.action("collega", "collega WhatsApp al telefono col QR (una volta sola)", needs_ready=False)
    async def collega(_a):
        if wa.mode() == "spento":
            raise AppError("WhatsApp è spento nelle impostazioni (apps.whatsapp): prima va acceso")
        return await wa.link()

    @app.action("stato", "se WhatsApp è collegato, acceso e con quale account", needs_ready=False)
    async def stato(_a):
        if not wa.linked():
            return "WhatsApp non è collegato (azione collega)"
        if wa.bridge.state == "pronto":
            return f"WhatsApp collegato e acceso{wa._me()}; modalità {wa.mode()}"
        return f"WhatsApp collegato, adesso spento (si accende da solo quando serve); modalità {wa.mode()}"

    @app.action("scollega", "toglie Jarvis dai dispositivi collegati e cancella la sessione", needs_ready=False,
                confirm=lambda a: "scollegare WhatsApp da Jarvis (per riusarlo andrà rifatto il QR)")
    async def scollega(_a):
        return await wa.unlink()

    # ---------------------------------------------------------- leggere

    @app.action("chat", "le chat più recenti, con i non letti",
                {"filtro": "? parte del nome", "quante": "? quante (10)", "archiviate": "? sì per le archiviate"})
    async def chat(a):
        d = await wa.directory(fresh=True)
        chats = [c for c in d.get("chats") or [] if bool(c.get("archived")) == _yes(a.get("archiviate"))]
        if a.get("filtro"):
            f = norm(str(a["filtro"]))
            chats = [c for c in chats if f in norm(c["name"])]
        chats.sort(key=lambda c: (not c.get("pinned"), -(c.get("ts") or 0)))
        n = max(1, min(int(a.get("quante") or 10), 40))
        rows = []
        for c in chats[:n]:
            tags = [f"{c['unread']} non letti" if c.get("unread") else "", "gruppo" if c.get("group") else "",
                    "fissata" if c.get("pinned") else "", "silenziata" if c.get("muted") else ""]
            rows.append(f"{c['name']}" + (f" ({', '.join(t for t in tags if t)})" if any(tags) else "")
                        + preview(c))
        return "; ".join(rows) if rows else "nessuna chat"

    @app.action("non_lette", "tutti i messaggi non letti, chat per chat", {"tutte": "? sì per includere le silenziate"})
    async def non_lette(a):
        d = await wa.directory(fresh=True)
        everything = _yes(a.get("tutte"))
        unread = [c for c in d.get("chats") or [] if c.get("unread") and (not c.get("muted") or everything)]
        muted = [] if everything else sorted((c for c in d.get("chats") or [] if c.get("unread") and c.get("muted")),
                                             key=lambda c: -c["unread"])
        # 27/10: "nessun messaggio non letto" con 28 non letti in un gruppo silenziato; ora lo si dice
        quiet = (f"{sum(c['unread'] for c in muted)} non letti in chat silenziate ("
                 + ", ".join(f"{c['name']} {c['unread']}" for c in muted[:5]) + "): con tutte=sì li leggo") if muted else ""
        if not unread:
            return f"nessun messaggio non letto nelle chat normali; {quiet}" if quiet else "nessun messaggio non letto"
        unread.sort(key=lambda c: -(c.get("ts") or 0))
        out = []
        for c in unread[:8]:
            data = await wa.call("messages", chat=c["id"], limit=min(c["unread"], 5) + 10)
            got = [m for m in data.get("messages") or [] if not m.get("from_me") and KINDS.get(m.get("type"), "")
                   is not None]  # i non letti sono quelli ricevuti: né i propri né le notifiche di servizio
            lines = [x for x in (message_line(m, now()) for m in got[-min(c["unread"], 5):]) if x]
            more = f" (+{c['unread'] - 5} più vecchi)" if c["unread"] > 5 else ""
            out.append(f"{c['name']} ({c['unread']}){more}: " + " | ".join(lines))
        if len(unread) > 8:
            out.append(f"altre {len(unread) - 8} chat con messaggi non letti")
        if quiet:
            out.append(f"in più {quiet}")
        return "\n".join(out)

    @app.action("leggi", "gli ultimi messaggi di una chat (non li segna come letti)",
                {"chat": "nome, numero o me stesso", "quanti": "? quanti (10, al massimo 60)"})
    async def leggi(a):
        c = await chat_of(a)
        n = max(1, min(int(a.get("quanti") or 10), 60))
        data = await wa.call("messages", chat=c["id"], limit=n)
        lines = [x for x in (message_line(m, now()) for m in data.get("messages") or []) if x]
        return f"{c['name']}:\n" + ("\n".join(lines) if lines else "(nessun messaggio)")

    @app.action("cerca", "cerca un testo nei messaggi", {"testo": "cosa cercare", "chat": "? solo in questa chat"})
    async def cerca(a):
        cid = (await chat_of(a))["id"] if a.get("chat") else None
        found = await wa.call("search", query=str(a["testo"]), chat=cid, limit=15)
        if not found:
            return f"nessun messaggio contiene «{a['testo']}»"
        names = {c["id"]: c["name"] for c in (await wa.directory()).get("chats") or []}
        return "\n".join(f"{names.get(m.get('chat'), m.get('chat'))} {message_line(m, now())}" for m in found)

    @app.action("info", "chi è un contatto (numero, info, bloccato) o com'è fatto un gruppo (membri, admin)",
                {"chat": "nome o numero"})
    async def info(a):
        c = await chat_of(a)
        i = await wa.call("info", chat=c["id"])
        if i.get("group"):
            names = {x["id"]: x["name"] for x in (await wa.directory()).get("contacts") or []}
            members = [names.get(p["id"], "+" + p["id"].split("@")[0]) + (" (admin)" if p.get("admin") else "")
                       for p in i.get("participants") or []]
            return (f"gruppo {i.get('name')}: {len(members)} membri: {', '.join(members)}"
                    + (f"; descrizione: {i['description']}" if i.get("description") else ""))
        bits = [f"{i.get('name')}", f"numero +{i.get('number')}"]
        if i.get("about"):
            bits.append(f"info: {i['about']}")
        if i.get("blocked"):
            bits.append("BLOCCATO")
        if i.get("business"):
            bits.append("account business")
        if not i.get("my_contact"):
            bits.append("non è in rubrica")
        return ", ".join(bits)

    # ---------------------------------------------------------- scrivere (sempre con conferma)

    async def invia_conferma(a):
        c = await chat_of(a)
        return f"inviare a {c['name']}{' (gruppo)' if c['group'] else ''}: «{a['testo']}»"

    @app.action("invia", "manda un messaggio di testo", {"chat": "a chi: nome, numero o me stesso", "testo": "il testo"},
                confirm=invia_conferma)
    async def invia(a):
        c = await chat_of(a)
        await wa.call("send", chat=c["id"], text=str(a["testo"]))
        wa.changed()
        return f"inviato a {c['name']}"

    async def rispondi_conferma(a):
        c = await chat_of(a)
        m = await pick_message(c["id"], a.get("messaggio"), False, "un messaggio a cui rispondere")
        a["_msg"] = m["id"]
        return f"rispondere a {c['name']} citando {quote(m)}: «{a['testo']}»"

    @app.action("rispondi", "risponde citando un messaggio ricevuto (di base l'ultimo)",
                {"chat": "nome", "testo": "la risposta", "messaggio": "? pezzo del messaggio da citare"},
                confirm=rispondi_conferma)
    async def rispondi(a):
        c = await chat_of(a)
        msg = a.get("_msg") or (await pick_message(c["id"], a.get("messaggio"), False, "un messaggio"))["id"]
        await wa.call("send", chat=c["id"], text=str(a["testo"]), quote=msg)
        wa.changed()
        return f"risposto a {c['name']}"

    def file_of(a) -> Path:
        p = Path(os.path.expandvars(os.path.expanduser(str(a["percorso"]).strip().strip('"'))))
        if not p.is_file():
            raise AppError(f"il file {p} non esiste (serve il percorso completo)")
        return p

    async def file_conferma(a):
        c, p = await chat_of(a), file_of(a)
        size = p.stat().st_size
        how = " come documento" if _yes(a.get("documento")) else ""
        cap = f" con la didascalia «{a['didascalia']}»" if a.get("didascalia") else ""
        mb = f"{size / 1024 ** 2:.1f}".replace(".", ",")
        return f"inviare a {c['name']} il file {p.name} ({mb} MB){how}{cap}"

    @app.action("invia_file", "manda una foto, un video, un audio o un documento dal PC",
                {"chat": "a chi", "percorso": "percorso completo del file", "didascalia": "? testo sotto",
                 "documento": "? sì per mandarlo come documento (qualità originale)"}, confirm=file_conferma)
    async def invia_file(a):
        c, p = await chat_of(a), file_of(a)
        await wa.call("send_file", 300, chat=c["id"], file=str(p), caption=str(a.get("didascalia") or ""),
                      document=_yes(a.get("documento")))
        wa.changed()
        return f"inviato {p.name} a {c['name']}"

    async def reagisci_conferma(a):
        c = await chat_of(a)
        m = await pick_message(c["id"], a.get("messaggio"), None, "il messaggio")
        a["_msg"] = m["id"]
        return f"reagire con {a['emoji']} a {quote(m)} nella chat {c['name']}"

    @app.action("reagisci", "mette una reazione (emoji) a un messaggio (di base l'ultimo)",
                {"chat": "nome", "emoji": "l'emoji, es. 👍 ❤️ 😂", "messaggio": "? pezzo del messaggio"},
                confirm=reagisci_conferma)
    async def reagisci(a):
        c = await chat_of(a)
        msg = a.get("_msg") or (await pick_message(c["id"], a.get("messaggio"), None, "il messaggio"))["id"]
        await wa.call("react", msg=msg, emoji=str(a["emoji"]))
        return f"reazione {a['emoji']} messa"

    async def inoltra_conferma(a):
        c = await chat_of(a)
        dest = await wa.resolve(a["a"])
        a["_to"] = dest
        m = await pick_message(c["id"], a.get("messaggio"), None, "il messaggio")
        a["_msg"] = m["id"]
        return f"inoltrare a {dest['name']} il messaggio {quote(m)} (dalla chat {c['name']})"

    @app.action("inoltra", "inoltra un messaggio a un'altra chat (di base l'ultimo)",
                {"chat": "da quale chat", "a": "a chi", "messaggio": "? pezzo del messaggio"},
                confirm=inoltra_conferma)
    async def inoltra(a):
        c = await chat_of(a)
        dest = a.get("_to") or await wa.resolve(a["a"])
        msg = a.get("_msg") or (await pick_message(c["id"], a.get("messaggio"), None, "il messaggio"))["id"]
        await wa.call("forward", msg=msg, to=dest["id"])
        wa.changed()
        return f"inoltrato a {dest['name']}"

    async def modifica_conferma(a):
        c = await chat_of(a)
        m = await pick_message(c["id"], a.get("messaggio"), True, "un tuo messaggio")
        a["_msg"] = m["id"]
        return f"correggere il tuo messaggio {quote(m)} nella chat {c['name']} in: «{a['testo']}»"

    @app.action("modifica_messaggio", "corregge un proprio messaggio (WhatsApp lo permette entro 15 minuti)",
                {"chat": "nome", "testo": "il testo nuovo", "messaggio": "? pezzo del messaggio (di base l'ultimo tuo)"},
                confirm=modifica_conferma)
    async def modifica_messaggio(a):
        c = await chat_of(a)
        msg = a.get("_msg") or (await pick_message(c["id"], a.get("messaggio"), True, "un tuo messaggio"))["id"]
        await wa.call("edit_msg", msg=msg, text=str(a["testo"]))
        return "messaggio corretto"

    async def elimina_conferma(a):
        c = await chat_of(a)
        everyone = _yes(a.get("per_tutti"))
        m = await pick_message(c["id"], a.get("messaggio"), True if everyone else None, "il messaggio")
        a["_msg"] = m["id"]
        return f"eliminare {'PER TUTTI' if everyone else 'solo per te'} il messaggio {quote(m)} nella chat {c['name']}"

    @app.action("elimina_messaggio", "cancella un messaggio (per tutti solo i propri)",
                {"chat": "nome", "messaggio": "? pezzo del messaggio (di base l'ultimo)",
                 "per_tutti": "? sì per cancellarlo anche agli altri"}, confirm=elimina_conferma)
    async def elimina_messaggio(a):
        c = await chat_of(a)
        everyone = _yes(a.get("per_tutti"))
        msg = a.get("_msg") or (await pick_message(c["id"], a.get("messaggio"), True if everyone else None,
                                                   "il messaggio"))["id"]
        await wa.call("delete_msg", msg=msg, everyone=everyone)
        return "messaggio eliminato" + (" per tutti" if everyone else " per te")

    # ---------------------------------------------------------- allegati e vocali

    @app.action("scarica", "salva sul PC l'ultimo allegato di una chat (foto, video, documento, vocale)",
                {"chat": "nome", "messaggio": "? pezzo della didascalia o nome del file"})
    async def scarica(a):
        c = await chat_of(a)
        data = await wa.call("messages", chat=c["id"], limit=60)
        media = [m for m in reversed(data.get("messages") or []) if m.get("has_media")]
        if a.get("messaggio"):
            f = norm(str(a["messaggio"]))
            media = [m for m in media if f in norm((m.get("body") or "") + " " + (m.get("filename") or ""))]
        if not media:
            raise AppError(f"nessun allegato negli ultimi messaggi di {c['name']}")
        got = await wa.call("download", 300, msg=media[0]["id"], dir=str(save_dir))
        return f"salvato {got['file']} ({KINDS.get(media[0].get('type')) or 'file'})"

    @app.action("trascrivi", "ascolta e trascrive un vocale ricevuto (di base l'ultimo)",
                {"chat": "nome", "quale": "? 'penultimo' per quello prima; di base l'ultimo"})
    async def trascrivi(a):
        if transcribe is None:
            raise AppError("le orecchie di Jarvis non sono disponibili per trascrivere")
        c = await chat_of(a)
        data = await wa.call("messages", chat=c["id"], limit=60)
        voices = [m for m in reversed(data.get("messages") or []) if m.get("type") in ("ptt", "audio")]
        if not voices:
            raise AppError(f"nessun vocale negli ultimi messaggi di {c['name']}")
        m = voices[1] if str(a.get("quale") or "").strip().lower() == "penultimo" and len(voices) > 1 else voices[0]
        with tempfile.TemporaryDirectory(prefix="jarvis_vocale_") as tmp:
            got = await wa.call("download", 120, msg=m["id"], dir=tmp)
            text = await transcribe(Path(got["file"]))
        who = "tuo" if m.get("from_me") else f"di {m.get('author') or c['name']}"
        return f"vocale {who} ({clock_text(m.get('ts') or 0, now())}, {seconds(m.get('duration'))}): {text or '(muto)'}"

    # ---------------------------------------------------------- ordinare le chat (reversibile, senza conferma)

    def simple(op: str, done: str, **extra: Any):
        async def run(a):
            c = await chat_of(a)
            await wa.call(op, chat=c["id"], **extra)
            wa.changed()
            return f"{c['name']}: {done}"
        return run

    app.action("segna_letta", "segna una chat come letta (gli altri vedono le spunte blu)", {"chat": "nome"})(
        simple("seen", "segnata come letta"))
    app.action("segna_non_letta", "segna una chat come non letta", {"chat": "nome"})(
        simple("mark_unread", "segnata come non letta"))
    app.action("archivia", "archivia una chat", {"chat": "nome"})(simple("archive", "archiviata", on=True))
    app.action("ripristina", "toglie una chat dall'archivio", {"chat": "nome"})(
        simple("archive", "tolta dall'archivio", on=False))
    app.action("fissa", "fissa una chat in cima", {"chat": "nome"})(simple("pin", "fissata in cima", on=True))
    app.action("togli_fissata", "toglie una chat dalle fissate", {"chat": "nome"})(
        simple("pin", "non più fissata", on=False))

    @app.action("silenzia", "silenzia le notifiche di una chat", {"chat": "nome", "ore": "? per quante ore (vuoto = sempre)"})
    async def silenzia(a):
        c = await chat_of(a)
        hours = float(str(a["ore"]).replace(",", ".")) if a.get("ore") else None
        await wa.call("mute", chat=c["id"], until=int(now() + hours * 3600) if hours else None)
        wa.changed()
        return f"{c['name']}: silenziata" + (f" per {a['ore']} ore" if hours else " per sempre")

    app.action("riattiva_audio", "riattiva le notifiche di una chat silenziata", {"chat": "nome"})(
        simple("unmute", "notifiche riattivate"))

    # ---------------------------------------------------------- cose che cancellano o si vedono (con conferma)

    def confirm_on_chat(what: str):
        async def conferma(a):
            c = await chat_of(a)
            return f"{what} {c['name']}"
        return conferma

    app.action("svuota_chat", "cancella tutti i messaggi di una chat (la chat resta)", {"chat": "nome"},
               confirm=confirm_on_chat("cancellare TUTTI i messaggi della chat"))(simple("clear", "svuotata"))
    app.action("elimina_chat", "elimina una chat dall'elenco", {"chat": "nome"},
               confirm=confirm_on_chat("eliminare la chat"))(simple("delete_chat", "chat eliminata"))
    app.action("blocca", "blocca un contatto", {"chat": "nome o numero"},
               confirm=confirm_on_chat("bloccare"))(simple("block", "bloccato", on=True))
    app.action("sblocca", "sblocca un contatto", {"chat": "nome o numero"})(simple("block", "sbloccato", on=False))

    # ---------------------------------------------------------- gruppi

    async def people(a) -> list[dict]:
        names = [x.strip() for x in re.split(r",|\be\b", str(a["persone"])) if x.strip()]
        if not names:
            raise AppError("chi? servono uno o più nomi separati da virgole")
        return [await wa.resolve(n, groups=False) for n in names]

    async def crea_conferma(a):
        ps = await people(a)
        a["_people"] = ps
        return f"creare il gruppo «{a['nome']}» con {', '.join(p['name'] for p in ps)}"

    @app.action("crea_gruppo", "crea un gruppo", {"nome": "nome del gruppo", "persone": "nomi separati da virgole"},
                confirm=crea_conferma)
    async def crea_gruppo(a):
        ps = a.get("_people") or await people(a)
        await wa.call("group_create", 90, name=str(a["nome"]), participants=[p["id"] for p in ps])
        wa.changed()
        return f"gruppo «{a['nome']}» creato"

    MEMBER_OPS = {"aggiungi": ("add", "aggiungere al gruppo"), "rimuovi": ("remove", "togliere dal gruppo"),
                  "admin": ("promote", "fare amministratori del gruppo"), "togli_admin": ("demote",
                                                                                         "togliere da amministratori del gruppo")}

    def members(kind: str):
        op, words = MEMBER_OPS[kind]

        async def conferma(a):
            g = await chat_of(a, groups=True)
            ps = await people(a)
            a["_people"] = ps
            return f"{words} {g['name']}: {', '.join(p['name'] for p in ps)}"

        async def run(a):
            g = await chat_of(a, groups=True)
            ps = a.get("_people") or await people(a)
            await wa.call("group_members", 60, chat=g["id"], participants=[p["id"] for p in ps], action=op)
            wa.changed()
            return f"fatto nel gruppo {g['name']}: {words} {', '.join(p['name'] for p in ps)}"
        return conferma, run

    for kind in MEMBER_OPS:
        conferma, run = members(kind)
        app.action(f"gruppo_{kind}", f"{MEMBER_OPS[kind][1]} una o più persone",
                   {"chat": "il gruppo", "persone": "nomi separati da virgole"}, confirm=conferma)(run)

    def group_text_confirm(what: str):
        async def conferma(a):
            g = await chat_of(a, groups=True)
            return f"cambiare {what} del gruppo {g['name']} in «{a['testo']}»"
        return conferma

    @app.action("gruppo_nome", "cambia il nome di un gruppo", {"chat": "il gruppo", "testo": "il nome nuovo"},
                confirm=group_text_confirm("il nome"))
    async def gruppo_nome(a):
        g = await chat_of(a, groups=True)
        await wa.call("group_subject", chat=g["id"], text=str(a["testo"]))
        wa.changed()
        return f"gruppo rinominato in «{a['testo']}»"

    @app.action("gruppo_descrizione", "cambia la descrizione di un gruppo",
                {"chat": "il gruppo", "testo": "la descrizione"}, confirm=group_text_confirm("la descrizione"))
    async def gruppo_descrizione(a):
        g = await chat_of(a, groups=True)
        await wa.call("group_description", chat=g["id"], text=str(a["testo"]))
        return f"descrizione del gruppo {g['name']} cambiata"

    @app.action("gruppo_link", "il link d'invito di un gruppo (serve essere admin)", {"chat": "il gruppo"})
    async def gruppo_link(a):
        g = await chat_of(a, groups=True)
        return f"link d'invito di {g['name']}: {await wa.call('group_invite', chat=g['id'])}"

    app.action("esci_gruppo", "esce da un gruppo", {"chat": "il gruppo"},
               confirm=confirm_on_chat("uscire dal gruppo"))(simple("group_leave", "sei uscito dal gruppo"))

    # ---------------------------------------------------------- profilo e stato

    @app.action("cambia_info", "cambia la frase info del profilo", {"testo": "la frase nuova"},
                confirm=lambda a: f"cambiare l'info del tuo profilo WhatsApp in «{a['testo']}»")
    async def cambia_info(a):
        await wa.call("set_about", text=str(a["testo"]))
        return "info del profilo cambiata"

    @app.action("cambia_nome", "cambia il nome visibile del profilo", {"nome": "il nome nuovo"},
                confirm=lambda a: f"cambiare il tuo nome su WhatsApp in «{a['nome']}»")
    async def cambia_nome(a):
        if not await wa.call("set_name", name=str(a["nome"])):
            raise AppError("WhatsApp non ha accettato il nome")
        return f"nome cambiato in «{a['nome']}»"

    @app.action("pubblica_stato", "pubblica uno stato di solo testo (lo vedono i contatti per 24 ore)",
                {"testo": "il testo"}, confirm=lambda a: f"pubblicare come stato, visibile ai contatti: «{a['testo']}»")
    async def pubblica_stato(a):
        await wa.call("post_status", text=str(a["testo"]))
        return "stato pubblicato"

    # ---------------------------------------------------------- l'app vera (chiamate)

    @app.action("apri_app", "apre l'app di WhatsApp (sulla chat, se data): per le chiamate, che si fanno da lì",
                {"chat": "? nome o numero di un contatto"}, needs_ready=False)
    async def apri_app(a):
        if not a.get("chat"):
            opener("whatsapp:")
            return "aperta l'app di WhatsApp"
        if wa.ready():
            raise AppError("per aprire una chat precisa serve WhatsApp collegato (oppure apri l'app senza chat)")
        c = await chat_of(a)
        if c["group"]:
            raise AppError("l'app si apre sulle chat delle persone, non dei gruppi")
        opener(f"whatsapp://send?phone={c['id'].split('@')[0]}")
        return f"aperta l'app di WhatsApp sulla chat di {c['name']}: per chiamare si preme il telefono o la videocamera"

    return app
