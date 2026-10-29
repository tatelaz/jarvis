"""Confronto dei cervelli OpenAI-compatibili (Ollama locale/cloud, Bonsai): prima parola, strumenti, italiano.

Uso: python scripts/bench/brain_bench.py ollama:deepseek-v4-flash:cloud ollama:glm-5.1:cloud llamacpp:bonsai2
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.brain.base import Done, TextDelta, ToolCall
from jarvis.brain.local import LocalBrain
from jarvis.brain.persona import system_prompt
from jarvis.brain.tools import ToolRegistry, schema
from jarvis.config import Config

URLS = {"ollama": "http://127.0.0.1:11434/v1", "llamacpp": "http://127.0.0.1:8085/v1"}
QUESTIONS = [
    ("chiacchiera", "Jarvis, buonasera. Come va?", None),
    ("strumento", "Jarvis, abbassa il volume al trenta per cento.", "volume"),
    ("ragionamento", "Jarvis, se parto alle 17:40 e il viaggio dura due ore e trentacinque minuti, a che ora arrivo?",
     ("20:15", "venti e quindici")),
]


def tools() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.register("volume", "Imposta il volume del PC (0-100).", schema({"livello": {"type": "integer"}}, ["livello"]))
    def volume(args):
        return f"volume impostato a {args['livello']}"

    @reg.register("nessuna_risposta", "Da usare quando la frase non è rivolta a te.", schema({}, []))
    def nessuna(args):
        return "ok"

    return reg


async def ask(brain: LocalBrain, text: str) -> dict:
    t = time.perf_counter()
    first, reply, calls = None, "", []
    async for ev in brain.respond(text):
        if isinstance(ev, TextDelta):
            first = first or time.perf_counter() - t
            reply += ev.text
        elif isinstance(ev, ToolCall):
            first = first or time.perf_counter() - t
            calls.append(ev.name)
        elif isinstance(ev, Done) and ev.error:
            reply += f" [ERRORE {ev.error}]"
    return {"prima_s": round(first or -1, 2), "totale_s": round(time.perf_counter() - t, 2), "strumenti": calls,
            "risposta": reply.strip()[:300]}


async def main(targets: list[str]) -> None:
    prompt = system_prompt(Config())
    for target in targets:
        kind, model = target.split(":", 1)
        brain = LocalBrain(prompt, tools(), URLS[kind], model, kind=kind, name=model)
        for label, q, expect in QUESTIONS:
            try:
                r = await ask(brain, q)
            except Exception as e:  # una prova: si riporta e si va avanti
                r = {"errore": repr(e)[:300]}
            alts = (expect,) if isinstance(expect, str) else expect or ()
            ok = "errore" not in r and (not alts or any(a in r["risposta"] or a in r["strumenti"] for a in alts))
            print(json.dumps({"modello": model, "prova": label, "ok": ok, **r}, ensure_ascii=False), flush=True)
        await brain.stop()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
