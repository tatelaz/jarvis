"""L'audio che sparisce, non si apre o cambia non fa mai cadere Jarvis.

Successo davvero: "Error starting stream: Unanticipated host error" all'avvio con le cuffie occupate, Jarvis cadeva,
il guardiano lo riavviava (motori ricaricati ogni volta) e alla terza caduta sarebbe tornato alla versione di codice
precedente, come se la colpa fosse del codice. Qui i dispositivi sono finti e comandati dal test.
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import sounddevice

from jarvis.audio import devices
from jarvis.audio.devices import RATE, ResilientBackend
from jarvis.audio.pipeline import AudioPipeline
from jarvis.config import AudioConfig

BLOCK = RATE // 100  # 10 ms


class FakeDevice:
    def __init__(self, world, in_name, out_name):
        self.world, self.in_name, self.out_name = world, in_name, out_name
        self.started = self.stopped = self.dead = False

    def start(self, on_input, on_output):
        if self.world.fail:
            raise sounddevice.PortAudioError(self.world.fail)
        self.started = True

    def stalled(self, seconds):
        return self.dead

    def stop(self):
        self.stopped = True

    def latency_ms(self):
        return (12.0, 8.0)


class World:
    """Il PC visto dal test: cosa è predefinito in Windows, se aprire un dispositivo riesce, quando è adesso."""

    def __init__(self):
        self.fail = None
        self.defaults = ("Microfono", "Casse")
        self.opens: list[FakeDevice] = []
        self.rescans = 0
        self.rescan_threads: list[str] = []
        self.now = 0.0
        self.name_suffix = ""  # PortAudio che chiama il dispositivo in modo un po' diverso da Windows

    def open(self):
        d = FakeDevice(self, self.defaults[0], self.defaults[1] + self.name_suffix)
        self.opens.append(d)
        return d

    def rescan(self):
        self.rescans += 1
        self.rescan_threads.append(threading.current_thread().name)


@pytest.fixture
def world():
    return World()


@pytest.fixture
def make(world):
    made = []

    def build(**kw):
        changes = []
        b = ResilientBackend(open_device=world.open, defaults=lambda: world.defaults, rescan=world.rescan,
                             on_change=changes.append, check_s=3600, clock=lambda: world.now, **kw)
        made.append(b)
        return b, changes

    yield build
    for b in made:
        b.stop()


def test_audio_that_does_not_open_does_not_stop_jarvis(world, make):
    world.fail = "Error starting stream: Unanticipated host error [PaErrorCode -9999]"
    b, changes = make()
    outputs, inputs = [], []
    b.start(inputs.append, lambda n: outputs.append(n) or np.zeros(n, np.float32))  # nessuna eccezione
    assert changes[-1]["ok"] is False and "Unanticipated host error" in changes[-1]["error"]
    assert b.describe()["output"] == "nessuno"
    time.sleep(0.4)  # senza audio il tempo scorre lo stesso: la conversazione scritta va avanti
    assert 20 <= len(outputs) <= 50 and set(outputs) == {BLOCK}
    assert all(x.size == BLOCK and not x.any() for x in inputs)  # al posto del microfono, silenzio

    world.fail = None
    world.now = 0.5
    b.check()
    assert b.device is None  # il primo nuovo tentativo è dopo 1 s, non a raffica
    world.now = 1.0
    b.check()
    assert b.status() == {"ok": True, "input": "Microfono", "output": "Casse", "error": None}
    assert changes[-1]["reason"] == "audio tornato" and changes[-1]["outage_s"] == 1.0
    n = len(outputs)
    time.sleep(0.1)
    assert len(outputs) == n  # tornato il dispositivo, l'orologio muto si ferma: mai due fonti insieme


def test_portaudio_restarts_in_the_thread_that_opens(world, make):
    """WASAPI vuole COM pronto nel thread che apre i flussi: PortAudio si riavvia lì prima del primo tentativo."""
    b, _ = make()
    b.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
    assert world.rescan_threads == [threading.current_thread().name]


def test_retries_slow_down_and_say_it_once(world, make):
    world.fail = "dispositivo occupato"
    b, changes = make()
    b.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
    attempts = []
    for t in np.arange(0.0, 21.0, 0.5):
        world.now = float(t)
        before = len(world.opens)
        b.check()
        if len(world.opens) > before:
            attempts.append(float(t))
    assert attempts == [1.0, 3.0, 6.0, 11.0, 16.0]  # 1, 2, 3, poi ogni 5 s
    assert [c["ok"] for c in changes] == [False]  # "audio assente" detto una volta, non a ogni tentativo
    assert world.rescans == 1 + len(attempts)  # all'avvio e a ogni tentativo PortAudio rilegge i dispositivi


def test_device_that_stops_answering_is_reopened(world, make):
    b, changes = make()
    b.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
    first = b.device
    b.check()
    assert b.device is first  # sano: nessun intervento
    first.dead = True
    b.check()
    assert first.stopped and b.device is not first and b.device.started
    assert changes[-1]["ok"] and changes[-1]["reason"] == "il dispositivo ha smesso di rispondere"


def test_unplugged_device_goes_silent_then_comes_back(world, make):
    b, changes = make()
    outputs = []
    b.start(lambda _b: None, lambda n: outputs.append(n) or np.zeros(n, np.float32))
    b.device.dead = True
    world.fail = "dispositivo scollegato"
    world.now = 10.0
    b.check()
    assert b.device is None and changes[-1]["ok"] is False
    time.sleep(0.15)
    assert outputs  # si va avanti senza audio
    world.fail = None
    world.now = 25.0
    b.check()
    assert changes[-1]["ok"] and changes[-1]["outage_s"] == 15.0


def test_follows_the_windows_default_device(world, make):
    b, changes = make()
    b.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
    world.defaults = ("Microfono", "Cuffie")  # cuffie collegate: Windows le mette come predefinite
    b.check()
    assert b.device.out_name == "Cuffie" and world.opens[0].stopped
    assert changes[-1]["reason"] == "nuovo dispositivo predefinito"
    opens = len(world.opens)
    for _ in range(5):
        b.check()
    assert len(world.opens) == opens


def test_never_loops_when_names_do_not_match(world, make):
    world.name_suffix = " (2)"
    b, _ = make()
    b.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
    world.defaults = ("Microfono", "Cuffie")
    b.check()
    opens = len(world.opens)
    for _ in range(5):  # "Cuffie (2)" non sarà mai uguale a "Cuffie": un cambio, un tentativo, poi basta
        b.check()
    assert len(world.opens) == opens


def test_a_device_chosen_by_name_stays_put(world, make):
    b, _ = make(output_name="Casse")
    b.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
    world.defaults = ("Microfono", "Cuffie")
    b.check()
    assert len(world.opens) == 1


def test_stop_ends_everything(world, make):
    world.fail = "occupato"
    b, _ = make()
    b.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
    b.stop()
    assert b._silence is None and not b._watcher.is_alive()
    world.fail, world.now = None, 100.0
    b.check()
    assert b.device is None  # fermato: niente più tentativi


def test_echo_is_measured_again_on_new_devices(world, make, tmp_path):
    """Dalle casse alle cuffie: la cancellazione d'eco riparte dalla decisione ricordata per le cuffie, e quella
    forzata con le casse non resta bloccata."""
    b, _ = make()

    def key(out):
        return json.dumps({"input": "Microfono", "output": out, "in_rate": RATE, "out_rate": RATE}, sort_keys=True)

    mem = tmp_path / "aec.json"
    mem.write_text(json.dumps({key("Casse"): True, key("Cuffie"): False}), encoding="utf-8")
    pipe = AudioPipeline(AudioConfig(aec_mode="auto"), b, listener=None, aec_memory=mem)
    b.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
    pipe.devices_changed()
    pipe.force_aec()
    assert pipe.aec_active and pipe._aec_locked
    world.defaults = ("Microfono", "Cuffie")
    b.check()
    pipe.devices_changed()
    assert not pipe._aec_locked and pipe.aec_active is False
    pipe.aec.close()


def test_real_device_closes_the_output_if_the_microphone_fails(monkeypatch):
    """Uscita partita, microfono rifiutato: prima l'uscita restava aperta per sempre."""
    made = []

    class Stream:
        def __init__(self, **kw):
            self.kw, self.active, self.closed = kw, False, False
            made.append(self)

        def start(self):
            if self.kind == "in":
                raise sounddevice.PortAudioError("Error starting stream")
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.closed = True

    class Out(Stream):
        kind = "out"

    class In(Stream):
        kind = "in"

    info = {"name": "Cuffie", "hostapi": 2, "max_input_channels": 1, "max_output_channels": 2,
            "default_samplerate": 44100.0}
    monkeypatch.setattr(devices, "find_device", lambda kind, name: 0 if kind == "input" else 1)
    monkeypatch.setattr(devices, "_wasapi_index", lambda: 2)
    monkeypatch.setattr(sounddevice, "query_devices", lambda i=None: info)
    monkeypatch.setattr(sounddevice, "OutputStream", Out)
    monkeypatch.setattr(sounddevice, "InputStream", In)
    dev = devices.SoundDeviceBackend()
    with pytest.raises(sounddevice.PortAudioError):
        dev.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
    out = next(s for s in made if s.kind == "out")
    assert out.closed and not out.active
    # sempre 48 kHz con la conversione di Windows: un dispositivo a 44,1 kHz non cambia nulla a valle
    assert all(s.kw["samplerate"] == RATE and s.kw["extra_settings"] is not None for s in made)


def test_windows_default_names_for_both_kinds_of_pycaw_objects(monkeypatch):
    """pycaw dà le casse con il nome ma il microfono come puntatore grezzo: entrambi devono dare un nome
    (prima il microfono risultava sempre None e un cambio di microfono non veniva mai seguito)."""
    import pycaw.pycaw as pc

    class Wrapped:
        def __init__(self, name):
            self.FriendlyName = name

    class RawPointer:  # come un POINTER(IMMDevice): niente FriendlyName
        name = "Microfono USB"

    monkeypatch.setattr(pc.AudioUtilities, "GetMicrophone", staticmethod(lambda: RawPointer()))
    monkeypatch.setattr(pc.AudioUtilities, "GetSpeakers", staticmethod(lambda: Wrapped("Cuffie")))
    monkeypatch.setattr(pc.AudioUtilities, "CreateDevice", staticmethod(lambda raw: Wrapped(raw.name)))
    assert devices.default_names() == ("Microfono USB", "Cuffie")

    def gone():
        raise OSError("nessun dispositivo")

    monkeypatch.setattr(pc.AudioUtilities, "GetSpeakers", staticmethod(gone))
    assert devices.default_names() == ("Microfono USB", None)  # un dispositivo che manca non nasconde l'altro


@pytest.mark.hardware
def test_real_devices_open_and_match_windows_defaults():
    """Sul PC vero (uscita muta): i predefiniti di Windows hanno lo stesso nome dei dispositivi che PortAudio apre."""
    changes, ins = [], []
    b = ResilientBackend(on_change=changes.append, check_s=3600)
    b.start(lambda blk: ins.append(blk.size), lambda n: np.zeros(n, np.float32))
    try:
        assert changes[-1]["ok"], changes[-1]
        assert devices.default_names() == (b.device.in_name, b.device.out_name)
        time.sleep(1.0)
        assert 70 <= len(ins) <= 130 and set(ins) == {BLOCK}
        first = b.device
        b.check()
        assert b.device is first  # sano e già sul predefinito: nessuna riapertura
    finally:
        b.stop()


class _Notes:
    def __init__(self):
        self.said = []

    def notify(self, text):
        self.said.append(text)


class _Pipe:
    def __init__(self):
        self.changed = 0

    def devices_changed(self):
        self.changed += 1


@pytest.mark.parametrize("st, mentioned", [
    ({"ok": False, "input": None, "output": None, "error": "occupato", "reason": "avvio"}, False),
    ({"ok": True, "input": "Mic", "output": "Cuffie", "error": None, "reason": "audio tornato", "outage_s": 3.0},
     False),  # cuffie ricollegate al volo: basta la finestra
    ({"ok": True, "input": "Mic", "output": "Cuffie", "error": None, "reason": "audio tornato", "outage_s": 42.0},
     True),
    ({"ok": True, "input": "Mic", "output": "Cuffie", "error": None, "reason": "nuovo dispositivo predefinito"}, False),
])
def test_jarvis_reacts_to_audio_changes(st, mentioned):
    from jarvis.app import Jarvis
    from jarvis.core.bus import Bus

    j = Jarvis.__new__(Jarvis)  # solo quello che serve, senza motori né file veri
    j.bus, j.pipeline, j.conv = Bus(), _Pipe(), _Notes()
    j._audio_changed(st)
    assert j.pipeline.changed == 1  # l'eco si rimisura sempre
    assert j.bus.state["audio"]["ok"] is st["ok"]  # chi apre la finestra dopo lo vede
    assert bool(j.conv.said) is mentioned
    if mentioned:
        assert "42 secondi" in j.conv.said[0] and "Cuffie" in j.conv.said[0]


def test_window_snapshot_carries_the_audio_state():
    from types import SimpleNamespace

    from jarvis.config import Config
    from jarvis.core.bus import Bus
    from jarvis.ui.server import UiServer

    bus = Bus()
    bus.emit("audio", sticky=True, ok=False, input=None, output=None, error="occupato", reason="avvio")
    snap = UiServer(SimpleNamespace(bus=bus, cfg=Config(), tasks=None))._snapshot()
    assert snap["audio"]["ok"] is False and snap["audio"]["error"] == "occupato"


SOUNDDEVICE_LOADED_ELSEWHERE = '''
import sys, threading
sys.path.insert(0, ROOT)
t = threading.Thread(target=lambda: __import__("sounddevice").query_devices())  # come l'autodiagnosi
t.start()
t.join()
import numpy as np
from jarvis.audio.devices import ResilientBackend
changes = []
b = ResilientBackend(on_change=changes.append, check_s=3600)
b.start(lambda _b: None, lambda n: np.zeros(n, np.float32))
b.stop()
print("OK" if changes[0]["ok"] else "ERRORE: " + str(changes[0]["error"]))
'''


@pytest.mark.hardware
def test_audio_opens_at_first_try_even_if_sounddevice_was_loaded_in_another_thread():
    """La causa vera della caduta del 6/10, sul PC vero e in un processo nuovo: sounddevice caricato in un thread
    di lavoro (l'autodiagnosi del microfono), flussi aperti dal thread principale -> "Unanticipated host error"."""
    root = str(Path(__file__).resolve().parents[1])
    out = subprocess.run([sys.executable, "-c", SOUNDDEVICE_LOADED_ELSEWHERE.replace("ROOT", repr(root))],
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().endswith("OK"), out.stdout + out.stderr
