"""Pipeline audio in tempo reale: dispositivo ⇄ eco ⇄ turni di parola ⇄ riproduzione.

Thread coinvolti:
- callback del dispositivo (PortAudio): legge il microfono e chiede campioni al Player. Non fa
  calcoli pesanti: copia i dati in code;
- thread di elaborazione: ricampiona a 16 kHz, cancella l'eco (se serve), passa al Listener;
- loop asyncio di Jarvis: riceve gli eventi tramite call_soon_threadsafe.

Cancellazione d'eco in modalità automatica, decisa misurando quanto la voce di Jarvis rientra nel
microfono (picco di correlazione tra uscita e ingresso):
- isolamento vero (≤ 0,035): AEC spento. Acceso senza un'eco da imparare, AEC3 attenua la voce
  dell'utente mentre Jarvis parla e l'interruzione arriva in ritardo (misurato: +2,2 s);
- cuffie che trasmettono (dal vivo, a volume 100%: 0,07) o casse: AEC acceso (−37/−41 dB);
- se nonostante tutto la conversazione riconosce la propria eco, force_aec() lo riaccende e lo
  blocca acceso per il resto della sessione.

Riferimento dell'eco: se c'è, ciò che suona davvero sull'uscita (loopback.py: video e musica compresi), così le
parole di un video ascoltato in cuffia non entrano dal microfono; altrimenti i campioni che manda Jarvis.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np
import soxr

from ..config import AudioConfig
from .aec import EchoCanceller
from .listener import Listener, ListenEvent
from .loopback import SystemOutputTap
from .player import Player, Segment

log = logging.getLogger("jarvis.audio")
SR = 16000


class Backend(Protocol):
    in_rate: int
    out_rate: int

    def start(self, on_input: Callable[[np.ndarray], None], on_output: Callable[[int], np.ndarray]) -> None: ...
    def stop(self) -> None: ...
    def latency_ms(self) -> tuple[float, float]: ...
    def describe(self) -> dict: ...


class EchoCouplingEstimator:
    """Stima quanto l'uscita rientra nel microfono: picco di correlazione normalizzata tra uscita e
    ingresso grezzo, su ritardi 0-600 ms. Decide con isteresi su più finestre."""

    def __init__(self, window_s: float = 1.5, max_lag_ms: int = 600, on_threshold: float = 0.05,
                 off_threshold: float = 0.035, votes: int = 3) -> None:
        self.n = int(window_s * SR)
        self.max_lag = int(max_lag_ms * SR / 1000)
        self.on_t, self.off_t, self.votes = on_threshold, off_threshold, votes
        self._far: deque[np.ndarray] = deque()
        self._mic: deque[np.ndarray] = deque()
        self._far_n = self._mic_n = 0
        self.history: deque[float] = deque(maxlen=votes)
        self.coupled: bool | None = None  # None = non ancora deciso
        self.last_value: float | None = None

    def add(self, far: np.ndarray, mic: np.ndarray, far_active: bool) -> None:
        self._far.append(far)
        self._mic.append(mic)
        self._far_n += far.size
        self._mic_n += mic.size
        if min(self._far_n, self._mic_n) < self.n + self.max_lag:
            return
        f = np.concatenate(self._far)[-(self.n + self.max_lag):]
        m = np.concatenate(self._mic)[-(self.n + self.max_lag):]
        self._far.clear()
        self._mic.clear()
        self._far_n = self._mic_n = 0
        # niente pausa durante la "voce dell'utente": con l'AEC spento quella voce può essere proprio l'eco
        # da scoprire (circolo vizioso trovato dai test); le tre misure concordi bastano come protezione
        if not far_active or np.sqrt(np.mean(f ** 2)) < 1e-3:
            return
        self.last_value = self.measure(f, m)
        self.history.append(self.last_value)
        if len(self.history) == self.votes:
            if all(v >= self.on_t for v in self.history):
                self.coupled = True
            elif all(v <= self.off_t for v in self.history):
                self.coupled = False

    def measure(self, far: np.ndarray, mic: np.ndarray) -> float:
        """Picco della correlazione normalizzata con ritardi 0..max_lag (mic in ritardo rispetto a far)."""
        nfft = 1 << int(np.ceil(np.log2(far.size + mic.size)))
        F = np.fft.rfft(far - far.mean(), nfft)
        M = np.fft.rfft(mic - mic.mean(), nfft)
        xc = np.fft.irfft(M * np.conj(F), nfft)[: self.max_lag + 1]
        # normalizzazione grossolana sull'energia delle due finestre
        denom = np.sqrt(np.sum(far ** 2) * np.sum(mic ** 2)) + 1e-12
        return float(np.max(np.abs(xc)) / denom)


class AudioPipeline:
    def __init__(self, cfg: AudioConfig, backend: Backend, listener: Listener,
                 loop: asyncio.AbstractEventLoop | None = None,
                 on_listen: Callable[[ListenEvent], None] | None = None,
                 on_player: Callable[[str, Segment], None] | None = None,
                 on_level: Callable[[float, float], None] | None = None,
                 aec_memory: Path | None = None, reference: SystemOutputTap | None = None) -> None:
        self.cfg = cfg
        self.backend = backend
        self.listener = listener
        self.loop = loop
        self._on_listen = on_listen
        self._on_player = on_player
        self._on_level = on_level
        self.player = Player(backend.out_rate, on_event=self._player_event)
        self._in_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._far_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._in_rs = soxr.ResampleStream(backend.in_rate, SR, 1, dtype="float32")
        self._out_rs = soxr.ResampleStream(backend.out_rate, SR, 1, dtype="float32")
        self.aec = EchoCanceller(noise_suppression=cfg.noise_suppression)
        self.coupling = EchoCouplingEstimator()
        self.aec_active = cfg.aec_mode != "off"  # in "auto" si parte acceso (prudente) e si decide misurando
        self._aec_locked = cfg.aec_mode != "auto"
        # la decisione presa per questa coppia di dispositivi si ricorda tra un avvio e l'altro
        self._aec_memory = aec_memory
        self._device_key = json.dumps(backend.describe(), sort_keys=True, default=str)
        self._recall_aec()
        self.reference = reference
        self.using_reference = False  # True = l'eco si toglie su tutto ciò che suona, non solo sulla voce di Jarvis
        self._output_name = backend.describe().get("output")
        # orologio audio in campioni d'uscita: rende la logica indipendente dal tempo reale
        # (e quindi testabile anche simulando più veloce del vero)
        self._out_clock = 0
        self._last_far_sample = -(10 ** 9)
        self._running = False
        self._thread: threading.Thread | None = None
        self.last_input_at = 0.0
        self.dropped_blocks = 0
        self.stats = {"blocks": 0, "events": 0}
        self._level_acc = [0.0, 0.0, 0]

    # ------------------------------------------------------------ ciclo di vita

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, name="jarvis-audio", daemon=True)
        self._thread.start()
        self.backend.start(self._on_input, self._on_output)
        self._output_name = self.backend.describe().get("output")
        lin, lout = self.backend.latency_ms()
        self.aec.set_delay(int(lin + lout))
        log.info("audio avviato: %s (latenza in %.0f ms, out %.0f ms)", self.backend.describe(), lin, lout)

    def devices_changed(self) -> None:
        """Altri dispositivi (cuffie collegate, predefinito cambiato, audio tornato): l'eco si rimisura da capo,
        partendo da quanto deciso l'ultima volta per questa coppia di dispositivi."""
        self._device_key = json.dumps(self.backend.describe(), sort_keys=True, default=str)
        self._output_name = self.backend.describe().get("output")
        if self.reference is not None:
            self.reference.restart()  # il loopback segue il nuovo dispositivo predefinito
        self.coupling = EchoCouplingEstimator()
        if self.cfg.aec_mode == "auto":
            self._aec_locked = False  # l'eco sentita con le casse non dice niente delle cuffie
            self.aec_active = True
            self._recall_aec()
        lin, lout = self.backend.latency_ms()
        self.aec.set_delay(int(lin + lout))
        log.info("dispositivi audio: %s (cancellazione d'eco %s)", self.backend.describe(),
                 "accesa" if self.aec_active else "spenta")

    def _recall_aec(self) -> None:
        if self._aec_memory is None or self._aec_locked:
            return
        with contextlib.suppress(OSError, ValueError, KeyError):
            self.aec_active = bool(json.loads(self._aec_memory.read_text(encoding="utf-8"))[self._device_key])

    def stop(self) -> None:
        self._running = False
        with contextlib.suppress(Exception):
            self.backend.stop()
        if self._thread:
            self._thread.join(timeout=2)
        if self.reference is not None:
            self.reference.stop()
        self.aec.close()

    # ------------------------------------------------------------ callback del dispositivo

    def _on_input(self, block: np.ndarray) -> None:
        self.last_input_at = time.monotonic()
        try:
            self._in_q.put_nowait(block)
        except queue.Full:
            self.dropped_blocks += 1

    def _on_output(self, n: int) -> np.ndarray:
        out = self.player.render(n)
        self._out_clock += n
        if np.any(out):
            self._last_far_sample = self._out_clock
        far16 = self._out_rs.resample_chunk(out)
        try:
            self._far_q.put_nowait(far16)
        except queue.Full:
            self.dropped_blocks += 1
        return out

    # ------------------------------------------------------------ elaborazione

    @property
    def jarvis_speaking(self) -> bool:
        # la coda dell'eco (riverbero, latenza) dura ancora un po' dopo l'ultimo campione
        return self.player.speaking or (self._out_clock - self._last_far_sample) < 0.4 * self.backend.out_rate

    def _process_loop(self) -> None:
        while self._running:
            try:
                block = self._in_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._process_block(block)
            except Exception:  # un blocco difettoso non deve fermare l'ascolto
                log.exception("errore nell'elaborazione audio")

    def _process_block(self, block: np.ndarray) -> None:
        mic = self._in_rs.resample_chunk(block)
        far_parts = []
        while True:
            try:
                far_parts.append(self._far_q.get_nowait())
            except queue.Empty:
                break
        far = np.concatenate(far_parts) if far_parts else np.zeros(0, np.float32)
        system = self._system_reference()
        echo_ref = far if system is None else system
        speaking = self.jarvis_speaking
        if self.cfg.aec_mode != "off":
            if echo_ref.size:
                self.aec.push_far(echo_ref)
            clean = self.aec.process(mic)
            # col riferimento di sistema l'eco si misura su tutto ciò che suona, non solo mentre parla Jarvis
            self.coupling.add(echo_ref if echo_ref.size else np.zeros(mic.size, np.float32), mic,
                              speaking or system is not None)
            # col riferimento di sistema si misura anche mentre l'utente parla sopra un video: la sua voce copre
            # l'eco e la misura crolla (26/10: 0,010, spenta per sbaglio e un attimo dopo Jarvis sentiva la propria
            # eco). Lì la misura può solo accendere; spegnere resta alla misura sulla voce di Jarvis
            if (not self._aec_locked and self.coupling.coupled is not None
                    and self.coupling.coupled != self.aec_active and (self.coupling.coupled or system is None)):
                self.aec_active = self.coupling.coupled
                log.info("cancellazione d'eco %s (accoppiamento misurato %.3f)",
                         "attivata" if self.aec_active else "disattivata: isolamento completo",
                         self.coupling.last_value or 0)
                self._remember_aec()
            if not self.aec_active:
                clean = mic
        else:
            clean = mic
        self.listener.set_jarvis_speaking(speaking)
        events = self.listener.feed(clean)
        self.stats["blocks"] += 1
        self._meter(mic, far)
        for ev in events:
            self.stats["events"] += 1
            self._post(self._on_listen, ev)

    def _system_reference(self) -> np.ndarray | None:
        """Ciò che suona davvero sull'uscita (video compresi), se si può usare come riferimento dell'eco.

        None = si usa la voce di Jarvis: impostazione "jarvis", cattura non disponibile o ferma, oppure Jarvis parla
        da un altro dispositivo (la sua voce mancherebbe dal riferimento e la sua eco resterebbe).
        """
        tap = self.reference
        if tap is None:
            return None
        wanted = self.cfg.echo_reference == "system" and self.cfg.aec_mode != "off"
        if wanted and not tap.running:
            tap.start()
        elif not wanted and tap.running:
            tap.stop()  # niente cattura che non serve
        audio = tap.drain()
        same = (tap.device or "").strip().lower() == (self._output_name or "").strip().lower() != ""
        usable = wanted and tap.alive and same
        if usable != self.using_reference:
            self.using_reference = usable
            log.info("riferimento dell'eco: %s", f"tutto ciò che suona su {tap.device}" if usable
                     else "solo la voce di Jarvis")
        return audio if usable else None

    def _meter(self, mic: np.ndarray, far: np.ndarray) -> None:
        acc = self._level_acc
        acc[0] = max(acc[0], float(np.sqrt(np.mean(mic ** 2))) if mic.size else 0.0)
        acc[1] = max(acc[1], float(np.sqrt(np.mean(far ** 2))) if far.size else 0.0)
        acc[2] += 1
        if acc[2] >= 3 and self._on_level:  # ~30 aggiornamenti al secondo
            self._post(self._on_level, acc[0], acc[1])
            self._level_acc = [0.0, 0.0, 0]

    def _post(self, fn: Callable | None, *args) -> None:
        if fn is None:
            return
        if self.loop is not None:
            self.loop.call_soon_threadsafe(fn, *args)
        else:
            fn(*args)

    def _player_event(self, kind: str, seg: Segment) -> None:
        self._post(self._on_player, kind, seg)

    # ------------------------------------------------------------ comandi

    def play(self, audio: np.ndarray, sr: int, text: str = "", meta: dict | None = None) -> Segment:
        if sr != self.backend.out_rate:
            audio = soxr.resample(audio, sr, self.backend.out_rate)
        return self.player.enqueue(Segment(np.asarray(audio, np.float32), text, meta or {}))

    def force_aec(self) -> None:
        """La conversazione ha sentito la propria eco: AEC acceso e bloccato per questa sessione."""
        if self.cfg.aec_mode == "off":
            return
        if not self.aec_active:
            log.warning("eco della propria voce rilevata con l'AEC spento: lo riaccendo")
        self.aec_active = True
        self._aec_locked = True
        self._remember_aec()

    def _remember_aec(self) -> None:
        if self._aec_memory is None:
            return
        try:
            data = json.loads(self._aec_memory.read_text(encoding="utf-8")) if self._aec_memory.exists() else {}
        except (OSError, ValueError):
            data = {}
        data[self._device_key] = self.aec_active
        with contextlib.suppress(OSError):
            self._aec_memory.parent.mkdir(parents=True, exist_ok=True)
            self._aec_memory.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def is_alive(self, timeout: float = 2.0) -> bool:
        return self._running and (time.monotonic() - self.last_input_at) < timeout
