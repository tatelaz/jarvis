"""La copia di prova: dati suoi, motori e modelli di un'altra copia, mai due Jarvis accesi insieme."""

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import avvia_prova


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main_copy(tmp_path) -> Path:
    src = tmp_path / "principale"
    (src / "engine").mkdir(parents=True)
    (src / "models").mkdir()
    return src


def test_engines_and_models_come_from_the_main_copy_data_stays_apart(tmp_path):
    src, data = main_copy(tmp_path), tmp_path / "prova" / "data"
    env = avvia_prova.environment(src, data, {"PATH": "x"})
    assert env["JARVIS_ENGINE_DIR"] == str(src / "engine") and env["JARVIS_MODELS_DIR"] == str(src / "models")
    assert env["JARVIS_DATA_DIR"] == str(data) and env["PATH"] == "x"
    assert avvia_prova.problems(src, data, free_port()) == []


def test_refuses_missing_models_shared_data_and_a_running_jarvis(tmp_path):
    empty = tmp_path / "vuota"
    empty.mkdir()
    said = avvia_prova.problems(empty, tmp_path / "d", free_port())
    assert any("manca la cartella engine" in p for p in said) and any("manca la cartella models" in p for p in said)
    src = main_copy(tmp_path)
    assert any("dati sarebbero quelli della copia principale" in p
               for p in avvia_prova.problems(src, src / "data", free_port()))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert any(f"porta {port} è occupata" in p for p in avvia_prova.problems(src, tmp_path / "d", port))


def test_main_does_not_start_when_something_is_wrong(tmp_path, monkeypatch, capsys):
    started = []
    monkeypatch.setattr(avvia_prova.subprocess, "call", lambda *a, **k: started.append(a) or 0)
    assert avvia_prova.main(["--da", str(tmp_path / "niente"), "--dati", str(tmp_path / "d")]) == 1
    assert started == [] and "Non parto" in capsys.readouterr().out
