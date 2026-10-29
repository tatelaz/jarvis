"""Quale Claude per il cervello frontale e il ripiego: ritardo della prima parola, uso degli strumenti, consumo.

Stesso percorso del Jarvis vero (ClaudeBrain, persona, strumenti del cervello frontale, connettori claude.ai spenti),
ma gli strumenti sono finti: se un modello chiama volume o apri, la chiamata si registra e basta.
Sicurezza sul consumo: se un messaggio supera MAX_INPUT token in ingresso (i connettori sono rientrati) ci si ferma.
Uso: python scripts/bench/claude_models_bench.py [modello:sforzo ...]   (sforzo "off" = ragionamento spento)
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.brain import claude as claude_mod  # noqa: E402
from jarvis.brain.base import Done, TextDelta, ToolCall  # noqa: E402
from jarvis.brain.claude import ClaudeBrain  # noqa: E402
from jarvis.brain.persona import system_prompt  # noqa: E402
from jarvis.config import ConfigStore  # noqa: E402
from jarvis.core.bus import Bus  # noqa: E402
from jarvis.core.context import now_it  # noqa: E402
from jarvis.memory.store import Memory  # noqa: E402
from jarvis.tools.front import Services, build_front_tools  # noqa: E402

MAX_INPUT = 40_000
PROMPTS = [
    ("ora", "Jarvis, che ore sono?", None),
    ("conto", "Quanto fa 17 per 23?", None),
    ("spiega", "Spiegami in due frasi perché il cielo è azzurro.", None),
    ("mostra", "Mostrami a schermo come si legge un file JSON in Python.", "mostra"),
    ("ricorda", "Ricordati che il mio colore preferito è il verde bosco.", "ricorda"),
    ("volume", "Abbassa un po' il volume, per favore.", "volume"),
]
DEFAULT = ["claude-opus-5-5:low", "claude-sonnet-5-5:low", "claude-haiku-5-5:low", "claude-haiku-5-5:off",
           "claude-haiku-4-5:off"]


def fake_tools(tmp: Path):
    s = Services(Bus(), ConfigStore(tmp / "config.yaml"), Memory(tmp / "m.db"))
    reg = build_front_tools(s)
    calls: list[tuple[str, dict]] = []
    for t in reg:
        def fake(args, _name=t.name):
            calls.append((_name, args))
            return "fatto (simulato)"
        t.handler = fake
    return reg, calls


async def run(model: str, effort: str, persona: str, tmp: Path) -> list[dict]:
    reg, calls = fake_tools(tmp)
    if effort == "off":
        claude_mod.NO_THINKING_MODELS = (model,)
        brain = ClaudeBrain(persona, reg, model, None, cwd=tmp / "cwd", name=model)
    else:
        claude_mod.NO_THINKING_MODELS = ()
        brain = ClaudeBrain(persona, reg, model, effort, cwd=tmp / "cwd", name=model)
    t = time.perf_counter()
    await brain.start()
    start_s = time.perf_counter() - t
    rows = []
    try:
        for key, text, want in PROMPTS:
            calls.clear()
            msg = f"<contesto>\nAdesso è {now_it()}.\n</contesto>\n{text}"
            t0 = time.perf_counter()
            first, reply, usage, err = None, "", {}, None
            async for ev in brain.respond(msg):
                if isinstance(ev, TextDelta) and first is None:
                    first = time.perf_counter() - t0
                if isinstance(ev, ToolCall) and first is None:
                    first = time.perf_counter() - t0  # anche uno strumento è "Jarvis che risponde"
                if isinstance(ev, Done):
                    reply, usage, err = ev.text, ev.usage or {}, ev.error
            total = time.perf_counter() - t0
            tokens_in = sum(int(usage.get(k) or 0) for k in
                            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            row = {"modello": model, "sforzo": effort, "prova": key, "prima_s": round(first or total, 2),
                   "totale_s": round(total, 2), "strumenti": [c[0] for c in calls], "atteso": want,
                   "ingresso": tokens_in, "uscita": int(usage.get("output_tokens") or 0), "errore": err,
                   "risposta": reply.strip()[:160], "avvio_s": round(start_s, 1)}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if tokens_in > MAX_INPUT:
                raise SystemExit(f"FERMO: {tokens_in} token in ingresso, i connettori sembrano rientrati")
    finally:
        await brain.stop()
    return rows


async def main(specs: list[str]) -> None:
    cfg = ConfigStore().config
    persona = system_prompt(cfg, ["l'utente preferisce la grafica classica (mark3)."])
    out = []
    with tempfile.TemporaryDirectory() as d:
        for spec in specs:
            model, effort = spec.split(":")
            out += await run(model, effort, persona, Path(d))
    Path(ROOT / "data" / "logs" / "bench_modelli.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                                                    encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT))
