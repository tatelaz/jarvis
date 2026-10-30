"""Il catalogo del primo avvio: indirizzi fissi, impronte ben fatte e file esattamente dove Jarvis li cerca."""

import re

import pytest

from jarvis import paths
from jarvis.identity import model as speaker_model
from jarvis.memory import embed
from jarvis.setup import catalog
from jarvis.speech import engine

ALL = [f for p in catalog.PACKAGES.values() for f in p.files]


def rel(path, root) -> str:
    return path.relative_to(root).as_posix()


@pytest.mark.parametrize("f", ALL, ids=lambda f: f.dest)
def test_every_file_is_well_formed(f):
    assert f.url.startswith("https://")
    assert re.fullmatch(r"[0-9a-f]{64}", f.sha256)
    assert f.size > 0
    root, _, rest = f.dest.partition("/")
    assert root in catalog.ROOTS and rest and ".." not in rest.split("/")
    if f.unzip_to:
        assert f.dest.endswith(".zip") and f.check.startswith(f.unzip_to + "/")


@pytest.mark.parametrize("f", ALL, ids=lambda f: f.dest)
def test_every_address_is_pinned_to_one_version(f):
    """Mai "main" o "master": un file cambiato alla fonte non deve arrivare a nessuno senza prove."""
    assert "/main/" not in f.url and "/master/" not in f.url
    pinned = re.search(r"/(resolve/|)[0-9a-f]{40}/", f.url) or "/releases/download/" in f.url
    assert pinned, f.url


def test_no_two_files_share_a_destination_or_a_fingerprint():
    dests = [f.dest for f in ALL]
    assert len(dests) == len(set(dests))
    shas = [f.sha256 for f in ALL]
    assert len(shas) == len(set(shas))


def test_files_land_where_jarvis_looks_for_them():
    by_key = {k: [f.dest for f in p.files] for k, p in catalog.PACKAGES.items()}
    models = "models/" + rel(paths.AUDIOCPP_MODELS, paths.MODELS_DIR) + "/"
    assert by_key["voice"] == [models + engine.MODEL_FILES["qwen3-1.7b"]]
    assert by_key["voice-small"] == [models + engine.MODEL_FILES["qwen3-0.6b"]]
    assert by_key["ears"] == [models + engine.MODEL_FILES["parakeet-ultra"]]
    assert by_key["voice-fallback"] == [models + engine.MODEL_FILES["supertonic"]]
    assert set(by_key["turns"]) == {"models/" + rel(paths.SILERO_VAD, paths.MODELS_DIR),
                                    "models/" + rel(paths.SMART_TURN, paths.MODELS_DIR)}
    assert by_key["speakers"] == ["models/" + rel(speaker_model.MODEL, paths.MODELS_DIR)]
    gemma = "models/" + rel(embed.MODEL_DIR, paths.MODELS_DIR) + "/"
    assert {gemma + "onnx/model_q4.onnx", gemma + "tokenizer.json"} <= set(by_key["memory"])
    server = "engine/" + rel(paths.AUDIOCPP_SERVER, paths.ENGINE_DIR)
    for key in catalog.ENGINES:
        first = catalog.PACKAGES[key].files[0]
        assert first.check == server
        assert first.unzip_to == "engine/" + rel(paths.AUDIOCPP_DIR, paths.ENGINE_DIR)


def test_the_onnx_data_file_travels_with_its_model():
    dests = {f.dest for f in catalog.PACKAGES["memory"].files}
    assert {d + "_data" for d in dests if d.endswith("model_q4.onnx")} <= dests


def test_cuda_engines_bring_their_runtime_and_the_processor_one_does_not():
    assert len(catalog.PACKAGES["engine-cuda13"].files) == 2
    assert len(catalog.PACKAGES["engine-cuda12"].files) == 2
    assert len(catalog.PACKAGES["engine-cpu"].files) == 1


def test_plan_has_one_engine_the_common_parts_and_one_voice():
    keys = [p.key for p in catalog.plan("engine-cuda13", "voice")]
    assert keys == ["engine-cuda13", "ears", "voice-fallback", "turns", "speakers", "memory", "voice"]
    assert [p.key for p in catalog.plan("engine-cpu", "voice-small")][-1] == "voice-small"


@pytest.mark.parametrize(("engine_key", "voice"), [("engine-gpu", "voice"), ("engine-cpu", "voce")])
def test_plan_refuses_unknown_choices(engine_key, voice):
    with pytest.raises(ValueError, match="sconosciut"):
        catalog.plan(engine_key, voice)


def test_package_size_is_the_sum_of_its_files():
    p = catalog.PACKAGES["memory"]
    assert p.size == sum(f.size for f in p.files) > 190 * 2**20
