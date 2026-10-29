"""Riferimento per l'eco: ciò che esce davvero dalle cuffie o dalle casse (cattura "loopback" di Windows).

Il 26/10 l'utente guardava YouTube con le cuffie chiuse e Jarvis capiva le parole del video: il suono delle cuffie
rientra nel microfono (misurato: correlazione 0,64 tra uscita e microfono, il video entra attenuato di soli 26 dB).
La cancellazione d'eco conosceva solo la voce di Jarvis; con questo riferimento conosce tutto ciò che suona (video,
musica, giochi e Jarvis stesso). Provato sullo stesso PC con 15 s di video: −25/−38 dB nel microfono.

L'audio catturato resta in memoria il tempo di arrivare alla cancellazione d'eco: non viene mai salvato.
Vale solo se Jarvis parla dallo stesso dispositivo (altrimenti la sua voce mancherebbe dal riferimento): lo decide
la pipeline confrontando i nomi.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager

import numpy as np
import soxr

log = logging.getLogger("jarvis.audio")
SR = 16000
# apre la cattura a una frequenza data: restituisce il nome del dispositivo e una funzione che legge il blocco
# successivo (mono, float32); per le prove si passa una sorgente finta
Opener = Callable[[int], AbstractContextManager[tuple[str, Callable[[], np.ndarray]]]]


@contextlib.contextmanager
def default_output_loopback(rate: int) -> Iterator[tuple[str, Callable[[], np.ndarray]]]:
    """L'uscita predefinita di Windows in cattura loopback (WASAPI condiviso, tramite soundcard)."""
    import warnings

    import soundcard as sc

    # "data discontinuity" arriva quando Windows salta un pacchetto: AEC3 si riaggancia da solo
    warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
    speaker = sc.default_speaker()
    source = sc.get_microphone(speaker.id, include_loopback=True)
    with source.recorder(samplerate=rate, channels=speaker.channels) as rec:

        def read() -> np.ndarray:
            block = rec.record(numframes=None)  # quello che c'è (almeno un pacchetto, o zeri se tutto tace)
            return block.mean(axis=1) if block.ndim == 2 else block

        yield speaker.name, read


def _load_soundcard() -> None:
    """soundcard inizializza COM quando viene importato e tratta come errore trovarlo già pronto (provato sul PC:
    "Error 0x100000001"): si importa nel thread della cattura prima di inizializzare COM. Mai nel thread principale,
    che cambierebbe modo per tutti (pycaw, PortAudio)."""
    import soundcard  # noqa: F401


default_output_loopback.prepare = _load_soundcard  # type: ignore[attr-defined]


def _com_init() -> bool:
    """COM nel thread che apre la cattura (la lezione del 6/10 con WASAPI). True se va chiuso alla fine."""
    try:
        return ctypes.windll.ole32.CoInitializeEx(None, 0) in (0, 1)  # 0 = MULTITHREADED; S_OK o S_FALSE
    except (AttributeError, OSError):
        return False


class SystemOutputTap:
    """Cattura in un thread proprio ciò che suona sull'uscita predefinita, a 16 kHz, per la cancellazione d'eco."""

    # stale_s: con 0,5 s il riferimento saltava per un attimo quando Jarvis carica un modello (CPU piena, 26/10)
    def __init__(self, opener: Opener = default_output_loopback, rate: int = 48000, stale_s: float = 1.5,
                 retry_s: float = 5.0, keep_s: float = 2.0) -> None:
        self._opener, self.rate = opener, rate
        self.stale_s, self.retry_s = stale_s, retry_s
        self._keep = int(keep_s * SR)
        self._buf: deque[np.ndarray] = deque()
        self._buf_n = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reopen = threading.Event()  # anche stop() lo accende: sveglia l'attesa prima di un nuovo tentativo
        self._thread: threading.Thread | None = None
        self._last_at = 0.0
        self.device: str | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def alive(self) -> bool:
        """Dati freschi: il riferimento si può usare (altrimenti la pipeline torna alla sola voce di Jarvis)."""
        return self.running and self.device is not None and time.monotonic() - self._last_at < self.stale_s

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._reopen.clear()
        self._thread = threading.Thread(target=self._run, name="jarvis-loopback", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._reopen.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        self.device = None
        with self._lock:
            self._buf.clear()
            self._buf_n = 0

    def restart(self) -> None:
        """Il dispositivo predefinito è cambiato (cuffie collegate, casse): si riapre la cattura su quello nuovo."""
        self._reopen.set()

    def drain(self) -> np.ndarray:
        """Tutto l'audio arrivato dall'ultima volta (16 kHz)."""
        with self._lock:
            if not self._buf:
                return np.zeros(0, np.float32)
            out = np.concatenate(self._buf)
            self._buf.clear()
            self._buf_n = 0
        return out

    def _push(self, block: np.ndarray) -> None:
        if not block.size:
            return
        with self._lock:
            self._buf.append(block)
            self._buf_n += block.size
            while self._buf_n > self._keep and len(self._buf) > 1:  # nessuno legge (audio fermo): solo la coda
                self._buf_n -= self._buf.popleft().size
        self._last_at = time.monotonic()

    def _run(self) -> None:
        with contextlib.suppress(Exception):  # se fallisce, l'errore vero arriva all'apertura e si registra lì
            getattr(self._opener, "prepare", lambda: None)()
        com = _com_init()
        try:
            while not self._stop.is_set():
                self._reopen.clear()
                try:
                    with self._opener(self.rate) as (name, read):
                        rs = soxr.ResampleStream(self.rate, SR, 1, dtype="float32")
                        if name != self.device or self.error:
                            log.info("riferimento d'eco pronto: tutto ciò che suona su %s", name)
                        self.device, self.error = name, None
                        while not self._reopen.is_set():
                            block = np.asarray(read(), np.float32)
                            if block.size:
                                self._push(rs.resample_chunk(block))
                            else:
                                time.sleep(0.005)
                except Exception as e:  # dispositivo sparito, formato non gestito...: intanto vale la voce di Jarvis
                    why = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                    if why != self.error:
                        log.warning("riferimento d'eco dalle cuffie non disponibile (%s): uso la voce di Jarvis", why)
                    self.device, self.error = None, why
                    self._reopen.wait(self.retry_s)
        finally:
            if com:
                ctypes.windll.ole32.CoUninitialize()
