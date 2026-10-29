"""Parità dei preprocessing con le implementazioni di riferimento e sanità dei modelli ONNX."""

from pathlib import Path

import numpy as np
import pytest

from jarvis.audio import features, turn, vad

FIX = Path(__file__).parent / "fixtures" / "whisper_features_ref.npz"


@pytest.fixture(scope="module")
def ref():
    return np.load(FIX)


# Il riferimento ufficiale di Smart Turn gira senza torch, quindi transformers usa il percorso
# numpy: è quello che dobbiamo riprodurre fedelmente. Il percorso torch (float32) differisce
# di ~4e-5 sulle feature, abbastanza da spostare di qualche millesimo il modello int8.
@pytest.mark.parametrize("name", ["sweep", "noise"])
def test_log_mel_matches_transformers_numpy_path(ref, name):
    ours = turn.prepare(ref[f"{name}_audio"])[0]
    theirs = ref[f"{name}_feats_np"]
    assert ours.shape == theirs.shape == (80, 800)
    # transformers salva lo spettro in complex64, noi restiamo in float64: scarto massimo ~3e-5
    assert np.max(np.abs(ours - theirs)) < 5e-5


@pytest.mark.parametrize("name", ["sweep", "noise"])
def test_smart_turn_probability_matches_reference(ref, name):
    model = turn.SmartTurn()
    p = model.probability(ref[f"{name}_audio"])
    assert abs(p - float(ref[f"{name}_prob_np"])) < 1e-4
    assert abs(p - float(ref[f"{name}_prob"])) < 0.02  # percorso torch: stessa decisione


def test_mel_filters_shape_and_normalization():
    fb = features.mel_filters()
    assert fb.shape == (201, 80)
    assert np.all(fb >= 0)
    # ogni filtro deve avere almeno un coefficiente positivo (nessuna banda vuota)
    assert np.all(fb.max(axis=0) > 0)


def test_turn_prepare_pads_left_and_truncates():
    short = np.ones(16000, dtype=np.float32)
    long = np.ones(16000 * 10, dtype=np.float32)
    assert turn.prepare(short).shape == (1, 80, 800)
    assert turn.prepare(long).shape == (1, 80, 800)


def test_vad_silence_is_not_speech():
    v = vad.SileroVad()
    probs = v.feed(np.zeros(16000, dtype=np.float32))
    assert len(probs) == 16000 // vad.WINDOW
    assert max(probs) < 0.1


def test_vad_buffers_partial_windows():
    v = vad.SileroVad()
    assert v.feed(np.zeros(300, dtype=np.float32)) == []
    assert len(v.feed(np.zeros(300, dtype=np.float32))) == 1


def test_vad_rejects_wrong_window():
    v = vad.SileroVad()
    with pytest.raises(ValueError):
        v.window_prob(np.zeros(100, dtype=np.float32))
