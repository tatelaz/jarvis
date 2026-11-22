"""Cosa si scarica al primo avvio, da dove e con quale impronta.

Ogni indirizzo punta a una versione fissa (un tag o un commit), mai all'ultima: un file cambiato alla fonte
non deve arrivare a nessuno senza essere stato provato qui. Le impronte SHA-256 sono quelle dei file che Jarvis
usa davvero (controllate il 30/10 contro i file di questo PC e le impronte pubblicate dalle fonti); se una fonte
cambia il file, il download fallisce in modo chiaro invece di installare qualcosa di diverso.

Due file non esistono già pronti da nessuna parte (parakeet-ultra convertito in GGUF, ReDimNet2+ esportato in
ONNX: vedi docs/STATO.md) e stanno nella release `modelli-1` del progetto (`MIRROR`), con le loro licenze.
`scripts/controlla_catalogo.py` verifica in rete che ogni indirizzo risponda con la misura giusta.
"""

from __future__ import annotations

from dataclasses import dataclass

AUDIOCPP = "https://github.com/0xShug0/audio.cpp/releases/download/v0.9.2-drama3-base-small/"
GGUF = "https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/661a224e9d9adbd49c01626f85e426f5f425d771/"
SMART_TURN = "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/"
SILERO = "https://raw.githubusercontent.com/snakers4/silero-vad/bfdc0193023f121ea5b3cc7b176dbed570a68a59/"
GEMMA = "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/daa72c51243991dfcaf9f9137d2c573d8f7790c0/"
MIRROR = "https://github.com/tatelaz/jarvis/releases/download/modelli-1/"  # le conversioni fatte da noi
# strumenti: le stesse versioni con cui Jarvis è provato (OpenCode come quello installato il 22/11)
OPENCODE = "https://github.com/anomalyco/opencode/releases/download/v1.19.3/"
NODE_VERSION = "22.19.0"
NODE = f"https://nodejs.org/dist/v{NODE_VERSION}/"
GIT = "https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/"

ROOTS = ("engine", "models")  # dest comincia sempre con una di queste: la cartella vera la decide paths


@dataclass(frozen=True)
class File:
    url: str
    sha256: str
    size: int
    dest: str  # "engine/..." o "models/...", con le barre in avanti
    unzip_to: str = ""  # archivi: la cartella in cui si estraggono (poi l'archivio si cancella)
    check: str = ""  # archivi: un file che dopo l'estrazione deve esserci
    # "zip", oppure "sfx": un archivio 7-Zip autoestraente (Git portatile), che si apre eseguendolo con -o e -y,
    # solo dopo averne controllato l'impronta
    kind: str = "zip"


@dataclass(frozen=True)
class Package:
    key: str
    what: str  # per chi sviluppa: la pagina del primo avvio ha i suoi testi, in italiano e in inglese
    licence: str
    files: tuple[File, ...]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


def _engine(variant: str, bin_sha: str, bin_size: int, rt_sha: str = "", rt_size: int = 0) -> tuple[File, ...]:
    name = f"audio-v0.9.2-drama3-base-small-bin-windows-x64-{variant}.zip"
    files = [File(AUDIOCPP + name, bin_sha, bin_size, f"engine/{name}", "engine/audiocpp",
                  "engine/audiocpp/audiocpp_server.exe")]
    if rt_sha:  # le librerie di CUDA stanno in un archivio a parte
        rt = f"audio-v0.9.2-drama3-base-small-cudart-windows-x64-{variant}.zip"
        files.append(File(AUDIOCPP + rt, rt_sha, rt_size, f"engine/{rt}", "engine/audiocpp",
                          "engine/audiocpp/cudart64_" + ("13" if variant == "cuda13.3" else "12") + ".dll"))
    return tuple(files)


PACKAGES = {p.key: p for p in (
    Package("engine-cuda13", "motore audio.cpp per schede NVIDIA (driver dalla 580)", "Apache-2.0", _engine(
        "cuda13.3", "b570efa4b17c2aa32c431c653489c009b58f57f60d68782b3b94b6c06304a9e8", 275837338,
        "cf58e70c53db680af5e4b8e5ef538ffd1d81bd489ef49843fe486fcd079c9182", 575457454)),
    Package("engine-cuda12", "motore audio.cpp per schede NVIDIA con driver più vecchi", "Apache-2.0", _engine(
        "cuda12.4", "48852da40790c40f3285bca6c53ff7829b3963080f0e95b6742f868fc1d7c757", 464268606,
        "4b1186a525c9ac70a4fbd031b47e0dfca2ebaa36cfcb760d5be1a76eb2b12da5", 607273675)),
    Package("engine-cpu", "motore audio.cpp solo processore", "Apache-2.0", _engine(
        "cpu", "cc34162b028d2b948d174259d58f63a1caf42fd45884bfb32fb632bdc062dd86", 28876613)),
    Package("ears", "orecchie: parakeet-ultra (Moondream) in GGUF q8_0", "CC-BY-4.0", (
        File(MIRROR + "parakeet-ultra-q8_0.gguf",
             "724c49c5e4530fa739244cce7a88f1740b00ee07a3f9d6c7827a1739b24c0cbb", 751250688,
             "models/audiocpp/Parakeet-Ultra-GGUF/parakeet-ultra-q8_0.gguf"),)),
    Package("voice", "voce: Qwen3-TTS 1.7B Base q8_0", "Apache-2.0", (
        File(GGUF + "Qwen3-TTS-12Hz-1.7B-Base-GGUF/qwen3-tts-12hz-1.7b-base-q8_0_v2.gguf",
             "b55e06c7890d43c208d15aed8b4ed3f18215f295e47d5960e061b15bff338ab0", 2695175104,
             "models/audiocpp/Qwen3-TTS-12Hz-1.7B-Base-GGUF/qwen3-tts-12hz-1.7b-base-q8_0_v2.gguf"),)),
    Package("voice-small", "voce leggera: Qwen3-TTS 0.6B Base q8_0", "Apache-2.0", (
        File(GGUF + "Qwen3-TTS-12Hz-0.6B-Base-GGUF/qwen3-tts-12hz-0.6b-base-q8_0.gguf",
             "771420bd20ff5f35407b4fa9cf9c5461e153800d3d772ef51c9febc0a520855d", 1991211136,
             "models/audiocpp/Qwen3-TTS-12Hz-0.6B-Base-GGUF/qwen3-tts-12hz-0.6b-base-q8_0.gguf"),)),
    Package("voice-fallback", "voce di riserva istantanea: Supertonic 3 (il motore della voce la carica sempre)",
            "OpenRAIL", (
        File(GGUF + "Supertonic-3-GGUF/supertonic-3-orig.gguf",
             "af814486a0bc9513fb36afabd9b1155ad14fb2c36a107ac6ffe62ea9adafb662", 454072836,
             "models/audiocpp/Supertonic-3-GGUF/supertonic-3-orig.gguf"),)),
    Package("turns", "turni di parola: Silero VAD e Smart Turn v3.2", "MIT, BSD-2-Clause", (
        File(SILERO + "src/silero_vad/data/silero_vad.onnx",
             "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3", 2327524,
             "models/onnx/silero_vad.onnx"),
        File(SMART_TURN + "smart-turn-v3.2-cpu.onnx",
             "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f", 8679182,
             "models/onnx/smart-turn-v3.2-cpu.onnx"))),
    Package("speakers", "chi parla: ReDimNet2+ (LAB260) in ONNX", "Apache-2.0 (pesi), MIT (codice)", (
        File(MIRROR + "redimnet2plus.onnx",
             "0c5edce801bb41be814364352f138e23aac062d9449ac18232ec68a8a7831d62", 50965301,
             "models/speaker/redimnet2-plus/redimnet2plus.onnx"),)),
    Package("memory", "memoria per significato: EmbeddingGemma 2 q4 in ONNX", "Apache-2.0", tuple(
        File(GEMMA + name, sha, size, "models/onnx/embeddinggemma-2/" + name) for name, sha, size in (
            ("config.json", "8d011bfe08b5e345bbe0b81e5c6fd02c381920b345b986047bc2a33ce7b90d1d", 5031),
            ("tokenizer.json", "4d777ef5bdc1aa36227abdfb77c3e49e7b9c892d16e1b6bda41c393504828be4", 32170510),
            ("tokenizer_config.json", "17bd5d6e9364ca49a534e1502076593317c298d4a663623091ed45388f004874", 1599),
            ("onnx/model_q4.onnx", "f9eeba97acddf139b8ee2ddf04bc30dceafa88de93fadf74d7644e0d61a477a9", 490742),
            ("onnx/model_q4.onnx_data", "c3975f2d1ab7a1878ae31a7d7a9b7804a827aff3800b60dfceafce21cac3df49",
             174028800)))),
    # strumenti, scaricati solo se il PC non li ha già (jarvis/setup/flow.py, missing_tools)
    Package("tool-opencode", "OpenCode: il cervello gratuito, senza account", "MIT", (
        File(OPENCODE + "opencode-windows-x64.zip",
             "e4ce9117f55fc068414513789f8c7d1049f855d7518bb4e350b9170a3a20d8c3", 62204138,
             "engine/tools/opencode-windows-x64.zip", "engine/tools/opencode", "engine/tools/opencode/opencode.exe"),)),
    Package("tool-node", "Node.js: il ponte di WhatsApp", "MIT", (
        File(NODE + f"node-v{NODE_VERSION}-win-x64.zip",
             "ea3fad0e67a991d8477d8c01344b56e69c676ccb733f065b22436994b1253f86", 35424607,
             f"engine/tools/node-v{NODE_VERSION}-win-x64.zip", "engine/tools/node",
             f"engine/tools/node/node-v{NODE_VERSION}-win-x64/node.exe"),)),
    Package("tool-git", "Git per Windows portatile, con bash: Claude Code e l'auto-miglioramento", "GPL-2.0", (
        File(GIT + "PortableGit-2.56.1-64-bit.7z.exe",
             "9dd04a608ceb5f6ab32a73709e0ecb159d211da71b697c45ff81f6dd0c6b49c7", 60125512,
             "engine/tools/PortableGit-2.56.1-64-bit.7z.exe", "engine/tools/git", "engine/tools/git/cmd/git.exe",
             kind="sfx"),)),
)}

ENGINES = ("engine-cuda13", "engine-cuda12", "engine-cpu")
TOOLS = ("tool-opencode", "tool-node", "tool-git")
VOICES = ("voice", "voice-small")
ALWAYS = ("ears", "voice-fallback", "turns", "speakers", "memory")


def plan(engine: str, voice: str) -> list[Package]:
    """I pacchetti da scaricare per un motore e una voce (li sceglie il profilo del PC)."""
    if engine not in ENGINES:
        raise ValueError(f"motore sconosciuto: {engine}")
    if voice not in VOICES:
        raise ValueError(f"voce sconosciuta: {voice}")
    return [PACKAGES[k] for k in (engine, *ALWAYS, voice)]
