"""OpenCode: il ripiego gratuito quando l'uso di Claude è esaurito (cervello a voce e sotto-agenti).

Perché passare da OpenCode e non chiamare Zen direttamente: la documentazione di Zen (28/10/2026) dice "Free-tier
chat models require OpenCode; use paid models or Go from other clients". I modelli gratis si usano solo
attraverso OpenCode, quindi Jarvis accende un `opencode serve` suo e gli parla con l'API HTTP (la stessa che usa
`opencode run --attach`): una sessione per conversazione o per compito, più sessioni in parallelo sullo stesso
server. Senza chiave i modelli gratis funzionano lo stesso (OpenCode usa la chiave "public"); con la chiave
dell'utente valgono i limiti del suo account.

Isolamento (come per Claude Code, `claude_cli.py`):
- cartelle di configurazione, dati, stato e cache tutte in `data/opencode` (XDG_*): niente sessioni, plugin,
  credenziali o modello predefinito dell'OpenCode che l'utente usa da terminale;
- `--pure` (niente plugin esterni), niente configurazione di progetto, niente CLAUDE.md né skill di `.claude`;
- solo il fornitore `opencode` (mai un modello a pagamento di un altro fornitore per errore);
- permessi scritti per intero in ogni agente: OpenCode legge comunque `~/.opencode/opencode.json` (verificato il
  28/10 anche con la configurazione di progetto spenta) e lì non deve poter allentare niente;
- server solo su 127.0.0.1 con una parola d'ordine nuova a ogni avvio (autenticazione HTTP di base).

Misurato il 28/10: il server parte in ~1,5 s. Il limite gratuito, una volta raggiunto, non dà un errore: OpenCode
mette la sessione in "retry" con `action.reason = "free_tier_limit"` e riproverebbe fino a mezzanotte UTC. Qui
quello stato diventa subito un BrainUnavailable con l'ora di ripristino, per tutto il fornitore.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any

import aiohttp

from .base import BrainEvent, BrainUnavailable, Done, TextDelta, ToolCall, ToolResult, ToolStart
from .mcp_bridge import McpBridge
from .tools import ToolRegistry

log = logging.getLogger("jarvis.brain.opencode")

PROVIDER = "opencode"  # il fornitore di Zen dentro OpenCode (gli ID dei modelli sono "opencode/<modello>")
USERNAME = "opencode"  # utente dell'autenticazione di base di `opencode serve`
FREE_LIMIT = "free_tier_limit"  # motivo dello stato "retry" quando l'uso gratuito di oggi è finito
# il 28/10 il limite annunciato "fino a mezzanotte UTC" alle 16:20 era già passato alle 20:04: si riprova al più
# dopo un'ora (un tentativo costa mezzo secondo: lo stato "retry" arriva subito)
FREE_RETRY_S = 3600.0
STALL_S = 45.0  # senza alcun segno di vita per tanto, la risposta si considera persa
# nessuna parola (né strumento) per tanto: modello troppo lento per la voce, si passa al successivo. Il 28/10 dal vivo
# beam-free ha parlato in 2,2 s e flint-40b-free in 3,8 s; nemotron-3.5-super-vl-free una volta 1,1 s e una volta mai
FIRST_WORD_S = 20.0
IDLE_STOP_S = 30 * 60  # server spento dopo mezz'ora senza conversazioni né compiti (libera la RAM)

# strumenti interni di OpenCode: negli agenti di Jarvis a voce sono tutti negati, nei sotto-agenti decisi qui
BUILTIN_PERMISSIONS = ("read", "edit", "glob", "grep", "list", "bash", "task", "skill", "lsp", "todowrite",
                       "todoread", "webfetch", "websearch", "codesearch", "question", "external_directory",
                       "doom_loop")
BUILTIN_TOOLS = ("bash", "edit", "write", "patch", "apply_patch", "multiedit", "read", "glob", "grep", "list", "lsp",
                 "todowrite", "todoread", "webfetch", "websearch", "codesearch", "task", "skill", "question", "batch")
# sotto-agenti: guardare è libero, tutto ciò che cambia qualcosa passa dalla politica di sicurezza di Jarvis
# (jarvis/tasks/safety.py) con le richieste di permesso di OpenCode; sotto-agenti annidati e domande no
WORKER_PERMISSIONS = {
    "*": "ask", "read": "allow", "glob": "allow", "grep": "allow", "list": "allow", "lsp": "allow",
    "todowrite": "allow", "todoread": "allow", "webfetch": "allow", "websearch": "allow", "codesearch": "allow",
    "edit": "ask", "bash": "ask", "external_directory": "ask", "doom_loop": "ask",
    "task": "deny", "skill": "deny", "question": "deny",
}
FRONT_AGENT = "jarvis"  # Jarvis a voce: solo i suoi strumenti (server MCP "jarvis")
EXAM_AGENT = "jarvis-esame"  # l'esame dei modelli gratis: solo lo strumento finto dell'esame
WORKER_AGENT = "jarvis-lavoro"  # i sotto-agenti: gli strumenti di OpenCode, le mani e "lavoro_*"
TEXT_AGENT = "jarvis-testo"  # risposte senza strumenti (il custode della memoria)
AGENT_PROMPT = ("Sei la voce di un assistente personale. Segui alla lettera le istruzioni di sistema che "
                "arrivano con ogni messaggio. Rispondi sempre in italiano.")


# ---------------------------------------------------------------------- dove sta OpenCode


def find_opencode(explicit: str | None = None) -> str:
    """L'eseguibile vero (non il .cmd di npm: chiudere cmd.exe lascerebbe vivo il server)."""
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    candidates = [Path(explicit)] if explicit else []
    candidates += [appdata / "npm" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe",
                   home / ".opencode" / "bin" / "opencode.exe"]
    on_path = shutil.which("opencode")
    if on_path:
        p = Path(on_path)
        candidates.append(p if p.suffix.lower() == ".exe" else p.parent / "node_modules" / "opencode-ai" / "bin" /
                          "opencode.exe")
    for c in candidates:
        if c.is_file():
            return str(c)
    raise FileNotFoundError("OpenCode non è installato (npm i -g opencode-ai)")


# ---------------------------------------------------------------------- la chiave dell'utente (facoltativa)


def _protect(data: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(data, "jarvis-opencode", None, None, None, 0x1)


def _unprotect(blob: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptUnprotectData(blob, None, None, None, 0x1)[1]


class ApiKeyStore:
    """La chiave API di OpenCode Zen: dalla variabile d'ambiente OPENCODE_API_KEY oppure dal file
    `data/opencode/chiave.txt`, che al primo avvio viene cifrato con DPAPI (`chiave.bin`, leggibile solo dal suo
    utente di Windows) e cancellato. Mai nei registri né nelle impostazioni. Senza chiave tutto funziona lo stesso
    con i soli modelli gratis."""

    def __init__(self, folder: Path, environ: Mapping[str, str] | None = None,
                 protect: Callable[[bytes], bytes] = _protect, unprotect: Callable[[bytes], bytes] = _unprotect) -> None:
        self.folder = folder
        self.environ = os.environ if environ is None else environ
        self.protect, self.unprotect = protect, unprotect
        self.plain, self.sealed = folder / "chiave.txt", folder / "chiave.bin"

    def get(self) -> str | None:
        key = (self.environ.get("OPENCODE_API_KEY") or "").strip()
        if key:
            return key
        if self.plain.is_file():
            key = self.plain.read_text(encoding="utf-8-sig").strip()
            if key:
                try:
                    self.sealed.write_bytes(self.protect(key.encode()))
                    self.plain.unlink()
                    log.info("chiave di OpenCode cifrata (chiave.bin) e tolta dal file in chiaro")
                except Exception as e:  # meglio una chiave in chiaro che nessuna: lo si dice nel registro
                    log.warning("non sono riuscito a cifrare la chiave di OpenCode: %s", type(e).__name__)
                return key
        if self.sealed.is_file():
            try:
                return self.unprotect(self.sealed.read_bytes()).decode().strip() or None
            except Exception as e:
                log.warning("chiave di OpenCode illeggibile (%s): proseguo con i soli modelli gratis", type(e).__name__)
        return None


# ---------------------------------------------------------------------- il processo


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_env(base: Path, password: str, api_key: str | None, environ: Mapping[str, str], config: dict) -> dict:
    """L'ambiente del server: tutto in `base`, nessuna variabile OPENCODE_* ereditata (potrebbero puntare a un'altra
    configurazione o a dei permessi), chiave solo se c'è."""
    keep = {"OPENCODE_GIT_BASH_PATH"}  # serve al bash dei sotto-agenti, non cambia permessi né modelli
    env = {k: v for k, v in environ.items() if not k.upper().startswith("OPENCODE_") or k.upper() in keep}
    for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "dati"), ("XDG_STATE_HOME", "stato"),
                     ("XDG_CACHE_HOME", "cache")):
        env[var] = str(base / sub)
    env.update({
        "OPENCODE_SERVER_PASSWORD": password,
        "OPENCODE_CONFIG_CONTENT": json.dumps(config, ensure_ascii=False),
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",  # un opencode.json nella cartella di un progetto non decide niente
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",  # niente CLAUDE.md né skill dalle cartelle .claude
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
    })
    if api_key:
        env["OPENCODE_API_KEY"] = api_key
    return env


def build_config(model: str | None, mcp: dict, worker_extra_tools: dict | None = None) -> dict:
    """La configurazione di OpenCode per Jarvis (passata in OPENCODE_CONFIG_CONTENT, la fonte più forte)."""
    front_permission = {"*": "ask", **{k: "deny" for k in BUILTIN_PERMISSIONS}}
    hidden = {"jarvis_*": False, "esame_*": False}
    servers = sorted({*mcp, "jarvis", "esame", "lavoro"})

    def voice_agent(prefix: str | None, what: str) -> dict:
        # spenti per nome (il jolly "*" insieme a "jarvis_*" non ha una precedenza documentata); ciò che sfugge
        # chiede il permesso e il cervello a voce lo rifiuta (OpenCodeBrain._special)
        tools = {**{t: False for t in BUILTIN_TOOLS}, **{f"{s}_*": False for s in servers if s != prefix}}
        if prefix:
            tools[f"{prefix}_*"] = True
        return {"mode": "primary", "description": what, "prompt": AGENT_PROMPT, "permission": dict(front_permission),
                "tools": tools}

    cfg: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False, "share": "disabled", "snapshot": False,
        "enabled_providers": [PROVIDER],  # mai un altro fornitore (es. una chiave OpenAI nell'ambiente)
        "mcp": mcp,
        "permission": dict(WORKER_PERMISSIONS),
        "agent": {
            FRONT_AGENT: voice_agent("jarvis", "Jarvis a voce"),
            EXAM_AGENT: voice_agent("esame", "esame dei modelli gratis"),
            TEXT_AGENT: voice_agent(None, "risposte senza strumenti"),
            WORKER_AGENT: {"mode": "primary", "description": "sotto-agente di Jarvis",
                           "permission": dict(WORKER_PERMISSIONS), "tools": {**hidden, **(worker_extra_tools or {})}},
        },
    }
    if model:  # anche i titoli e i riassunti con un modello gratis: senza, OpenCode ne sceglierebbe uno "economico"
        cfg["model"] = cfg["small_model"] = f"{PROVIDER}/{model}"
    return cfg


class OpenCodeProcess:
    """`opencode serve` legato a Jarvis (Job Object: muore con lui), sorvegliato dall'esterno via /global/health."""

    def __init__(self, command: list[str], base: Path, log_path: Path,
                 adopt: Callable[[int], bool] | None = None, environ: Mapping[str, str] | None = None) -> None:
        self.command = command
        self.base = base
        self.log_path = log_path
        self.adopt = adopt
        self.environ = os.environ if environ is None else environ
        self.proc: asyncio.subprocess.Process | None = None
        self.password = ""
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def _prepare(self) -> Any:
        """Cartelle e registro del server (il file resta aperto al figlio finché vive)."""
        for sub in ("config", "dati", "stato", "cache"):
            (self.base / sub).mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        return open(self.log_path, "ab")

    async def start(self, config: dict, api_key: str | None, timeout: float = 30.0) -> None:
        self.password = secrets.token_urlsafe(24)
        self.port = free_port()
        env = server_env(self.base, self.password, api_key, self.environ, config)
        out = await asyncio.to_thread(self._prepare)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.command, "serve", "--port", str(self.port), "--hostname", "127.0.0.1", "--pure",
                cwd=str(self.base), env=env, stdin=asyncio.subprocess.DEVNULL, stdout=out, stderr=out,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            out.close()  # il figlio ha la sua copia
        if self.adopt is not None:
            self.adopt(self.proc.pid)
        headers = {"Authorization": basic_auth(self.password)}
        deadline = time.monotonic() + timeout
        async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as s:
            while time.monotonic() < deadline:
                if self.proc.returncode is not None:
                    raise RuntimeError(f"OpenCode si è chiuso all'avvio (codice {self.proc.returncode}): vedi "
                                       f"{self.log_path.name}")
                with contextlib.suppress(aiohttp.ClientError, TimeoutError):
                    async with s.get(f"{self.url}/global/health") as r:
                        if r.status == 200:
                            return
                await asyncio.sleep(0.1)
        await self.stop()
        raise RuntimeError(f"OpenCode non risponde dopo {timeout:.0f} s")

    async def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.returncode is not None:
            return
        children = _children(proc.pid)  # i server MCP locali (browser, desktop): non devono restare orfani
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        for c in children:
            with contextlib.suppress(Exception):
                c.kill()


def _children(pid: int) -> list:
    try:
        import psutil

        return psutil.Process(pid).children(recursive=True)
    except Exception:
        return []


def basic_auth(password: str) -> str:
    return "Basic " + base64.b64encode(f"{USERNAME}:{password}".encode()).decode()


# ---------------------------------------------------------------------- il client HTTP


class OpenCodeHttpError(RuntimeError):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"HTTP {status}: {text[:300]}")
        self.status = status


def session_of(payload: dict) -> str | None:
    p = payload.get("properties") or {}
    return p.get("sessionID") or (p.get("part") or {}).get("sessionID") or (p.get("info") or {}).get("sessionID")


class OpenCodeClient:
    """L'API del server e un unico flusso di eventi (/global/event, tutte le cartelle) smistato per sessione."""

    def __init__(self, url: str, password: str) -> None:
        self.url = url.rstrip("/")
        self.password = password
        self._http: aiohttp.ClientSession | None = None
        self._subs: dict[str, asyncio.Queue] = {}
        self._reader: asyncio.Task | None = None
        self.connected = asyncio.Event()
        self._closing = False

    async def start(self, timeout: float = 10.0) -> None:
        self._http = aiohttp.ClientSession(headers={"Authorization": basic_auth(self.password)},
                                           timeout=aiohttp.ClientTimeout(total=None, sock_connect=5))
        self._reader = asyncio.ensure_future(self._read_events())
        try:
            await asyncio.wait_for(self.connected.wait(), timeout)
        except TimeoutError:
            await self.close()
            raise RuntimeError("OpenCode non manda eventi") from None

    async def close(self) -> None:
        self._closing = True
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
            self._reader = None
        if self._http is not None:
            await self._http.close()
            self._http = None

    @property
    def alive(self) -> bool:
        return self._http is not None and not self._closing

    def subscribe(self, sid: str) -> asyncio.Queue:
        q = self._subs.get(sid)
        if q is None:
            q = self._subs[sid] = asyncio.Queue()
        return q

    def unsubscribe(self, sid: str) -> None:
        self._subs.pop(sid, None)

    async def _read_events(self) -> None:
        delay = 0.5
        while not self._closing:
            try:
                assert self._http is not None
                async with self._http.get(f"{self.url}/global/event") as r:
                    if r.status != 200:
                        raise OpenCodeHttpError(r.status, await r.text())
                    delay = 0.5
                    buf = b""
                    async for chunk in r.content.iter_any():
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            line = line.strip()
                            if line.startswith(b"data:"):
                                self._dispatch(line[5:].strip())
            except asyncio.CancelledError:
                raise
            except Exception as e:  # server riavviato o caduto: si riprova, intanto le risposte vanno in stallo
                log.debug("flusso di eventi di OpenCode interrotto: %s", e)
            self.connected.clear()
            if self._closing:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)

    def _dispatch(self, data: bytes) -> None:
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return
        payload = obj.get("payload", obj) if isinstance(obj, dict) else {}
        if payload.get("type") == "server.connected":
            self.connected.set()
            return
        sid = session_of(payload)
        if sid is not None and sid in self._subs:
            self._subs[sid].put_nowait(payload)

    async def request(self, method: str, path: str, directory: str | None = None, body: Any = None,
                      timeout: float | None = 30.0) -> Any:
        if self._http is None:
            raise OpenCodeHttpError(0, "client chiuso")
        params = {"directory": directory} if directory else None
        async with self._http.request(method, f"{self.url}{path}", params=params, json=body,
                                      timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            text = await r.text()
            if r.status >= 400:
                raise OpenCodeHttpError(r.status, text)
            return json.loads(text) if text.strip() else None

    async def create_session(self, directory: str, title: str) -> str:
        return (await self.request("POST", "/session", directory, {"title": title}))["id"]

    async def prompt(self, sid: str, directory: str, body: dict) -> dict:
        """Manda un messaggio e aspetta la fine del giro (gli eventi arrivano intanto dal flusso)."""
        return await self.request("POST", f"/session/{sid}/message", directory, body, timeout=None)

    async def prompt_async(self, sid: str, directory: str, body: dict) -> None:
        await self.request("POST", f"/session/{sid}/prompt_async", directory, body)

    async def abort(self, sid: str, directory: str) -> None:
        with contextlib.suppress(Exception):
            await self.request("POST", f"/session/{sid}/abort", directory, timeout=10)

    async def reply(self, request_id: str, directory: str, reply: str, message: str | None = None) -> None:
        body = {"reply": reply, **({"message": message} if message else {})}
        await self.request("POST", f"/permission/{request_id}/reply", directory, body)

    async def messages(self, sid: str, directory: str, limit: int = 10) -> list:
        return await self.request("GET", f"/session/{sid}/message?limit={limit}", directory) or []

    async def providers(self, directory: str) -> dict:
        return await self.request("GET", "/provider", directory) or {}


# ---------------------------------------------------------------------- errori


def next_utc_midnight(now: float) -> float:
    return (int(now) // 86400 + 1) * 86400.0


class ModelProblem(Exception):
    """Questo modello non va (sparito, sovraccarico): si prova il successivo della classifica."""

    def __init__(self, message: str, gone: bool = False) -> None:
        super().__init__(message)
        self.gone = gone


def classify_error(err: dict | None, now: float | None = None) -> BrainUnavailable | ModelProblem | None:
    """L'errore di un messaggio di OpenCode -> cosa fare. None = nessun errore (o interruzione voluta)."""
    if not err:
        return None
    name = str(err.get("name", ""))
    data = err.get("data") or {}
    msg = str(data.get("message") or name)
    low = msg.lower()
    now = time.time() if now is None else now
    if name == "MessageAbortedError":
        return None
    if "free" in low and ("exceeded" in low or "limit" in low):  # il limite gratuito vale per tutti i modelli
        return BrainUnavailable(f"OpenCode: {msg}", retry_at=min(next_utc_midnight(now), now + FREE_RETRY_S),
                                scope="provider")
    status = data.get("statusCode")
    if name == "ProviderAuthError" or status in (401, 403):
        return BrainUnavailable(f"OpenCode: accesso rifiutato ({msg})", retry_at=now + 600, scope="provider")
    if status == 404 or "not found" in low or "modelnotfound" in low.replace(" ", ""):
        return ModelProblem(f"modello non disponibile: {msg}", gone=True)
    if status == 429 or status in (500, 502, 503, 529) or "overloaded" in low or "rate limit" in low:
        return ModelProblem(f"modello occupato: {msg}")
    return BrainUnavailable(f"OpenCode: {msg}")


def free_limit_until(status: dict) -> float | None:
    """Lo stato "retry" del limite gratuito -> l'ora di ripristino (epoch in secondi)."""
    if status.get("type") != "retry" or (status.get("action") or {}).get("reason") != FREE_LIMIT:
        return None
    nxt = status.get("next")
    return float(nxt) / 1000 if isinstance(nxt, int | float) and nxt > 0 else next_utc_midnight(time.time())


# ---------------------------------------------------------------------- dagli eventi alle parole


class TurnState:
    """Traduce gli eventi di una sessione negli eventi del cervello di Jarvis.

    Si parla solo il testo dei messaggi dell'assistente (mai la domanda dell'utente, che arriva come parte di
    testo anche lei, né il ragionamento). Le parti arrivano prima con i pezzi (`message.part.delta`) e alla fine
    intere (`message.part.updated`): si dice ciò che manca, mai due volte."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix  # "jarvis_": i nomi degli strumenti di Jarvis come li vede il resto del codice
        self.roles: dict[str, str] = {}  # messageID -> user / assistant
        self.kinds: dict[str, str] = {}  # partID -> text / reasoning / tool ...
        self.said: dict[str, int] = {}  # caratteri già detti per ogni parte di testo
        self.waiting: dict[str, list[tuple[str, dict]]] = {}  # messaggio ancora sconosciuto -> parti e pezzi
        self.early: dict[str, list[dict]] = {}  # pezzi arrivati prima della loro parte (partID -> pezzi)
        self.tool_seen: dict[str, set[str]] = {}  # callID -> stati già annunciati
        self.tool_names: dict[str, str] = {}  # callID -> nome dello strumento in OpenCode
        self.text = ""
        self.produced = False

    def name(self, tool: str) -> str:
        return tool.removeprefix(self.prefix)

    def feed(self, ev: dict) -> list[BrainEvent]:
        t = ev.get("type")
        p = ev.get("properties") or {}
        if t == "message.updated":
            info = p.get("info") or {}
            mid, role = info.get("id"), info.get("role")
            if mid and role:
                self.roles[mid] = role
                out: list[BrainEvent] = []
                for kind, item in self.waiting.pop(mid, []):
                    out += self._part(item) if kind == "part" else self._delta(item)
                return out
            return []
        if t == "message.part.updated":
            part = p.get("part") or {}
            return self._part(part)
        if t == "message.part.delta":
            return self._delta(p)
        return []

    def _part(self, part: dict) -> list[BrainEvent]:
        pid, mid, kind = part.get("id"), part.get("messageID"), part.get("type")
        if not pid or not mid:
            return []
        role = self.roles.get(mid)
        if role is None:  # di chi è il messaggio lo dirà message.updated
            self.waiting.setdefault(mid, []).append(("part", part))
            return []
        self.kinds[pid] = kind
        early = self.early.pop(pid, [])
        if role != "assistant":
            return []
        if kind == "text" and not part.get("synthetic") and not part.get("ignored"):
            out: list[BrainEvent] = []
            for d in early:
                out += self._say(pid, str(d.get("delta") or ""))
            full = str(part.get("text") or "")
            done = self.said.get(pid, 0)
            if len(full) > done:
                out += self._say(pid, full[done:])
            return out
        if kind == "tool":
            return self._tool(part)
        return []

    def _delta(self, p: dict) -> list[BrainEvent]:
        pid, mid = p.get("partID"), p.get("messageID")
        if not pid or not mid or p.get("field") != "text":
            return []
        if self.roles.get(mid) is None:
            self.waiting.setdefault(mid, []).append(("delta", p))
            return []
        if pid not in self.kinds:  # la parte non si è ancora presentata: testo o ragionamento?
            self.early.setdefault(pid, []).append(p)
            return []
        if self.roles[mid] != "assistant" or self.kinds[pid] != "text":
            return []
        return self._say(pid, str(p.get("delta") or ""))

    def _say(self, pid: str, text: str) -> list[BrainEvent]:
        if not text:
            return []
        self.said[pid] = self.said.get(pid, 0) + len(text)
        self.text += text
        self.produced = True
        return [TextDelta(text)]

    def _tool(self, part: dict) -> list[BrainEvent]:
        call, tool = str(part.get("callID") or part.get("id")), str(part.get("tool") or "")
        state = part.get("state") or {}
        status = state.get("status")
        self.tool_names[call] = tool
        seen = self.tool_seen.setdefault(call, set())
        out: list[BrainEvent] = []
        name = self.name(tool)
        if "start" not in seen:
            seen.add("start")
            out.append(ToolStart(name))
        if status in ("running", "completed", "error") and "call" not in seen:
            seen.add("call")
            out.append(ToolCall(name, dict(state.get("input") or {})))
        if status in ("completed", "error") and "result" not in seen:
            seen.add("result")
            output = str(state.get("output") if status == "completed" else state.get("error") or "")
            out.append(ToolResult(name, status == "completed" and not output.startswith("Errore"), output[:200]))
        if out:
            self.produced = True
        return out

    def finish(self, result: dict | None) -> list[BrainEvent]:
        """La risposta finale del server: ciò che il flusso non ha portato (pezzi persi) si recupera qui."""
        if not result:
            return []
        info = result.get("info") or {}
        mid = info.get("id")
        if mid:
            self.roles.setdefault(mid, info.get("role") or "assistant")
        out: list[BrainEvent] = []
        for part in result.get("parts") or []:
            if isinstance(part, dict):
                out += self._part({**part, "messageID": part.get("messageID") or mid})
        return out


# ---------------------------------------------------------------------- il servizio condiviso


class OpenCodeHub:
    """Un solo server OpenCode per Jarvis, acceso alla prima richiesta e spento dopo mezz'ora di inattività.

    Insieme al server tiene il ponte MCP (gli strumenti di Jarvis per OpenCode) e la scelta del modello gratis
    (jarvis/brain/zen.py). Chi lo usa lo segnala con `using()`: il server non si spegne né si riavvia a metà."""

    def __init__(self, base: Path, log_path: Path, picker: Any, keys: ApiKeyStore | None = None,
                 command: Callable[[], list[str]] | None = None, adopt: Callable[[int], bool] | None = None,
                 extra_mcp: Callable[[], dict] | None = None, worker_tools: Callable[[], dict] | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        self.base = base
        self.log_path = log_path
        self.picker = picker
        self.keys = keys
        self.command = command or (lambda: [find_opencode()])
        self.adopt = adopt
        self.extra_mcp = extra_mcp or dict
        self.worker_tools = worker_tools or dict
        self.environ = environ
        self.bridge = McpBridge()
        self.directory = str(base / "voce")  # la cartella delle conversazioni a voce (niente file di progetto)
        self.process: OpenCodeProcess | None = None
        self.client: OpenCodeClient | None = None
        self._config_key: str | None = None
        self._lock = asyncio.Lock()
        self._users = 0
        self.last_used = time.monotonic()

    def mount(self, name: str, registry: ToolRegistry, intercept: Any = None) -> None:
        self.bridge.mount(name, registry, intercept)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.alive and self.client is not None and self.client.alive

    def installed(self) -> bool:
        try:
            self.command()
        except FileNotFoundError:
            return False
        return True

    @contextlib.asynccontextmanager
    async def using(self) -> AsyncIterator[OpenCodeClient]:
        self._users += 1
        try:
            yield await self.ensure()
        finally:
            self._users -= 1
            self.last_used = time.monotonic()

    def _wanted(self) -> tuple[dict, str]:
        model = self.picker.best() if self.picker is not None else None
        config = build_config(model, {**self.bridge.opencode_config(), **self.extra_mcp()}, self.worker_tools())
        # si riavvia solo se cambiano strumenti o mani: il modello di riserva per i titoli non vale un riavvio
        same = {k: v for k, v in config.items() if k not in ("model", "small_model")}
        return config, json.dumps(same, sort_keys=True)

    async def ensure(self) -> OpenCodeClient:
        async with self._lock:
            await asyncio.to_thread(Path(self.directory).mkdir, parents=True, exist_ok=True)
            await self.bridge.start()
            config, key = self._wanted()
            if self.running and (key == self._config_key or self._users > 1):
                assert self.client is not None
                return self.client
            await self._stop_locked()
            try:
                command = self.command()
            except FileNotFoundError as e:
                raise BrainUnavailable(str(e)) from e
            proc = OpenCodeProcess(command, self.base, self.log_path, self.adopt, self.environ)
            try:
                await proc.start(config, self.keys.get() if self.keys is not None else None)
                client = OpenCodeClient(proc.url, proc.password)
                await client.start()
            except Exception as e:
                await proc.stop()
                raise BrainUnavailable(f"OpenCode non parte: {e}") from e
            self.process, self.client, self._config_key = proc, client, key
            log.info("OpenCode pronto su %s", proc.url)
            await self.refresh_catalog()
            return client

    async def refresh_catalog(self) -> None:
        """L'elenco dei modelli come lo vede OpenCode (dal catalogo di models.dev, aggiornato da lui)."""
        if self.client is None or self.picker is None:
            return
        try:
            self.picker.set_catalog(await self.client.providers(self.directory))
        except Exception as e:
            log.warning("elenco dei modelli di OpenCode non letto: %s", e)

    async def stop_if_idle(self, idle_s: float = IDLE_STOP_S) -> bool:
        if not self.running or self._users or time.monotonic() - self.last_used < idle_s:
            return False
        async with self._lock:
            await self._stop_locked()
        log.info("OpenCode spento: inutilizzato da %d minuti", idle_s // 60)
        return True

    async def _stop_locked(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None
        if self.process is not None:
            await self.process.stop()
            self.process = None
        self._config_key = None

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()
        await self.bridge.stop()


# ---------------------------------------------------------------------- il cervello a voce


class OpenCodeBrain:
    """Cervello di Jarvis su un modello gratis di OpenCode Zen. Stessa interfaccia degli altri (start, respond,
    interrupt, stop): una sessione OpenCode tiene il filo della conversazione, gli strumenti di Jarvis arrivano
    dal ponte MCP. Se un modello sparisce o è sovraccarico si passa al successivo della classifica (fino a
    `max_models`); il limite gratuito ferma tutto il fornitore fino all'ora di ripristino."""

    def __init__(self, system_prompt: str, hub: OpenCodeHub, models: Callable[[], list[str]],
                 enabled: Callable[[], bool] = lambda: True, name: str = "opencode", agent: str = FRONT_AGENT,
                 prefix: str = "jarvis_", max_models: int = 3, stall_s: float = STALL_S,
                 on_model_problem: Callable[[str, ModelProblem], None] | None = None, ephemeral: bool = False,
                 clock: Callable[[], float] = time.time, first_word_s: float = FIRST_WORD_S) -> None:
        self.first_word_s = first_word_s  # oltre, il modello è troppo lento per la voce: si passa al successivo
        self.ephemeral = ephemeral  # usa e getta (custode, esame): la sessione si cancella alla chiusura
        self.clock = clock
        self.name = name
        self.provider = PROVIDER
        self.system_prompt = system_prompt
        self.hub = hub
        self.models = models
        self.enabled = enabled
        self.agent = agent
        self.prefix = prefix
        self.max_models = max_models
        self.stall_s = stall_s
        self.on_model_problem = on_model_problem
        self.session_id: str | None = None
        self.model = ""
        self._lock = asyncio.Lock()
        self._interrupted = False
        self._client: OpenCodeClient | None = None

    async def start(self) -> None:
        if not self.enabled():
            raise BrainUnavailable("OpenCode è spento nelle impostazioni")
        await self.hub.ensure()  # se manca o non parte: BrainUnavailable, e il router passa oltre

    async def stop(self) -> None:
        if self._client is not None and self.session_id is not None:
            self._client.unsubscribe(self.session_id)
            if self.ephemeral:
                with contextlib.suppress(Exception):
                    await self._client.request("DELETE", f"/session/{self.session_id}", self.hub.directory, timeout=5)
        self.session_id, self._client = None, None

    async def interrupt(self) -> None:
        self._interrupted = True
        if self._client is not None and self.session_id is not None and self._lock.locked():
            await self._client.abort(self.session_id, self.hub.directory)

    async def _session(self, client: OpenCodeClient) -> str:
        if self._client is not client:  # server riavviato: la sessione resta nel suo archivio, gli eventi no
            if self._client is not None and self.session_id is not None:
                self._client.unsubscribe(self.session_id)
            self._client = client
            if self.session_id is not None:
                client.subscribe(self.session_id)
        if self.session_id is None:
            self.session_id = await client.create_session(self.hub.directory, f"Jarvis ({self.name})")
            client.subscribe(self.session_id)
        return self.session_id

    async def respond(self, message: str) -> AsyncIterator[BrainEvent]:
        if not self.enabled():
            raise BrainUnavailable("OpenCode è spento nelle impostazioni")
        async with self._lock, self.hub.using() as client:
            self._interrupted = False
            tried: list[str] = []
            last: Exception | None = None
            lost_once = False
            queue = [m for m in self.models() if m][: self.max_models]
            while queue:
                model = queue[0]
                if model not in tried:
                    tried.append(model)
                self.model = model
                state = TurnState(self.prefix)
                try:
                    async for ev in self._turn(client, model, message, state):
                        yield ev
                    return
                except ModelProblem as e:
                    last = e
                    log.warning("OpenCode, modello %s: %s", model, e)
                    if self.on_model_problem is not None:
                        self.on_model_problem(model, e)
                    if state.produced:  # a metà risposta non si ricomincia con un altro modello
                        raise BrainUnavailable(str(e)) from e
                    queue.pop(0)
                except OpenCodeHttpError as e:
                    # sessione sparita (archivio di OpenCode cancellato o server nuovo): se ne apre un'altra e la
                    # domanda si ripete una volta; il filo lo riporta il riassunto del router al giro dopo
                    if e.status == 404 and self.session_id is not None and not state.produced and not lost_once:
                        log.warning("sessione di OpenCode sparita: ne apro un'altra")
                        lost_once, self.session_id = True, None
                        continue
                    if e.status == 404:
                        self.session_id = None
                    raise BrainUnavailable(f"OpenCode: {e}") from e
            raise BrainUnavailable(f"nessun modello gratis di OpenCode disponibile ({', '.join(tried) or 'nessuno'}"
                                   f"{': ' + str(last) if last else ''})")

    def _body(self, model: str, text: str) -> dict:
        body: dict[str, Any] = {"agent": self.agent, "model": {"providerID": PROVIDER, "modelID": model},
                                "system": self.system_prompt, "parts": [{"type": "text", "text": text}]}
        variant = getattr(self.hub.picker, "variant", lambda m: None)(model) if self.hub.picker else None
        if variant:
            body["variant"] = variant  # il ragionamento più corto che il modello offre: a voce conta la prima parola
        return body

    async def _turn(self, client: OpenCodeClient, model: str, message: str,
                    state: TurnState) -> AsyncIterator[BrainEvent]:
        sid = await self._session(client)
        q = client.subscribe(sid)
        while not q.empty():  # avanzi del giro prima (un'interruzione li lascia lì)
            q.get_nowait()
        post = asyncio.ensure_future(client.prompt(sid, self.hub.directory, self._body(model, message)))
        problem: Exception | None = None
        result: dict | None = None
        first_by = time.monotonic() + self.first_word_s
        try:
            while not post.done():
                # quale attesa scade prima si decide adesso: su Windows i timer di asyncio scattano fino a ~15 ms in
                # anticipo, e rileggere l'orologio al risveglio sbagliava il motivo (visto nella suite il 28/10)
                remaining = first_by - time.monotonic()
                by_first_word = not state.produced and remaining < self.stall_s
                wait = max(0.0, remaining) if by_first_word else self.stall_s
                getter = asyncio.ensure_future(q.get())
                done, _ = await asyncio.wait({getter, post}, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    if not done:  # nessun segno di vita, o nessuna parola in tempo: a voce il silenzio non va
                        problem = (ModelProblem(f"nessuna parola dopo {self.first_word_s:.0f} s") if by_first_word
                                   else BrainUnavailable("OpenCode non risponde"))
                        await client.abort(sid, self.hub.directory)
                        break
                    continue
                ev = getter.result()
                problem = await self._special(client, sid, ev, state)
                if problem is not None:
                    await client.abort(sid, self.hub.directory)
                    break
                if not self._interrupted:  # interrotto: non si dice più niente di questo giro
                    for out in state.feed(ev):
                        yield out
            if problem is None:
                try:
                    result = await post
                except aiohttp.ClientError as e:  # server caduto a metà
                    raise BrainUnavailable(f"OpenCode: {e}") from e
            else:  # dopo l'interruzione la richiesta si chiude da sola; se non lo fa, la si abbandona
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(post, 15)
            while not q.empty() and not self._interrupted:  # ciò che è arrivato insieme alla risposta finale
                for out in state.feed(q.get_nowait()):
                    yield out
        finally:
            if not post.done():
                post.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await post
        if problem is not None:
            raise problem
        info = (result or {}).get("info") or {}
        err = classify_error(info.get("error"))
        if err is not None and not self._interrupted:
            raise err
        if not self._interrupted:
            for out in state.finish(result):
                yield out
        yield Done(state.text, interrupted=self._interrupted, model=f"{PROVIDER}/{model}")

    async def _special(self, client: OpenCodeClient, sid: str, ev: dict, state: TurnState) -> Exception | None:
        """Stato della sessione e richieste di permesso: quello che non diventa parole."""
        t = ev.get("type")
        p = ev.get("properties") or {}
        if t == "session.status":
            status = p.get("status") or {}
            until = free_limit_until(status)
            if until is not None:
                return BrainUnavailable("OpenCode: uso gratuito di oggi esaurito",
                                        retry_at=min(until, self.clock() + FREE_RETRY_S), scope="provider")
            if status.get("type") == "retry" and not state.produced:  # sovraccarico: meglio il modello dopo
                return ModelProblem(f"in attesa di riprovare: {status.get('message', '')}")
        elif t == "session.error":
            err = classify_error(p.get("error"))
            if err is not None and not self._interrupted:
                return err
        elif t == "permission.asked":
            call = (p.get("tool") or {}).get("callID")
            tool = state.tool_names.get(call or "", "") or str(p.get("permission", ""))
            ok = tool.startswith(self.prefix)
            await client.reply(p["id"], self.hub.directory, "once" if ok else "reject",
                               None if ok else "Strumento non disponibile in questa conversazione.")
        return None
