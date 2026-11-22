"""HTTPS su un Windows nuovo (jarvis/certs.py): da quando Jarvis parte i certificati li verifica Windows."""

import ssl
import sys
from pathlib import Path

import truststore

from jarvis import certs, toolbox
from jarvis.setup import flow


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


async def test_main_trusts_windows_before_anything_is_downloaded(monkeypatch):
    from jarvis import __main__ as entry

    order = []
    monkeypatch.setattr(certs, "trust_windows", lambda: order.append("certificati"))
    monkeypatch.setattr(toolbox, "use_downloaded_tools", lambda: order.append("strumenti"))
    monkeypatch.setattr(flow, "missing_files", lambda cfg: order.append("controllo") or [Path("motore.exe")])
    assert await entry.main(console=False, window=False) == 2
    assert order == ["certificati", "strumenti", "controllo"]
