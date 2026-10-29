import numpy as np
import pytest

from jarvis.audio.voicefx import VoiceFx, VoiceFxParams


@pytest.fixture
def speechlike():
    rng = np.random.default_rng(0)
    sr = 24000
    t = np.arange(sr * 2) / sr
    # vocale sintetica: armoniche di 140 Hz modulate + un po' di rumore
    x = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 30))
    x *= 0.5 * (1 + np.sin(2 * np.pi * 3 * t))
    return (0.2 * x / np.abs(x).max() + 0.002 * rng.standard_normal(t.size)).astype(np.float32), sr


def test_intensity_zero_is_identity(speechlike):
    x, sr = speechlike
    y = VoiceFx(VoiceFxParams(intensity=0.0)).process(x, sr)
    assert np.array_equal(y, x)


@pytest.mark.parametrize("params", [
    VoiceFxParams(), VoiceFxParams(flanger_mix=0.0),
    VoiceFxParams(eq_strength=1.0, flanger_mix=0.55, flanger_feedback=0.4, reflections_mix=0.18, drive=0.25),
])
def test_keeps_level_no_clipping_and_length(speechlike, params):
    x, sr = speechlike
    y = VoiceFx(params).process(x, sr)
    assert y.shape == x.shape and y.dtype == np.float32
    assert np.all(np.isfinite(y))
    assert np.max(np.abs(y)) <= 0.98 + 1e-6
    rms = lambda a: float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))  # noqa: E731
    assert abs(rms(y) / rms(x) - 1) < 0.05


def test_effect_changes_spectrum_like_the_film():
    # rumore bianco: ogni banda ha energia, così si vede davvero la curva applicata
    sr = 24000
    x = (0.1 * np.random.default_rng(1).standard_normal(sr * 2)).astype(np.float32)
    y = VoiceFx(VoiceFxParams(flanger_mix=0.0)).process(x, sr)
    f = np.fft.rfftfreq(x.size, 1 / sr)
    X, Y = np.abs(np.fft.rfft(x)) ** 2, np.abs(np.fft.rfft(y)) ** 2

    def band(P, lo, hi):
        return 10 * np.log10(P[(f >= lo) & (f < hi)].sum())

    # come nel film: i bassi profondi calano rispetto alla zona 300-500 Hz
    assert (band(Y, 60, 120) - band(Y, 300, 500)) < (band(X, 60, 120) - band(X, 300, 500)) - 6


def test_deterministic_and_empty_input(speechlike):
    x, sr = speechlike
    fx = VoiceFx()
    assert np.array_equal(fx.process(x, sr), fx.process(x, sr))
    assert fx.process(np.zeros(0, np.float32), sr).size == 0
