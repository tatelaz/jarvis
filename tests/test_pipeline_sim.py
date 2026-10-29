"""Pipeline audio completa in una stanza simulata: eco, cuffie/casse, voce dell'utente sopra Jarvis."""

import threading

import numpy as np
import pytest
import soundfile as sf

from jarvis import paths
from jarvis.audio.devices import SimulatedRoom
from jarvis.audio.listener import Listener
from jarvis.audio.loopback import SystemOutputTap
from jarvis.audio.pipeline import AudioPipeline
from jarvis.audio.turn import SmartTurn
from jarvis.audio.vad import SileroVad
from jarvis.config import AudioConfig

# dalle cartelle vere anche nelle copie separate (auto-miglioramento): prima lì questi test venivano saltati
FLEURS = paths.MODELS_DIR / "testdata" / "fleurs_it"
pytestmark = pytest.mark.skipif(not FLEURS.exists(), reason="dati di prova FLEURS non scaricati")


def jarvis_speech(seconds):
    """'Voce di Jarvis' per i test: frasi FLEURS di un altro parlatore, concatenate."""
    parts, total = [], 0
    for i in (10, 11, 12, 13, 14, 15):
        a, _ = sf.read(FLEURS / f"it_{i:03d}.wav", dtype="float32")
        parts.append(a)
        total += a.size
        if total > seconds * 16000:
            break
    return np.concatenate(parts)[: int(seconds * 16000)]


def user_utterance(i=1):
    a, _ = sf.read(FLEURS / f"it_{i:03d}.wav", dtype="float32")
    fr = 320
    rms = np.array([np.sqrt(np.mean(a[k:k + fr] ** 2)) for k in range(0, len(a) - fr, fr)])
    loud = np.convolve(rms > rms.max() * 0.05, np.ones(5), mode="valid") == 5
    onset = np.where(loud)[0][0] * fr / 16000
    return a, onset


def run_scenario(echo_gain, jarvis_seconds, user_at=None, duration=None, aec_mode="auto"):
    cfg = AudioConfig(aec_mode=aec_mode)
    vad, turn = SileroVad(), SmartTurn()
    listener = Listener(cfg, vad.feed, turn.probability)
    room = SimulatedRoom(echo_gain=echo_gain, echo_delay_ms=150, speed=6.0)
    events, lock = [], threading.Lock()

    def on_listen(ev):
        with lock:
            events.append(ev)

    pipe = AudioPipeline(cfg, room, listener, loop=None, on_listen=on_listen)
    if jarvis_seconds:
        pipe.play(jarvis_speech(jarvis_seconds), 16000, "frase di Jarvis")
    onset = None
    if user_at is not None:
        ua, onset = user_utterance()
        room.say(user_at, ua, 16000)
        onset += user_at
    room.duration_s = duration or (jarvis_seconds + 2)
    pipe.start()
    assert room.done.wait(120), "simulazione bloccata"
    import time
    time.sleep(0.3)  # lascia svuotare la coda di elaborazione
    pipe.stop()
    return pipe, events, onset


def test_isolated_headphones_no_false_trigger_and_aec_turns_off():
    pipe, events, _ = run_scenario(echo_gain=0.0, jarvis_seconds=9)
    assert [e.kind for e in events] == []
    assert pipe.coupling.coupled is False
    assert pipe.aec_active is False


@pytest.mark.parametrize("echo_gain", [0.01, 0.6])  # cuffie che trasmettono un po', casse
def test_echo_no_false_barge_in_and_aec_stays_on(echo_gain):
    pipe, events, _ = run_scenario(echo_gain=echo_gain, jarvis_seconds=9)
    assert [e.kind for e in events if e.kind == "speech_start"] == []
    assert pipe.coupling.coupled is True
    assert pipe.aec_active is True


@pytest.mark.parametrize("echo_gain", [0.0, 0.6])
def test_user_talking_over_jarvis_is_detected_fast(echo_gain):
    _pipe, events, onset = run_scenario(echo_gain=echo_gain, jarvis_seconds=14, user_at=6.0, duration=14)
    starts = [e for e in events if e.kind == "speech_start"]
    assert starts, "l'utente che parla sopra Jarvis non è stato rilevato"
    first = starts[0]
    # rilevato dopo l'inizio reale della voce, entro mezzo secondo
    assert onset < first.t < onset + 0.5, (onset, first.t)


def test_aec_decision_is_remembered_for_the_same_devices(tmp_path):
    mem = tmp_path / "aec.json"
    cfg = AudioConfig()

    def pipeline(room):
        return AudioPipeline(cfg, room, Listener(cfg, SileroVad().feed, SmartTurn().probability), loop=None,
                             aec_memory=mem)

    room = SimulatedRoom(echo_gain=0.0, speed=8.0)
    room.duration_s = 7
    pipe = pipeline(room)
    pipe.play(jarvis_speech(7), 16000, "x")
    pipe.start()
    room.done.wait(60)
    pipe.stop()
    assert pipe.aec_active is False and mem.exists()
    again = pipeline(SimulatedRoom(echo_gain=0.0))
    assert again.aec_active is False  # parte già con la scelta giusta
    again.force_aec()
    assert again.aec_active is True
    assert pipeline(SimulatedRoom(echo_gain=0.0)).aec_active is True  # e ricorda anche la correzione


def video_speech(seconds):
    """'Video' per i test: frasi FLEURS di altri parlatori (né l'utente né Jarvis), come un video in cuffia."""
    parts, total = [], 0
    for i in range(20, 40):
        a, _ = sf.read(FLEURS / f"it_{i:03d}.wav", dtype="float32")
        parts.append(a)
        total += a.size
        if total > seconds * 16000:
            break
    return np.concatenate(parts)[: int(seconds * 16000)]


HOOKED_S = 2.5  # AEC3 si aggancia nei primi secondi; nel Jarvis vero succede una volta sola, all'avvio


def run_video_scenario(echo_reference="system", tap_device=None, video=True, user_at=None, jarvis_seconds=0,
                       duration=10.0, echo_gain=0.3):
    """Il 26/10: Andrea guarda YouTube con le cuffie e il suono rientra nel microfono (Jarvis capiva il video)."""
    import time

    cfg = AudioConfig(echo_reference=echo_reference)
    listener = Listener(cfg, SileroVad().feed, SmartTurn().probability)
    room = SimulatedRoom(echo_gain=echo_gain, echo_delay_ms=150, speed=4.0)
    tap = SystemOutputTap(room.loopback(tap_device), rate=room.out_rate)
    events, lock = [], threading.Lock()

    def on_listen(ev):
        with lock:
            events.append(ev)

    pipe = AudioPipeline(cfg, room, listener, loop=None, on_listen=on_listen, reference=tap)
    if video:
        room.play_other(0.5, video_speech(duration), 16000, gain=0.5)
    if jarvis_seconds:
        pipe.play(jarvis_speech(jarvis_seconds), 16000, "frase di Jarvis")
    onset = None
    if user_at is not None:
        ua, onset = user_utterance()
        room.say(user_at, ua, 16000)
        onset += user_at
    room.duration_s = duration
    pipe.start()
    assert room.done.wait(120), "simulazione bloccata"
    time.sleep(0.3)
    tap_was_running = tap.running
    pipe.stop()
    starts = [e for e in events if e.kind == "speech_start" and e.t > HOOKED_S]
    return pipe, starts, onset, tap_was_running


def test_video_in_headphones_is_not_heard():
    pipe, starts, _, _ = run_video_scenario()
    assert pipe.using_reference
    assert starts == [], [round(e.t, 2) for e in starts]
    assert pipe.aec_active and pipe.coupling.coupled  # la perdita si misura anche senza che Jarvis parli


def test_with_only_jarvis_as_reference_the_video_is_heard():
    """La prova che lo scenario è vero (com'era fino al 26/10), e che l'impostazione spegne la cattura."""
    pipe, starts, _, tap_was_running = run_video_scenario(echo_reference="jarvis")
    assert not pipe.using_reference and not tap_was_running
    assert len(starts) >= 2


def test_user_is_still_heard_over_the_video():
    _pipe, starts, onset, _ = run_video_scenario(user_at=5.0, duration=10.0)
    assert starts, "l'utente che parla sopra il video non è stato sentito"
    assert onset < starts[0].t < onset + 0.5, (onset, [round(e.t, 2) for e in starts])


def test_system_reference_never_switches_the_aec_off():
    """26/10: con il riferimento di sistema la misura cadeva mentre Andrea parlava sopra un video e la
    cancellazione d'eco si spegneva per sbaglio. Anche con cuffie isolate per davvero ora resta accesa."""
    # senza la voce dell'utente: con lei la misura a volte restava indecisa (1 volta su 5) e la regola non si provava
    pipe, _, _, _ = run_video_scenario(echo_gain=0.0)
    assert pipe.using_reference and pipe.coupling.coupled is False  # la misura dice "isolate"...
    assert pipe.aec_active  # ...ma da sola non basta a spegnere


def test_jarvis_on_another_device_keeps_his_own_voice_as_reference():
    """Se Jarvis parla da un'uscita diversa da quella catturata, la sua voce mancherebbe dal riferimento."""
    pipe, starts, _, _ = run_video_scenario(tap_device="altre casse", video=False, jarvis_seconds=9, echo_gain=0.6)
    assert not pipe.using_reference
    assert starts == []  # la sua eco si toglie lo stesso, come prima


def test_aec_turns_back_on_when_echo_appears(tmp_path):
    """Era spenta (cuffie isolate), poi la voce inizia a rientrare (es. passaggio alle casse): si riaccende."""
    import json

    room = SimulatedRoom(echo_gain=0.3, speed=8.0)
    room.duration_s = 7
    mem = tmp_path / "aec.json"
    mem.write_text(json.dumps({json.dumps(room.describe(), sort_keys=True, default=str): False}), encoding="utf-8")
    cfg = AudioConfig()
    pipe = AudioPipeline(cfg, room, Listener(cfg, SileroVad().feed, SmartTurn().probability), loop=None, aec_memory=mem)
    assert pipe.aec_active is False
    pipe.play(jarvis_speech(7), 16000, "x")
    pipe.start()
    room.done.wait(60)
    pipe.stop()
    assert pipe.aec_active is True
