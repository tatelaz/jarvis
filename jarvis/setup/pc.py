"""Controllo del PC al primo avvio: scheda video, memoria, spazio, microfono e casse, rete. Ne esce un profilo.

Le regole del profilo sono funzioni pure (`choose`), separate dalla raccolta dei dati (`gather`), così si provano
su qualsiasi PC immaginario. Le soglie vengono dalle misure su questo progetto (docs/STATO.md): voce 1.7B e
orecchie insieme sulla scheda video occupano 5,6 GB al lavoro; sul solo processore la voce clonata impiega
12,8 s a frase. Le schede AMD e Intel (motore Vulkan) e le NVIDIA sotto 8 GB non sono ancora state misurate su
un PC vero: il profilo lo dice invece di promettere. Il motore della voce oggi parte solo su una NVIDIA
(`EngineSpec.config` scrive `backend: cuda`): finché non c'è un ripiego, il profilo "processore" è un blocco
(`voce_solo_nvidia`) e il setup non installa un Jarvis che non saprebbe parlare.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import logging
import shutil
import socket
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import catalog

log = logging.getLogger("jarvis.setup")

FULL_VRAM_MB = 7600  # le schede da 8 GB dichiarano 8188 MiB: sopra questa soglia va tutto come sul PC di sviluppo
LIGHT_VRAM_MB = 4000  # sotto non ci sta neanche la voce leggera con un po' di margine
CUDA13_DRIVER = 580.0  # driver NVIDIA minimo per i programmi CUDA 13
CUDA12_DRIVER = 551.61  # driver NVIDIA minimo per CUDA 12.4 su Windows
MIN_RAM_MB = 7500  # i PC da 8 GB dichiarano un po' meno di 8192
MARGIN_MB = 2048  # spazio che deve restare libero dopo il download (registri, memoria, aggiornamenti)
UNZIP_FACTOR = 1.5  # gli archivi del motore, estratti, occupano circa una volta e mezza (per un momento ci sono entrambi)
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: niente finestre nere che lampeggiano durante il controllo


@dataclass(frozen=True)
class Gpu:
    name: str
    vendor: str  # "nvidia", "amd", "intel" o "altro"
    vram_mb: int = 0  # 0 = non si sa
    driver: str = ""


@dataclass(frozen=True)
class Facts:
    gpus: tuple[Gpu, ...]
    ram_mb: int
    free_mb: int  # spazio libero dove vanno motore e modelli
    mics: int
    speakers: int
    online: bool


@dataclass
class Profile:
    key: str  # "completo", "leggero" o "processore"
    engine: str  # chiavi del catalogo
    voice: str
    gpu: Gpu | None
    download_mb: int  # quanto resta da scaricare
    need_mb: int  # quanto spazio serve, compreso l'estrazione e il margine
    notes: list[str] = field(default_factory=list)  # cose da dire, in codici: la pagina ha i testi nelle due lingue
    blockers: list[str] = field(default_factory=list)  # cose che impediscono di continuare

    @property
    def ok(self) -> bool:
        return not self.blockers

    def public(self) -> dict:
        return {"key": self.key, "engine": self.engine, "voice": self.voice, "ok": self.ok,
                "gpu": self.gpu and {"name": self.gpu.name, "vram_mb": self.gpu.vram_mb},
                "download_mb": self.download_mb, "need_mb": self.need_mb,
                "notes": list(self.notes), "blockers": list(self.blockers)}


def vendor_of(name: str) -> str:
    n = name.lower()
    for vendor, words in (("nvidia", ("nvidia", "geforce", "quadro", "rtx")), ("amd", ("amd", "radeon")),
                          ("intel", ("intel", "arc "))):
        if any(w in n for w in words):
            return vendor
    return "altro"


def driver_number(driver: str) -> float:
    """"610.88" -> 610.88; vuoto o illeggibile -> 0 (come se il driver non ci fosse)."""
    try:
        return float(".".join(driver.strip().split(".")[:2]))
    except ValueError:
        return 0.0


def choose(facts: Facts, present: Iterable[str] = (), tools: Iterable[str] = ()) -> Profile:
    """Il profilo per questo PC. present: i `dest` del catalogo già scaricati e controllati; tools: i pacchetti
    strumento che mancano al PC (OpenCode, Node.js, Git: si scaricano con il resto)."""
    notes: list[str] = []
    nvidia = sorted((g for g in facts.gpus if g.vendor == "nvidia"), key=lambda g: g.vram_mb, reverse=True)
    gpu = nvidia[0] if nvidia else None
    key, engine, voice = "processore", "engine-cpu", "voice-small"
    if gpu is not None:
        drv = driver_number(gpu.driver)
        cuda = "engine-cuda13" if drv >= CUDA13_DRIVER else "engine-cuda12" if drv >= CUDA12_DRIVER else ""
        if not cuda:
            notes.append("driver_vecchio")
            gpu = None
        elif gpu.vram_mb >= FULL_VRAM_MB:
            key, engine, voice = "completo", cuda, "voice"
        elif gpu.vram_mb >= LIGHT_VRAM_MB:
            key, engine, voice = "leggero", cuda, "voice-small"
            notes.append("da_misurare")
        else:
            notes.append("poca_memoria_video")
            gpu = None
    elif any(g.vendor in ("amd", "intel") for g in facts.gpus):
        notes.append("scheda_non_nvidia")
    if key == "processore":
        notes.append("voce_lenta")
    if facts.mics == 0:
        notes.append("niente_microfono")
    if facts.speakers == 0:
        notes.append("niente_casse")

    have = set(present)
    packages = [*catalog.plan(engine, voice), *(catalog.PACKAGES[k] for k in tools)]
    todo = [f for p in packages for f in p.files if f.dest not in have]
    download = sum(f.size for f in todo)
    unzip = sum(f.size for f in todo if f.unzip_to)
    download_mb = -(-download // 2**20)
    need_mb = -(-int(download + unzip * (UNZIP_FACTOR - 1)) // 2**20) + (MARGIN_MB if todo else 0)
    blockers = []
    if key == "processore":
        blockers.append("voce_solo_nvidia")
    if facts.ram_mb < MIN_RAM_MB:
        blockers.append("poca_ram")
    if todo and facts.free_mb < need_mb:
        blockers.append("poco_spazio")
    if todo and not facts.online:
        blockers.append("niente_rete")
    return Profile(key, engine, voice, gpu, download_mb, need_mb, notes, blockers)


# --- raccolta dei dati veri ---


def parse_nvidia_smi(text: str) -> list[Gpu]:
    """Uscita di `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits`."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        name, mem, drv = (c.strip() for c in row[:3])
        with contextlib.suppress(ValueError):
            out.append(Gpu(name, "nvidia", int(float(mem)), drv))
    return out


def parse_cim(text: str) -> list[Gpu]:
    """Le schede da Win32_VideoController in JSON (un oggetto o un elenco). La memoria lì si ferma a 4 GB: per le
    NVIDIA vale quella di nvidia-smi, per le altre resta un'indicazione."""
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for d in data if isinstance(data, list) else [data]:
        name = str(d.get("Name") or "").strip()
        if not name or "basic display" in name.lower() or "remote" in name.lower():
            continue  # driver di base di Windows e schermi remoti: non sono schede vere
        ram = d.get("AdapterRAM") or 0
        out.append(Gpu(name, vendor_of(name), int(ram) // 2**20 if isinstance(ram, int | float) else 0,
                       str(d.get("DriverVersion") or "")))
    return out


def merge_gpus(smi: list[Gpu], cim: list[Gpu]) -> tuple[Gpu, ...]:
    """nvidia-smi ha la memoria e il driver giusti per le NVIDIA; le altre schede vengono da Windows. Se nvidia-smi
    non risponde, le NVIDIA di Windows restano senza driver: vuol dire che CUDA non partirebbe."""
    others = [g for g in cim if g.vendor != "nvidia"]
    nvidia = smi or [Gpu(g.name, "nvidia", g.vram_mb, "") for g in cim if g.vendor == "nvidia"]
    return (*nvidia, *others)


def count_devices(devices: list[dict], hostapis: list[dict]) -> tuple[int, int]:
    """Microfoni e casse visti da Windows (WASAPI: ogni dispositivo una volta sola, come nelle impostazioni)."""
    wasapi = {i for i, h in enumerate(hostapis) if "wasapi" in str(h.get("name", "")).lower()}
    mine = [d for d in devices if not wasapi or d.get("hostapi") in wasapi]
    return (sum(1 for d in mine if d.get("max_input_channels", 0) > 0),
            sum(1 for d in mine if d.get("max_output_channels", 0) > 0))


def _run(cmd: list[str], timeout: float = 15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        log.info("controllo del PC: %s non risponde (%s)", cmd[0], e)
        return ""
    return r.stdout if r.returncode == 0 else ""


def reachable(hosts: Iterable[str] = ("huggingface.co", "github.com"), timeout: float = 4) -> bool:
    """Si raggiungono i siti da cui si scarica? (una connessione e basta, niente dati inviati)"""
    for host in hosts:
        try:
            with socket.create_connection((host, 443), timeout=timeout):
                pass
        except OSError:
            return False
    return True


def gather(target: Path) -> Facts:
    """I dati di questo PC. target: la cartella dove andranno motore e modelli (per lo spazio libero)."""
    import psutil
    import sounddevice as sd

    smi = parse_nvidia_smi(_run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                                 "--format=csv,noheader,nounits"]))
    cim = parse_cim(_run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion"
                          " | ConvertTo-Json -Compress"]))
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        mics, speakers = count_devices([dict(d) for d in sd.query_devices()],
                                       [dict(h) for h in sd.query_hostapis()])
    except Exception:  # driver audio rotti: si dice "niente microfono" invece di fermare tutto
        log.exception("controllo del PC: dispositivi audio illeggibili")
        mics = speakers = 0
    return Facts(merge_gpus(smi, cim), psutil.virtual_memory().total // 2**20,
                 shutil.disk_usage(probe).free // 2**20, mics, speakers, reachable())
