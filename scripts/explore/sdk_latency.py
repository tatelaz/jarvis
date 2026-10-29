import asyncio
import os
import statistics
import sys
import time

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import ResultMessage, StreamEvent

CLI = os.path.join(os.environ["APPDATA"], "npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
SYS = "Sei JARVIS, l'assistente personale dell'utente. Rispondi in italiano, in modo conciso, elegante e un po' ironico, come il JARVIS dei film. Niente markdown: il testo verrà letto ad alta voce."
PROMPTS = ["Buongiorno Jarvis.", "Quanto fa 17 per 23?", "Dammi un consiglio rapido per concentrarmi.", "Grazie, è tutto."]
async def run(model, effort, thinking):
    kw = dict(cli_path=CLI, system_prompt=SYS, model=model, include_partial_messages=True, tools=[], setting_sources=[],
              strict_mcp_config=True, mcp_servers={}, permission_mode="bypassPermissions", max_turns=1, cwd=os.environ.get("TEMP"),
              env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"})
    if effort: kw["effort"] = effort
    if thinking: kw["thinking"] = thinking
    ttft = []
    t0 = time.perf_counter()
    async with ClaudeSDKClient(options=ClaudeAgentOptions(**kw)) as c:
        conn = time.perf_counter() - t0
        for p in PROMPTS:
            t = time.perf_counter(); first = None; txt = ""
            await c.query(p)
            async for m in c.receive_response():
                if isinstance(m, StreamEvent) and m.event.get("type") == "content_block_delta" and m.event["delta"].get("type") == "text_delta":
                    if first is None: first = time.perf_counter() - t
                    txt += m.event["delta"]["text"]
                if isinstance(m, ResultMessage) and m.is_error: txt = "ERRORE " + str(m.result)
            ttft.append(first or 99)
            print(f"    {first if first is None else round(first,2)}s  {txt[:90]!r}", flush=True)
    print(f"  => {model}/{effort}/{thinking}: connessione {conn:.1f}s, primo testo mediano {statistics.median(ttft):.2f}s (min {min(ttft):.2f})\n", flush=True)
async def main():
    for spec in sys.argv[1:]:
        model, effort, th = (spec.split(":") + ["", ""])[:3]
        thinking = {"type": th} if th else None
        try: await run(model, effort or None, thinking)
        except Exception as e: print("  ERRORE", spec, type(e).__name__, str(e)[:300])
asyncio.run(main())
