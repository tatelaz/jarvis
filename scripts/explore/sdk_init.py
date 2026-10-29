import asyncio
import os
import sys

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import ResultMessage, SystemMessage

CLI = os.path.join(os.environ["APPDATA"], "npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
async def main(strict):
    kw = dict(cli_path=CLI, system_prompt="Rispondi solo: ok", model="claude-haiku-4-5", tools=[], setting_sources=[],
              permission_mode="bypassPermissions", max_turns=1, cwd=os.environ.get("TEMP"))
    if strict:
        kw.update(strict_mcp_config=True, mcp_servers={}, plugins=[], skills=[],
                  env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    async with ClaudeSDKClient(options=ClaudeAgentOptions(**kw)) as c:
        await c.query("ciao")
        async for m in c.receive_response():
            if isinstance(m, SystemMessage) and m.subtype == "init":
                d = m.data
                print("tools:", len(d.get("tools", [])), d.get("tools", [])[:8])
                print("mcp:", [(s.get("name"), s.get("status")) for s in d.get("mcp_servers", [])][:40])
                print("plugins:", [p.get("name") for p in d.get("plugins", [])][:20], " skills:", len(d.get("skills", []) or []), " slash:", len(d.get("slash_commands", []) or []))
                print("model:", d.get("model"), " keys:", list(d.keys()))
            if isinstance(m, ResultMessage):
                u = m.usage or {}
                print("usage:", {k: u.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}, "result:", m.result)
asyncio.run(main(sys.argv[1] == "strict"))
