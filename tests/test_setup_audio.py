"""Prova di microfono e casse del primo avvio, con un audio di Windows finto."""

import numpy as np
import pytest
import soundfile as sf

from jarvis.setup import audio_check as ac

DEVICES = [
    {"name": "Microfono (USB)", "hostapi": 1, "max_input_channels": 1, "max_output_channels": 0},
    {"name": "Altoparlanti (Realtek)", "hostapi": 1, "max_input_channels": 0, "max_output_channels": 2},
    {"name": "Cuffie (Bluetooth)", "hostapi": 1, "max_input_channels": 0, "max_output_channels": 2},
    {"name": "Microfono (MME)", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
]
HOSTAPIS = [{"name": "MME", "devices": [3]},
            {"name": "Windows WASAPI", "devices": [0, 1, 2], "default_input_device": 0, "default_output_device": 1}]


class FakeStream:
    def __init__(self, sd, **kw):
        self.sd, self.kw = sd, kw
        self.callback = kw["callback"]
        self.closed = False
        if sd.busy:
            raise RuntimeError("Unanticipated host error")

    def start(self):
        self.sd.streams.append(self)

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def feed(self, block):
        self.callback(block.reshape(-1, 1).astype(np.float32), len(block), None, None)


class FakeSD:
    def __init__(self):
        self.busy = False
        self.streams: list[FakeStream] = []
        self.played = []

    def query_hostapis(self, i=None):
        return HOSTAPIS if i is None else HOSTAPIS[i]

    def query_devices(self, i=None):
        return DEVICES if i is None else DEVICES[i]

    def WasapiSettings(self, auto_convert):
        return ("wasapi", auto_convert)

    def InputStream(self, **kw):
        return FakeStream(self, **kw)

    def play(self, data, sr, device, extra_settings):
        self.played.append((len(data), sr, device, extra_settings))


def find(kind, name):
    for i, d in enumerate(DEVICES[:3]):
        key = "max_input_channels" if kind == "input" else "max_output_channels"
        if d[key] > 0 and (name is None or name.lower() in d["name"].lower()):
            return i
    raise RuntimeError(f"nessun dispositivo di {kind} contiene '{name}'")


@pytest.fixture
def check():
    now = [0.0]
    c = ac.AudioCheck(sd=FakeSD(), find=find, clock=lambda: now[0])
    c.now = now
    return c


def tone(db: float, n: int = ac.BLOCK) -> np.ndarray:
    return np.full(n, 10 ** (db / 20), np.float32)


def test_lists_windows_devices_once(check):
    d = check.devices()
    assert d["inputs"] == ["Microfono (USB)"]  # quello MME è lo stesso visto da un'altra strada
    assert d["outputs"] == ["Altoparlanti (Realtek)", "Cuffie (Bluetooth)"]
    assert (d["default_input"], d["default_output"]) == ("Microfono (USB)", "Altoparlanti (Realtek)")


def test_level_scale():
    assert ac.level_of(tone(-80)) == 0.0
    assert ac.level_of(tone(0)) == 1.0
    assert 0.4 < ac.level_of(tone(-36)) < 0.6
    assert ac.level_of(np.zeros(0, np.float32)) == 0.0


def test_voice_is_heard_background_noise_is_not(check):
    check.start_mic(None)
    s = check.sd.streams[-1]
    assert s.kw["device"] == 0 and s.kw["samplerate"] == ac.RATE and s.kw["extra_settings"] == ("wasapi", True)
    for _ in range(200):
        s.feed(tone(-50))  # fondo della stanza
    m = check.mic()
    assert m["running"] and not m["heard"] and m["level"] < ac.HEARD_LEVEL
    for _ in range(ac.HEARD_BLOCKS):
        s.feed(tone(-20))  # qualcuno parla
    assert check.mic()["heard"]


def test_a_single_click_is_not_a_voice(check):
    check.start_mic(None)
    s = check.sd.streams[-1]
    for _ in range(ac.HEARD_BLOCKS - 1):
        s.feed(tone(-15))
    assert not check.mic()["heard"]


def test_bar_falls_slowly_after_a_peak(check):
    check.start_mic(None)
    s = check.sd.streams[-1]
    s.feed(tone(-12))
    s.feed(tone(-80))
    assert check.mic()["level"] == pytest.approx(ac.DECAY, abs=1e-6)


def test_microphone_closes_by_itself(check):
    check.start_mic("usb")
    s = check.sd.streams[-1]
    check.now[0] += ac.MIC_SECONDS + 1
    assert check.mic()["running"] is False and s.closed


def test_restarting_closes_the_previous_stream(check):
    check.start_mic(None)
    first = check.sd.streams[-1]
    check.start_mic(None)
    assert first.closed and len(check.sd.streams) == 2


@pytest.mark.parametrize("problem", ["busy", "missing"])
def test_a_microphone_that_does_not_open_is_reported(check, problem):
    if problem == "busy":
        check.sd.busy = True
        check.start_mic(None)
    else:
        check.start_mic("inesistente")
    m = check.mic()
    assert not m["running"] and m["error"]


def test_plays_on_the_chosen_output(check, tmp_path):
    f = tmp_path / "prova.wav"
    sf.write(f, np.zeros(24000, np.float32), 24000)
    assert check.play(f, "cuffie") == pytest.approx(1.0)
    assert check.sd.played == [(24000, 24000, 2, ("wasapi", True))]
