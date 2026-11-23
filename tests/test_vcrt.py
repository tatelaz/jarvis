"""Il runtime di Visual C++ portato da Jarvis (jarvis/vcrt.py) per i PC che non ce l'hanno.

Trovato in Windows Sandbox il 23/11: senza msvcp140.dll onnxruntime e soxr non si caricano ("DLL load failed").
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis import vcrt

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = vcrt.system_dir()
needs_windows = pytest.mark.skipif(os.name != "nt", reason="solo Windows")


@pytest.fixture(autouse=True)
def _clean_list(monkeypatch):
    monkeypatch.setattr(vcrt, "LOADED", [])  # le prove con i finti caricamenti non sporcano quella vera


@needs_windows
def test_reads_the_version_written_in_the_file(tmp_path):
    k = vcrt.file_version(SYSTEM / "kernel32.dll")
    assert k is not None and k[0] >= 10 and len(k) == 4
    (tmp_path / "finta.dll").write_bytes(b"MZ niente versione")
    assert vcrt.file_version(tmp_path / "finta.dll") is None
    assert vcrt.file_version(tmp_path / "manca.dll") is None


def _versions(table):
    return lambda p: table.get((p.parent.name, p.name))


def test_ours_only_when_the_system_one_is_missing_or_older(tmp_path):
    sys_dir, ours = tmp_path / "System32", tmp_path / "vcrt"
    mine = (14, 51, 36247, 0)
    assert vcrt.needed(sys_dir, ours, _versions({("vcrt", "msvcp140.dll"): mine}))  # Windows appena installato
    assert vcrt.needed(sys_dir, ours, _versions({("vcrt", "msvcp140.dll"): mine,
                                                 ("System32", "msvcp140.dll"): (14, 38, 33135, 0)}))
    assert not vcrt.needed(sys_dir, ours, _versions({("vcrt", "msvcp140.dll"): mine, ("System32", "msvcp140.dll"): mine}))
    assert not vcrt.needed(sys_dir, ours, _versions({("vcrt", "msvcp140.dll"): mine,
                                                     ("System32", "msvcp140.dll"): (14, 52, 0, 0)}))  # Windows Update
    assert not vcrt.needed(sys_dir, ours, _versions({}))  # copia presa con git: niente tools/vcrt


def _folder(tmp_path, names):
    d = tmp_path / "tools" / "vcrt"
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_bytes(b"MZ")
    return d


def test_loads_the_whole_family_by_full_path_skipping_what_is_already_there(tmp_path):
    ours = _folder(tmp_path, vcrt.FILES)
    calls = []
    got = vcrt.use([tmp_path / "altrove", ours], tmp_path / "System32",
                   version=_versions({("vcrt", "msvcp140.dll"): (14, 51, 0, 0)}), load=calls.append,
                   loaded=lambda n: n == "vcruntime140.dll")  # quella l'ha già caricata Python
    assert calls == [str(ours / n) for n in vcrt.FILES if n != "vcruntime140.dll"]
    assert got == [n for n in vcrt.FILES if n != "vcruntime140.dll"] and vcrt.CHECKED
    assert calls.index(str(ours / "vcruntime140_1.dll")) < calls.index(str(ours / "msvcp140.dll"))


def test_nothing_when_not_needed_and_never_an_error(tmp_path):
    ours = _folder(tmp_path, vcrt.FILES)
    calls = []
    same = _versions({("vcrt", "msvcp140.dll"): (14, 51, 0, 0), ("System32", "msvcp140.dll"): (14, 51, 0, 0)})
    assert vcrt.use([ours], tmp_path / "System32", version=same, load=calls.append, loaded=lambda n: False) == []
    assert calls == []
    assert vcrt.use([tmp_path / "vuota"], tmp_path / "System32", load=calls.append, loaded=lambda n: False) == []

    def broken(path):
        if path.endswith("msvcp140_1.dll"):
            raise OSError("[WinError 193] non è un'applicazione Win32 valida")
        calls.append(path)

    newer = _versions({("vcrt", "msvcp140.dll"): (14, 51, 0, 0)})
    got = vcrt.use([ours], tmp_path / "System32", version=newer, load=broken, loaded=lambda n: False)
    assert got == ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"]  # si ferma lì, senza eccezioni


def test_found_also_next_to_the_python_of_the_installed_copy(monkeypatch, tmp_path):
    """Le copie separate (data/migliorie/..., data/aggiornamenti/...) non hanno tools/: lo si trova risalendo dal
    Python della copia installata, che sta in tools/python/<versione>."""
    base = tmp_path / "Jarvis" / "tools" / "python" / "cpython-3.12.12-windows-x86_64-none"
    monkeypatch.setattr(sys, "base_prefix", str(base))
    assert tmp_path / "Jarvis" / "tools" / "vcrt" in vcrt.places()
    assert ROOT / "tools" / "vcrt" in vcrt.places()


def test_importing_jarvis_checks_first_thing():
    code = "import jarvis.vcrt as v, sys; print(v.CHECKED, 'onnxruntime' in sys.modules, 'soxr' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.stdout.split() == ["True", "False", "False"]


@needs_windows
def test_with_a_real_copy_the_libraries_come_from_tools_vcrt(tmp_path):
    """Davvero, in un processo a parte: con le librerie del sistema in una cartella e il sistema "senza", soxr e
    onnxruntime si caricano e il msvcp140 in memoria è proprio quello della cartella."""
    if vcrt.file_version(SYSTEM / "msvcp140.dll") is None:
        pytest.skip("questo PC non ha il runtime da copiare")
    ours = tmp_path / "tools" / "vcrt"
    ours.mkdir(parents=True)
    for n in vcrt.FILES:
        if (SYSTEM / n).is_file():
            shutil.copy2(SYSTEM / n, ours / n)
    code = f"""
import ctypes, pathlib
from jarvis import vcrt
got = vcrt.use([pathlib.Path(r"{ours}")], pathlib.Path(r"{tmp_path / 'System32'}"))
import soxr, onnxruntime
k = ctypes.WinDLL("kernel32")
k.GetModuleHandleW.restype = ctypes.c_void_p
buf = ctypes.create_unicode_buffer(600)
k.GetModuleFileNameW(ctypes.c_void_p(k.GetModuleHandleW("msvcp140.dll")), buf, 600)
print("msvcp140.dll" in got, buf.value)
"""
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    loaded_now, where = out.stdout.split(maxsplit=1)
    assert loaded_now == "True" and Path(where.strip()) == ours / "msvcp140.dll"
