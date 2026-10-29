"""Jarvis guarda i video e ascolta l'audio (jarvis/media): immagini nei risultati degli strumenti, scelta dei
fotogrammi, pezzi per le orecchie, file cercati per nome, siti scaricati, WhatsApp, lo strumento guarda_video.

Qui niente PyAV né rete: decodifica, scaricamento e orecchie sono finti. I video veri (sintetici) sono in
tests/test_media_av.py, che gira quando PyAV è installato.
"""

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from jarvis.apps import whatsapp as wa_mod
from jarvis.apps.base import AppError
from jarvis.brain import claude
from jarvis.brain.claude import ClaudeBrain, preview
from jarvis.brain.tools import Image, ToolOutput, ToolRegistry
from jarvis.config import ConfigStore, MediaConfig, settings_catalog
from jarvis.core.bus import Bus
from jarvis.media import fetch, service
from jarvis.media import watch as W
from jarvis.media.watch import SR, Frame, Limits, MediaError, Sample, Segment, Watched
from jarvis.memory.store import Memory
from jarvis.tools.front import TOOL_GROUPS, Services, build_front_tools

JPEG = b"\xff\xd8\xff\xe0finto-jpeg"


# ------------------------------------------------------------------ immagini nei risultati degli strumenti


def picture_tool(text="ecco il video"):
    reg = ToolRegistry()

    @reg.register("foto", "Restituisce testo e immagini.")
    def foto(_args):
        return ToolOutput([text, "[fotogramma 1]", Image(JPEG), "[fotogramma 2]", Image(b"png", "image/png")])

    return reg


async def test_images_reach_claude_as_mcp_image_blocks():
    """Il formato è quello che l'SDK installato accetta e passa a Claude (stessa conversione che fa lui)."""
    from claude_agent_sdk import _convert_tool_content
    from mcp.types import CallToolResult

    res = await picture_tool().sdk_tools()[0].handler({})
    assert res["is_error"] is False
    blocks = res["content"]
    assert [b["type"] for b in blocks] == ["text", "text", "image", "text", "image"]
    assert base64.b64decode(blocks[2]["data"]) == JPEG and blocks[2]["mimeType"] == "image/jpeg"
    assert blocks[4]["mimeType"] == "image/png"
    checked = CallToolResult.model_validate({"content": _convert_tool_content(blocks), "isError": False})
    assert [c.type for c in checked.content].count("image") == 2


async def test_text_only_brains_get_the_text_and_are_told_what_they_missed():
    """Ollama cloud e Bonsai leggono solo testo (ToolRegistry.call): mai finta di aver visto le immagini."""
    out = await picture_tool().call("foto", {})
    assert out.startswith("ecco il video\n[fotogramma 1]\n[fotogramma 2]")
    assert "2 immagini non mostrate" in out and "solo testo" in out
    assert "/9j/" not in out and "base64" not in out


async def test_errors_stay_errors_and_plain_text_tools_are_unchanged():
    reg = picture_tool("Errore: file illeggibile")
    assert (await reg.sdk_tools()[0].handler({}))["is_error"] is True
    plain = ToolRegistry()

    @plain.register("ora", "Che ore sono.")
    def ora(_args):
        return "sono le 10"

    res = await plain.sdk_tools()[0].handler({})
    assert res == {"content": [{"type": "text", "text": "sono le 10"}], "is_error": False}
    assert await plain.call("ora", {}) == "sono le 10"


def test_tool_results_in_events_count_images_but_never_carry_them():
    content = [{"type": "text", "text": "Video: prova"}, {"type": "image", "data": "A" * 5000, "mimeType": "image/jpeg"},
               {"type": "image", "data": "B" * 5000, "mimeType": "image/jpeg"}]
    p = preview(content)
    assert p == "Video: prova [+2 immagini]" and "AAAA" not in p
    assert preview("testo semplice") == "testo semplice"


def test_the_front_brain_lets_twenty_frames_through_whole(monkeypatch):
    """Claude Code stima 1600 token a immagine e oltre metà del tetto le toglie o le rimpicciolisce (25.000 di base):
    con 20 fotogrammi e la trascrizione il tetto del cervello frontale deve stare sopra."""
    monkeypatch.setattr(claude, "find_cli", lambda explicit=None: "claude.exe")
    env = ClaudeBrain("prompt", ToolRegistry(), "claude-opus-5-5")._options().env
    assert int(env["MAX_MCP_OUTPUT_TOKENS"]) * 0.5 >= 20 * 1600 + 30000 / 4
    assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"  # l'isolamento resta


# ------------------------------------------------------------------ tempi


@pytest.mark.parametrize("said, seconds", [
    ("90", 90.0), ("1:30", 90.0), ("1:02:03", 3723.0), ("2m", 120.0), ("1h5m", 3900.0), ("1m30s", 90.0),
    ("1,5", 1.5), ("", None), (None, None), ("12:30", 750.0)])
def test_times_said_in_many_ways(said, seconds):
    assert W.parse_time(said) == seconds


@pytest.mark.parametrize("said", ["mezzo minuto", "1:xx", "3 giorni"])
def test_unreadable_times_are_clear_errors(said):
    with pytest.raises(MediaError, match="tempo non valido"):
        W.parse_time(said)


def test_clock():
    assert [W.clock(t) for t in (0, 5.4, 65, 3723)] == ["0:00", "0:05", "1:05", "1:02:03"]


# ------------------------------------------------------------------ scelta dei fotogrammi


def scene(level, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    img = np.full((W.SMALL_H, W.SMALL_W), level, np.float32)
    if noise:
        img = np.clip(img + noise * rng.standard_normal(img.shape), 0, 1).astype(np.float32)
    return img


def samples_from(levels, step=1.0):
    return [Sample(i * step, scene(v, 0.01, i)) for i, v in enumerate(levels)]


def chosen_times(samples, n):
    return [samples[i].t for i, _ in W.pick(samples, n)]


def test_scene_cuts_are_chosen_and_the_first_frame_always():
    levels = [0.1] * 10 + [0.8] * 10 + [0.4] * 10  # due tagli: a 10 s e a 20 s
    times = chosen_times(samples_from(levels), 20)
    assert times[0] == 0.0 and 10.0 in times and 20.0 in times


def test_a_dissolve_is_one_change_not_many():
    """Una dissolvenza lunga cambia un po' ogni secondo: prima i fotogrammi si sarebbero ammucchiati lì."""
    levels = [0.1] * 20 + [0.1 + 0.08 * k for k in range(1, 10)] + [0.82] * 31
    s = samples_from(levels)
    times = chosen_times(s, 6)
    near = [t for t in times if 19 <= t <= 30]
    assert len(near) <= 2, times
    assert len(times) <= 6


def test_a_still_interview_still_gets_frames_spread_over_time():
    s = samples_from([0.5] * 181)  # tre minuti, la scena non cambia mai
    times = chosen_times(s, 20)
    assert len(times) == W.MIN_FRAMES
    gaps = np.diff([*times, 180.0])
    assert gaps.max() <= 60.0, times  # niente buchi enormi: 0, 45, 90, 135


def test_never_more_frames_than_asked():
    levels = [(k % 2) * 0.9 for k in range(60)]  # un taglio ogni secondo
    assert len(W.pick(samples_from(levels), 5)) == 5
    assert W.pick([], 5) == [] and W.pick(samples_from([0.2]), 0) == []


def test_fit_keeps_proportions():
    assert W.fit(1920, 1080, 768) == (768, 432)
    assert W.fit(1080, 1920, 768) == (432, 768)
    assert W.fit(320, 240, 768) == (320, 240)  # mai ingrandire


# ------------------------------------------------------------------ pezzi per le orecchie


def voice_like(seconds, level=0.1, seed=0):
    rng = np.random.default_rng(seed)
    return (level * rng.standard_normal(int(seconds * SR))).astype(np.float32)


def test_long_audio_is_cut_in_the_pauses():
    audio = np.concatenate([voice_like(24.6), np.zeros(int(0.4 * SR), np.float32), voice_like(20, seed=1)])
    parts = W.chunks(audio)
    assert parts[0][0] == 0 and parts[-1][1] == len(audio)
    assert all(b - a <= W.CHUNK_MAX_S * SR for a, b in parts)
    assert all(parts[i][1] == parts[i + 1][0] for i in range(len(parts) - 1))  # niente buchi né doppioni
    assert 24.6 <= parts[0][1] / SR <= 25.0  # proprio nella pausa


def test_silence_is_silence_but_music_and_noise_are_not():
    assert W.silent(np.zeros(SR, np.float32)) and W.silent(np.zeros(0, np.float32))
    assert not W.silent(voice_like(1, 0.02))
    t = np.arange(SR) / SR
    assert not W.silent((0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32))


async def test_transcription_skips_silence_and_keeps_the_time():
    heard = []

    async def ears(a):
        heard.append(len(a) / SR)
        return "  ciao a tutti  " if len(heard) == 1 else ""

    audio = np.concatenate([np.zeros(40 * SR, np.float32), voice_like(10), np.zeros(5 * SR, np.float32)])
    segs = await W.transcribe(ears, audio, t0=100.0)
    assert len(heard) == 1  # il silenzio non va alle orecchie (prima 2 pezzi muti costavano tempo)
    assert segs == [Segment(segs[0].t0, segs[0].t1, "ciao a tutti")] and segs[0].t0 >= 100.0 + 18


def test_transcript_has_times_and_stops_honestly_at_the_limit():
    segs = [Segment(i * 30, i * 30 + 30, f"frase numero {i} " * 5) for i in range(10)]
    text, cut = W.transcript_text(segs, 400)
    assert text.startswith("[0:00] frase numero 0") and "[0:30]" in text
    assert len(text) <= 400 and cut is not None and W.clock(cut) in ("1:30", "2:00", "2:30", "3:00")
    whole, none = W.transcript_text(segs, 100_000)
    assert none is None and whole.count("\n") == 9
    one, at = W.transcript_text([Segment(0, 30, "x" * 500)], 100)
    assert len(one) <= 101 and at == 30


# ------------------------------------------------------------------ il risultato per il cervello


def watched(frames=3, speech=True, **kw):
    w = Watched("prova.mp4 (in Desktop)", 95.0, 0.0, 95.0, has_video=frames > 0, has_audio=True, **kw)
    w.frames = [Frame(10.0 * i, JPEG, 768, 432, 1.0) for i in range(frames)]
    if speech:
        w.transcript = [Segment(0, 30, "buongiorno"), Segment(65, 95, "arrivederci")]
    return w


def test_output_puts_each_frame_after_its_time_then_the_speech():
    out = watched().output(30000)
    kinds = ["img" if isinstance(p, Image) else p.split(",")[0] for p in out.parts]
    assert kinds[:7] == [kinds[0], "[fotogramma 1 di 3", "img", "[fotogramma 2 di 3", "img", "[fotogramma 3 di 3",
                         "img"]
    assert out.parts[0].startswith("Video: prova.mp4 (in Desktop) (durata 1:35).")
    assert "[0:00] buongiorno\n[1:05] arrivederci" in out.text and "nomi propri" in out.text


def test_output_without_speech_or_video_says_so():
    out = watched(frames=0, speech=False).output(30000)
    assert out.parts[0].startswith("Audio:") and "nessuna parola riconosciuta" in out.text and not out.images
    part = Watched("x", 600.0, 60.0, 120.0, True, False, sounds="rain and thunder", notes=["guardati solo 1 minuti"])
    t = part.output(30000).text
    assert "guardato da 1:00 a 2:00 su 10:00" in t and "Nessuna traccia audio" in t
    assert "rain and thunder" in t and "Nota: guardati solo 1 minuti" in t


def test_token_estimate_counts_pixels():
    w = watched(frames=20)
    assert 20 * 443 <= w.tokens(30000) <= 20 * 443 + 400  # 768×432/750 = 443 a fotogramma, più poco testo


# ------------------------------------------------------------------ guardare (decodifica finta)


@pytest.fixture
def fake_decoder(monkeypatch):
    calls = {}

    def probe(p):
        return W.Probe(600.0 if "lungo" in str(p) else 30.0, has_video="audio" not in str(p), has_audio=True,
                       width=1280, height=720)

    def scan(path, s, e, px):
        calls["scan"] = (s, e, px)
        return [Sample(s + k, scene(0.1 if k < 5 else 0.9, 0.01, k), JPEG, 768, 432) for k in range(int(e - s))]

    def load(path, s, e):
        calls["audio"] = (s, e)
        return voice_like(e - s)

    monkeypatch.setattr(W, "probe", probe)
    monkeypatch.setattr(W, "scan_video", scan)
    monkeypatch.setattr(W, "load_audio", load)
    return calls


async def fake_ears(a):
    return f"parole di {len(a) / SR:.0f} secondi"


async def test_watch_a_short_video(fake_decoder):
    w, pcm = await W.watch(Path("prova.mp4"), Path("prova.mp4"), fake_ears, Limits(), "prova.mp4")
    assert w.has_video and w.has_audio and 5.0 in [f.t for f in w.frames]
    assert w.transcript[0].text == "parole di 30 secondi" and pcm is None
    assert fake_decoder["scan"] == (0.0, 30.0, 768)


async def test_watch_never_goes_past_the_limit_and_says_where_to_continue(fake_decoder):
    w, pcm = await W.watch(Path("lungo.mp4"), Path("lungo.mp4"), fake_ears, Limits(max_minutes=2), "lungo",
                           start=60, keep_audio=True)
    assert (w.start, w.end) == (60.0, 180.0) and fake_decoder["audio"] == (60.0, 180.0)
    assert any("inizio=3:00" in n for n in w.notes)
    assert pcm is not None and len(pcm) == 120 * SR


async def test_watch_rejects_impossible_intervals(fake_decoder):
    with pytest.raises(MediaError, match="oltre la fine"):
        await W.watch(Path("prova.mp4"), Path("prova.mp4"), fake_ears, Limits(), "x", start=31)
    with pytest.raises(MediaError, match="intervallo vuoto"):
        await W.watch(Path("prova.mp4"), Path("prova.mp4"), fake_ears, Limits(), "x", start=20, end=10)


async def test_watch_audio_only_and_without_ears(fake_decoder):
    w, _ = await W.watch(None, Path("audio.m4a"), None, Limits(), "vocale")
    assert not w.has_video and not w.frames and "scan" not in fake_decoder
    assert any("orecchie" in n for n in w.notes)


# ------------------------------------------------------------------ file del PC


@pytest.fixture
def folders(tmp_path):
    desk, down = tmp_path / "Desktop", tmp_path / "Downloads"
    desk.mkdir()
    down.mkdir()
    files = {"vacanze mare 2026.mp4": desk, "riunione.m4a": desk, "note.txt": desk, "intervista Rossi.mkv": down,
             "intervista Bianchi.mkv": down, "canzone.mp3": down}
    for i, (name, d) in enumerate(files.items()):
        (d / name).write_bytes(b"x")
        os.utime(d / name, (time.time() - 1000 + i * 10, time.time() - 1000 + i * 10))
    return [desk, down, tmp_path / "manca"]


def test_a_file_by_name_full_path_or_latest(folders):
    assert fetch.find_local("vacanze", folders).name == "vacanze mare 2026.mp4"
    assert fetch.find_local("Riunione.m4a", folders).name == "riunione.m4a"
    full = folders[0] / "vacanze mare 2026.mp4"
    assert fetch.find_local(f'"{full}"', folders) == full
    assert fetch.find_local("l'ultimo video", folders).name == "intervista Bianchi.mkv"
    assert fetch.find_local("ultimo audio", folders).name == "canzone.mp3"
    assert fetch.find_local("ultimo", folders).name == "canzone.mp3"


def test_unclear_or_missing_files_are_questions_not_guesses(folders, tmp_path):
    with pytest.raises(MediaError, match="intervista Rossi"):  # due interviste: chiede quale
        fetch.find_local("intervista", folders)
    with pytest.raises(MediaError, match="non trovo"):
        fetch.find_local("matrimonio", folders)
    with pytest.raises(MediaError, match="non esiste"):
        fetch.find_local(str(tmp_path / "nulla.mp4"), folders)
    with pytest.raises(MediaError, match="serve il percorso"):
        fetch.find_local("vacanze", [tmp_path / "vuota"])
    with pytest.raises(MediaError, match="quale video"):
        fetch.find_local("  ", folders)


def test_urls():
    assert fetch.is_url("https://www.youtube.com/watch?v=abc") and fetch.is_url("http://x.it/v.mp4")
    assert not fetch.is_url("youtube.com/watch?v=abc") and not fetch.is_url("file:///C:/a.mp4")
    assert not fetch.is_url("C:\\video.mp4")


# ------------------------------------------------------------------ siti (yt-dlp finto)


class FakeYdl:
    """Come yt_dlp.YoutubeDL: scrive i file chiesti nella cartella e restituisce le informazioni."""

    last: ClassVar[dict] = {}

    def __init__(self, opts, info=None, fail=None):
        self.opts, self.info, self.fail = opts, info, fail
        FakeYdl.last = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        if self.fail:
            raise RuntimeError(self.fail)
        dest = Path(self.opts["outtmpl"]).parent
        info = dict(self.info or {"title": "Il gatto", "uploader": "Mario", "duration": 125, "extractor_key": "Youtube"})
        reqs = []
        for fid, v, a in ([("140", "none", "mp4a")] if self.opts["format"] == "ba/b" else
                          [("244", "vp9", "none"), ("140", "none", "mp4a")]):
            p = dest / f"abc.{fid}.{'webm' if v != 'none' else 'm4a'}"
            p.write_bytes(b"x")
            reqs.append({"filepath": str(p), "vcodec": v, "acodec": a})
        info.setdefault("requested_downloads", reqs)
        return info


def ydl(**kw):
    return lambda opts: FakeYdl(opts, **kw)


def test_download_keeps_video_and_audio_apart(tmp_path):
    m = fetch.download("https://www.youtube.com/watch?v=abc", tmp_path, ydl_factory=ydl())
    assert m.video.name == "abc.244.webm" and m.audio.name == "abc.140.m4a" and m.origin == "web"
    assert m.name == "«Il gatto» di Mario (YouTube, 2:05)"
    o = FakeYdl.last
    assert o["format"].startswith("bv*[height<=480]") and o["format"].endswith(",ba/b")  # virgola: niente FFmpeg
    assert "[vcodec^=avc1]" in o["format"].split("/")[0]  # H.264 prima: solo lì si saltano i fotogrammi non chiave
    assert o["js_runtimes"] == {"deno": {}, "node": {}} and o["noplaylist"] and o["max_filesize"] == 2 * 1024 ** 3
    assert o["outtmpl"].startswith(str(tmp_path))


def test_download_audio_only_and_bigger_frames(tmp_path):
    m = fetch.download("https://youtu.be/abc", tmp_path, audio_only=True, ydl_factory=ydl())
    assert m.video is None and m.audio.suffix == ".m4a" and FakeYdl.last["format"] == "ba/b"
    fetch.download("https://youtu.be/abc", tmp_path, frame_px=1024, ydl_factory=ydl())
    assert "height<=720" in FakeYdl.last["format"]


@pytest.mark.parametrize("kw, words", [
    ({"fail": "\x1b[0;31mERROR:\x1b[0m [youtube] abc: Video unavailable"}, "non riesco a scaricarlo: [youtube] abc"),
    ({"info": {"_type": "playlist", "entries": [{}], "requested_downloads": []}}, "playlist"),
    ({"info": {"title": "x", "requested_downloads": []}}, "senza file"),
])
def test_download_problems_are_explained(tmp_path, kw, words):
    with pytest.raises(MediaError, match=words.replace("[", r"\[").replace("]", r"\]")):
        fetch.download("https://www.youtube.com/watch?v=abc", tmp_path, ydl_factory=ydl(**kw))
    with pytest.raises(MediaError, match="indirizzo non valido"):
        fetch.download("ftp://x/y", tmp_path, ydl_factory=ydl())


# ------------------------------------------------------------------ WhatsApp


class FakeWa:
    def __init__(self, messages, ready=None):
        self.messages, self._ready = messages, ready
        self.calls = []

    def ready(self):
        return self._ready

    async def resolve(self, q, groups=None):
        if q == "nessuno":
            raise AppError("non trovo una chat chiamata 'nessuno'")
        return {"id": "39333@c.us", "name": "Mamma", "group": False}

    async def call(self, op, timeout_s=60.0, **args):
        self.calls.append((op, args))
        if op == "messages":
            return {"messages": self.messages}
        if op == "download":
            p = Path(args["dir"]) / f"{args['msg']}.ogg"
            p.write_bytes(b"x")
            return {"file": str(p)}
        raise AssertionError(op)


MSGS = [{"id": "v1", "type": "video", "ts": 1000, "author": "Mamma"},
        {"id": "t1", "type": "chat", "body": "ciao"},
        {"id": "p1", "type": "ptt", "ts": 2000, "from_me": True},
        {"id": "i1", "type": "image"}]


async def test_whatsapp_gives_the_latest_video_or_voice_note(tmp_path):
    wa = FakeWa(MSGS)
    path, what = await wa_mod.fetch_media(wa, "mamma", tmp_path, now=lambda: 3000)
    assert path.name == "p1.ogg" and what.startswith("vocale tuo nella chat Mamma")
    path, what = await wa_mod.fetch_media(wa, "mamma", tmp_path, "penultimo", now=lambda: 3000)
    assert path.name == "v1.ogg" and what.startswith("video di Mamma")


async def test_whatsapp_problems(tmp_path):
    with pytest.raises(AppError, match="nessun video, vocale o audio"):
        await wa_mod.fetch_media(FakeWa([{"id": "t", "type": "chat"}]), "mamma", tmp_path)
    with pytest.raises(AppError, match="non è pronto: non è collegato"):
        await wa_mod.fetch_media(FakeWa(MSGS, ready="non è collegato al telefono"), "mamma", tmp_path)
    with pytest.raises(AppError, match="non trovo"):
        await wa_mod.fetch_media(FakeWa(MSGS), "nessuno", tmp_path)


# ------------------------------------------------------------------ lo strumento guarda_video


class FakeOmni:
    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    async def describe(self, audio, t0, frames, question=""):
        self.calls.append((len(audio), t0, len(frames), question))
        if self.fail:
            raise MediaError(self.fail)
        return "[0:00-0:30] soft piano music"


@pytest.fixture
def media(monkeypatch, tmp_path, folders):
    """Il servizio vero con decodifica, sito, WhatsApp e modello dei suoni finti."""
    cfg = MediaConfig()
    seen = {}

    async def fake_watch(video, audio, stt, limits, name, start=None, end=None, keep_audio=False):
        seen["watch"] = (video, audio, limits, name, start, end, keep_audio)
        seen["tmp_exists"] = (audio or video).exists()
        w = watched()
        w.name = name
        return w, (voice_like(30) if keep_audio else None)

    def fake_download(url, dest, audio_only, frame_px):
        seen["download"] = (url, audio_only, frame_px)
        v, a = dest / "v.webm", dest / "a.m4a"
        v.write_bytes(b"x")
        a.write_bytes(b"x")
        return fetch.Media("«Il gatto» (YouTube, 2:05)", v, a, "web")

    async def fake_whatsapp(chat, dest, which):
        seen["whatsapp"] = (chat, which)
        p = dest / "vocale.ogg"
        p.write_bytes(b"x")
        return p, "vocale di Mamma nella chat Mamma (ieri 10:00)"

    monkeypatch.setattr(service, "watch", fake_watch)
    perms = {"why": None}
    omni = FakeOmni()
    m = service.MediaWatcher(lambda: cfg, fake_ears, folders=folders, whatsapp=fake_whatsapp,
                             may_whatsapp=lambda: perms["why"], omni=omni, download=fake_download, tmp_root=tmp_path)
    return m, cfg, seen, perms, omni


async def test_look_at_a_local_file(media):
    m, _, seen, _, _ = media
    out = await m.look({"sorgente": "vacanze", "inizio": "0:10", "fine": "1:00"})
    assert len(out.images) == 3 and "vacanze mare 2026.mp4 (in " in out.parts[0]
    video, audio, limits, _, start, end, keep = seen["watch"]
    assert video == audio and video.name == "vacanze mare 2026.mp4" and (start, end) == (10.0, 60.0) and not keep
    assert limits == Limits()


async def test_look_at_a_site_in_a_temporary_folder_then_clean_up(media, tmp_path):
    m, cfg, seen, _, _ = media
    cfg.frame_px = 1024
    out = await m.look({"sorgente": "https://www.youtube.com/watch?v=abc", "solo_audio": True})
    assert seen["download"] == ("https://www.youtube.com/watch?v=abc", True, 1024)
    video, audio, *_ = seen["watch"]
    assert video is None and audio.name == "a.m4a" and seen["tmp_exists"]
    assert not audio.exists() and not list(tmp_path.glob("jarvis_video_*"))  # nessun file lasciato in giro
    assert "Il gatto" in out.parts[0]


async def test_sites_can_be_switched_off(media):
    m, cfg, seen, _, _ = media
    cfg.web = False
    out = await m.look({"sorgente": "https://www.youtube.com/watch?v=abc"})
    assert out.text.startswith("Errore: scaricare video dai siti è spento") and "download" not in seen


async def test_whatsapp_videos_need_the_messages_permission(media):
    m, _, seen, perms, _ = media
    out = await m.look({"chat": "mamma", "quale": "penultimo"})
    assert seen["whatsapp"] == ("mamma", "penultimo") and "WhatsApp, vocale di Mamma" in out.parts[0]
    seen.clear()
    perms["why"] = "Mi dispiace, i messaggi sono solo di Andrea."
    out = await m.look({"chat": "mamma"})
    assert out.text == "Mi dispiace, i messaggi sono solo di Andrea." and not seen  # niente scaricato


async def test_sounds_only_when_asked_and_failures_become_notes(media):
    m, _, seen, _, omni = media
    out = await m.look({"sorgente": "vacanze"})
    assert not omni.calls and "Musica e suoni" not in out.text
    out = await m.look({"sorgente": "vacanze", "suoni": True, "domanda": "che canzone è?"})
    assert seen["watch"][-1] is True and omni.calls[-1][1:] == (0.0, 3, "che canzone è?")
    assert "soft piano music" in out.text
    omni.fail = "il modello per musica e suoni è spento (impostazione media.omni)"
    out = await m.look({"sorgente": "vacanze", "suoni": "true"})
    assert "Nota: il modello per musica e suoni è spento" in out.text and len(out.images) == 3


async def test_look_problems_are_clear_errors(media):
    m, *_ = media
    assert (await m.look({})).text.startswith("Errore: serve sorgente")
    assert (await m.look({"sorgente": "vacanze", "inizio": "dopo"})).text.startswith("Errore: tempo non valido")
    assert (await m.look({"sorgente": "matrimonio"})).text.startswith("Errore: non trovo")


def test_settings_written_by_hand_stay_in_measure():
    cfg = MediaConfig(frames=500, frame_px=50, max_minutes=0, transcript_chars=10)
    assert service.limits_of(cfg) == Limits(frames=50, frame_px=256, max_minutes=1.0, transcript_chars=2000)


# ------------------------------------------------------------------ nel cervello frontale


@pytest.fixture
def front(tmp_path, media):
    m, *_ = media
    store = ConfigStore(tmp_path / "config.yaml")
    mem = Memory(tmp_path / "m.db")
    yield build_front_tools(Services(Bus(), store, mem, media=m)), store
    mem.close()


async def test_guarda_video_is_a_front_tool_with_pictures(front):
    reg, _ = front
    tool = reg.get("guarda_video")
    assert tool.group == TOOL_GROUPS["guarda_video"] == "pc" and tool.read_only
    out = await tool.run({"sorgente": "vacanze"})
    assert len(out.images) == 3
    text = await reg.call("guarda_video", {"sorgente": "vacanze"})
    assert "3 immagini non mostrate" in text  # i cervelli senza vista lo sanno
    sdk = next(t for t in reg.sdk_tools() if t.name == "guarda_video")
    res = await sdk.handler({"sorgente": "vacanze"})
    assert sum(b["type"] == "image" for b in res["content"]) == 3


async def test_media_settings_by_voice(front):
    reg, store = front
    out = await reg.call("impostazione", {"chiave": "fotogrammi_video", "valore": "12"})
    assert store.get("media.frames") == 12 and "vale subito" in out
    await reg.call("impostazione", {"chiave": "modello_suoni", "valore": "Audio Flamingo"})
    assert store.get("media.omni") == "audio-flamingo-next"
    await reg.call("impostazione", {"chiave": "media.omni", "valore": "qwen"})
    assert store.get("media.omni") == "qwen2.5-omni-7b"
    await reg.call("impostazione", {"chiave": "media.web", "valore": "no"})
    assert store.get("media.web") is False
    await reg.call("impostazione", {"chiave": "media.max_minutes", "valore": "30"})
    assert store.get("media.max_minutes") == 30.0
    for key, bad in (("fotogrammi_video", "500"), ("media.frame_px", "4000"), ("modello_suoni", "gemini"),
                     ("media.transcript_chars", "10")):
        out = await reg.call("impostazione", {"chiave": key, "valore": bad})
        assert out.startswith("Errore") and ("fuori misura" in out or "sconosciuto" in out), out
    assert store.get("media.frames") == 12 and store.get("media.omni") == "qwen2.5-omni-7b"


def test_media_settings_are_documented_for_jarvis(tmp_path):
    rows = {r["chiave"]: r for r in settings_catalog(ConfigStore(tmp_path / "c.yaml").config)
            if r["chiave"].startswith("media.")}
    assert set(rows) == {"media.frames", "media.frame_px", "media.max_minutes", "media.transcript_chars",
                         "media.web", "media.omni"}
    assert all(r["descrizione"] and r["subito"] for r in rows.values())
    assert rows["media.omni"]["scelte"] == ["audio-flamingo-next", "lfm3-omni-2b", "qwen2.5-omni-7b", "spento"]


def test_dependencies_are_declared():
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"av>=' in text and '"yt-dlp[default]>=' in text


async def test_missing_pyav_is_explained(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_av(name, *a, **k):
        if name == "av":
            raise ImportError("no av")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_av)
    with pytest.raises(MediaError, match="uv add av"):
        W.probe(Path("x.mp4"))


def test_frames_events_never_log_bytes():
    assert "byte" in repr(Image(JPEG)) and "finto" not in repr(Image(JPEG))
    assert json.dumps({"x": repr(Image(JPEG))})  # serializzabile nei registri
    assert asyncio.iscoroutinefunction(service.MediaWatcher.look)
