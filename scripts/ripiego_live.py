"""Prova dal vivo del ripiego: Claude vero, poi Claude esaurito (simulato) -> OpenCode coi modelli gratis veri.

Uso: .venv\\Scripts\\python scripts\\ripiego_live.py

Consuma una risposta dell'abbonamento di Claude e un po' dell'uso gratuito di OpenCode. Usa cartelle sue
(data/prove/ripiego_live): il Jarvis acceso non viene toccato. La catena è quella di app.py (Opus, Haiku, OpenCode,
Ollama cloud), con uno strumento innocuo al posto di quelli veri.
1. Jarvis risponde con Claude vero;
2. i due Claude ricevono il risultato del limite ("You've hit your 5-hour limit · resets 1:40pm (Europe/Rome)"),
   come lo manda Claude Code: Jarvis deve passare da solo a OpenCode e avere una risposta vera da un modello gratis;
3. il primo modello gratis risponde "This model is not available in your country": si deve passare subito al
   successivo, senza mettere OpenCode in pausa; poi l'esame: il modello vietato è bocciato e l'esame continua.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claude_agent_sdk import ResultMessage  # noqa: E402

from jarvis import log, paths  # noqa: E402
from jarvis.brain.base import BrainUnavailable, Done, Notice, TextDelta  # noqa: E402
from jarvis.brain.claude import ClaudeBrain  # noqa: E402
from jarvis.brain.local import LocalBrain  # noqa: E402
from jarvis.brain.opencode import ApiKeyStore, OpenCodeClient  # noqa: E402
from jarvis.brain.router import BrainRouter  # noqa: E402
from jarvis.brain.tools import ToolRegistry, schema  # noqa: E402
from jarvis.config import ConfigStore  # noqa: E402
from jarvis.opencode_kit import OpenCodeKit  # noqa: E402

LIMIT = "You've hit your 5-hour limit · resets 1:40pm (Europe/Rome)"
COUNTRY = {"name": "APIError", "data": {"message": "This model is not available in your country", "statusCode": 403,
                                        "isRetryable": False}}
PERSONA = ("Sei Jarvis, l'assistente vocale dell'utente: rispondi in italiano, in una o due frasi, senza markdown. "
           "Per l'ora usa lo strumento ora.")


def voice_tools() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.register("ora", "L'ora esatta adesso.", schema({}), read_only=True)
    def ora(_args):
        return "sono le 21:37"

    return reg


class LimitClient:
    """Al posto della sessione di Claude Code esaurita: a ogni domanda il risultato d'errore del limite."""

    async def query(self, _message):
        pass

    async def receive_response(self):
        yield ResultMessage(subtype="success", duration_ms=40, duration_api_ms=0, is_error=True, num_turns=1,
                            session_id="esaurito", result=LIMIT)

    async def interrupt(self):
        pass

    async def disconnect(self):
        pass


async def turn(router: BrainRouter, text: str) -> tuple[str, str, list[dict], float]:
    t = time.perf_counter()
    said, model, switches = "", "", []
    async for ev in router.respond(text):
        if isinstance(ev, TextDelta):
            said += ev.text
        elif isinstance(ev, Notice) and ev.kind == "brain_switch":
            switches.append(ev.info)
        elif isinstance(ev, Done):
            model = ev.model or ""
    return said.strip(), model, switches, time.perf_counter() - t


def esito(ok: bool, what: str) -> bool:
    print(f"  {'OK ' if ok else 'NO '} {what}")
    return ok


async def main() -> int:
    log.setup(filename="ripiego_live.log")
    base = paths.DATA_DIR / "prove" / "ripiego_live"
    store = ConfigStore(base / "config.yaml")
    cfg = store.config
    kit = OpenCodeKit(store, base=base / "opencode")
    kit.hub.keys = ApiKeyStore(paths.DATA_DIR / "opencode")
    tools = voice_tools()
    kit.mount_front(tools)
    claude = ClaudeBrain(PERSONA, tools, cfg.brain.claude_model, cfg.brain.claude_effort, cwd=base / "_jarvis")
    fast = ClaudeBrain(PERSONA, tools, cfg.brain.fast_claude_model, cfg.brain.fast_claude_effort, cwd=base / "_jarvis",
                       name="claude-veloce")
    chain = [claude, fast, kit.brain(PERSONA)]
    if cfg.brain.cloud_fallback:
        chain.append(LocalBrain(PERSONA, tools, cfg.brain.ollama_base_url, cfg.brain.cloud_model, kind="ollama",
                                name=cfg.brain.cloud_model, provider="ollama-cloud"))
    router = BrainRouter(chain)
    ok = True
    real_prompt = OpenCodeClient.prompt
    try:
        print("1. Claude vero")
        said, model, switches, dt = await turn(router, "Dimmi in una frase che è tutto a posto.")
        print(f"  risposta ({model}, {dt:.1f} s): {said}")
        ok &= esito(model.startswith("claude") and bool(said) and not switches, "risponde Claude")

        print("2. Claude esaurito (simulato) -> OpenCode")
        await claude.stop()
        claude._client, fast._client = LimitClient(), LimitClient()
        said, model, switches, dt = await turn(router, "Che ore sono?")
        print(f"  risposta ({model}, {dt:.1f} s): {said}")
        print(f"  cambi: {[(s['to'], s['reason']) for s in switches]}")
        ok &= esito(router.provider_paused_until("claude") is not None, "tutti i Claude in pausa (limite dell'account)")
        ok &= esito(model.startswith("opencode/") and bool(said), "risposta vera da un modello gratis di OpenCode")
        ok &= esito(any(s["to_provider"] == "opencode" for s in switches), "cambio annunciato verso OpenCode")

        print("3. primo modello gratis vietato nel paese (simulato)")
        ranking = kit.picker.ranking()
        target = ranking[0] if ranking else ""
        print(f"  classifica: {', '.join(ranking)}; vietato: {target}")
        asked: list[str] = []

        async def prompt(self, sid, directory, body):
            mid = (body.get("model") or {}).get("modelID", "")
            asked.append(mid)
            if mid == target:  # come lo restituisce OpenCode: il messaggio dell'assistente con l'errore
                return {"info": {"role": "assistant", "sessionID": sid, "modelID": mid, "providerID": "opencode",
                                 "error": COUNTRY}, "parts": []}
            return await real_prompt(self, sid, directory, body)

        OpenCodeClient.prompt = prompt
        said, model, switches, dt = await turn(router, "Dimmi una curiosità sulla Luna in una frase.")
        print(f"  modelli provati: {asked}; risposta ({model}, {dt:.1f} s): {said}")
        ok &= esito(bool(target) and asked[:1] == [target] and model.startswith("opencode/")
                    and model != f"opencode/{target}" and bool(said), "passato da solo al modello successivo")
        ok &= esito(router.provider_paused_until("opencode") is None and all(
            router.paused_until.get(id(b), 0.0) <= time.time() for b in chain[2:3]), "OpenCode non messo in pausa")
        ok &= esito(target not in kit.picker.ranking(), "il modello vietato non si sceglie più")

        print("3b. esame con il modello vietato fra i candidati")
        kit.picker.results.pop(target, None)  # come un modello mai esaminato: in testa alla lista dell'esame
        kit.picker.paused.pop(target, None)
        asked.clear()
        results = await kit.examiner.run(limit=2)
        for r in results:
            print(f"  esame {r.model}: italiano {r.italian}, strumenti {r.tools}, prima parola {r.first_word_s} s"
                  + (f" ({r.error})" if r.error else ""))
        by = {r.model: r for r in results}
        ok &= esito(target in by and by[target].blocked and not by[target].passed, "vietato = bocciato")
        ok &= esito(len(results) == 2, "l'esame continua con gli altri")
        ok &= esito(target not in kit.picker.ranking(), "e non viene più scelto")
    except BrainUnavailable as e:
        ok = esito(False, f"nessun cervello: {e}")
    finally:
        OpenCodeClient.prompt = real_prompt
        await router.stop()
        await kit.stop()
    print("ESITO:", "tutto verde" if ok else "qualcosa non va (data/logs/ripiego_live.log)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
