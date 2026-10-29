"""Dispositivi audio: quello vero (WASAPI via sounddevice) e una stanza simulata per i test.

Il dispositivo vero è avvolto da ResilientBackend: l'audio che non si apre o che smette di rispondere non fa
mai cadere Jarvis (successo davvero: "Unanticipated host error" all'avvio con le cuffie occupate), si va avanti
senza audio e si riprova da soli; se Windows cambia il dispositivo predefinito, Jarvis lo segue.

La stanza simulata riproduce ciò che conta per la conversazione: la voce di Jarvis esce, rientra
nel microfono con ritardo, attenuazione e riverbero (eco), si somma alla voce dell'utente e al
rumore di fondo. Può andare più veloce del tempo reale, perché la pipeline misura il tempo in
campioni e non con l'orologio di sistema.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable

import numpy as np
import soxr
from scipy import signal

log = logging.getLogger("jarvis.audio.devices")


# ------------------------------------------------------------------ dispositivo reale


def _wasapi_index() -> int | None:
    import sounddevice as sd

    for i, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return i
    return None


def find_device(kind: str, name: str | None) -> int:
    """Indice del dispositivo WASAPI di ingresso/uscita: predefinito o il primo il cui nome contiene `name`."""
    import sounddevice as sd

    api = _wasapi_index()
    devices = sd.query_devices()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    if api is not None:
        hostapi = sd.query_hostapis(api)
        default = hostapi["default_input_device" if kind == "input" else "default_output_device"]
        candidates = [i for i in hostapi["devices"] if devices[i][key] > 0]
    else:
        default = sd.default.device[0 if kind == "input" else 1]
        candidates = [i for i, d in enumerate(devices) if d[key] > 0]
    if name:
        for i in candidates:
            if name.lower() in devices[i]["name"].lower():
                return i
        raise RuntimeError(f"nessun dispositivo di {kind} contiene '{name}': "
                           + ", ".join(devices[i]["name"] for i in candidates))
    if default is None or default < 0:
        raise RuntimeError(f"nessun dispositivo di {kind} predefinito")
    return int(default)


RATE = 48000  # ogni dispositivo si apre a questa frequenza (Windows converte): cambiare cuffie non cambia nulla a valle


def default_names() -> tuple[str | None, str | None]:
    """(microfono, uscita) predefiniti di Windows in questo momento; None se non si riesce a saperlo.

    Lo si chiede a Windows e non a PortAudio, che vede solo i dispositivi presenti quando si è avviato.
    I nomi coincidono con quelli WASAPI di PortAudio (es. "Headphones (High Definition Audio Device)")."""
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return None, None

    def name(get) -> str | None:
        try:
            dev = get()
            if not dev:
                return None
            # pycaw dà le casse già avvolte (con FriendlyName) ma il microfono come puntatore COM grezzo
            return dev.FriendlyName if hasattr(dev, "FriendlyName") else AudioUtilities.CreateDevice(dev).FriendlyName
        except Exception:  # nessun dispositivo, servizio audio che si riavvia, COM non pronto
            return None

    return name(AudioUtilities.GetMicrophone), name(AudioUtilities.GetSpeakers)


def _fix_sounddevice(sd) -> None:
    """sounddevice 0.5.6 (l'ultima) crea gli array delle chiamate audio con `data.shape = ...`, deprecato da
    NumPy 2.5: oggi un avviso a ogni blocco, con un NumPy futuro un errore che ammutolisce tutto. Stesso
    risultato con reshape (una vista sullo stesso buffer, niente copie)."""
    if getattr(sd._array, "jarvis", False):
        return

    def _array(buffer, channels, dtype):
        return np.frombuffer(buffer, dtype=dtype).reshape(-1, channels)

    _array.jarvis = True
    sd._array = _array


def rescan() -> None:
    """PortAudio rilegge i dispositivi (cuffie collegate dopo l'avvio). Solo con tutti i flussi chiusi."""
    import sounddevice as sd

    sd._terminate()
    sd._initialize()


class SoundDeviceBackend:
    def __init__(self, input_name: str | None = None, output_name: str | None = None, block_ms: int = 10) -> None:
        import sounddevice as sd

        _fix_sounddevice(sd)
        self._sd = sd
        self.in_dev = find_device("input", input_name)
        self.out_dev = find_device("output", output_name)
        din, dout = sd.query_devices(self.in_dev), sd.query_devices(self.out_dev)
        self.in_rate = self.out_rate = RATE
        self.in_name, self.out_name = din["name"], dout["name"]
        self._in_ch = max(1, min(2, din["max_input_channels"]))
        self._out_ch = max(1, min(2, dout["max_output_channels"]))
        wasapi = _wasapi_index()
        self._in_extra = sd.WasapiSettings(auto_convert=True) if din["hostapi"] == wasapi else None
        self._out_extra = sd.WasapiSettings(auto_convert=True) if dout["hostapi"] == wasapi else None
        self.block_ms = block_ms
        self._in = self._out = None
        self.status_errors = 0
        self.last_in = self.last_out = 0.0

    def start(self, on_input: Callable[[np.ndarray], None], on_output: Callable[[int], np.ndarray]) -> None:
        sd = self._sd

        def in_cb(indata, frames, _t, status):
            self.last_in = time.monotonic()
            if status:
                self.status_errors += 1
            on_input(indata.mean(axis=1).astype(np.float32) if indata.shape[1] > 1 else indata[:, 0].copy())

        def out_cb(outdata, frames, _t, status):
            self.last_out = time.monotonic()
            if status:
                self.status_errors += 1
            outdata[:] = on_output(frames)[:, None]

        self.last_in = self.last_out = time.monotonic()
        block = int(RATE * self.block_ms / 1000)
        try:
            self._out = sd.OutputStream(device=self.out_dev, samplerate=RATE, channels=self._out_ch, dtype="float32",
                                        blocksize=block, latency="low", extra_settings=self._out_extra, callback=out_cb)
            self._in = sd.InputStream(device=self.in_dev, samplerate=RATE, channels=self._in_ch, dtype="float32",
                                      blocksize=block, latency="low", extra_settings=self._in_extra, callback=in_cb)
            self._out.start()
            self._in.start()
        except BaseException:
            self.stop()  # un'uscita già partita senza microfono resterebbe aperta per sempre
            raise

    def stalled(self, seconds: float) -> bool:
        """Il dispositivo ha smesso di chiamare (cuffie staccate, driver riavviato)."""
        now = time.monotonic()
        return (now - self.last_in > seconds or now - self.last_out > seconds
                or not all(s is not None and s.active for s in (self._in, self._out)))

    def stop(self) -> None:
        for s in (self._in, self._out):
            if s is not None:
                with contextlib.suppress(Exception):  # un flusso di un dispositivo sparito può rifiutare anche lo stop
                    s.stop()
                with contextlib.suppress(Exception):
                    s.close()
        self._in = self._out = None

    def latency_ms(self) -> tuple[float, float]:
        return ((self._in.latency if self._in else 0.0) * 1000, (self._out.latency if self._out else 0.0) * 1000)

    def describe(self) -> dict:
        return {"input": self.in_name, "output": self.out_name, "in_rate": self.in_rate, "out_rate": self.out_rate}


RETRY_S = (1.0, 2.0, 3.0, 5.0)  # attese tra un tentativo e l'altro quando l'audio non c'è (poi sempre l'ultima)


class ResilientBackend:
    """Il dispositivo vero, che però non fa mai cadere Jarvis.

    - Non si apre (occupato, appena scollegato, driver che si riavvia) o smette di rispondere (cuffie staccate):
      si va avanti senza audio e si riprova da soli. Intanto un orologio muto tiene il tempo reale, così la
      conversazione scritta continua e i sottotitoli scorrono.
    - Windows cambia il dispositivo predefinito (cuffie collegate): ci si sposta lì, se nelle impostazioni non è
      stato scelto un dispositivo per nome.
    on_change(stato) avvisa di ogni passaggio (dal thread di sorveglianza).
    """

    def __init__(self, input_name: str | None = None, output_name: str | None = None, block_ms: int = 10, *,
                 open_device: Callable[[], SoundDeviceBackend] | None = None,
                 defaults: Callable[[], tuple[str | None, str | None]] = default_names,
                 rescan: Callable[[], None] = rescan, on_change: Callable[[dict], None] | None = None,
                 check_s: float = 1.0, dead_s: float = 2.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.input_name, self.output_name, self.block_ms = input_name, output_name, block_ms
        self.in_rate = self.out_rate = RATE
        self._open = open_device or (lambda: SoundDeviceBackend(input_name, output_name, block_ms))
        self._defaults, self._rescan, self.on_change = defaults, rescan, on_change
        self.check_s, self.dead_s, self._clock = check_s, dead_s, clock
        self.device: SoundDeviceBackend | None = None
        self.error: str | None = None
        self._failures = 0
        self._next_try = 0.0
        self._down_since: float | None = None
        self._seen_defaults: tuple[str | None, str | None] = (None, None)
        self._on_input: Callable[[np.ndarray], None] = lambda _b: None
        self._on_output: Callable[[int], np.ndarray] = lambda n: np.zeros(n, np.float32)
        self._lock = threading.RLock()
        self._halt = threading.Event()
        self._silence: tuple[threading.Thread, threading.Event] | None = None
        self._watcher: threading.Thread | None = None

    # ------------------------------------------------------------ interfaccia della pipeline

    def start(self, on_input: Callable[[np.ndarray], None], on_output: Callable[[int], np.ndarray]) -> None:
        self._on_input, self._on_output = on_input, on_output
        with self._lock:
            # PortAudio si riavvia qui, nel thread che apre i flussi: WASAPI vuole COM pronto in quel thread, e se
            # sounddevice era stato caricato altrove (l'autodiagnosi gira in un thread di lavoro) ogni apertura
            # falliva con "Unanticipated host error". Era la causa vera della caduta del 6/10.
            if not self._open_now(rescan=True):
                self._go_silent()
                log.warning("audio non disponibile (%s): vado avanti senza e riprovo da solo", self.error)
            self._notify("avvio")
        self._watcher = threading.Thread(target=self._watch, name="audio-sorveglianza", daemon=True)
        self._watcher.start()

    def stop(self) -> None:
        self._halt.set()
        if self._watcher is not None and self._watcher is not threading.current_thread():
            self._watcher.join(timeout=5)
        with self._lock:
            self._stop_silence()
            if self.device is not None:
                self.device.stop()
                self.device = None

    def latency_ms(self) -> tuple[float, float]:
        return self.device.latency_ms() if self.device is not None else (float(self.block_ms), float(self.block_ms))

    def describe(self) -> dict:
        d = self.device
        return {"input": d.in_name if d else "nessuno", "output": d.out_name if d else "nessuno",
                "in_rate": RATE, "out_rate": RATE}

    def status(self) -> dict:
        d = self.device
        return {"ok": d is not None, "input": d.in_name if d else None, "output": d.out_name if d else None,
                "error": self.error}

    # ------------------------------------------------------------ sorveglianza

    def check(self) -> None:
        """Un giro di controllo (il thread di sorveglianza lo chiama ogni check_s secondi)."""
        with self._lock:
            if self._halt.is_set():
                return
            if self.device is None:
                if self._clock() >= self._next_try:
                    self._reopen("audio tornato")
                return
            if self.device.stalled(self.dead_s):
                log.warning("il dispositivo audio ha smesso di rispondere: lo riapro")
                self._reopen("il dispositivo ha smesso di rispondere")
                return
            seen = self._defaults()
            if seen == self._seen_defaults:
                return
            self._seen_defaults = seen  # si reagisce solo a un cambio: niente giri infiniti se i nomi non tornano
            mic, out = seen
            d = self.device
            if ((self.input_name is None and mic is not None and mic != d.in_name)
                    or (self.output_name is None and out is not None and out != d.out_name)):
                log.info("Windows ha cambiato il dispositivo predefinito (%s / %s): lo seguo", mic, out)
                self._reopen("nuovo dispositivo predefinito")

    def _watch(self) -> None:
        com = None
        with contextlib.suppress(Exception):  # pycaw usa COM, che va acceso in ogni thread
            import comtypes

            comtypes.CoInitialize()
            com = comtypes
        try:
            while not self._halt.wait(self.check_s):
                try:
                    self.check()
                except Exception:  # la sorveglianza non deve morire per un errore imprevisto
                    log.exception("errore nel controllo dell'audio")
        finally:
            if com is not None:
                with contextlib.suppress(Exception):
                    com.CoUninitialize()

    # ------------------------------------------------------------ passaggi di stato

    def _reopen(self, reason: str) -> None:
        was_down = self.device is None
        if self.device is not None:
            self.device.stop()
            self.device = None
        self._stop_silence()  # mai due fonti a chiamare la pipeline insieme
        if self._open_now(rescan=True):
            if was_down:
                since = self._down_since if self._down_since is not None else self._clock()
                outage = self._clock() - since
                log.info("audio tornato dopo %.0f s: %s / %s", outage, self.device.in_name, self.device.out_name)
                self._down_since = None
                self._notify(reason, outage_s=round(outage, 1))
            else:
                log.info("audio spostato su %s / %s", self.device.in_name, self.device.out_name)
                self._notify(reason)
            return
        self._go_silent()
        if not was_down:
            log.warning("audio perso (%s): vado avanti senza e riprovo da solo", self.error)
            self._notify(reason)
        else:
            log.debug("audio ancora assente (%s), prossimo tentativo tra %.0f s", self.error,
                      self._next_try - self._clock())

    def _open_now(self, rescan: bool) -> bool:
        dev = None
        try:
            if rescan:
                self._rescan()
            dev = self._open()
            dev.start(self._on_input, self._on_output)
        except Exception as e:  # PortAudioError, nessun dispositivo, nome che non c'è più...
            if dev is not None:
                dev.stop()
            self.error = str(e).strip() or type(e).__name__
            self._failures += 1
            self._next_try = self._clock() + RETRY_S[min(self._failures, len(RETRY_S)) - 1]
            return False
        self.device, self.error, self._failures = dev, None, 0
        self._seen_defaults = self._defaults()
        return True

    def _go_silent(self) -> None:
        if self._down_since is None:
            self._down_since = self._clock()
        if self._silence is None:
            stop = threading.Event()
            th = threading.Thread(target=self._silent_clock, args=(stop,), name="audio-muto", daemon=True)
            self._silence = (th, stop)
            th.start()

    def _stop_silence(self) -> None:
        if self._silence is not None:
            th, stop = self._silence
            stop.set()
            th.join(timeout=2)
            self._silence = None

    def _silent_clock(self, stop: threading.Event) -> None:
        """Al posto del dispositivo: chiede l'uscita (che va persa) e dà silenzio al microfono, a tempo reale."""
        n = int(RATE * self.block_ms / 1000)
        step = self.block_ms / 1000
        t = time.perf_counter()
        while not stop.is_set():
            try:
                self._on_output(n)
                self._on_input(np.zeros(n, np.float32))
            except Exception:
                log.exception("errore nell'orologio muto")
            t += step
            lag = t - time.perf_counter()
            if lag > 0:
                stop.wait(lag)
            elif lag < -0.5:  # rimasto indietro (PC sospeso): si riparte da adesso, senza raffiche
                t = time.perf_counter()

    def _notify(self, reason: str, **extra) -> None:
        if self.on_change is not None:
            try:
                self.on_change({**self.status(), "reason": reason, **extra})
            except Exception:
                log.exception("errore nell'avviso di cambio audio")


# ------------------------------------------------------------------ stanza simulata


class SimulatedRoom:
    """Dispositivo finto: eco configurabile + voci dell'utente programmate nel tempo."""

    def __init__(self, in_rate: int = 48000, out_rate: int = 48000, block_ms: int = 10, echo_gain: float = 0.0,
                 echo_delay_ms: float = 150.0, drr_db: float = 6.0, rt60_ms: float = 300.0, noise: float = 0.002,
                 speed: float = 0.0, seed: int = 0) -> None:
        """speed: 0 = il più veloce possibile, 1 = tempo reale."""
        self.in_rate, self.out_rate = in_rate, out_rate
        self.block_in = int(in_rate * block_ms / 1000)
        self.block_out = int(out_rate * block_ms / 1000)
        self.speed = speed
        self.noise = noise
        self.rng = np.random.default_rng(seed)
        n = int(rt60_ms / 1000 * in_rate)
        tail = self.rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / max(1, n))
        tail *= np.sqrt(10 ** (-drr_db / 10) / max(1e-12, np.sum(tail ** 2)))
        tail[0] = 1.0
        self.ir = echo_gain * tail
        self.delay = int(echo_delay_ms / 1000 * in_rate)
        self._echo_tail = np.zeros(self.delay + n + self.block_in * 4, np.float32)
        self._user: list[tuple[int, np.ndarray]] = []
        self._other: list[tuple[int, np.ndarray]] = []  # altre app sulla stessa uscita (un video, la musica)
        self._out_t = 0  # tempo dell'uscita, in campioni d'uscita
        self._loop_qs: list[queue.Queue[np.ndarray]] = []  # sorgenti loopback aperte (vedi loopback())
        self._out_rs = soxr.ResampleStream(out_rate, in_rate, 1, dtype="float32") if in_rate != out_rate else None
        self.t_samples = 0  # tempo del microfono, in campioni di ingresso
        self.output_log: list[np.ndarray] = []
        self._thread: threading.Thread | None = None
        self._running = False
        self.done = threading.Event()
        self.duration_s = 0.0
        self.lock = threading.Lock()

    def say(self, at_s: float, audio: np.ndarray, sr: int, gain: float = 0.5) -> None:
        """Programma una frase dell'utente all'istante at_s (secondi dall'inizio)."""
        a = soxr.resample(np.asarray(audio, np.float32), sr, self.in_rate) if sr != self.in_rate else audio
        self._user.append((int(at_s * self.in_rate), gain * np.asarray(a, np.float32)))

    def play_other(self, at_s: float, audio: np.ndarray, sr: int, gain: float = 1.0) -> None:
        """Un'altra app (un video) suona sulla stessa uscita da at_s: rientra come l'eco, Jarvis non la conosce."""
        a = soxr.resample(np.asarray(audio, np.float32), sr, self.out_rate) if sr != self.out_rate else audio
        self._other.append((int(at_s * self.out_rate), gain * np.asarray(a, np.float32)))

    def loopback(self, name: str | None = None):
        """Sorgente finta per SystemOutputTap: tutto ciò che l'uscita suona (Jarvis e altre app), come il loopback."""

        @contextlib.contextmanager
        def opener(rate: int):
            assert rate == self.out_rate
            q: queue.Queue[np.ndarray] = queue.Queue()
            with self.lock:
                self._loop_qs.append(q)

            def read() -> np.ndarray:
                try:
                    return q.get(timeout=0.05)
                except queue.Empty:
                    return np.zeros(0, np.float32)

            try:
                yield (name or self.describe()["output"]), read
            finally:
                with self.lock:
                    self._loop_qs.remove(q)

        return opener

    @property
    def now(self) -> float:
        return self.t_samples / self.in_rate

    @staticmethod
    def _scheduled(items: list[tuple[int, np.ndarray]], start: int, n: int) -> np.ndarray:
        out = np.zeros(n, np.float32)
        for t0, a in items:
            lo, hi = max(start, t0), min(start + n, t0 + a.size)
            if lo < hi:
                out[lo - start:hi - start] += a[lo - t0:hi - t0]
        return out

    def _user_block(self, start: int, n: int) -> np.ndarray:
        return self._scheduled(self._user, start, n)

    def step(self, on_input, on_output) -> None:
        out = on_output(self.block_out)
        self.output_log.append(out)
        if self._other:  # ciò che esce davvero: Jarvis più le altre app
            out = out + self._scheduled(self._other, self._out_t, out.size)
        self._out_t += out.size
        with self.lock:
            for q in self._loop_qs:
                q.put(out.copy())
        far = self._out_rs.resample_chunk(out) if self._out_rs else out
        if self.ir.any() and far.size:
            conv = signal.fftconvolve(far, self.ir)
            seg = self._echo_tail[self.delay:self.delay + conv.size]
            seg += conv[: seg.size]
        n = self.block_in
        mic = self._echo_tail[:n].copy()
        self._echo_tail = np.concatenate([self._echo_tail[n:], np.zeros(n, np.float32)])
        mic += self._user_block(self.t_samples, n)
        mic += self.noise * self.rng.standard_normal(n).astype(np.float32)
        self.t_samples += n
        on_input(mic.astype(np.float32))

    def start(self, on_input, on_output) -> None:
        self._running = True

        def run():
            t0 = time.perf_counter()
            while self._running and (self.duration_s <= 0 or self.now < self.duration_s):
                self.step(on_input, on_output)
                if self.speed > 0:
                    lag = t0 + self.now / self.speed - time.perf_counter()
                    if lag > 0:
                        time.sleep(lag)
            self.done.set()

        self._thread = threading.Thread(target=run, name="sim-room", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def latency_ms(self) -> tuple[float, float]:
        return (10.0, 10.0)

    def describe(self) -> dict:
        return {"input": "stanza simulata", "output": "stanza simulata", "echo_gain": float(self.ir.max())}
