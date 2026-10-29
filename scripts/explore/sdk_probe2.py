"""Sonda dettagliata: stampa ogni messaggio dell'SDK con il tempo trascorso."""
import asyncio
import os
import sys
import time

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent

CLI = os.path.join(os.environ["APPDATA"], "npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
SYS = "Sei JARVIS, assistente vocale. Rispondi in italiano, frasi brevi, niente markdown."
async def main(model, effort):
    opts = ClaudeAgentOptions(cli_path=CLI, system_prompt=SYS, model=model, effort=effort, include_partial_messages=True,
                              tools=[], setting_sources=[], permission_mode="bypassPermissions", max_turns=2,
                              cwd=os.environ.get("TEMP"))
    async with ClaudeSDKClient(options=opts) as c:
        for p in ["Ciao Jarvis, come va?", "Che giorno è domani se oggi è lunedì?"]:
            t = time.perf_counter(); await c.query(p); print(f"--- {p}")
            async for m in c.receive_response():
                dt = time.perf_counter() - t
                if isinstance(m, StreamEvent):
                    ev = m.event; et = ev.get("type")
                    if et == "content_block_delta":
                        d = ev["delta"]; print(f"  {dt:5.2f} delta {d.get('type')} {str(d.get('text') or d.get('thinking') or '')[:60]!r}")
                    elif et in ("message_start", "content_block_start", "message_stop"):
                        print(f"  {dt:5.2f} {et} {str(ev.get('content_block', ''))[:80]}")
                elif isinstance(m, AssistantMessage):
                    print(f"  {dt:5.2f} AssistantMessage {[type(b).__name__ for b in m.content]} {str(m.content)[:150]}")
                elif isinstance(m, ResultMessage):
                    print(f"  {dt:5.2f} Result {m.subtype} turns={m.num_turns} api_ms={m.duration_api_ms} result={str(m.result)[:80]!r}")
                else:
                    print(f"  {dt:5.2f} {type(m).__name__} {getattr(m,'subtype','')!s} {str(getattr(m,'data',''))[:100]}")
asyncio.run(main(sys.argv[1], sys.argv[2]))
