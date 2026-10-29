import asyncio, os, sys, time
sys.path.insert(0, ".")
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, ResultMessage, UserMessage, SystemMessage
from jarvis.brain.claude_cli import find_cli, ISOLATED_ENV
WS = sys.argv[1]
opts = ClaudeAgentOptions(cli_path=find_cli(), model="claude-opus-5-5", effort="low",
    system_prompt={"type": "preset", "preset": "claude_code", "append": "Lavori per JARVIS. Rispondi in italiano."},
    tools={"type": "preset", "preset": "claude_code"}, strict_mcp_config=True, mcp_servers={}, setting_sources=[],
    permission_mode="bypassPermissions", cwd=WS, env=ISOLATED_ENV, max_turns=30)
async def main():
    t0 = time.perf_counter()
    async with ClaudeSDKClient(options=opts) as c:
        await c.query("Crea nella cartella corrente i file uno.txt, due.txt, tre.txt, quattro.txt, cinque.txt, uno alla volta, ciascuno con una chiamata separata allo strumento Write, e dentro scrivi il numero in lettere. Poi dimmi cosa hai fatto.")
        injected = False
        async for m in c.receive_messages():
            dt = time.perf_counter() - t0
            if isinstance(m, AssistantMessage):
                for b in m.content:
                    if type(b).__name__ == "ToolUseBlock":
                        print(f"{dt:6.1f}s strumento {b.name} {str(b.input)[:70]}")
                        if not injected and "due" in str(b.input):
                            injected = True
                            await c.query("Aggiunta dell'utente: crea anche sei.txt con dentro 'sei', e in cinque.txt scrivi CINQUE in maiuscolo.")
                            print(f"{dt:6.1f}s >>> messaggio iniettato")
                    elif type(b).__name__ == "TextBlock":
                        print(f"{dt:6.1f}s testo: {b.text[:160]!r}")
            elif isinstance(m, ResultMessage):
                print(f"{dt:6.1f}s RESULT {m.subtype} turns={m.num_turns}: {str(m.result)[:200]!r}")
                if injected and m.num_turns > 1 and os.path.exists(os.path.join(WS, "sei.txt")):
                    break
                if dt > 240: break
    print("file:", sorted(os.listdir(WS)), "cinque.txt =", open(os.path.join(WS, "cinque.txt"), encoding="utf-8").read() if os.path.exists(os.path.join(WS, "cinque.txt")) else None)
asyncio.run(main())
