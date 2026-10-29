import asyncio
import os
import sys
import time

sys.path.insert(0, ".")
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent

from jarvis.brain.claude_cli import ISOLATED_ENV, find_cli
from jarvis.brain.tools import ToolRegistry, schema

reg = ToolRegistry()
@reg.register("ora_attuale", "Data e ora correnti del PC.", read_only=True)
def ora(args): return time.strftime("%A %d %B %Y, %H:%M:%S")
@reg.register("ricorda", "Salva un fatto da ricordare.", schema({"fatto": {"type": "string"}}, ["fatto"]))
def ricorda(args): return f"salvato: {args['fatto']}"
srv, allowed = reg.claude_mcp_server()
opts = ClaudeAgentOptions(cli_path=find_cli(), system_prompt="Sei JARVIS. Italiano, conciso, niente markdown.", model="claude-opus-5-5", effort="low",
    include_partial_messages=True, tools=[], allowed_tools=allowed, mcp_servers={"jarvis": srv}, strict_mcp_config=True,
    setting_sources=[], permission_mode="bypassPermissions", max_turns=6, cwd=os.environ["TEMP"], env=ISOLATED_ENV)
async def turn(c, prompt, stop_after=None):
    t = time.perf_counter(); txt = ""; tools = []
    await c.query(prompt)
    async for m in c.receive_response():
        if isinstance(m, StreamEvent) and m.event.get("type") == "content_block_delta" and m.event["delta"].get("type") == "text_delta":
            txt += m.event["delta"]["text"]
            if stop_after and len(txt) > stop_after:
                await c.interrupt(); stop_after = None; print(f"   [interrotto a {time.perf_counter()-t:.2f}s dopo {len(txt)} caratteri]")
        elif isinstance(m, AssistantMessage):
            tools += [b.name for b in m.content if type(b).__name__ == "ToolUseBlock"]
        elif isinstance(m, ResultMessage):
            print(f"   Result {m.subtype} error={m.is_error} stop={m.stop_reason} turns={m.num_turns} {time.perf_counter()-t:.2f}s")
    print(f"-> {prompt!r}\n   strumenti={tools}\n   testo={txt[:200]!r}")
async def main():
    async with ClaudeSDKClient(options=opts) as c:
        await turn(c, "Che ore sono?")
        await turn(c, "Ricordati che il mio colore preferito è il blu notte.")
        await turn(c, "Raccontami nel dettaglio la storia dei computer, dagli abachi ad oggi.", stop_after=120)
        await turn(c, "Scusa se ti ho interrotto. Cosa stavi dicendo prima che ti fermassi? Rispondi in una frase.")
asyncio.run(main())
