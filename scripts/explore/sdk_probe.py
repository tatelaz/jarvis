"""Sonda del Claude Agent SDK: latenza al primo token per modello, strumenti, interruzione."""
import asyncio
import sys
import time

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server, tool
from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent


@tool("ora_attuale", "Restituisce data e ora correnti.", {})
async def ora(args):
    return {"content": [{"type": "text", "text": time.strftime("%Y-%m-%d %H:%M:%S")}]}

SYS = "Sei JARVIS, assistente vocale. Rispondi in italiano, frasi brevi, niente markdown."

async def probe(model, effort, prompts):
    srv = create_sdk_mcp_server("jarvis", tools=[ora])
    import os
    cli = os.path.join(os.environ["APPDATA"], "npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
    opts = ClaudeAgentOptions(cli_path=cli, system_prompt=SYS, model=model, effort=effort, include_partial_messages=True,
                              mcp_servers={"jarvis": srv}, allowed_tools=["mcp__jarvis__ora_attuale"],
                              tools=[], setting_sources=[], permission_mode="bypassPermissions", max_turns=4)
    t0 = time.perf_counter()
    async with ClaudeSDKClient(options=opts) as c:
        print(f"[{model}/{effort}] connessione {time.perf_counter()-t0:.2f}s", flush=True)
        for p in prompts:
            t = time.perf_counter(); first = None; text = ""; tools = []
            await c.query(p)
            async for m in c.receive_response():
                if isinstance(m, StreamEvent):
                    ev = m.event
                    if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                        if first is None: first = time.perf_counter() - t
                        text += ev["delta"]["text"]
                elif isinstance(m, AssistantMessage):
                    for b in m.content:
                        if type(b).__name__ == "ToolUseBlock": tools.append(b.name)
                elif isinstance(m, ResultMessage):
                    total = time.perf_counter() - t
            print(f"   {p[:40]!r}: primo testo {first if first is None else round(first,2)}s, totale {total:.2f}s, strumenti {tools} -> {text[:120]!r}", flush=True)

async def main():
    which = sys.argv[1:] or ["claude-haiku-4-5:low", "claude-sonnet-5-5:low", "claude-opus-5-5:low"]
    for w in which:
        model, effort = w.split(":")
        try:
            await probe(model, effort, ["Ciao Jarvis, come va?", "Che ore sono?", "Dimmi in una frase cos'è un buco nero."])
        except Exception as e:
            print(f"[{model}] ERRORE: {type(e).__name__}: {e}"[:400], flush=True)
asyncio.run(main())
