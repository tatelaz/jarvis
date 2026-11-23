"""Le abilità: gesti pronti per usare le app del PC (WhatsApp, Gmail, Steam e giochi) senza sotto-agenti.

Il cervello vede un solo strumento (usa_app) con un catalogo corto: meno di mille token, contro i ~20.000 a passo
di un sotto-agente che guarda lo schermo. I dettagli di un'app arrivano solo quando servono (azione "aiuto"), e le
app lavorano in background: niente finestre in primo piano, niente mouse.

Il codice, non il cervello, decide:
- chi può usare cosa: i messaggi e la posta sono solo dell'utente (gruppo "messaggi"), i giochi seguono il permesso "pc";
- le azioni delicate (inviare, cancellare, chiudere un gioco): restano in sospeso con un codice e partono solo se, DOPO
  la richiesta, chi ha il permesso ha risposto con un sì chiaro. Il cervello non può confermarsi da solo nello stesso
  turno, e parte l'azione salvata, non una nuova: destinatario e testo non cambiano dopo il sì.
Ciò che le abilità non coprono lo fa un sotto-agente (avvia_compito).
"""

from __future__ import annotations

import asyncio
import difflib
import inspect
import re
import secrets
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Result = str | Awaitable[str]


class AppError(Exception):
    """Errore che si spiega da solo (app non collegata, chat che non esiste...): torna al cervello come testo."""


@dataclass
class Action:
    name: str
    summary: str  # una riga, per l'aiuto dell'app
    run: Callable[[dict[str, Any]], Result]
    params: dict[str, str] = field(default_factory=dict)  # nome -> descrizione; "?" in testa = facoltativo
    # azione delicata: riceve una copia dei parametri (può aggiungerci ciò che ha risolto, es. la chat esatta) e
    # restituisce cosa si sta per fare, in parole da leggere all'utente
    confirm: Callable[[dict[str, Any]], Result] | None = None
    needs_ready: bool = True  # False = funziona anche con l'app non pronta (es. "collega")
    # chiede il sì anche con le conferme delle azioni delicate spente (le segnalazioni: escono dal PC verso altri)
    always_confirm: bool = False

    def required(self) -> list[str]:
        return [k for k, v in self.params.items() if not v.startswith("?")]

    def signature(self) -> str:
        args = [k if not v.startswith("?") else f"[{k}]" for k, v in self.params.items()]
        return f"{self.name}({', '.join(args)})" if args else self.name


@dataclass
class App:
    name: str
    title: str
    group: str  # permesso che serve: "messaggi" (solo l'utente) o "pc"
    notes: str = ""  # come usarla bene: arriva al cervello con l'aiuto
    ready: Callable[[], str | None] = lambda: None  # None = pronta; altrimenti cosa manca, detto per il cervello
    aliases: tuple[str, ...] = ()
    actions: dict[str, Action] = field(default_factory=dict)

    def action(self, name: str, summary: str, params: dict[str, str] | None = None,
               confirm: Callable[[dict[str, Any]], Result] | None = None, needs_ready: bool = True,
               always_confirm: bool = False):
        def deco(fn):
            if name in self.actions:
                raise ValueError(f"azione duplicata: {self.name}.{name}")
            self.actions[name] = Action(name, summary, fn, params or {}, confirm, needs_ready, always_confirm)
            return fn
        return deco

    def help(self) -> str:
        why = self.ready()
        lines = [f"{self.title}" + (f" — NON PRONTA: {why}" if why else "")]
        if self.notes:
            lines.append(self.notes)
        for a in self.actions.values():
            extra = " [chiede conferma]" if a.confirm else ""
            lines.append(f"- {a.signature()}: {a.summary}{extra}")
            lines += [f"    {k}: {v.lstrip('?').strip()}" for k, v in a.params.items()]
        return "\n".join(lines)


@dataclass
class Pending:
    code: str
    app: str
    action: str
    params: dict[str, Any]
    text: str
    turn: int
    at: float


# un sì chiaro, e niente che lo smentisca o chieda di cambiare qualcosa (meglio richiedere che inviare il testo vecchio)
# il 27/10 "Va benissimo." e un "sì" trascritto "Se" non passavano: la lista era troppo stretta
_YES = re.compile(r"\b(s[iìí]|ok(ay)?|vai|certo|certamente|conferm\w*|procedi|fallo|fai pure|esatto|giusto|corretto|"
                  r"va bene|va benissimo|benissimo|bene|ottimo|perfetto|d'accordo|sicuro|assolutamente|affermativo|"
                  r"come no|volentieri|dai|invia\w*|manda\w*|chiudi\w*|cancella\w*|elimina\w*|disinstall\w*|"
                  r"yes|yeah|yep|sure)\b", re.I)
# un "sì" detto da solo che il riconoscimento vocale scrive "se" (da solo "se" non vuol dire altro)
_ONLY_SE = re.compile(r"^\W*s[eéè]\W*$", re.I)
_NOT_YES = re.compile(r"^\W*(no|non|aspetta|annulla|fermo|ferma|stop|lascia|niente)\b"
                      r"|\b(cambia|modifica|invece|correggi|aspetta|non (inviare|mandare|farlo|chiudere|cancellare))\b"
                      r"|\bno\W*$", re.I)


def affirmative(text: str) -> bool:
    t = text or ""
    return bool(_YES.search(t) or _ONLY_SE.match(t)) and not _NOT_YES.search(t)


_NUMBERS = {"uno": "1", "due": "2", "tre": "3", "quattro": "4", "cinque": "5", "sei": "6", "sette": "7", "otto": "8",
            "nove": "9", "dieci": "10", "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def norm(s: str) -> str:
    """Per confrontare nomi detti a voce: niente accenti, emoji e punteggiatura, "tre" = "3", "III" = "3"."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return " ".join(_NUMBERS.get(w, w) for w in re.sub(r"[^a-z0-9]+", " ", s).split())


def pick_by_name[T](query: str, items: list[T], name: Callable[[T], str], plural: str, empty: str, missing: str) -> T:
    """La cosa (gioco, chat...) che l'utente intende col nome detto a voce: uguale, poi che inizia così, che lo
    contiene, con tutte le parole, infine somigliante. Se più cose vanno bene si chiede quale: mai indovinare."""
    q = norm(query)
    if not q:
        raise AppError(empty)
    names = [norm(name(x)) for x in items]
    flat = q.replace(" ", "")
    tiers = ([x for x, n in zip(items, names, strict=True) if n == q],
             [x for x, n in zip(items, names, strict=True) if n.startswith(q)],
             [x for x, n in zip(items, names, strict=True) if flat in n.replace(" ", "")],
             [x for x, n in zip(items, names, strict=True) if all(w in n.split() for w in q.split())])
    for found in tiers:
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise AppError(f"più {plural} corrispondono a '{query}': {', '.join(name(x) for x in found[:8])}. "
                           "Chiedi quale.")
    by_name = {n: x for x, n in zip(items, names, strict=True)}
    close = difflib.get_close_matches(q, list(by_name), n=3, cutoff=0.6)
    if len(close) == 1:
        return by_name[close[0]]
    if close:
        raise AppError(f"forse intendi: {', '.join(name(by_name[c]) for c in close)}. Chiedi quale.")
    raise AppError(missing)


def _key(s: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(s or "").strip().lower())


class Apps:
    """Le app con le loro azioni, i permessi e le conferme in sospeso."""

    CONFIRM_TTL_S = 600.0

    def __init__(self, apps: list[App], may: Callable[[str], str | None] = lambda group: None,
                 turns: Callable[[], int] = lambda: 0, last_text: Callable[[], str] = lambda: "",
                 confirm: Callable[[], bool] = lambda: True, clock: Callable[[], float] = time.monotonic) -> None:
        self.apps = {a.name: a for a in apps}
        self.may = may  # None se chi parla ha il permesso del gruppo, altrimenti il rifiuto (jarvis/identity/guard.py)
        self.turns = turns  # quanti turni dell'utente ci sono stati finora
        self.last_text = last_text  # l'ultima frase dell'utente
        self.confirm_on = confirm  # safety.confirm_destructive (riletta ogni volta)
        self.clock = clock
        self.pending: dict[str, Pending] = {}

    def catalog(self) -> str:
        """Il catalogo corto per la descrizione dello strumento: le app e i nomi delle azioni."""
        rows = []
        for a in self.apps.values():
            who = " (solo l'utente)" if a.group == "messaggi" else ""
            rows.append(f"{a.name}{who}: {', '.join(x.signature() for x in a.actions.values())}")
        return "; ".join(rows)

    def find(self, name: str) -> App | None:
        k = _key(name)
        for a in self.apps.values():
            if k == a.name or k in a.aliases:
                return a
        return None

    async def use(self, app_name: str, action: str, params: dict[str, Any] | None = None,
                  code: str | None = None) -> str:
        app = self.find(app_name)
        if app is None:
            return f"Errore: app sconosciuta '{app_name}'. Ci sono: {', '.join(self.apps)}"
        if why := self.may(app.group):
            return why
        name = _key(action)
        if name == "aiuto":
            return app.help()
        act = app.actions.get(name)
        if act is None:
            return f"Errore: {app.name} non ha l'azione '{action}'. Azioni: {', '.join(app.actions)} (oppure aiuto)"
        if act.needs_ready and (why := app.ready()):
            return f"Errore: {app.title} non è pronta: {why}"
        params = dict(params or {})
        delicate = act.confirm is not None and (act.always_confirm or self.confirm_on())
        if delicate and code:
            outcome = self._confirmed(app, act, str(code))
            if isinstance(outcome, str):
                return outcome
            params = outcome.params  # quella salvata: niente parametri nuovi dopo il sì
        else:
            missing = [k for k in act.required() if params.get(k) in (None, "")]
            if missing:
                return f"Errore: per {app.name}.{act.name} servono: {', '.join(missing)} (vedi aiuto)"
            if delicate:
                return await self._ask(app, act, params)
        try:
            return await self._call(act.run, params)
        except AppError as e:
            return f"Errore: {e}"

    async def _ask(self, app: App, act: Action, params: dict[str, Any]) -> str:
        self._expire()
        stored = dict(params)
        try:
            text = await self._call(act.confirm, stored)
        except AppError as e:
            return f"Errore: {e}"
        code = secrets.token_hex(2)
        while code in self.pending:
            code = secrets.token_hex(2)
        self.pending[code] = Pending(code, app.name, act.name, stored, text, self.turns(), self.clock())
        return (f"DA CONFERMARE (codice {code}): {text}. Chiedi all'utente se procedere, senza cambiare destinatario "
                f"né contenuto. Solo dopo un suo sì esplicito richiama usa_app con app {app.name}, azione {act.name} e "
                f"conferma \"{code}\"; se vuole cambiare qualcosa rifai la richiesta (nascerà un codice nuovo).")

    def _confirmed(self, app: App, act: Action, code: str) -> Pending | str:
        self._expire()
        p = self.pending.get(code.strip().lower())
        if p is None:
            return "Errore: codice di conferma sconosciuto o scaduto: rifai la richiesta."
        if (p.app, p.action) != (app.name, act.name):
            return f"Errore: il codice {p.code} è per {p.app}.{p.action}, non per {app.name}.{act.name}."
        if self.turns() <= p.turn:
            return ("Errore: l'utente non ha ancora risposto. Leggigli cosa stai per fare e richiama solo dopo il suo sì: "
                    "non si conferma da soli.")
        del self.pending[p.code]  # una risposta sola: dopo un no, un sì detto più tardi non la riporta in vita
        said = self.last_text()
        if not affirmative(said):
            return (f"Errore: l'ultima risposta dell'utente non è un sì chiaro (\"{said[:80]}\"): non ho fatto niente "
                    "e la richiesta è annullata. Se vuole cambiare qualcosa rifai la richiesta; se ha detto di no "
                    "lascia perdere.")
        return p

    def _expire(self) -> None:
        now = self.clock()
        for k in [k for k, p in self.pending.items() if now - p.at > self.CONFIRM_TTL_S]:
            del self.pending[k]

    @staticmethod
    async def _call(fn: Callable[[dict[str, Any]], Result], params: dict[str, Any]) -> str:
        """Le azioni lente (file, rete, altre app) girano in un thread: la conversazione non si ferma."""
        if inspect.iscoroutinefunction(fn):
            return await fn(params)
        res = await asyncio.to_thread(fn, params)
        return await res if inspect.isawaitable(res) else res
