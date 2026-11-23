"""Il motore della finestra (jarvis/ui/window.py): sempre WebView2, mai la pagina rotta di Internet Explorer.

Visto in Windows Sandbox il 22/11: WebView2 c'era nella sua cartella ma senza la voce di Edge Update nel registro,
pywebview è passato da solo a MSHTML e il primo avvio era una pagina bianca con "JARVIS" e una barra nera.
"""

import sys

import pytest

from jarvis.ui import window as W


def runtime(root, *versions, exe=True):
    for v in versions:
        d = root / v
        d.mkdir(parents=True)
        if exe:
            (d / "msedgewebview2.exe").write_bytes(b"")
    return root


def test_the_newest_installed_runtime_folder_is_found(tmp_path):
    a = runtime(tmp_path / "x86", "120.0.2210.91", "131.0.2903.70")
    runtime(a, "140.0.0.1", exe=False)  # una cartella a metà aggiornamento, senza l'eseguibile
    (a / "SetupMetrics").mkdir()
    b = runtime(tmp_path / "utente", "99.0.1150.30")
    assert W.runtime_folder([tmp_path / "manca", a, b]) == a / "131.0.2903.70"
    assert W.runtime_folder([tmp_path / "manca"]) is None


def test_engine_choice():
    folder = W.Path("C:/WebView2/131.0.2903.70")
    assert W.engine(lambda: True, lambda: folder) == ("webview2", None)  # registrato: come sempre
    assert W.engine(lambda: False, lambda: folder) == ("cartella", folder)
    assert W.engine(lambda: False, lambda: None) == ("browser", None)


class Stop(Exception):
    pass


@pytest.fixture
def started(monkeypatch):
    calls = {"opened": [], "windows": [], "started": 0}
    monkeypatch.setattr(sys, "argv", ["window", "--port", "8799"])
    monkeypatch.setattr(W, "wait_server", lambda url: True)
    monkeypatch.setattr(W.webbrowser, "open", calls["opened"].append)
    monkeypatch.setattr(W.webview, "create_window", lambda *a, **k: calls["windows"].append(a))
    monkeypatch.setattr(W.webview, "start", lambda **k: calls.__setitem__("started", calls["started"] + 1))
    monkeypatch.setitem(W.webview.settings, "WEBVIEW2_RUNTIME_PATH", None)

    def sleep(_s):
        raise Stop  # nel processo vero aspetta finché il primo avvio o Jarvis lo chiudono

    monkeypatch.setattr(W.time, "sleep", sleep)
    return calls


def test_without_webview2_the_page_opens_in_the_browser_and_the_process_stays(started, monkeypatch):
    monkeypatch.setattr(W, "engine", lambda: ("browser", None))
    with pytest.raises(Stop):
        W.main()
    assert started["opened"] == ["http://127.0.0.1:8799/"] and started["windows"] == [] and started["started"] == 0


def test_an_unregistered_runtime_is_given_to_pywebview(started, monkeypatch):
    folder = W.Path("C:/WebView2/131.0.2903.70")
    monkeypatch.setattr(W, "engine", lambda: ("cartella", folder))
    W.main()
    assert W.webview.settings["WEBVIEW2_RUNTIME_PATH"] == str(folder)
    assert started["windows"] and started["started"] == 1 and started["opened"] == []


def test_a_registered_runtime_changes_nothing(started, monkeypatch):
    monkeypatch.setattr(W, "engine", lambda: ("webview2", None))
    W.main()
    assert W.webview.settings["WEBVIEW2_RUNTIME_PATH"] is None and started["started"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="il registro c'è solo su Windows")
def test_on_this_pc_the_answer_matches_the_registry():
    """Quello che dice reg.exe, letto per conto suo: sul PC di sviluppo WebView2 è registrato (la finestra resta
    com'era), in Windows Sandbox no. Non si pretende che lo sia: la prova girerebbe rossa sui PC così."""
    import re
    import subprocess

    keys = (rf"HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{W.RUNTIME_KEY}",
            rf"HKCU\SOFTWARE\Microsoft\EdgeUpdate\Clients\{W.RUNTIME_KEY}")
    found = any(re.search(r"\bpv\s+REG_SZ\s+[1-9]", subprocess.run(["reg", "query", k, "/v", "pv"], capture_output=True,
                                                                     text=True).stdout) for k in keys)
    assert W.runtime_registered() == found
