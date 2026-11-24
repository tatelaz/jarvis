"""Il server delle segnalazioni (servizi/segnalazioni, un Cloudflare Worker): le sue prove sono in JavaScript e girano
con Node, senza rete (GitHub e i limiti di Cloudflare sono finti)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "servizi" / "segnalazioni"


def node() -> str:
    found = shutil.which("node") or next((str(p) for p in (ROOT / "engine" / "tools").glob("node*/node.exe")), None)
    if not found:
        pytest.skip("Node non c'è su questo PC")
    return found


def test_the_server_tests_pass():
    r = subprocess.run([node(), "--test", str(SERVER / "worker.test.mjs")], capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
    assert "# fail 0" in r.stdout and "# pass 6" in r.stdout  # sei gruppi di prove


def test_the_worker_only_hands_over_to_the_tested_logic():
    worker = (SERVER / "worker.js").read_text(encoding="utf-8")
    assert 'import { handle } from "./logica.js";' in worker and "handle(request, env, fetch)" in worker
    toml = (SERVER / "wrangler.toml").read_text(encoding="utf-8")
    assert 'main = "worker.js"' in toml and "GITHUB_TOKEN" not in toml.split("[vars]")[1].split("[[")[0]  # mai qui
