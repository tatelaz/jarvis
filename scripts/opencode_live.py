"""Prova dal vivo di OpenCode con i modelli gratis veri (niente Claude). Consuma un po' dell'uso gratuito del giorno.

Uso: .venv\\Scripts\\python scripts\\opencode_live.py [--modelli 2] [--senza-compito]

Usa cartelle sue (data/prove/opencode_live): il Jarvis acceso non viene toccato. La chiave, se l'utente l'ha messa,
è quella vera (data/opencode/chiave.txt o OPENCODE_API_KEY).
1. accende OpenCode isolato e legge l'elenco dei modelli gratis;
2. esame vero dei primi N modelli (italiano, strumento finto, prima parola);
3. Jarvis a voce col modello vincitore: uno strumento (finto, innocuo) e una risposta interrotta;
4. un piccolo compito col sotto-agente OpenCode: un file nella sua cartella (libero), uno fuori (deve chiedere
   conferma: qui la risposta è sempre NO) e un comando innocuo. Stampa passi, richieste e conferme: il formato
   vero degli eventi, da confrontare con il finto dei test (tests/fixtures/fake_opencode.py).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis import log, paths  # noqa: E402
from jarvis.brain.base import Done, TextDelta, ToolCall  # noqa: E402
from jarvis.brain.opencode import ApiKeyStore  # noqa: E402
from jarvis.brain.tools import ToolRegistry, schema  # noqa: E402
from jarvis.config import ConfigStore  # noqa: E402
from jarvis.opencode_kit import OpenCodeKit  # noqa: E402
from jarvis.tasks.manager import Task, WorkerHooks  # noqa: E402
from jarvis.tasks.safety import SafetyPolicy  # noqa: E402


def voice_tools(calls: list) -> ToolRegistry:
    """Uno strumento innocuo al posto di quelli veri di Jarvis (che qui non agiscono sul PC)."""
    reg = ToolRegistry()

    @reg.register("ora", "L'ora esatta adesso.", schema({}), read_only=True)
    def ora(args):
        calls.append(args)
        return "sono le 21:37"

    return reg


async def voice(kit: OpenCodeKit, calls: list) -> None:
    """Jarvis a voce su OpenCode: uno strumento, poi una risposta lunga interrotta dopo tre pezzi."""
    brain = kit.brain("Sei Jarvis, l'assistente vocale dell'utente: rispondi in italiano, in una o due frasi, senza "
                      "markdown. Per l'ora usa lo strumento ora.")
    try:
        t = time.perf_counter()
        first, text = None, ""
        async for ev in brain.respond("Che ore sono?"):
            if isinstance(ev, TextDelta) and first is None:
                first = time.perf_counter() - t
            elif isinstance(ev, ToolCall):
                print(f"  strumento: {ev.name}({ev.args})")
            elif isinstance(ev, Done):
                text = ev.text
        print(f"  risposta ({brain.model}, prima parola {first or 0:.2f} s): {text}")
        print(f"  strumento chiamato davvero: {bool(calls)}; '21:37' nella risposta: {'21:37' in text}")
        pieces, after = 0, 0
        t = time.perf_counter()
        async for ev in brain.respond("Raccontami in dieci frasi la storia di Iron Man."):
            if isinstance(ev, TextDelta):
                pieces += 1
                if pieces == 3:
                    await brain.interrupt()
                elif pieces > 3:
                    after += 1
            elif isinstance(ev, Done):
                print(f"  interruzione: fermo in {time.perf_counter() - t:.1f} s, interrotto={ev.interrupted}, "
                      f"pezzi detti dopo lo stop: {after}")
    finally:
        await brain.stop()


async def main(models: int, task: bool) -> int:
    log.setup(filename="opencode_live.log")
    base = paths.DATA_DIR / "prove" / "opencode_live"
    kit = OpenCodeKit(ConfigStore(base / "config.yaml"), base=base / "opencode")
    kit.hub.keys = ApiKeyStore(paths.DATA_DIR / "opencode")
    calls: list = []
    kit.mount_front(voice_tools(calls))
    try:
        t = time.perf_counter()
        async with kit.hub.using():
            pass
        print(f"OpenCode acceso in {time.perf_counter() - t:.1f} s "
              f"({'con' if kit.hub.keys.get() else 'senza'} chiave)")
        print("modelli gratis:", ", ".join(m.id for m in kit.picker.catalog) or "nessuno")
        results = await kit.examiner.run(limit=models)
        for r in results:
            print(f"  esame {r.model}: italiano {r.italian}, strumenti {r.tools}, prima parola {r.first_word_s} s"
                  + (f" ({r.error})" if r.error else ""))
        if not results:
            print("  esame non fatto: guarda data/logs/opencode_live.log (limite gratuito raggiunto?)")
        print(kit.describe())
        print("Jarvis a voce su OpenCode:")
        try:
            await voice(kit, calls)
        except Exception as e:
            print("  conversazione non riuscita:", e)
        if not task:
            return 0
        work = base / "lavoro"
        outside = base / "fuori.txt"
        outside.unlink(missing_ok=True)
        job = Task(1, "prova dal vivo", f"Crea il file dentro.txt nella cartella di lavoro con scritto ciao. Poi "
                                        f"crea il file {outside} con scritto ciao. Poi esegui il comando: echo fatto. "
                                        f"Infine riassumi cosa è riuscito.", work / "compito_001")
        asked: list[str] = []

        async def confirm(text: str) -> bool:
            asked.append(text)
            print(f"  CONFERMA CHIESTA: {text} -> no")
            return False

        hooks = WorkerHooks(progress=lambda s: print("  passo:", s), confirm=confirm)
        worker = kit.worker(job, SafetyPolicy([work]), None)
        try:
            out = await asyncio.wait_for(worker.run(job, hooks), 300)
            print("esito del compito:", out)
        except Exception as e:
            print("compito fallito:", e)
        print(f"conferme chieste: {len(asked)}; dentro.txt: {(job.cwd / 'dentro.txt').exists()}; "
              f"fuori.txt (deve mancare): {outside.exists()}")
        return 0 if not outside.exists() else 1
    finally:
        await kit.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--modelli", type=int, default=2, help="quanti modelli esaminare (ognuno: due domande)")
    ap.add_argument("--senza-compito", action="store_true", help="solo l'esame, niente sotto-agente")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.modelli, not a.senza_compito)))
