"""Una voce nuova da un file: scelta del pezzo, volume, trascrizione, scheda con l'avvertenza. Il parlato qui è
sintetico (sillabe di rumore colorato separate da pause): niente voci di persone vere."""

import json

import numpy as np
import pytest
import soundfile as sf

from jarvis.audio.voicefx import VoiceFxParams
from jarvis.speech import engine
from jarvis.speech import voice_maker as V

SR = V.REF_SR


def syllables(seconds: float, rng, level=0.2, pause_every=3.0, pause_s=0.5, sr=SR) -> np.ndarray:
    """Parlato finto: sillabe piene da 200 ms (attacco e coda di 20 ms) con stacchi da 30 ms, e una pausa di fine
    frase ogni pause_every secondi. Come il parlato vero delle voci incluse: circa l'85 % di finestre con voce."""
    n, edge = int(0.2 * sr), int(0.02 * sr)
    env = np.ones(n)
    env[:edge], env[-edge:] = np.linspace(0, 1, edge), np.linspace(1, 0, edge)
    out, t = [], 0.0
    while t < seconds:
        phrase = min(pause_every, seconds - t)
        k = 0.0
        while k < phrase:
            out += [rng.standard_normal(n) * level * env * rng.uniform(0.5, 1.0), np.zeros(int(0.03 * sr))]
            k += 0.23
        out.append(np.zeros(int(pause_s * sr)))
        t += phrase + pause_s
    return np.concatenate(out).astype(np.float32)


def silence(seconds: float, sr=SR) -> np.ndarray:
    return np.zeros(int(seconds * sr), np.float32)


@pytest.fixture
def rng():
    return np.random.default_rng(7)


# --- il nome ---


def test_names_become_folder_names():
    assert V.slug("Voce del Nonno") == "voce-del-nonno"
    assert V.slug("  Àèìòù!! 2 ") == "aeiou-2"
    for bad in ("", "  !!  ", "base-it", "Base Mia"):
        with pytest.raises(V.VoiceMakerError):
            V.slug(bad)


# --- la scelta del pezzo ---


def test_the_span_is_full_of_speech_and_starts_and_ends_in_a_pause(rng):
    audio = np.concatenate([silence(5), syllables(40, rng), silence(5)])
    a, b = V.best_span(audio, SR)
    assert 5.0 - 0.15 <= a and b <= 45.5
    assert V.BEST_MIN_S <= b - a <= V.BEST_MAX_S + 0.2
    db = V.frame_db(audio, SR)
    voiced = V.voiced_frames(db)
    i, j = round(a / V.HOP_S), round(b / V.HOP_S)
    assert not voiced[i] or not voiced[i - 1]  # comincia in una pausa (o subito sulla prima sillaba)
    assert not voiced[min(j, voiced.size - 1)] or not voiced[j - 1]  # e finisce in una pausa


def test_long_pauses_and_clipping_are_avoided(rng):
    gappy = np.concatenate([syllables(6, rng), silence(2.5), syllables(6, rng)])  # una pausa lunga in mezzo
    loud = syllables(14, rng, level=3.0)  # saturato
    clean = syllables(14, rng)
    audio = np.concatenate([silence(1), gappy, silence(1), np.clip(loud, -1, 1), silence(1), clean, silence(1)])
    a, b = V.best_span(audio, SR)
    clean_from = (1 + len(gappy) / SR + 1 + len(loud) / SR + 1)
    assert a >= clean_from - 0.15, (a, b, clean_from)


def test_one_long_pause_weighs_more_than_short_ones(rng):
    """Un pezzo con una sola pausa lunga (più voce in tutto: 0,84 contro 0,78) perde contro uno con le pause brevi
    di fine frase: la pausa lunga pesa di più."""
    one_gap = np.concatenate([syllables(5.5, rng, pause_every=99), silence(1.2), syllables(5.5, rng, pause_every=99)])
    phrases = syllables(12.5, rng, pause_every=2.0, pause_s=0.6)
    audio = np.concatenate([silence(1), one_gap, silence(1.5), phrases, silence(1)])
    a, _b = V.best_span(audio, SR)
    assert a >= 1 + len(one_gap) / SR + 1.5 - 0.15


@pytest.mark.parametrize("audio", [
    silence(30),  # niente
    np.zeros(int(6 * SR), np.float32) + 0.1,  # troppo corto
    np.tile(np.concatenate([np.random.default_rng(3).standard_normal(int(0.2 * SR)).astype(np.float32) * 0.2,
                            silence(0.7)]), 70),  # parole rade: troppo poco parlato
])
def test_no_speech_no_voice(audio):
    with pytest.raises(V.VoiceMakerError):
        V.best_span(audio, SR)


def test_a_sound_without_pauses_is_not_taken_for_speech(rng):
    hum = (0.2 * np.sin(2 * np.pi * 220 * np.arange(60 * SR) / SR)).astype(np.float32)  # un suono continuo
    with pytest.raises(V.VoiceMakerError, match="inizio e fine"):
        V.best_span(hum, SR)


def test_the_choice_is_quick_on_a_long_file(rng):
    import time

    audio = syllables(600, rng)  # dieci minuti, il massimo che si guarda
    t = time.perf_counter()
    V.best_span(audio, SR)
    assert time.perf_counter() - t < 5


# --- il volume ---


def test_prepare_levels_the_speech_and_fades_the_edges(rng):
    quiet = syllables(12, rng, level=0.01) + 0.003  # basso e con un po' di continua
    x = V.prepare(quiet, SR)
    db = V.frame_db(x, SR)
    voiced = V.voiced_frames(db)
    hop = int(V.HOP_S * SR)
    speech = x[:voiced.size * hop].reshape(-1, hop)[voiced]
    assert 0.06 < float(np.sqrt(np.mean(speech ** 2))) <= 0.11
    assert float(np.max(np.abs(x))) <= V.PEAK + 1e-6 and x[0] == 0 and x[-1] == 0
    assert abs(float(np.mean(x))) < 1e-3
    with pytest.raises(V.VoiceMakerError, match="silenzio"):
        V.prepare(silence(12), SR)
    spiky = syllables(12, rng, level=0.05)
    spiky[SR] = 0.9  # un colpo isolato molto più forte del parlato
    assert float(np.max(np.abs(V.prepare(spiky, SR)))) <= V.PEAK + 1e-6


# --- la voce intera ---


class FakeFile:
    """Il file da cui si prende la voce, già decodificato: si chiedono pezzi a una certa frequenza."""

    def __init__(self, audio24: np.ndarray) -> None:
        self.audio, self.asked = audio24, []

    def load(self, path, start, end, rate):
        self.asked.append((round(start, 2), round(end, 2), rate))
        x = self.audio[int(start * SR):int(min(end, len(self.audio) / SR) * SR)]
        return x if rate == SR else x[::3].copy() if rate == 8000 else np.interp(
            np.arange(int(len(x) * rate / SR)) * SR / rate, np.arange(len(x)), x).astype(np.float32)


@pytest.fixture
def maker(tmp_path, rng):
    heard = []

    async def transcribe(audio16):
        heard.append(len(audio16))
        return "  Buongiorno a tutti,   questa è una prova  della voce. "

    f = FakeFile(np.concatenate([silence(3), syllables(30, rng), silence(3)]))
    m = V.VoiceMaker(transcribe, tmp_path / "voices", load=f.load, language=lambda: "it")
    return m, f, heard


async def test_a_voice_is_made_with_its_card_and_the_warning(maker, tmp_path):
    m, f, heard = maker
    made = await m.make(tmp_path / "Intervista del nonno.mp4", "Il Nonno", whose="mio nonno Aldo")
    assert made.name == "il-nonno" and made.folder == tmp_path / "voices" / "il-nonno"
    info = sf.info(made.folder / "ref.wav")
    assert info.samplerate == 24000 and info.channels == 1 and info.subtype == "PCM_16"
    assert V.BEST_MIN_S <= info.duration <= V.BEST_MAX_S + 0.2
    meta = json.loads((made.folder / "voice.json").read_text(encoding="utf-8"))
    assert meta["description"].startswith("Ispirata alla voce di mio nonno Aldo.")
    assert "Non è la voce vera" in meta["description"] and "uso personale" in meta["description"]
    assert meta["imitation"] is True and meta["language"] == "it" and meta["ref"] == "ref.wav"
    assert meta["ref_text"] == "Buongiorno a tutti, questa è una prova della voce."
    assert meta["fx"] == V.JARVIS_FX and VoiceFxParams(**meta["fx"]).intensity == 1.0
    assert meta["source"]["file"] == "Intervista del nonno.mp4" and meta["source"]["from_s"] == round(made.start, 2)
    assert f.asked[0] == (0.0, V.MAX_SCAN_S, 24000)  # prima si cerca il pezzo, poi lo si prende
    assert f.asked[-1][2] == V.STT_SR and heard  # alle orecchie a 16 kHz
    assert engine.voice_dir("il-nonno", tmp_path / "voices", tmp_path / "incluse") == made.folder
    assert "il-nonno" in engine.available_voices(tmp_path / "voices", tmp_path / "incluse")


async def test_a_chosen_span_and_no_effect(maker, tmp_path):
    m, f, _ = maker
    made = await m.make(tmp_path / "a.wav", "pulita", start=4.0, end=15.5, effect=False)
    assert (made.start, made.end) == (4.0, 15.5)
    assert all(a[0] == 4.0 for a in f.asked)  # il file intero non si legge nemmeno
    meta = json.loads((made.folder / "voice.json").read_text(encoding="utf-8"))
    assert meta["fx"] == {"intensity": 0.0} and VoiceFxParams(**meta["fx"]).intensity == 0.0
    assert meta["description"].startswith("Ispirata alla voce di a.")  # senza "di chi" si usa il nome del file


@pytest.mark.parametrize(("kw", "msg"), [
    ({"start": 3.0}, "tutti e due"),
    ({"start": 3.0, "end": 5.0}, "deve durare"),
    ({"start": 3.0, "end": 40.0}, "deve durare"),
    ({"start": -1.0, "end": 10.0}, "deve durare"),
    ({"start": 30.0, "end": 45.0}, "finisce prima"),  # il file dura 36 s
])
async def test_wrong_spans_are_refused(maker, tmp_path, kw, msg):
    m, _, _ = maker
    with pytest.raises(V.VoiceMakerError, match=msg):
        await m.make(tmp_path / "a.wav", "x", **kw)
    assert not (tmp_path / "voices" / "x").exists()


async def test_nothing_is_left_behind_when_the_words_are_not_understood(maker, tmp_path):
    m, _, _ = maker

    async def mumble(audio16):
        return "ehm"

    m.transcribe = mumble
    with pytest.raises(V.VoiceMakerError, match="parole"):
        await m.make(tmp_path / "a.wav", "borbotti")
    assert not (tmp_path / "voices" / "borbotti").exists()


async def test_an_existing_voice_is_never_overwritten(maker, tmp_path):
    m, _, _ = maker
    await m.make(tmp_path / "a.wav", "mia")
    before = (tmp_path / "voices" / "mia" / "ref.wav").read_bytes()
    with pytest.raises(V.VoiceMakerError, match="c'è già"):
        await m.make(tmp_path / "b.wav", "Mia")
    assert (tmp_path / "voices" / "mia" / "ref.wav").read_bytes() == before


def test_load_audio_reads_at_the_rate_asked(tmp_path, rng):
    from jarvis.media.watch import load_audio

    path = tmp_path / "prova.wav"
    sf.write(path, (0.3 * np.sin(2 * np.pi * 440 * np.arange(5 * 48000) / 48000)).astype(np.float32), 48000)
    for rate in (24000, 16000):
        x = load_audio(path, 1.0, 3.0, rate) if rate != 16000 else load_audio(path, 1.0, 3.0)  # di norma 16 kHz
        assert x.size == 2 * rate
        peak_hz = np.argmax(np.abs(np.fft.rfft(x))) * rate / x.size
        assert abs(peak_hz - 440) < 2  # lo stesso suono: letto davvero a quella frequenza


# --- a voce: lo strumento ---


@pytest.fixture
def tool(maker, tmp_path):
    from jarvis.config import ConfigStore
    from jarvis.core.bus import Bus
    from jarvis.memory.store import Memory
    from jarvis.tools.front import TOOL_GROUPS, Services, build_front_tools

    assert TOOL_GROUPS["crea_voce"] == "sistema"  # solo l'utente
    m, f, _ = maker
    mem = Memory(tmp_path / "m.db")
    yield build_front_tools(Services(Bus(), ConfigStore(tmp_path / "config.yaml"), mem, voice_maker=m)), f
    mem.close()


async def test_the_tool_makes_the_voice_and_says_it_is_not_the_real_one(tool, tmp_path):
    reg, _ = tool
    src = tmp_path / "Intervista.mp4"
    src.write_bytes(b"finto")
    out = await reg.call("crea_voce", {"file": str(src), "nome": "Nonno Aldo", "di_chi": "mio nonno",
                                       "inizio": "0:04", "fine": "0:15,5", "effetto": False})
    assert out.startswith("voce 'nonno-aldo' creata da Intervista.mp4 (0:04-0:16)")  # i tempi arrotondati
    assert "NON è la voce vera" in out and "uso personale" in out and "voce_di_jarvis = nonno-aldo" in out
    meta = json.loads((tmp_path / "voices" / "nonno-aldo" / "voice.json").read_text(encoding="utf-8"))
    assert meta["fx"] == {"intensity": 0.0} and meta["source"]["to_s"] == 15.5


async def test_the_tool_says_what_is_wrong(tool, tmp_path):
    reg, _ = tool
    out = await reg.call("crea_voce", {"file": str(tmp_path / "non-esiste.mp3"), "nome": "x"})
    assert out.startswith("Errore") and "non esiste" in out
    src = tmp_path / "a.wav"
    src.write_bytes(b"finto")
    out = await reg.call("crea_voce", {"file": str(src), "nome": "y", "inizio": "domani", "fine": "1:00"})
    assert out.startswith("Errore") and "tempo non valido" in out
    out = await reg.call("crea_voce", {"file": str(src), "nome": "base-it"})
    assert out.startswith("Errore") and "voci incluse" in out
