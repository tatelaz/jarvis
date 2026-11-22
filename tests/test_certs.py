"""HTTPS su un Windows nuovo (jarvis/certs.py): da quando Jarvis parte i certificati li verifica Windows."""

import os
import ssl
import subprocess
import sys
from pathlib import Path

import truststore

from jarvis import certs, toolbox
from jarvis.setup import flow

ROOT = Path(__file__).resolve().parents[1]


def test_after_trusting_windows_every_ssl_context_is_verified_by_windows():
    try:
        assert certs.trust_windows() is True
        assert isinstance(ssl.create_default_context(), truststore.SSLContext)  # aiohttp, urllib, imaplib, smtplib
    finally:
        truststore.extract_from_ssl()
    assert not isinstance(ssl.create_default_context(), truststore.SSLContext)


def test_without_truststore_jarvis_still_starts(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "truststore", None)  # come se mancasse: l'import fallisce
    assert certs.trust_windows() is False
    assert "truststore manca" in caplog.text


def test_at_start_the_downloads_too_are_verified_by_windows(tmp_path):
    """In un processo nuovo, come python -m jarvis su un PC nuovo: anche aiohttp (download, aggiornamenti) deve
    verificare con Windows. aiohttp prepara la sua verifica quando lo si importa: nella Sandbox, con truststore
    chiamato dopo l'import, i download fallivano ancora."""
    code = ("import asyncio, truststore\n"
            "from jarvis import __main__ as entry\n"
            "assert asyncio.run(entry.main(console=False, window=False)) == 2\n"
            "import aiohttp.connector as c\n"
            "print('verifica di aiohttp con Windows:', isinstance(c._SSL_CONTEXT_VERIFIED, truststore.SSLContext))\n")
    env = {**os.environ, "JARVIS_DATA_DIR": str(tmp_path / "dati"), "JARVIS_ENGINE_DIR": str(tmp_path / "motore"),
           "JARVIS_MODELS_DIR": str(tmp_path / "modelli"), "PYTHONUTF8": "1"}
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.stdout.strip().splitlines()[-1:] == ["verifica di aiohttp con Windows: True"], r.stderr[-2000:]


async def test_main_trusts_windows_before_anything_is_downloaded(monkeypatch):
    from jarvis import __main__ as entry

    order = []
    monkeypatch.setattr(certs, "trust_windows", lambda: order.append("certificati"))
    monkeypatch.setattr(toolbox, "use_downloaded_tools", lambda: order.append("strumenti"))
    monkeypatch.setattr(flow, "missing_files", lambda cfg: order.append("controllo") or [Path("motore.exe")])
    assert await entry.main(console=False, window=False) == 2
    assert order == ["certificati", "strumenti", "controllo"]
