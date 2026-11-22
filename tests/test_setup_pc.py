"""Controllo del PC: dai dati (veri o inventati) al profilo che il primo avvio propone."""

import json

import pytest

from jarvis.setup import catalog, pc

RTX_4060 = pc.Gpu("NVIDIA GeForce RTX 4060", "nvidia", 8188, "610.88")


def facts(*gpus, ram=32768, free=500_000, mics=1, speakers=1, online=True) -> pc.Facts:
    return pc.Facts(tuple(gpus), ram, free, mics, speakers, online)


def all_dests(engine: str, voice: str) -> set[str]:
    return {f.dest for p in catalog.plan(engine, voice) for f in p.files}


def test_eight_gigabyte_nvidia_gets_everything():
    p = pc.choose(facts(RTX_4060))
    assert (p.key, p.engine, p.voice) == ("completo", "engine-cuda13", "voice")
    assert p.gpu == RTX_4060 and p.ok and p.notes == []
    assert p.download_mb > 4000


def test_older_driver_takes_the_cuda12_engine():
    p = pc.choose(facts(pc.Gpu("RTX 3070", "nvidia", 8192, "560.94")))
    assert (p.key, p.engine) == ("completo", "engine-cuda12")


@pytest.mark.parametrize("driver", ["531.18", "", "illeggibile"])
def test_too_old_or_missing_driver_falls_back_to_the_processor(driver):
    p = pc.choose(facts(pc.Gpu("RTX 3070", "nvidia", 8192, driver)))
    assert (p.key, p.engine, p.gpu) == ("processore", "engine-cpu", None)
    assert "driver_vecchio" in p.notes and "voce_lenta" in p.notes


def test_six_gigabytes_is_the_light_profile_and_says_it_is_not_measured():
    p = pc.choose(facts(pc.Gpu("RTX 2060", "nvidia", 6144, "610.00")))
    assert (p.key, p.engine, p.voice) == ("leggero", "engine-cuda13", "voice-small")
    assert "da_misurare" in p.notes


def test_tiny_nvidia_is_not_used():
    p = pc.choose(facts(pc.Gpu("GT 1030", "nvidia", 2048, "610.00")))
    assert (p.key, p.gpu) == ("processore", None)
    assert "poca_memoria_video" in p.notes


@pytest.mark.parametrize("vendor", ["amd", "intel"])
def test_amd_and_intel_go_on_the_processor_for_now(vendor):
    p = pc.choose(facts(pc.Gpu("Radeon RX 7800", vendor, 16384, "31.0")))
    assert (p.key, p.engine, p.voice) == ("processore", "engine-cpu", "voice-small")
    assert p.notes == ["scheda_non_nvidia", "voce_lenta"]


def test_no_graphics_card_at_all():
    p = pc.choose(facts())
    assert p.key == "processore" and p.notes == ["voce_lenta"]


def test_processor_profile_is_blocked_until_the_voice_runs_without_nvidia():
    """Il motore della voce oggi parte solo con CUDA: meglio dirlo prima che installare un Jarvis muto."""
    assert pc.choose(facts()).blockers == ["voce_solo_nvidia"]
    assert pc.choose(facts(pc.Gpu("Radeon", "amd", 16384))).blockers == ["voce_solo_nvidia"]
    assert pc.choose(facts(RTX_4060)).ok


def test_biggest_nvidia_wins():
    small = pc.Gpu("GTX 1650", "nvidia", 4096, "610.00")
    p = pc.choose(facts(small, RTX_4060))
    assert p.gpu == RTX_4060 and p.key == "completo"


def test_missing_microphone_and_speakers_are_said_but_do_not_block():
    p = pc.choose(facts(RTX_4060, mics=0, speakers=0))
    assert {"niente_microfono", "niente_casse"} <= set(p.notes)
    assert p.ok  # si può scrivere a Jarvis e collegare le casse dopo


def test_too_little_memory_blocks():
    p = pc.choose(facts(RTX_4060, ram=4096))
    assert p.blockers == ["poca_ram"] and not p.ok


def test_disk_space_threshold_is_exact():
    need = pc.choose(facts(RTX_4060)).need_mb
    assert pc.choose(facts(RTX_4060, free=need)).ok
    assert pc.choose(facts(RTX_4060, free=need - 1)).blockers == ["poco_spazio"]


def test_needed_space_counts_extraction_and_margin():
    p = pc.choose(facts(RTX_4060))
    zips = sum(f.size for f in catalog.PACKAGES["engine-cuda13"].files)
    assert p.need_mb >= p.download_mb + (zips // 2) // 2**20 + pc.MARGIN_MB


def test_offline_blocks_only_if_something_is_missing():
    assert pc.choose(facts(RTX_4060, online=False)).blockers == ["niente_rete"]
    done = all_dests("engine-cuda13", "voice")
    p = pc.choose(facts(RTX_4060, online=False, free=0), present=done)
    assert p.ok and p.download_mb == 0 and p.need_mb == 0


def test_files_already_present_are_not_counted():
    whole = pc.choose(facts(RTX_4060))
    voice = catalog.PACKAGES["voice"].files[0]
    less = pc.choose(facts(RTX_4060), present=[voice.dest])
    assert whole.download_mb - less.download_mb == pytest.approx(voice.size / 2**20, abs=1)


def test_profile_for_the_page_is_plain_json():
    data = json.loads(json.dumps(pc.choose(facts(RTX_4060, mics=0)).public()))
    assert data["gpu"] == {"name": RTX_4060.name, "vram_mb": 8188}
    assert data["ok"] is True and data["notes"] == ["niente_microfono"]
    assert json.loads(json.dumps(pc.choose(facts()).public()))["gpu"] is None


def test_reads_nvidia_smi():
    text = "NVIDIA GeForce RTX 4060, 8188, 610.88\nNVIDIA RTX A2000, 6138, 610.88\nrotto\n, x, y\n"
    assert pc.parse_nvidia_smi(text) == [RTX_4060, pc.Gpu("NVIDIA RTX A2000", "nvidia", 6138, "610.88")]
    assert pc.parse_nvidia_smi("") == []


def test_reads_windows_video_controllers():
    one = json.dumps({"Name": "AMD Radeon RX 6600", "AdapterRAM": 4293918720, "DriverVersion": "31.0.1"})
    assert pc.parse_cim(one) == [pc.Gpu("AMD Radeon RX 6600", "amd", 4095, "31.0.1")]
    many = json.dumps([{"Name": "Microsoft Basic Display Adapter", "AdapterRAM": 0},
                       {"Name": "Intel(R) UHD Graphics 770", "AdapterRAM": None, "DriverVersion": "31.0"},
                       {"Name": "Microsoft Remote Display Adapter"}, {"Name": ""}])
    assert pc.parse_cim(many) == [pc.Gpu("Intel(R) UHD Graphics 770", "intel", 0, "31.0")]
    assert pc.parse_cim("non json") == [] and pc.parse_cim("") == []


def test_nvidia_from_windows_only_means_no_working_driver():
    cim = [pc.Gpu("NVIDIA GeForce RTX 4060", "nvidia", 4095, "32.0.15.6109"), pc.Gpu("Intel UHD", "intel")]
    merged = pc.merge_gpus([], cim)
    assert merged[0].driver == "" and merged[1].vendor == "intel"
    assert "driver_vecchio" in pc.choose(facts(*merged)).notes
    assert pc.merge_gpus([RTX_4060], cim) == (RTX_4060, pc.Gpu("Intel UHD", "intel"))


def test_counts_each_audio_device_once():
    hostapis = [{"name": "MME"}, {"name": "Windows WASAPI"}]
    devices = [{"hostapi": 0, "max_input_channels": 2, "max_output_channels": 0},
               {"hostapi": 1, "max_input_channels": 1, "max_output_channels": 0},
               {"hostapi": 1, "max_input_channels": 0, "max_output_channels": 2},
               {"hostapi": 1, "max_input_channels": 0, "max_output_channels": 2}]
    assert pc.count_devices(devices, hostapis) == (1, 2)
    assert pc.count_devices(devices[:1], [{"name": "MME"}]) == (1, 0)  # senza WASAPI vale quello che c'è


@pytest.mark.parametrize(("name", "vendor"), [("NVIDIA GeForce GTX 1080", "nvidia"), ("AMD Radeon 780M", "amd"),
                                              ("Intel(R) Arc A770", "intel"), ("Matrox G200", "altro")])
def test_vendor_from_the_name(name, vendor):
    assert pc.vendor_of(name) == vendor


def test_driver_number():
    assert pc.driver_number("610.88") == 610.88
    assert pc.driver_number("551.61.01") == 551.61
    assert pc.driver_number("") == 0.0


@pytest.mark.hardware
def test_gather_on_this_pc(tmp_path):
    f = pc.gather(tmp_path / "non" / "ancora" / "creata")
    assert f.ram_mb > 1000 and f.free_mb > 0
    assert pc.choose(f).key in ("completo", "leggero", "processore")


def test_missing_tools_are_downloaded_and_counted_with_the_rest():
    bare = pc.choose(facts(RTX_4060))
    tools = pc.choose(facts(RTX_4060), tools=catalog.TOOLS)
    size = sum(catalog.PACKAGES[k].size for k in catalog.TOOLS)
    assert tools.download_mb - bare.download_mb == pytest.approx(size / 2**20, abs=1)
    assert tools.need_mb > bare.need_mb + size // 2**20  # anche gli strumenti si estraggono
    done = all_dests("engine-cuda13", "voice")
    p = pc.choose(facts(RTX_4060, online=False), present=done, tools=["tool-git"])
    assert p.blockers == ["niente_rete"]  # manca solo Git, ma senza rete non arriva
