import numpy as np
import pytest

from jarvis.audio.stretch import wsola

SR = 24000


def dominant_hz(x: np.ndarray, sr: int) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    return float(np.fft.rfftfreq(x.size, 1 / sr)[int(np.argmax(spec))])


def voiced(seconds=1.0, f0=150.0):
    """Un parlato finto: armoniche di f0 con un inviluppo sillabico a 4 Hz."""
    t = np.arange(int(SR * seconds)) / SR
    harm = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 6))
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)
    return (0.2 * harm * env).astype(np.float32)


@pytest.mark.parametrize("factor", [0.8, 1.15, 1.3])
def test_duration_changes_pitch_does_not(factor):
    x = voiced(1.0)
    y = wsola(x, factor, SR)
    assert abs(y.size - round(x.size * factor)) <= 1
    mid = slice(y.size // 4, 3 * y.size // 4)
    assert abs(dominant_hz(y[mid], SR) - dominant_hz(x[x.size // 4: 3 * x.size // 4], SR)) < 4.0
    assert np.isfinite(y).all() and np.abs(y).max() < 1.0


def test_syllable_rhythm_slows_down():
    x = voiced(2.0)
    y = wsola(x, 1.25, SR)

    def env_rate(sig):  # frequenza dell'inviluppo (il "ritmo delle sillabe")
        e = np.abs(sig).reshape(-1, 240).mean(axis=1)  # 10 ms
        e = e - e.mean()
        spec = np.abs(np.fft.rfft(e))
        spec[0] = 0
        return float(np.fft.rfftfreq(e.size, 0.01)[int(np.argmax(spec))])

    assert env_rate(y[: y.size // 240 * 240]) == pytest.approx(4.0 / 1.25, abs=0.4)


def test_no_clicks_on_a_steady_tone():
    t = np.arange(SR) / SR
    x = (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    y = wsola(x, 1.2, SR)
    core = y[1000:-1000]
    # un raccordo sbagliato produce salti tra campioni molto più grandi di quelli della sinusoide
    assert np.abs(np.diff(core)).max() < 2.0 * np.abs(np.diff(x)).max()


@pytest.mark.parametrize("factor", [0.6, 0.75, 1.5])
def test_extreme_factors_on_long_input_stay_in_bounds(factor):
    x = voiced(5.0)
    y = wsola(x, factor, SR)
    assert abs(y.size - round(x.size * factor)) <= 1 and np.isfinite(y).all()
    assert np.sqrt(np.mean(y ** 2)) == pytest.approx(np.sqrt(np.mean(x ** 2)), rel=0.15)  # stesso volume


def test_identity_and_empty():
    x = voiced(0.3)
    assert np.array_equal(wsola(x, 1.0, SR), x)
    assert wsola(np.zeros(0, np.float32), 1.2, SR).size == 0
    assert wsola(x[:100], 1.2, SR).size == 120  # più corto di una finestra: non si rompe
