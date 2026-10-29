"""Turni di parola: logica con VAD finto (deterministico) e prova con modelli e voci reali."""

import itertools
import json

import numpy as np
import pytest
import soundfile as sf

from jarvis import paths
from jarvis.audio.listener import WIN_MS, Listener
from jarvis.audio.turn import SmartTurn
from jarvis.audio.vad import WINDOW, SileroVad
from jarvis.config import AudioConfig

# dalle cartelle vere anche nelle copie separate (auto-miglioramento): prima lì questi test venivano saltati
FLEURS = paths.MODELS_DIR / "testdata" / "fleurs_it"


class ScriptedVad:
    """Restituisce probabilità prefissate, una per finestra da 32 ms."""

    def __init__(self, probs):
        self.probs = list(probs)
        self.i = 0

    def __call__(self, win):
        p = self.probs[self.i] if self.i < len(self.probs) else 0.0
        self.i += 1
        return [p]


def windows(ms: float) -> int:
    return round(ms / WIN_MS)


def run(probs, turn_prob=0.9, cfg=None, chunk=None, jarvis_speaking=False):
    cfg = cfg or AudioConfig()
    calls = []

    def turn_fn(audio):
        calls.append(len(audio))
        return turn_prob(len(calls)) if callable(turn_prob) else turn_prob

    lst = Listener(cfg, ScriptedVad(probs), turn_fn)
    lst.set_jarvis_speaking(jarvis_speaking)
    audio = np.zeros(len(probs) * WINDOW, np.float32)
    events = []
    step = chunk or len(audio)
    for i in range(0, len(audio), step):
        events.extend(lst.feed(audio[i:i + step]))
    return events, calls


def kinds(events):
    return [e.kind for e in events if e.kind != "partial"]


def test_preroll_keeps_audio_before_speech_start():
    """La frase deve cominciare pre_roll_ms prima della prima finestra di voce (prima sillaba salva)."""
    cfg = AudioConfig()
    lead, speech = windows(1000), windows(800)
    probs = [0.0] * lead + [0.95] * speech + [0.0] * windows(1000)
    # ogni finestra porta il proprio indice come valore costante: si vede da dove parte l'audio
    audio = np.concatenate([np.full(WINDOW, i, np.float32) for i in range(len(probs))])
    lst = Listener(cfg, ScriptedVad(probs), lambda a: 0.9)
    end = next(e for e in lst.feed(audio) if e.kind == "turn_end")
    first_window = int(end.audio[0])
    assert first_window == lead - int(cfg.pre_roll_ms / WIN_MS)
    # e nessun buco: le finestre sono tutte consecutive
    idx = end.audio[::WINDOW].astype(int)
    assert np.all(np.diff(idx) == 1)


def test_short_noise_is_not_speech():
    events, _ = run([0.9] * windows(100) + [0.0] * windows(2000))
    assert events == []


def test_utterance_then_silence_ends_with_smart_turn():
    probs = [0.0] * windows(500) + [0.95] * windows(1200) + [0.0] * windows(1000)
    events, calls = run(probs, turn_prob=0.9)
    assert kinds(events) == ["speech_start", "turn_end"]
    start, end = events[0], next(e for e in events if e.kind == "turn_end")
    cfg = AudioConfig()
    assert start.t == pytest.approx(0.5 + cfg.min_speech_ms / 1000, abs=0.05)
    assert end.info["reason"] == "smart_turn"
    # la fine arriva dopo il silenzio minimo, non dopo quello massimo
    assert end.t == pytest.approx(0.5 + 1.2 + cfg.min_silence_ms / 1000, abs=0.07)
    # l'audio comprende il pre-roll: non si perde la prima sillaba
    assert len(end.audio) / 16000 >= 1.2 + cfg.pre_roll_ms / 1000 - 0.05
    assert len(calls) == 1


def test_pause_mid_sentence_is_not_end_of_turn():
    # 1 s voce, 600 ms pausa ("sto pensando"), 1 s voce, poi silenzio
    probs = [0.95] * windows(1000) + [0.0] * windows(600) + [0.95] * windows(1000) + [0.0] * windows(1500)
    # Smart Turn dice "non finito" alla prima pausa, "finito" alla seconda
    seq = iter([0.1, 0.1, 0.1, 0.9, 0.9, 0.9])
    events, _ = run(probs, turn_prob=lambda n: next(seq))
    assert kinds(events) == ["speech_start", "turn_end"]
    end = events[-1]
    assert end.info["duration_ms"] > 2500


def test_incomplete_turn_ends_at_max_silence():
    cfg = AudioConfig()
    probs = [0.95] * windows(800) + [0.0] * windows(cfg.max_silence_ms + 400)
    events, calls = run(probs, turn_prob=0.05)
    end = next(e for e in events if e.kind == "turn_end")
    assert end.info["reason"] == "max_silence"
    assert end.t == pytest.approx(0.8 + cfg.max_silence_ms / 1000, abs=0.07)
    assert len(calls) >= 3  # ha ricontrollato più volte prima di arrendersi


def test_max_length():
    events, _ = run([0.95] * windows(3000), cfg=None)
    assert kinds(events) == ["speech_start"]
    cfg = AudioConfig()
    lst = Listener(cfg, ScriptedVad([0.95] * windows(3000)), lambda a: 0.0, max_utterance_ms=2000)
    ev = lst.feed(np.zeros(windows(3000) * WINDOW, np.float32))
    assert [e.info.get("reason") for e in ev if e.kind == "turn_end"] == ["max_length"]


def test_partials_during_speech():
    probs = [0.95] * windows(2000) + [0.0] * windows(800)
    events, _ = run(probs)
    partials = [e for e in events if e.kind == "partial"]
    assert 3 <= len(partials) <= 5
    assert all(len(p.audio) > 0 for p in partials)
    assert all(a.t < b.t for a, b in itertools.pairwise(partials))


def test_threshold_is_stricter_while_jarvis_speaks():
    probs = [0.6] * windows(1000) + [0.0] * windows(1000)
    assert kinds(run(probs)[0]) == ["speech_start", "turn_end"]
    assert run(probs, jarvis_speaking=True)[0] == []


def test_block_size_does_not_change_events():
    probs = [0.0] * 10 + [0.95] * 40 + [0.0] * 8 + [0.9] * 30 + [0.0] * 60
    ref, _ = run(probs, turn_prob=0.7)
    for chunk in (37, 160, 512, 999, 4096):
        ev, _ = run(probs, turn_prob=0.7, chunk=chunk)
        assert [(e.kind, round(e.t, 3)) for e in ev] == [(e.kind, round(e.t, 3)) for e in ref]


def test_disabled_listener_ignores_everything():
    lst = Listener(AudioConfig(), ScriptedVad([0.95] * 100), lambda a: 1.0)
    lst.enabled = False
    assert lst.feed(np.zeros(100 * WINDOW, np.float32)) == []


# ------------------------------------------------------------------ con modelli e voci reali

@pytest.fixture(scope="module")
def real_listener_factory():
    vad, turn = SileroVad(), SmartTurn()

    def make(cfg=None):
        vad.reset()
        return Listener(cfg or AudioConfig(), vad.feed, turn.probability)

    return make


def fleurs(i):
    a, sr = sf.read(FLEURS / f"it_{i:03d}.wav", dtype="float32")
    assert sr == 16000
    return a


def speech_span(a):
    """Inizio e fine (s) della parte parlata di una registrazione, ignorando i silenzi agli estremi."""
    fr = 320
    rms = np.array([np.sqrt(np.mean(a[k:k + fr] ** 2)) for k in range(0, len(a) - fr, fr)])
    loud = rms > rms.max() * 0.05
    # serve energia sostenuta (100 ms): un clic isolato all'inizio della registrazione non è parlato
    sustained = np.convolve(loud, np.ones(5), mode="valid") == 5
    idx = np.where(sustained)[0]
    return idx[0] * fr / 16000, (idx[-1] + 5) * fr / 16000


@pytest.mark.skipif(not FLEURS.exists(), reason="dati di prova FLEURS non scaricati")
def test_real_speech_one_turn_per_utterance(real_listener_factory):
    meta = json.loads((FLEURS / "meta.json").read_text(encoding="utf-8"))
    rng = np.random.default_rng(3)
    for i in (1, 2, 3, 7):
        lst = real_listener_factory()
        noise = 0.003 * rng.standard_normal(16000 * 2).astype(np.float32)
        audio = np.concatenate([noise[:8000], fleurs(i), noise])
        events = []
        for k in range(0, len(audio), 480):  # blocchi da 30 ms come dal microfono
            events.extend(lst.feed(audio[k:k + 480]))
        turns = [e for e in events if e.kind == "turn_end"]
        assert turns, (i, meta[i]["text"])
        main = max(turns, key=lambda e: e.info["voiced_ms"])
        # la frase è un unico turno che contiene tutta la parte parlata...
        s0, s1 = speech_span(fleurs(i))
        assert main.info["duration_ms"] / 1000 > (s1 - s0), (i, s0, s1, [e.info for e in turns])
        # ...e gli eventuali turni extra sono solo respiri/rumori brevissimi, da scartare a valle
        assert all(e.info["voiced_ms"] < 500 for e in turns if e is not main), [e.info for e in turns]
        # la fine del turno arriva dopo la fine reale della frase, entro il silenzio massimo
        speech_end = 0.5 + s1
        assert speech_end < main.t < speech_end + AudioConfig().max_silence_ms / 1000 + 0.1


@pytest.mark.skipif(not FLEURS.exists(), reason="dati di prova FLEURS non scaricati")
def test_real_silence_and_noise_produce_nothing(real_listener_factory):
    lst = real_listener_factory()
    rng = np.random.default_rng(4)
    audio = np.concatenate([np.zeros(16000, np.float32), 0.01 * rng.standard_normal(16000 * 3).astype(np.float32)])
    assert lst.feed(audio) == []
