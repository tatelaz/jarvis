"""Cattura di ciò che suona sull'uscita (riferimento dell'eco): dati, guasti, cambio di dispositivo."""

import contextlib
import threading
import time

import numpy as np

from jarvis.audio.loopback import SystemOutputTap


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeOutput:
    """Un'uscita che suona un tono a 48 kHz; può rifiutarsi di aprire le prime volte (come un formato non gestito)."""

    def __init__(self, failures=0, name="Cuffie", stall_s=0.0):
        self.failures, self.name, self.stall_s = failures, name, stall_s
        self.opened = 0
        self.lock = threading.Lock()

    @contextlib.contextmanager
    def __call__(self, rate):
        with self.lock:
            self.opened += 1
            if self.failures:
                self.failures -= 1
                raise AssertionError  # come soundcard con un formato che non conosce: nessun messaggio
        t = [0]

        def read():
            time.sleep(0.01)
            if self.stall_s and t[0] >= rate // 2:  # dopo mezzo secondo, un solo intoppo
                time.sleep(self.stall_s)
                self.stall_s = 0.0
            n = rate // 100
            block = 0.3 * np.sin(2 * np.pi * 440 * (t[0] + np.arange(n)) / rate).astype(np.float32)
            t[0] += n
            return block

        yield self.name, read


def test_tap_delivers_audio_at_16k_and_stops_cleanly():
    tap = SystemOutputTap(FakeOutput(), rate=48000)
    tap.start()
    assert wait_for(lambda: tap.alive)
    tap.drain()
    time.sleep(0.3)
    audio = tap.drain()
    assert 0.2 * 16000 < audio.size < 0.45 * 16000  # ~0,3 s a 16 kHz (non a 48)
    assert 0.15 < float(np.sqrt(np.mean(audio ** 2))) < 0.25  # il tono c'è (0,3 di picco → 0,21 efficace)
    assert tap.device == "Cuffie"
    tap.stop()
    assert not tap.alive and not tap.running and tap.drain().size == 0


def test_failure_means_no_reference_and_a_later_retry():
    out = FakeOutput(failures=1)
    tap = SystemOutputTap(out, rate=48000, retry_s=0.1)
    tap.start()
    assert wait_for(lambda: tap.error is not None)
    assert tap.error == "AssertionError" and not tap.alive  # la pipeline torna alla voce di Jarvis
    assert wait_for(lambda: tap.alive)  # nuovo tentativo: riuscito
    assert tap.error is None and out.opened == 2
    tap.stop()


def test_a_one_second_hiccup_does_not_drop_the_reference():
    """26/10 dal vivo: caricando un modello (CPU piena) la cattura è arrivata in ritardo e il riferimento saltava."""
    out = FakeOutput(stall_s=1.0)
    tap = SystemOutputTap(out, rate=48000)
    tap.start()
    assert wait_for(lambda: tap.alive)
    seen_dead = False
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        seen_dead |= not tap.alive
        time.sleep(0.02)
    tap.stop()
    assert out.stall_s == 0.0 and not seen_dead  # l'intoppo c'è stato, il riferimento no


def test_library_is_prepared_in_the_capture_thread_before_com():
    """26/10, sul PC vero: soundcard importato dopo l'inizializzazione di COM falliva con "Error 0x100000001"."""
    order = []
    out = FakeOutput()

    def prepare():
        order.append(("prepara", threading.current_thread().name))

    real = out.__call__

    @contextlib.contextmanager
    def opener(rate):
        order.append(("apre", threading.current_thread().name))
        with real(rate) as dev:
            yield dev

    opener.prepare = prepare
    tap = SystemOutputTap(opener, rate=48000)
    tap.start()
    assert wait_for(lambda: tap.alive)
    tap.stop()
    assert order == [("prepara", "jarvis-loopback"), ("apre", "jarvis-loopback")]


def test_device_change_reopens_and_old_audio_is_not_kept_forever():
    out = FakeOutput()
    tap = SystemOutputTap(out, rate=48000, keep_s=0.2)
    tap.start()
    assert wait_for(lambda: tap.alive)
    tap.restart()  # cuffie collegate, casse: il predefinito cambia
    assert wait_for(lambda: out.opened == 2)
    time.sleep(0.5)  # nessuno legge (audio fermo): si tiene solo la coda
    assert tap.drain().size <= 0.2 * 16000 + 160
    tap.stop()
