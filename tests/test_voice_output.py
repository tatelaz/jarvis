"""VoiceOutput con un motore finto: cache, ritentativi, ripiego, riscaldamento vero, precedenza alle frasi vere."""

import asyncio
import time

import numpy as np
import pytest

from jarvis.speech.voice import VoiceError, VoiceOutput, plausible_duration

SR = 24000
META = {"name": "prova", "seed": 7, "fx": {"intensity": 0.0}}


class FakeTts:
    """Parla a 14 caratteri al secondo; si può far sbagliare la durata per seme o far fallire un modello."""

    def __init__(self, bad_seeds=(), broken_models=(), delay=0.0):
        self.calls: list[tuple[str, str, int]] = []
        self.bad_seeds, self.broken_models, self.delay = set(bad_seeds), set(broken_models), delay

    async def synthesize(self, model, text, voice=None, options=None, seed=None, speed=None, timeout=60.0):
        self.calls.append((model, text, seed))
        if self.delay:
            await asyncio.sleep(self.delay)
        if model in self.broken_models:
            raise ConnectionError("motore giù")
        seconds = 0.05 if seed in self.bad_seeds else max(0.3, len(text) / 14)
        t = np.arange(int(SR * seconds)) / SR
        return (0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), SR


def voice_with(tmp_path, tts) -> VoiceOutput:
    return VoiceOutput(tts, META, cache_dir=tmp_path)


async def test_cache_in_memory_and_on_disk(tmp_path):
    tts = FakeTts()
    v = voice_with(tmp_path, tts)
    a1, sr, spoken = await v.say("Certamente, signore.")
    a2, _, _ = await v.say("Certamente, signore.")
    assert len(tts.calls) == 1 and np.array_equal(a1, a2) and spoken == "Certamente, signore." and sr == SR
    assert v.is_cached("Certamente, signore.") and not v.is_cached("Mai detta.")
    fresh = voice_with(tmp_path, tts)  # nuovo avvio: la frase breve è rimasta su disco
    await fresh.say("Certamente, signore.")
    assert len(tts.calls) == 1
    long_text = "Questa è una frase lunga che non vale la pena di salvare su disco perché non tornerà."
    await v.say(long_text)
    assert not voice_with(tmp_path, tts).is_cached(long_text)


async def test_implausible_audio_is_retried_with_another_seed_then_fallback(tmp_path):
    tts = FakeTts(bad_seeds={7})
    v = voice_with(tmp_path, tts)
    await v.say("Ho preparato il rapporto, signore.")
    assert [c[2] for c in tts.calls] == [7, 108] and v.stats["retries"] == 1
    tts2 = FakeTts(broken_models={"tts"})
    v2 = voice_with(tmp_path / "b", tts2)
    await v2.say("Ho preparato il rapporto, signore.")
    assert tts2.calls[-1][0] == "tts-fallback" and v2.stats["fallbacks"] == 1


async def test_total_failure_raises(tmp_path):
    v = voice_with(tmp_path, FakeTts(broken_models={"tts", "tts-fallback"}))
    with pytest.raises(VoiceError):
        await v.say("Niente da fare.")


async def test_prime_really_synthesizes_even_if_cached(tmp_path):
    """Il difetto vero: il riscaldamento prendeva la frase dalla cache e il modello restava da caricare."""
    tts = FakeTts()
    v = voice_with(tmp_path, tts)
    await v.say("Sistemi in linea.")
    await v.say("Sistemi in linea.")
    assert len(tts.calls) == 1
    seconds = await v.prime()
    assert len(tts.calls) == 2 and seconds >= 0


async def test_warm_up_gives_way_to_real_sentences(tmp_path):
    tts = FakeTts(delay=0.05)
    v = voice_with(tmp_path, tts)
    warm = asyncio.ensure_future(v.warm_up(["Prima frase di prova.", "Seconda frase di prova."]))
    await asyncio.sleep(0)
    t = time.perf_counter()
    await v.say("Una risposta vera.")
    assert time.perf_counter() - t < 0.5  # non aspetta tutto il riscaldamento
    # la seconda frase della stessa risposta arriva poco dopo: il riscaldamento non deve infilarsi in mezzo
    await asyncio.sleep(0.01)
    await v.say("E la seconda frase della risposta.")
    # la frase di riscaldamento già partita finisce, poi solo frasi vere finché la conversazione è attiva
    assert [c[1] for c in tts.calls] == ["Prima frase di prova.", "Una risposta vera.",
                                         "E la seconda frase della risposta."]
    warm.cancel()


def test_plausible_duration_bounds():
    assert plausible_duration("Certamente, signore.", 1.4)
    assert not plausible_duration("Certamente, signore.", 0.1)  # troncato
    assert not plausible_duration("Sì.", 30.0)  # generazione impazzita


class PacedTts(FakeTts):
    """Parla a un ritmo fisso (caratteri al secondo), come fa il modello vero con certe frasi e certi semi."""

    def __init__(self, rate):
        super().__init__()
        self.rate = rate

    async def synthesize(self, model, text, voice=None, options=None, seed=None, speed=None, timeout=60.0):
        self.calls.append((model, text, seed))
        t = np.arange(int(SR * len(text) / self.rate)) / SR
        return (0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), SR


TEXT = "Le ricordo che alle diciotto ha la riunione con il gruppo di progetto, signore."


async def test_too_fast_speech_is_brought_to_a_natural_pace(tmp_path):
    v = voice_with(tmp_path, PacedTts(26.0))
    audio, sr, spoken = await v.say(TEXT)
    assert len(spoken) / (audio.size / sr) == pytest.approx(19.0, rel=0.02)  # come il caso segnalato da Andrea


async def test_pace_within_tolerance_is_left_alone(tmp_path):
    v = voice_with(tmp_path, PacedTts(19.8))
    audio, sr, spoken = await v.say(TEXT)
    assert len(spoken) / (audio.size / sr) == pytest.approx(19.8, rel=0.01)


async def test_speed_setting_really_changes_the_pace_and_the_cache(tmp_path):
    speed = {"v": 1.0}
    v = VoiceOutput(PacedTts(26.0), META, cache_dir=tmp_path, speed=lambda: speed["v"])
    slow, sr, _ = await v.say(TEXT)
    speed["v"] = 1.2  # "Jarvis, parla più veloce": vale dalla frase dopo
    fast, _, _ = await v.say(TEXT)
    assert len(v.client.calls) == 2  # la cache non restituisce la versione al ritmo vecchio
    assert fast.size / sr == pytest.approx(slow.size / sr / 1.2, rel=0.03)
    speed["v"] = 9.0
    assert v.speed == 1.6  # limiti di buon senso


async def test_synthesis_time_estimate_learns_from_reality(tmp_path):
    """Il caso vero: con la GPU contesa la sintesi era 2-4× più lenta della stima e i pezzi arrivavano tardi."""
    other = TEXT + " Ancora una frase di prova per la stima."
    v = voice_with(tmp_path, FakeTts(delay=0.0))
    v.synth_a, v.synth_b = 0.01, 0.001  # stima di partenza: ~0,09 s per questa frase
    before = v.estimate_seconds(other)
    v.client.delay = 0.4  # la realtà: molto più lenta
    for i in range(4):
        await v.say(f"{TEXT} {i}")
    slower = v.estimate_seconds(other)
    assert slower > 2.5 * before  # la stima si è adeguata
    assert v.estimate_seconds(f"{TEXT} 0") == 0.0  # già in cache: costa niente
    v.client.delay = 0.0
    for i in range(6):
        await v.say(f"{TEXT} veloce {i}")
    assert v.estimate_seconds(other) < slower  # e torna giù quando la macchina si libera
