"""Sotto-agenti su OpenCode: lavorano SOLO quando l'uso di Claude è esaurito.

Di norma i compiti restano a Claude Code (`claude_worker.py`). `FallbackWorker` sceglie a ogni compito: se Claude è
esaurito (lo sa il cervello a voce, oppure un sotto-agente che ha appena sbattuto contro il limite) il compito va
a OpenCode; se il limite arriva a metà lavoro, il compito continua su OpenCode con l'avviso di non rifare ciò che
è già stato fatto.

Sicurezza: identica ai sotto-agenti Claude. OpenCode ha i permessi in "chiedi" per tutto ciò che cambia qualcosa
(jarvis/brain/opencode.py, WORKER_PERMISSIONS); ogni richiesta arriva qui dal flusso di eventi, viene tradotta
nello strumento equivalente di Claude Code (bash -> Bash, edit -> Edit, browser_browser_click ->
mcp__browser__browser_click...) e passa dalla stessa `SafetyPolicy`, dalle stesse regole delle mani (desktop a un
compito alla volta, mai tasti alla finestra di Jarvis) e dalla stessa conferma a voce (`hooks.confirm`). Nessuna
risposta = negata. Mai un sì dato da qui senza la politica o l'utente.

Una sessione OpenCode per compito, tutte sullo stesso server: i compiti in parallelo (oggi fino a 3) vanno insieme.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..brain.claude import classify_error as claude_limit
from ..brain.opencode import PROVIDER, WORKER_AGENT, OpenCodeHub, free_limit_until
from ..config import HandsConfig
from . import hands
from .claude_worker import WORKER_PROMPT, _describe_tool
from .hands import GUIDE, PLAYWRIGHT_MCP, WINDOWS_MCP, WINDOWS_TOOLS, DesktopLease, needs_desktop
from .manager import Task, WorkerHooks
from .safety import SafetyPolicy

log = logging.getLogger("jarvis.worker.opencode")

# gli strumenti interni di OpenCode con il nome che hanno in Claude Code (la politica di sicurezza parla così)
BUILTIN_NAMES = {"bash": "Bash", "edit": "Edit", "write": "Write", "patch": "Edit", "apply_patch": "Edit",
                 "multiedit": "MultiEdit", "read": "Read", "glob": "Glob", "grep": "Grep", "list": "LS",
                 "webfetch": "WebFetch", "websearch": "WebSearch", "codesearch": "WebSearch", "todowrite": "TodoWrite",
                 "todoread": "TodoWrite", "task": "Task", "skill": "Skill", "lsp": "Read"}
MCP_SERVERS = ("browser", "windows", "lavoro", "jarvis", "esame")  # in OpenCode: server_strumento
QUIET = {"TodoWrite", "mcp__lavoro__chiedi_conferma", "mcp__lavoro__riferisci"}
APPROVED = "APPROVATA"
DENIED = "NEGATA: non procedere e trova un'alternativa o fermati"
NOT_APPROVED = "L'utente non ha approvato questa azione. Non riprovarla: cerca un'alternativa sicura o concludi spiegando il motivo."
RESUME_NOTE = ("\n\nNota: un altro assistente aveva cominciato questo compito e si è fermato a metà (limite d'uso). "
               "Prima di fare qualcosa guarda cosa esiste già nella cartella di lavoro e non ripetere azioni già "
               "fatte, soprattutto invii, pubblicazioni o acquisti.")
STATUS_POLL_S = 60.0  # se il flusso di eventi perde la fine, la si scopre chiedendo lo stato
EXHAUSTED_GUESS_S = 3600.0  # limite di Claude senza ora di ripristino: si riprova Claude dopo un'ora

OPENCODE_PROMPT = (WORKER_PROMPT.replace("`riferisci`", "`lavoro_riferisci`")
                   .replace("`chiedi_conferma`", "`lavoro_chiedi_conferma`")
                   .replace("Hai ricevuto un compito", "Hai ricevuto il compito #{task}")
                   + "- Se un'azione ti viene rifiutata, non riprovarla uguale: cerca un'alternativa sicura o concludi "
                     "spiegando il motivo.\n")


def claude_name(tool: str) -> str:
    """Il nome dello strumento come in Claude Code: bash -> Bash, windows_Click -> mcp__windows__Click."""
    if tool in BUILTIN_NAMES:
        return BUILTIN_NAMES[tool]
    server, _, rest = tool.partition("_")
    if rest and server in MCP_SERVERS:
        return f"mcp__{server}__{rest}"
    return tool


def claude_args(inp: dict) -> dict:
    """Gli argomenti con i nomi di Claude Code (filePath -> file_path), il resto com'è."""
    out = dict(inp)
    path = inp.get("filePath") or inp.get("file_path") or inp.get("path")
    if path and "file_path" not in out:
        out["file_path"] = path
    return out


def opencode_hands(cfg: HandsConfig, user: str) -> tuple[dict, list[str]]:
    """Le mani per OpenCode come server MCP locali: browser usa e getta e desktop. Il browser dell'utente (Claude in
    Chrome) esiste solo dentro Claude Code: qui non c'è."""
    mcp: dict[str, dict] = {}
    guide: list[str] = []
    node = shutil.which("node")
    if cfg.disposable_browser and node and PLAYWRIGHT_MCP.exists():
        cmd = [node, str(PLAYWRIGHT_MCP), "--isolated", "--browser", cfg.disposable_browser_channel]
        if not cfg.disposable_browser_visible:
            cmd.append("--headless")
        mcp["browser"] = {"type": "local", "command": cmd, "enabled": True}
        guide.append(GUIDE["disposable_browser"])
    if cfg.desktop and WINDOWS_MCP.exists():
        mcp["windows"] = {"type": "local", "enabled": True,
                          "command": [str(WINDOWS_MCP), "serve", "--transport", "stdio", "--tools", ",".join(WINDOWS_TOOLS)],
                          "environment": {"ANONYMIZED_TELEMETRY": "false", "WINDOWS_MCP_SCREENSHOT_SCALE": "0.8"}}
        guide.append(GUIDE["desktop"])
    return mcp, [g.replace("{user}", user) for g in guide]


def worker_tools() -> dict:
    """Strumenti spenti per i sotto-agenti OpenCode (oltre a quelli di Jarvis a voce e dell'esame)."""
    return {f"browser_{t.removeprefix('mcp__browser__')}": False for t in hands.BLOCKED_TOOLS}


# ---------------------------------------------------------------------- conferme chieste dal modello


class ConfirmDesk:
    """Lo strumento `lavoro_chiedi_conferma` (server MCP "lavoro" sul ponte di Jarvis).

    La conferma la chiede il sotto-agente quando arriva la richiesta di permesso per quello strumento (lì si sa di
    quale compito è). Lo strumento risponde APPROVATA solo se quella stessa richiesta è appena stata approvata
    dall'utente; se il permesso non fosse passato da Jarvis, cerca il compito che sta facendo proprio quella
    chiamata e chiede da lì. In ogni altro caso: NEGATA."""

    def __init__(self, wait_s: float = 2.0) -> None:
        self.workers: set[OpenCodeWorker] = set()
        self._approved: list[tuple[str, float]] = []
        self.wait_s = wait_s

    @staticmethod
    def _key(args: dict) -> str:
        return json.dumps(args, sort_keys=True, ensure_ascii=False)

    def approve(self, args: dict) -> None:
        self._approved.append((self._key(args), time.monotonic()))

    def take(self, args: dict) -> bool:
        now, key = time.monotonic(), self._key(args)
        self._approved = [(k, at) for k, at in self._approved if now - at < 600]
        for i, (k, _) in enumerate(self._approved):
            if k == key:
                del self._approved[i]
                return True
        return False

    async def intercept(self, tool: str, args: dict) -> str | None:
        if tool == "riferisci":
            return "ricevuto"  # l'aggiornamento arriva al compito dal flusso di eventi
        if tool != "chiedi_conferma":
            return None
        if self.take(args):
            return APPROVED
        deadline = time.monotonic() + self.wait_s
        while True:
            owners = [w for w in self.workers if w.running_call("lavoro_chiedi_conferma", args)]
            if len(owners) == 1:
                return APPROVED if await owners[0].confirm_text(args) else DENIED
            if owners or time.monotonic() >= deadline:
                return DENIED  # nessuno o più compiti con la stessa richiesta: mai indovinare
            await asyncio.sleep(0.05)


# ---------------------------------------------------------------------- il sotto-agente


class OpenCodeWorker:
    def __init__(self, task: Task, user: str, hub: OpenCodeHub, safety: SafetyPolicy, models: Callable[[], list[str]],
                 desk: ConfirmDesk, guide: list[str] | None = None, desktop: DesktopLease | None = None,
                 note: str = "") -> None:
        self.task = task
        self.user = user
        self.hub = hub
        self.safety = safety
        self.models = models
        self.desk = desk
        self.guide = guide or []
        self.desktop = desktop
        self.note = note
        self.model = ""
        self.sid: str | None = None
        self._client: Any = None
        self._hooks: WorkerHooks | None = None
        self._parts: dict[str, dict] = {}  # callID -> ultima versione della parte "tool"
        self._announced: set[str] = set()
        self._approved: list[tuple[str, float]] = []
        self._inbox: list[str] = []
        self._cancelled = False
        self._busy_seen = False
        self._error: str | None = None

    # ------------------------------------------------------------ interfaccia di TaskManager

    def prompt(self) -> str:
        hands_text = ""
        if self.guide:
            hands_text = ("\nStrumenti per agire sul mondo esterno, oltre a shell, file e web:\n" + "\n".join(self.guide)
                          + "\n")
        return OPENCODE_PROMPT.format(user=self.user, cwd=self.task.cwd, hands=hands_text, task=self.task.id)

    def _body(self, text: str) -> dict:
        return {"agent": WORKER_AGENT, "model": {"providerID": PROVIDER, "modelID": self.model},
                "system": self.prompt(), "parts": [{"type": "text", "text": text}]}

    async def run(self, task: Task, hooks: WorkerHooks) -> str:
        self._hooks = hooks
        await asyncio.to_thread(Path(task.cwd).mkdir, parents=True, exist_ok=True)
        models = [m for m in self.models() if m]
        if not models:
            raise RuntimeError("nessun modello gratis di OpenCode disponibile")
        self.model = models[0]
        directory = str(task.cwd)
        self.desk.workers.add(self)
        try:
            async with self.hub.using() as client:
                self._client = client
                self.sid = await client.create_session(directory, f"Compito #{task.id}: {task.title}")
                q = client.subscribe(self.sid)
                try:
                    await client.prompt_async(self.sid, directory, self._body(f"Compito: {task.goal}{self.note}"))
                    hooks.progress(f"lavoro con OpenCode ({self.model})")
                    return await self._follow(q, directory)
                finally:
                    client.unsubscribe(self.sid)
        finally:
            self.desk.workers.discard(self)
            self._close()

    async def send(self, text: str) -> None:
        msg = f"Messaggio dell'utente (tramite JARVIS) durante il lavoro: {text}"
        if self._client is None or self.sid is None:
            self._inbox.append(msg)
            return
        try:  # OpenCode lo mette in coda e il modello lo legge al passo successivo
            await self._client.prompt_async(self.sid, str(self.task.cwd), self._body(msg))
        except Exception as e:
            log.warning("messaggio al compito #%s rimandato: %s", self.task.id, e)
            self._inbox.append(msg)

    async def cancel(self) -> None:
        self._cancelled = True
        if self._client is not None and self.sid is not None:
            await self._client.abort(self.sid, str(self.task.cwd))
        self._close()

    def _close(self) -> None:
        if self.desktop is not None:
            self.desktop.release(self.task.id)

    # ------------------------------------------------------------ eventi

    async def _follow(self, q: asyncio.Queue, directory: str) -> str:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), STATUS_POLL_S)
            except TimeoutError:  # niente eventi da un minuto: il lavoro è ancora in corso?
                if self._busy_seen and not await self._still_busy(directory):
                    ev = {"type": "session.idle", "properties": {"sessionID": self.sid}}
                else:
                    continue
            t = ev.get("type")
            p = ev.get("properties") or {}
            if t == "message.part.updated":
                self._on_part(p.get("part") or {})
            elif t == "permission.asked":
                await self._on_permission(p, directory)
            elif t == "session.status":
                status = p.get("status") or {}
                if status.get("type") == "busy":
                    self._busy_seen = True
                until = free_limit_until(status)
                if until is not None:
                    await self._client.abort(self.sid, directory)
                    raise RuntimeError("uso gratuito di OpenCode esaurito per ora (OpenCode indica le "
                                       + time.strftime("%H:%M", time.localtime(until)) + ", spesso torna prima)")
                if status.get("type") == "idle" and self._busy_seen:
                    ev = {"type": "session.idle"}
                    t = "session.idle"
            elif t == "session.error":
                err = p.get("error") or {}
                if not self._cancelled and err.get("name") != "MessageAbortedError":
                    self._error = str((err.get("data") or {}).get("message") or err.get("name") or "errore")
            elif t == "message.updated" and (p.get("info") or {}).get("role") == "assistant":
                self._busy_seen = True
            if t == "session.idle" and self._busy_seen:
                if self._inbox and not self._cancelled and self._error is None:
                    pending, self._inbox = self._inbox, []
                    for msg in pending:
                        await self._client.prompt_async(self.sid, directory, self._body(msg))
                    self._busy_seen = False
                    continue
                return await self._summary(directory)

    async def _still_busy(self, directory: str) -> bool:
        try:
            status = await self._client.request("GET", "/session/status", directory) or {}
        except Exception:
            return True  # nel dubbio si aspetta ancora
        return (status.get(self.sid) or {}).get("type") in ("busy", "retry")

    async def _summary(self, directory: str) -> str:
        if self._cancelled:
            raise asyncio.CancelledError
        if self._error is not None:
            raise RuntimeError(self._error)
        for m in reversed(await self._client.messages(self.sid, directory, limit=20)):
            if (m.get("info") or {}).get("role") == "assistant":
                text = "".join(str(p.get("text") or "") for p in m.get("parts") or []
                               if p.get("type") == "text" and not p.get("synthetic"))
                if text.strip():
                    return text.strip()
        return "fatto, senza riepilogo"

    def _on_part(self, part: dict) -> None:
        if part.get("type") != "tool":
            return
        call = str(part.get("callID") or part.get("id"))
        self._parts[call] = part
        state = part.get("state") or {}
        if state.get("status") not in ("running", "completed") or call in self._announced or self._hooks is None:
            return
        self._announced.add(call)
        tool, inp = str(part.get("tool") or ""), dict(state.get("input") or {})
        if tool == "lavoro_riferisci":
            self._hooks.progress(str(inp.get("testo") or ""))
            return
        name = claude_name(tool)
        if name not in QUIET:
            self._hooks.progress(_describe_tool(name, claude_args(inp)))

    def running_call(self, tool: str, args: dict) -> bool:
        return any(p.get("tool") == tool and (p.get("state") or {}).get("status") in ("pending", "running")
                   and dict((p.get("state") or {}).get("input") or {}) == args for p in self._parts.values())

    async def confirm_text(self, args: dict) -> bool:
        assert self._hooks is not None
        text = str(args.get("azione") or "azione delicata")
        if args.get("motivo"):
            text += f" (motivo: {args['motivo']})"
        ok = await self._hooks.confirm(text)
        if ok:
            self._approved.append((text, time.monotonic()))
        return ok

    # ------------------------------------------------------------ permessi

    async def _part_for(self, req: dict, directory: str) -> dict | None:
        ref = req.get("tool") or {}
        call = ref.get("callID")
        if not call:
            return None
        if call in self._parts:
            return self._parts[call]
        try:  # la parte non è ancora arrivata dal flusso: la si chiede
            msg = await self._client.request("GET", f"/session/{self.sid}/message/{ref.get('messageID')}", directory)
        except Exception:
            return None
        for part in (msg or {}).get("parts") or []:
            if part.get("callID") == call:
                self._parts[call] = part
                return part
        return None

    async def _on_permission(self, req: dict, directory: str) -> None:
        try:
            reply, message = await self.decide(req, await self._part_for(req, directory))
        except Exception as e:  # mai un sì per un errore nostro
            log.exception("permesso del compito #%s", self.task.id)
            reply, message = "reject", f"controllo di sicurezza non riuscito: {e}"
        await self._client.reply(req["id"], directory, reply, message)

    async def decide(self, req: dict, part: dict | None) -> tuple[str, str | None]:
        """Una richiesta di permesso di OpenCode -> ("once", None) oppure ("reject", motivo per il modello)."""
        perm = str(req.get("permission") or "")
        tool = str((part or {}).get("tool") or perm)
        inp = dict(((part or {}).get("state") or {}).get("input") or {})
        if perm == "doom_loop":
            return "reject", "Stai ripetendo la stessa azione con lo stesso input: cambia strada o concludi."
        if perm == "external_directory":
            return "once", None  # guardare fuori è libero; scrivere o eseguire chiede il suo permesso, controllato
        if tool == "lavoro_riferisci":
            return "once", None
        if tool == "lavoro_chiedi_conferma":
            if await self.confirm_text(inp):
                self.desk.approve(inp)
                return "once", None
            return "reject", DENIED
        if tool.startswith(("jarvis_", "esame_")):
            return "reject", "Strumento non disponibile ai sotto-agenti."
        name = claude_name(tool)
        args = claude_args(inp) if inp else self._args_from_request(perm, req)
        if name in hands.BLOCKED_TOOLS:
            return "reject", "Strumento bloccato: esegue codice sul PC fuori da ogni controllo."
        if self.desktop is not None and needs_desktop(name) and not self.desktop.acquire(self.task.id):
            return "reject", (f"Il desktop è occupato dal compito #{self.desktop.owner}: non usarlo ora. Trova un'altra "
                              "strada (riga di comando, file, browser) oppure concludi spiegando che serve il desktop.")
        if hands.keys_would_hit_jarvis(name, args):
            return "reject", ("In primo piano c'è la finestra di Jarvis, non l'app che vuoi: i tasti finirebbero lì. "
                              "Porta davanti l'app giusta, controlla con Snapshot chi ha il fuoco e poi riprova.")
        verdicts = [self.safety.check(name, a, cwd=self.task.cwd) for a in self._targets(name, args)]
        asks = [v for v in verdicts if not v.allow]
        if not asks:
            return "once", None
        if any(not v.ask for v in asks):
            return "reject", next(v.reason for v in asks if not v.ask) or "azione non consentita"
        reason = "; ".join(dict.fromkeys(v.reason for v in asks))
        if self._already_approved(args):
            return "once", None
        assert self._hooks is not None
        if await self._hooks.confirm(reason):
            self._approved.append((reason, time.monotonic()))
            return "once", None
        return "reject", NOT_APPROVED

    def _targets(self, name: str, args: dict) -> list[dict]:
        """Per le modifiche ai file: ogni file con il percorso completo (i relativi partono dalla cartella del
        compito), così "dentro la cartella di lavoro" si decide sul file vero."""
        if name not in ("Edit", "Write", "MultiEdit") or not args.get("file_path"):
            return [args]
        files = args["file_path"] if isinstance(args["file_path"], list) else [args["file_path"]]
        out = []
        for f in files:
            p = Path(str(f))
            out.append({**args, "file_path": str(p if p.is_absolute() else Path(self.task.cwd) / p)})
        return out

    @staticmethod
    def _args_from_request(perm: str, req: dict) -> dict:
        """Senza la parte dello strumento: quello che dice la richiesta stessa (comandi o file). I percorsi relativi
        delle richieste partono dalla radice del progetto di OpenCode, che per una cartella senza git è "/" (visto
        il 28/10: "Users/<nome>/..."): si leggono da lì, mai dalla cartella del compito. Se la radice era un'altra
        il file risulta fuori dalle cartelle di lavoro e si chiede: un sì in più, mai un permesso di troppo."""
        meta = req.get("metadata") or {}
        patterns = [str(x) for x in req.get("patterns") or []]
        if perm == "bash":
            return {"command": str(meta.get("command") or " ; ".join(patterns))}
        if perm == "edit":
            path = meta.get("filepath") or meta.get("filePath")
            if path:
                return {"file_path": str(path)}
            return {"file_path": [p if Path(p).is_absolute() else str(Path("/") / p) for p in patterns]}
        return dict(meta)

    def _already_approved(self, args: dict) -> bool:
        """Vero se l'utente ha appena approvato proprio questa azione (stesso file o stesso comando)."""
        target = str(args.get("file_path") or args.get("command") or "").strip()
        if not target:
            return False
        now = time.monotonic()
        self._approved = [(t, at) for t, at in self._approved if now - at < 600]
        norm = target.replace("/", "\\").lower()
        return any(norm in t.replace("/", "\\").lower() for t, _ in self._approved)


# ---------------------------------------------------------------------- quando usare OpenCode


class ClaudeQuota:
    """Fino a quando l'uso di Claude è esaurito: lo sa il cervello a voce (in pausa per il limite dell'account) o
    un sotto-agente che ci ha appena sbattuto contro."""

    def __init__(self, router_until: Callable[[], float | None] = lambda: None,
                 clock: Callable[[], float] = time.time) -> None:
        self.router_until = router_until
        self.clock = clock
        self._until = 0.0

    def mark(self, until: float | None) -> None:
        self._until = max(self._until, until if until and until > self.clock() else self.clock() + EXHAUSTED_GUESS_S)

    def exhausted_until(self) -> float | None:
        now = self.clock()
        found = [u for u in (self.router_until(), self._until) if u and u > now]
        return max(found) if found else None


class FallbackWorker:
    """Claude Code finché c'è; OpenCode quando Claude è esaurito (prima di cominciare o a metà lavoro)."""

    def __init__(self, task: Task, claude: Callable[[], Any], opencode: Callable[[str], Any] | None,
                 quota: ClaudeQuota, enabled: Callable[[], bool]) -> None:
        self.task = task
        self.claude = claude
        self.opencode = opencode  # riceve la nota da aggiungere all'obiettivo
        self.quota = quota
        self.enabled = enabled
        self.current: Any = None
        self._cancelled = False

    def _can_switch(self) -> bool:
        return self.opencode is not None and self.enabled() and not self._cancelled

    async def run(self, task: Task, hooks: WorkerHooks) -> str:
        if self.quota.exhausted_until() is not None and self._can_switch():
            assert self.opencode is not None
            self.current = self.opencode("")
            return await self.current.run(task, hooks)
        self.current = self.claude()
        try:
            return await self.current.run(task, hooks)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            limit = claude_limit(str(e))
            if limit is None or limit.scope != "provider" or not self._can_switch():
                raise
            self.quota.mark(limit.retry_at)
            assert self.opencode is not None
            hooks.progress("Claude ha esaurito l'uso: continuo il compito con un modello gratuito di OpenCode")
            self.current = self.opencode(RESUME_NOTE)
            return await self.current.run(task, hooks)

    async def send(self, text: str) -> None:
        if self.current is not None:
            await self.current.send(text)

    async def cancel(self) -> None:
        self._cancelled = True
        if self.current is not None:
            with contextlib.suppress(Exception):
                await self.current.cancel()
