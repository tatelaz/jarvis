"""Video e audio veri (sintetici, creati qui con PyAV): decodifica, cambi di scena, audio a 16 kHz, intervalli.

Gira solo se PyAV è installato nel venv (dipendenza di jarvis/media): altrimenti si salta, come i test del motore.
"""

import asyncio
import io
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

av = pytest.importorskip("av")

from PIL import Image as PilImage  # noqa: E402

from jarvis.media import watch as W  # noqa: E402
from jarvis.media.watch import SR, Limits, MediaError  # noqa: E402

FPS = 25
CUTS = (4.0, 8.0)  # tagli netti tra tre scene


def scene_image(t: float, w: int, h: int) -> np.ndarray:
    """Tre scene con una trama diversa (come immagini vere: istogrammi larghi) e un quadrato che si muove."""
    k = sum(t >= c for c in CUTS)
    rng = np.random.default_rng(k)
    base = rng.integers(0, 256, (h // 8, w // 8, 3), dtype=np.uint8).repeat(8, 0).repeat(8, 1)
    base = (base * [0.3, 0.6, 1.0][k] + [140, 30, 70][k]).clip(0, 255).astype(np.uint8)
    x = int(t * 20) % (w - 40)
    base[20:60, x:x + 40] = 255  # movimento dentro la scena: non è un cambio
    return base


def make_video(path: Path, seconds=12.0, gop=50, size=(640, 360), audio=True) -> Path:
    w, h = size
    with av.open(str(path), "w") as c:
        vs = c.add_stream("libx264", rate=FPS)
        vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
        vs.options = {"g": str(gop), "sc_threshold": "0"}  # niente fotogrammi chiave ai tagli: il caso difficile
        a = None
        if audio:
            a = c.add_stream("aac", rate=SR)
            a.layout = "mono"
        for i in range(int(seconds * FPS)):
            for p in vs.encode(av.VideoFrame.from_ndarray(scene_image(i / FPS, w, h), format="rgb24")):
                c.mux(p)
        for p in vs.encode():
            c.mux(p)
        if a is not None:
            write_audio(c, a, sound(seconds))
    return path


def sound(seconds: float) -> np.ndarray:
    """Tono 0-3 s, silenzio 3-6 s, "voce" (rumore modulato) da 6 s in poi."""
    t = np.arange(int(seconds * SR)) / SR
    x = np.where(t < 3, 0.3 * np.sin(2 * np.pi * 440 * t), 0.0)
    rng = np.random.default_rng(1)
    x = np.where(t >= 6, 0.2 * rng.standard_normal(t.size) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)), x)
    return x.astype(np.float32)


def write_audio(c, stream, wave: np.ndarray) -> None:
    pts = 0
    for i in range(0, len(wave), 1024):
        chunk = wave[i:i + 1024]
        f = av.AudioFrame.from_ndarray(chunk[None, :], format="flt", layout="mono")
        f.sample_rate, f.pts, f.time_base = SR, pts, Fraction(1, SR)
        pts += len(chunk)
        for p in stream.encode(f):
            c.mux(p)
    for p in stream.encode():
        c.mux(p)


def make_wav(path: Path, wave: np.ndarray) -> Path:
    with av.open(str(path), "w") as c:
        a = c.add_stream("pcm_s16le", rate=SR)
        a.layout = "mono"
        write_audio(c, a, wave)
    return path


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("av") / "scene.mp4")


def test_probe(clip, tmp_path):
    p = W.probe(clip)
    assert p.has_video and p.has_audio and (p.width, p.height) == (640, 360) and abs(p.duration - 12) < 0.2
    a = W.probe(make_wav(tmp_path / "voce.wav", sound(5)))
    assert a.has_audio and not a.has_video and abs(a.duration - 5) < 0.1


def test_scene_cuts_are_found_even_without_keyframes_there(clip):
    samples = W.scan_video(clip, 0.0, 12.0, 768)
    assert 20 <= len(samples) <= W.MAX_SAMPLES
    picked = [samples[i].t for i, _ in W.pick(samples, 20)]
    for cut in CUTS:  # il primo fotogramma della scena nuova, entro un campione
        assert any(cut <= t <= cut + 0.6 for t in picked), (cut, picked)
    moving = [samples[i].t for i, s in W.pick(samples, 20) if s < W.SCENE_CUT]
    assert len(picked) - len(moving) == 3  # il quadrato che si muove non conta come cambio di scena


def test_frames_are_real_jpegs_no_bigger_than_asked(clip):
    s = W.scan_video(clip, 0.0, 2.0, 320)[0]
    img = PilImage.open(io.BytesIO(s.jpeg))
    assert img.format == "JPEG" and img.size == (320, 180) == (s.width, s.height)
    big = W.scan_video(clip, 0.0, 1.0, 1568)[0]
    assert (big.width, big.height) == (640, 360)  # mai ingranditi


def test_a_window_of_the_video(clip):
    samples = W.scan_video(clip, 5.0, 7.0, 320)
    assert samples and all(5.0 - 1e-3 <= s.t <= 7.0 for s in samples)


def test_dense_keyframes_are_used_when_there_are_enough(tmp_path, clip):
    dense = make_video(tmp_path / "denso.mp4", seconds=6, gop=5, audio=False)
    assert W._keyframes_enough(dense, 0, 6, 10)
    assert not W._keyframes_enough(clip, 0, 12, 10)  # un fotogramma chiave ogni 2 s: si decodifica tutto
    samples = W.scan_video(dense, 0, 6, 320)
    assert len(samples) >= 10


def test_audio_comes_out_at_16k_mono_and_exactly_the_window(clip):
    x = W.load_audio(clip, 2.0, 5.0)
    assert x.dtype == np.float32 and len(x) == 3 * SR
    tone_part, quiet_part = x[: SR // 2], x[int(1.5 * SR):]
    assert np.sqrt(np.mean(tone_part ** 2)) > 0.1  # 2,0-2,5 s: ancora il tono
    assert W.silent(quiet_part)  # 3,5-5 s: silenzio
    whole = W.load_audio(clip, 0.0, 99.0)
    assert abs(len(whole) / SR - 12) < 0.1


async def test_watch_a_real_clip_end_to_end(clip):
    heard = []

    async def ears(a):
        heard.append(len(a) / SR)
        return "ciao"

    w, pcm = await W.watch(clip, clip, ears, Limits(frames=5), "scene.mp4", keep_audio=True)
    assert 3 <= len(w.frames) <= 5 and w.frames[0].t == 0.0
    assert w.transcript and w.transcript[0].text == "ciao" and len(pcm) == pytest.approx(12 * SR, abs=SR // 10)
    out = w.output(30000)
    assert len(out.images) == len(w.frames) and "Parlato" in out.text
    assert set(w.timings) >= {"frames", "audio", "stt", "total"}


async def test_audio_only_file_is_listened_not_watched(tmp_path):
    path = make_wav(tmp_path / "vocale.wav", sound(8))

    async def ears(a):
        return "una frase"

    w, _ = await W.watch(path, path, ears, Limits(), "vocale.wav")
    assert not w.has_video and not w.frames and w.transcript
    assert w.output(30000).parts[0].startswith("Audio: vocale.wav")


async def test_broken_files_are_clear_errors(tmp_path):
    bad = tmp_path / "rotto.mp4"
    bad.write_bytes(b"questo non e un video" * 100)
    with pytest.raises(MediaError, match=r"non riesco ad aprire rotto\.mp4"):
        await W.watch(bad, bad, None, Limits(), "rotto.mp4")
    assert asyncio.iscoroutinefunction(W.watch)
