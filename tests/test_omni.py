"""Il modello dei suoni caricato solo quando serve (jarvis/media/omni.py), con motori e scheda video finti.

La regola: la voce di Jarvis si scarica solo dopo aver finito la frase già scritta, e torna sempre, anche se il
modello fallisce, se la richiesta viene annullata a metà o se la voce non riparte al primo colpo. Mai durante un
riposo o un risveglio (stesso lucchetto). I motori veri (audio.cpp e llama-server) sono sostituiti da server HTTP
finti che controllano cosa ricevono.
"""

import asyncio
import base64
import io
import wave

import numpy as np
import pytest
from aiohttp import web

from jarvis import paths
from jarvis.media import omni as O
from jarvis.media.omni import MODELS, GpuLease, Omni
from jarvis.media.watch import SR, Frame, MediaError

# ------------------------------------------------------------------ prestito della scheda video


class Gpu:
    """Annota in ordine cosa succede a voce e udito."""

    def __init__(self, ears="cuda", resting=False, voice_fails=0):
        self.log: list[str] = []
        self.ears = ears
        self.resting = resting
        self.voice_fails = voice_fails
        self.notes: list[str] = []
        self.lock = asyncio.Lock()

    async def quiet(self):
        self.log.append("frase finita")

    async def stop_voice(self):
        self.log.append("voce spenta")

    async def start_voice(self):
        if self.voice_fails:
            self.voice_fails -= 1
            self.log.append("voce non riparte")
            raise RuntimeError("porta occupata")
        self.log.append("voce accesa")

    async def move_ears(self, device):
        self.log.append(f"udito su {device}")
        self.ears = device

    async def wanted(self):
        return "cuda"

    def lease(self):
        return GpuLease(lock=self.lock, resting=lambda: self.resting, quiet=self.quiet, stop_voice=self.stop_voice,
                        start_voice=self.start_voice, ears_device=lambda: self.ears, move_ears=self.move_ears,
                        wanted_ears=self.wanted, notify=self.notes.append, retry_s=0.0)


async def test_the_voice_waits_for_its_sentence_then_everything_comes_back():
    g = Gpu()
    lease = g.lease()
    async with lease.borrow(ears_off_gpu=True):
        g.log.append("modello al lavoro")
        assert lease.active
    assert g.log == ["frase finita", "voce spenta", "udito su cpu", "modello al lavoro", "voce accesa", "udito su cuda"]
    assert g.ears == "cuda" and not lease.active


async def test_a_small_model_leaves_the_ears_where_they_are():
    g = Gpu()
    async with g.lease().borrow(ears_off_gpu=False):
        g.log.append("modello al lavoro")
    assert g.log == ["frase finita", "voce spenta", "modello al lavoro", "voce accesa"]
    g2 = Gpu(ears="cpu")  # udito già sul processore (scheda video piena all'avvio): resta lì
    async with g2.lease().borrow(ears_off_gpu=True):
        pass
    assert "udito su cpu" not in g2.log and "udito su cuda" not in g2.log


async def test_everything_comes_back_when_the_model_fails():
    g = Gpu()
    with pytest.raises(RuntimeError, match="modello caduto"):
        async with g.lease().borrow(ears_off_gpu=True):
            raise RuntimeError("modello caduto")
    assert g.log[-2:] == ["voce accesa", "udito su cuda"]


async def test_everything_comes_back_when_the_request_is_cancelled():
    """L'utente dice "stop" a metà: la richiesta si annulla ma voce e udito tornano prima di lasciare il lucchetto."""
    g = Gpu()
    lease = g.lease()
    started = asyncio.Event()

    async def borrow_forever():
        async with lease.borrow(ears_off_gpu=True):
            started.set()
            await asyncio.sleep(3600)

    task = asyncio.ensure_future(borrow_forever())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert g.log[-2:] == ["voce accesa", "udito su cuda"] and not g.lock.locked()


async def test_a_second_stop_during_the_restore_does_not_leave_jarvis_mute():
    """Due "stop" di fila: il secondo arriva mentre la voce si sta riaccendendo. Non deve interromperla."""
    g = Gpu()
    restoring = asyncio.Event()

    async def slow_voice():
        restoring.set()
        await asyncio.sleep(0.1)
        g.log.append("voce accesa")

    lease = GpuLease(lock=g.lock, resting=lambda: False, quiet=g.quiet, stop_voice=g.stop_voice,
                     start_voice=slow_voice, ears_device=lambda: g.ears, move_ears=g.move_ears,
                     wanted_ears=g.wanted, retry_s=0.0)
    started = asyncio.Event()

    async def borrow_forever():
        async with lease.borrow(ears_off_gpu=False):
            started.set()
            await asyncio.sleep(3600)

    task = asyncio.ensure_future(borrow_forever())
    await started.wait()
    task.cancel()
    await restoring.wait()
    task.cancel()  # il secondo stop, a metà del ripristino
    with pytest.raises(asyncio.CancelledError):
        await task
    assert g.log[-1] == "voce accesa" and not g.lock.locked()


async def test_the_voice_is_retried_and_a_lasting_failure_is_said():
    g = Gpu(voice_fails=2)
    async with g.lease().borrow(ears_off_gpu=False):
        pass
    assert g.log.count("voce non riparte") == 2 and g.log[-1] == "voce accesa" and not g.notes
    bad = Gpu(voice_fails=99)
    async with bad.lease().borrow(ears_off_gpu=False):
        pass
    assert bad.log.count("voce non riparte") == GpuLease.VOICE_TRIES
    assert bad.notes and "non è ripartita" in bad.notes[0]


async def test_at_rest_nothing_is_touched():
    g = Gpu(resting=True)
    async with g.lease().borrow(ears_off_gpu=True):
        g.log.append("modello al lavoro")
    assert g.log == ["modello al lavoro"]


async def test_never_during_a_rest_or_a_wake_up():
    """Stesso lucchetto del riposo: un riposo chiesto durante il prestito aspetta che sia tutto a posto."""
    g = Gpu()
    lease = g.lease()
    inside = asyncio.Event()
    release = asyncio.Event()

    async def borrow():
        async with lease.borrow(ears_off_gpu=True):
            inside.set()
            await release.wait()

    async def rest():
        async with g.lock:
            g.log.append("riposo")

    b = asyncio.ensure_future(borrow())
    await inside.wait()
    r = asyncio.ensure_future(rest())
    await asyncio.sleep(0.01)
    assert "riposo" not in g.log
    release.set()
    await asyncio.gather(b, r)
    assert g.log.index("riposo") > g.log.index("udito su cuda")


# ------------------------------------------------------------------ il modello


def tone(seconds, freq=440.0, level=0.2):
    t = np.arange(int(seconds * SR)) / SR
    return (level * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_audio_goes_by_the_minute_and_silence_is_skipped():
    audio = np.concatenate([tone(60), np.zeros(60 * SR, np.float32), tone(30)])
    parts = O.windows(audio, t0=120.0)
    assert [(a, b) for a, b, _ in parts] == [(120.0, 180.0), (240.0, 270.0)]
    assert len(O.windows(tone(15 * 60), 0.0)) == O.MAX_WINDOWS


@pytest.fixture
def models_here(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "AUDIOCPP_MODELS", tmp_path / "audiocpp")
    monkeypatch.setattr(paths, "MODELS_DIR", tmp_path)

    def install(key):
        for p in MODELS[key].paths():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"gguf")
    return install


class FakeBackend:
    def __init__(self, model, fail_at=None):
        self.model, self.fail_at = model, fail_at
        self.asked: list[tuple[float, int, str]] = []
        self.started = self.stopped = False

    async def start(self):
        self.started = True

    async def ask(self, audio, frames, prompt):
        self.asked.append((len(audio) / SR, len(frames), prompt))
        if self.fail_at is not None and len(self.asked) > self.fail_at:
            raise MediaError("il modello si è bloccato")
        return f"music {len(self.asked)}"

    async def stop(self):
        self.stopped = True


def frames_every(seconds, until):
    return [Frame(t, b"jpg", 768, 432, 0.5) for t in np.arange(0, until, seconds)]


async def test_a_model_that_is_off_or_missing_is_explained(models_here):
    g = Gpu()
    choice = {"v": "spento"}
    o = Omni(lambda: choice["v"], g.lease(), backend=FakeBackend)
    with pytest.raises(MediaError, match="spento"):
        await o.describe(tone(10), 0.0, [])
    choice["v"] = "qwen2.5-omni-7b"
    with pytest.raises(MediaError, match=r"non è ancora scaricato \(6.23 GB da ggml-org/Qwen2.5-Omni-7B-GGUF"):
        await o.describe(tone(10), 0.0, [])
    assert g.log == []  # la voce non si è mai spenta per niente


async def test_describe_minute_by_minute_with_the_frames_of_that_minute(models_here):
    models_here("lfm3-omni-2b")
    g = Gpu()
    made = []

    def backend(m):
        made.append(FakeBackend(m))
        return made[-1]

    o = Omni(lambda: "lfm3-omni-2b", g.lease(), backend=backend)
    audio = np.concatenate([tone(60), np.zeros(60 * SR, np.float32), tone(45)])
    text = await o.describe(audio, 30.0, frames_every(5, 200), "che canzone è?")
    be = made[0]
    assert text == "[0:30-1:30] music 1\n[2:30-3:15] music 2"
    assert [(round(s), n) for s, n, _ in be.asked] == [(60, O.FRAMES_PER_WINDOW), (45, O.FRAMES_PER_WINDOW)]
    assert "frames from the same minute" in be.asked[0][2] and "che canzone è?" in be.asked[0][2]
    assert be.started and be.stopped
    assert "udito su cpu" not in g.log  # LFM3 è piccolo: l'udito resta sulla scheda video


async def test_a_model_that_does_not_see_gets_no_frames_and_frees_the_ears(models_here):
    models_here("audio-flamingo-next")
    g = Gpu()
    made = []
    o = Omni(lambda: "audio-flamingo-next", g.lease(), backend=lambda m: made.append(FakeBackend(m)) or made[-1])
    await o.describe(tone(30), 0.0, frames_every(5, 30))
    assert made[0].asked[0][1] == 0  # solo audio: i fotogrammi non partono nemmeno
    assert "frames" not in made[0].asked[0][2]
    assert g.log[:3] == ["frase finita", "voce spenta", "udito su cpu"] and g.log[-1] == "udito su cuda"


async def test_a_failing_model_is_stopped_and_the_voice_comes_back(models_here):
    models_here("lfm3-omni-2b")
    g = Gpu()
    made = []
    o = Omni(lambda: "lfm3-omni-2b", g.lease(), backend=lambda m: made.append(FakeBackend(m, fail_at=0)) or made[-1])
    with pytest.raises(MediaError, match="bloccato"):
        await o.describe(tone(30), 0.0, [])
    assert made[0].stopped and g.log[-1] == "voce accesa"


async def test_only_silence_does_not_even_load_the_model(models_here):
    models_here("lfm3-omni-2b")
    g = Gpu()
    o = Omni(lambda: "lfm3-omni-2b", g.lease(), backend=FakeBackend)
    assert await o.describe(np.zeros(30 * SR, np.float32), 0.0, []) == "solo silenzio"
    assert g.log == []


def test_the_candidates_and_their_sizes():
    """Le dimensioni dette a Andrea (file da scaricare) e dove stanno: le cambia la gara, non la memoria."""
    assert {k: m.download_gb for k, m in MODELS.items()} == {
        "audio-flamingo-next": 5.46, "qwen2.5-omni-7b": 6.23, "lfm3-omni-2b": 2.39}
    assert MODELS["audio-flamingo-next"].runtime == "audiocpp" and not MODELS["audio-flamingo-next"].sees
    assert all(m.runtime == "llamacpp" and m.sees and len(m.files) == 2
               for k, m in MODELS.items() if k != "audio-flamingo-next")
    assert MODELS["lfm3-omni-2b"].ears_off_gpu is False


# ------------------------------------------------------------------ motori finti sopra HTTP


@pytest.fixture
async def http_server():
    got = {}
    app = web.Application()

    async def chat(request):
        got["chat"] = await request.json()
        return web.json_response({"choices": [{"message": {"content": "  rain and a dog barking  "}}]})

    async def transcribe(request):
        form = await request.post()
        got["form"] = {k: (v if isinstance(v, str) else v.file.read()) for k, v in form.items()}
        return web.json_response({"text": " upbeat electronic music "})

    async def health(_request):
        return web.json_response({"status": "ok"})

    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_post("/v1/audio/transcriptions", transcribe)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield site._server.sockets[0].getsockname()[1], got
    await runner.cleanup()


async def test_llama_server_gets_audio_and_frames_in_one_question(http_server, models_here):
    port, got = http_server
    be = O.LlamaServerBackend(MODELS["qwen2.5-omni-7b"], port=port)
    out = await be.ask(tone(2), [Frame(0, b"\xff\xd8jpg", 768, 432, 1.0)], "describe")
    assert out == "rain and a dog barking"
    content = got["chat"]["messages"][0]["content"]
    assert [c["type"] for c in content] == ["input_audio", "image_url", "text"]
    wav = base64.b64decode(content[0]["input_audio"]["data"])
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (SR, 1, 2 * SR)
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    cmd = be.command()
    assert "--mmproj" in cmd and cmd[cmd.index("--port") + 1] == str(port) and cmd[cmd.index("-ngl") + 1] == "99"
    # gara del 29/10: col contesto 16384 e più posti Qwen2.5-Omni 7B usciva dalla scheda video (1,6 token/s invece
    # di 46): un posto solo e 8192 di contesto
    assert cmd[cmd.index("-np") + 1] == "1" and cmd[cmd.index("-c") + 1] == "8192"


async def test_llama_server_missing_is_explained(tmp_path, models_here):
    be = O.LlamaServerBackend(MODELS["lfm3-omni-2b"], exe=tmp_path / "manca.exe")
    with pytest.raises(MediaError, match=r"manca llama\.cpp"):
        await be.start()


async def test_audio_flamingo_gets_the_question_as_prompt(http_server, models_here):
    """Gara del 29/10 col motore vero: "instruct" era ignorato e Audio Flamingo trascriveva invece di rispondere
    ("The spoken content of the audio is" sul tuono). La domanda va nel campo "prompt"."""
    port, got = http_server
    be = O.AudioCppBackend(MODELS["audio-flamingo-next"], port=port)
    assert be.proc.spec.models[0]["family"] == "audio_flamingo" and be.proc.spec.port == port
    out = await be.ask(tone(3), [], "what music is this?")
    assert out == "upbeat electronic music"
    assert got["form"]["model"] == "omni" and got["form"]["prompt"] == "what music is this?"
    assert "instruct" not in got["form"] and got["form"]["max_tokens"] == "400"


# ------------------------------------------------------------------ dentro Jarvis (app.py), motori finti


class FakeEngine:
    def __init__(self, device, log, port=8999):
        self.device, self.port, self.host, self.log = device, port, "127.0.0.1", log
        self.proc = None

    async def start(self, timeout=30.0):
        from types import SimpleNamespace

        self.proc = SimpleNamespace(returncode=None)
        self.log.append(("avvio", self.device))

    async def stop(self):
        if self.proc is not None:
            self.proc.returncode = 0
        self.log.append(("spento", self.device))

    def supervise(self):
        self.log.append(("sorvegliato", self.device))


class Primer:
    def __init__(self, log):
        self.log = log

    async def prime(self):
        self.log.append(("riscaldata", "voce"))


async def test_jarvis_lends_its_gpu_and_gets_voice_and_ears_back(monkeypatch, tmp_path):
    """Il collegamento vero in app.py: voce spenta e riaccesa (sorvegliata e riscaldata), udito sul processore e
    ritorno sulla scheda video se ci sta; WhatsApp assente spiegato; nessun permesso chiesto senza identità."""
    from jarvis import health
    from jarvis.app import Jarvis
    from jarvis.apps.base import AppError
    from jarvis.config import Config
    from jarvis.core.bus import Bus

    log = []
    j = Jarvis.__new__(Jarvis)
    j.cfg, j.bus, j.conv, j.resting, j._rest_lock = Config(), Bus(), None, False, asyncio.Lock()
    j.identity, j.whatsapp, j._voice = None, None, Primer(log)
    j._ears_process = lambda device, port: FakeEngine(device, log, port)
    j.voice_engine = FakeEngine("voce", log)
    j.ears, j.ears_device = FakeEngine("cuda", log), "cuda"
    monkeypatch.setattr(health, "free_vram_mb", lambda: 7000)
    media = j._build_media()
    assert media.may_whatsapp() is None and media.settings() is j.cfg.media
    async with media.omni.lease.borrow(ears_off_gpu=True):
        assert j.ears_device == "cpu" and ("spento", "voce") in log
        log.append(("lavoro", "modello"))
    after = log[log.index(("lavoro", "modello")):]
    assert ("avvio", "voce") in after and ("sorvegliato", "voce") in after and ("riscaldata", "voce") in after
    assert j.ears_device == "cuda"
    with pytest.raises(AppError, match="WhatsApp non è disponibile"):
        await media.whatsapp("mamma", tmp_path, "")
