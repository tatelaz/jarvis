from pathlib import Path

from jarvis import health
from jarvis.config import Config


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_heartbeat_and_known_good_after_a_while(tmp_path):
    clock = Clock()
    hb = health.Heartbeat(tmp_path, healthy_after_s=300, commit="abc123", clock=clock)
    beat = hb.beat("in attesa")
    assert beat["status"] == "in attesa" and health.read_json(tmp_path / health.HEARTBEAT)["pid"] == beat["pid"]
    assert health.known_good(tmp_path) is None  # appena partito: non ancora "sana"
    clock.t += 301
    hb.beat()
    assert health.known_good(tmp_path) == "abc123"


def test_unsaved_code_never_becomes_known_good(tmp_path):
    clock = Clock()
    hb = health.Heartbeat(tmp_path, healthy_after_s=1, commit=None, clock=clock)
    clock.t += 1000
    hb.beat()
    assert health.known_good(tmp_path) is None


def test_recovery_note_is_read_once(tmp_path):
    health.write_json(tmp_path / health.RECOVERY, {"reason": "hang", "rollback": True})
    note = health.take_recovery_note(tmp_path)
    assert note["reason"] == "hang" and health.take_recovery_note(tmp_path) is None
    text = health.recovery_sentence(note, "signore")
    assert text.startswith("Mi sono ripreso da un blocco, signore.") and "versione precedente" in text
    assert "arresto imprevisto" in health.recovery_sentence({"reason": "crash"})


def ok_model(key):
    return f"/modelli/{key}.gguf"


def ok_cli():
    return "claude.exe"


def test_preflight_all_good(tmp_path):
    exe = tmp_path / "audiocpp_server.exe"
    exe.touch()
    assert health.preflight(Config(), ok_model, ok_cli, vram=lambda: 7400, has_microphone=lambda: True,
                            engine_exe=exe) == []


def test_preflight_reports_each_problem_in_plain_words(tmp_path):
    def missing(key):
        if key == "qwen3-1.7b":
            raise FileNotFoundError(key)
        return ok_model(key)

    def no_cli():
        raise FileNotFoundError("claude")

    problems = health.preflight(Config(), missing, no_cli, vram=lambda: 3100, has_microphone=lambda: False,
                                engine_exe=Path(tmp_path / "manca.exe"))
    text = " ".join(problems)
    assert "motore della voce" in text and "Manca il modello della voce." in text
    assert "Claude Code" in text and "restano 3 gigabyte" in text and "microfono" in text


def test_preflight_checks_the_chosen_ears_model(tmp_path):
    """Dal 26/10 le orecchie sono parakeet-ultra (impostazione speech.stt_model): si controlla quello scelto."""
    exe = tmp_path / "audiocpp_server.exe"
    exe.touch()
    asked = []

    def only_old(key):
        asked.append(key)
        if key == "parakeet-ultra":
            raise FileNotFoundError(key)
        return ok_model(key)

    cfg = Config()
    assert cfg.speech.stt_model == "parakeet-ultra"
    assert "Manca il modello dell'udito." in health.preflight(cfg, only_old, ok_cli, vram=lambda: 7400,
                                                              has_microphone=lambda: True, engine_exe=exe)
    cfg.speech.stt_model = "parakeet-v3"  # il ripiego: quello di prima
    assert health.preflight(cfg, only_old, ok_cli, vram=lambda: 7400, has_microphone=lambda: True,
                            engine_exe=exe) == []
    assert "parakeet-v3" in asked


def test_ears_go_on_the_gpu_only_when_voice_and_ears_both_fit():
    assert health.ears_device_for("cuda", 7000) == "cuda"
    assert health.ears_device_for("cuda", health.VOICE_VRAM_MB + health.EARS_VRAM_MB - 1) == "cpu"  # gioco aperto
    assert health.ears_device_for("cuda", None) == "cuda"  # nvidia-smi non risponde: si prova come scelto
    assert health.ears_device_for("cpu", 7000) == "cpu"  # scelto sul processore: resta lì


def test_preflight_says_when_the_ears_stay_on_the_processor(tmp_path):
    exe = tmp_path / "audiocpp_server.exe"
    exe.touch()
    tight = health.VOICE_VRAM_MB + 500  # la voce ci sta, voce + udito no
    problems = health.preflight(Config(), ok_model, ok_cli, vram=lambda: tight, has_microphone=lambda: True,
                                engine_exe=exe)
    assert len(problems) == 1 and "udito resta sul processore" in problems[0]


class FakeEngine:
    def __init__(self, device, port, log):
        self.device, self.port, self.host, self.log = device, port, "127.0.0.1", log
        self.proc = None

    async def start(self, timeout=30.0):
        from types import SimpleNamespace

        self.proc = SimpleNamespace(returncode=None)
        self.log.append(("avvio", self.device, self.port))

    async def stop(self):
        if self.proc is not None:
            self.proc.returncode = 0
        self.log.append(("spento", self.device, self.port))

    def supervise(self):
        pass


async def test_rest_frees_the_gpu_and_waking_brings_the_ears_back(monkeypatch):
    """Dal 26/10 l'udito sta sulla scheda video: a riposo deve liberarla tutta (giochi) e sentire ancora il nome."""
    import asyncio

    from jarvis.app import EARS_ALT_PORT, EARS_PORT, Jarvis
    from jarvis.core.bus import Bus

    log = []
    j = Jarvis.__new__(Jarvis)
    j.cfg, j.bus, j.conv, j._voice, j.resting, j._rest_lock = Config(), Bus(), None, None, False, asyncio.Lock()
    j._ears_process = lambda device, port: FakeEngine(device, port, log)
    j.voice_engine = FakeEngine("voce", 8792, log)
    j.ears, j.ears_device = FakeEngine("cuda", EARS_PORT, log), "cuda"
    await j.ears.start()
    await j.rest()
    assert j.ears_device == "cpu" and j.ears.port == EARS_ALT_PORT
    assert ("avvio", "cpu", EARS_ALT_PORT) in log and log.index(("avvio", "cpu", EARS_ALT_PORT)) < log.index(
        ("spento", "cuda", EARS_PORT))  # prima parte il nuovo, poi si spegne il vecchio: mai sordo
    monkeypatch.setattr(health, "free_vram_mb", lambda: 7000)
    await j.wake_up()
    assert j.ears_device == "cuda" and j.ears.port == EARS_PORT
    await j.rest()
    monkeypatch.setattr(health, "free_vram_mb", lambda: 3000)  # un gioco ancora aperto: l'udito resta dov'è
    await j.wake_up()
    assert j.ears_device == "cpu"


def test_both_ears_models_exist_on_disk():
    from jarvis.speech import engine

    for key in ("parakeet-ultra", "parakeet-v3"):
        assert engine.model_path(key).endswith(".gguf")
    assert engine.ears_spec(8999).models[0]["path"].endswith("parakeet-ultra-q8_0.gguf")


def test_preflight_ignores_claude_when_another_brain_is_chosen(tmp_path):
    exe = tmp_path / "audiocpp_server.exe"
    exe.touch()
    cfg = Config()
    cfg.brain.provider = "ollama-cloud"

    def no_cli():
        raise FileNotFoundError("claude")

    assert health.preflight(cfg, ok_model, no_cli, vram=lambda: None, engine_exe=exe) == []


async def test_engine_can_restart_after_a_deliberate_stop(monkeypatch, tmp_path):
    """Il riposo spegne i motori di proposito: al risveglio devono poter ripartire con la loro sorveglianza."""
    import asyncio

    import pytest

    from jarvis.speech import engine

    e = engine.EngineProcess(engine.EngineSpec("prova", 8999, []))
    e._watch_task = asyncio.ensure_future(asyncio.sleep(10))  # sorveglianza in corso
    await e.stop()
    assert e._watch_task is None and e._stopping  # spento di proposito: la sorveglianza non lo rianima
    monkeypatch.setattr(engine.paths, "AUDIOCPP_SERVER", tmp_path / "manca.exe")
    with pytest.raises(engine.EngineError):
        await e.start()
    assert e._stopping is False  # alla ripartenza il motore torna sorvegliato
