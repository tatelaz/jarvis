# Licenze di terze parti

Jarvis non contiene modelli né motori: li scarica ognuno dalla loro fonte, con la loro licenza. Qui sotto quelli
usati, con la licenza verificata alla fonte il 30/10/2026. Prima di usare Jarvis per scopi commerciali, leggi le
licenze dei modelli che hai installato: alcune lo vietano.

## Modelli

| Modello | A cosa serve | Licenza | Fonte |
|---|---|---|---|
| parakeet-ultra (Moondream, da Parakeet TDT v3) | orecchie: dalla voce al testo | CC-BY-4.0 | [moondream/parakeet-ultra](https://huggingface.co/moondream/parakeet-ultra) |
| Parakeet TDT 0.6B v3 (NVIDIA) | orecchie di riserva | CC-BY-4.0 | [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) |
| Qwen3-TTS 12Hz 1.7B e 0.6B Base (Qwen) | la voce | Apache-2.0 | [Qwen/Qwen3-TTS-12Hz-1.7B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) |
| Supertonic 3 (Supertone) | voce di riserva istantanea, se la voce principale non risponde | OpenRAIL (con limiti d'uso) | [Supertone/supertonic-3](https://huggingface.co/Supertone/supertonic-3) |
| Silero VAD | capire quando c'è voce | MIT | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) |
| Smart Turn v3 | capire quando hai finito di parlare | BSD-2-Clause | [pipecat-ai/smart-turn](https://github.com/pipecat-ai/smart-turn) |
| ReDimNet2+ (LAB260, sull'architettura ReDimNet di IDR&D) | riconoscere chi parla | pesi Apache-2.0, codice MIT | [lab260/redimnet2-plus](https://huggingface.co/lab260/redimnet2-plus) |
| EmbeddingGemma 2 (Google), in ONNX da onnx-community | memoria per significato | Apache-2.0 | [google/embeddinggemma-2](https://huggingface.co/google/embeddinggemma-2) |
| Audio Flamingo Next (NVIDIA), facoltativo | musica e suoni nei video | licenza NVIDIA propria, solo uso non commerciale | [nvidia/audio-flamingo-next-hf](https://huggingface.co/nvidia/audio-flamingo-next-hf) |
| Sidon v0.1, usato solo per preparare la voce | restauro dell'audio di riferimento | MIT | [sarulab-speech/sidon-v0.1](https://huggingface.co/sarulab-speech/sidon-v0.1) |
| Mel-Band RoFormer, usato solo per preparare la voce | separare la voce dalla musica | MIT | [ZFTurbo/Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training) |

I modelli nel formato GGUF arrivano da [audio-cpp/audio.cpp-gguf](https://huggingface.co/audio-cpp/audio.cpp-gguf):
ogni conversione segue la licenza del modello da cui viene. Due conversioni non esistevano e le abbiamo fatte noi
(parakeet-ultra in GGUF, ReDimNet2+ in ONNX, solo il formato e non i pesi): il primo avvio le scarica dalla release
`modelli-1` di questo repository, che contiene anche provenienza, modifiche e testi delle licenze.

## Voci incluse

Le due voci di base stanno nel codice (`jarvis/speech/voci`): un tratto di circa 12 secondi letto da un volontario
di LibriVox, le cui registrazioni sono di dominio pubblico. Il file e il punto esatto sono scritti in ogni
`voice.json`.

| Voce | Lettore | Da | Licenza | Fonte |
|---|---|---|---|---|
| `base-it` | Riccardo Fasol | *Il fu Mattia Pascal* di Luigi Pirandello, capitolo II | dominio pubblico (LibriVox) | [archive.org](https://archive.org/details/mattia_pascal_1012_librivox) |
| `base-en` | Simon Evers | *Huntingtower* di John Buchan, capitolo II | dominio pubblico (LibriVox) | [archive.org](https://archive.org/details/huntingtower_1205_librivox) |

## Motori e programmi

| Componente | A cosa serve | Licenza | Fonte |
|---|---|---|---|
| audio.cpp | motore di orecchie, voce e modello dei suoni | Apache-2.0 | [0xShug0/audio.cpp](https://github.com/0xShug0/audio.cpp) |
| Claude Code e Claude Agent SDK | il cervello con Claude | Claude Code: termini di Anthropic; SDK Python: MIT | [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) |
| OpenCode | modelli gratuiti quando Claude non c'è (il primo avvio lo scarica se manca) | MIT | [anomalyco/opencode](https://github.com/anomalyco/opencode) |
| Node.js | il ponte di WhatsApp (il primo avvio lo scarica se manca) | MIT | [nodejs.org](https://nodejs.org/) |
| Git per Windows (portatile) | la bash di Claude Code e git per l'auto-miglioramento (il primo avvio lo scarica se manca, dalla release ufficiale; non è incluso in Jarvis) | GPL-2.0 | [git-for-windows/git](https://github.com/git-for-windows/git) |
| whatsapp-web.js | WhatsApp in background (non ufficiale) | Apache-2.0 | [pedroslopez/whatsapp-web.js](https://github.com/pedroslopez/whatsapp-web.js) |
| Playwright MCP | browser usa e getta dei sotto-agenti | Apache-2.0 | [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) |
| Windows-MCP | mouse e tastiera sulle app di Windows | MIT | [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) |
| yt-dlp | scaricare i video da guardare | Unlicense | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| PyAV (con FFmpeg) | fotogrammi e audio dei video | BSD-3-Clause (FFmpeg: LGPL) | [PyAV-Org/PyAV](https://github.com/PyAV-Org/PyAV) |
| uv (Astral) | prepara Python e le librerie; incluso nell'installatore (`tools\uv.exe`) | MIT o Apache-2.0 | [astral-sh/uv](https://github.com/astral-sh/uv) |
| Runtime di Visual C++ (Microsoft) | le librerie che chiedono onnxruntime e soxr, per i PC che non le hanno; incluso nell'installatore (`tools\vcrt`), si usa solo se quello di sistema manca o è più vecchio | file ridistribuibili del Visual C++ Redistributable, licenza di Microsoft | [Microsoft Learn](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist) |

Le librerie Python e le loro versioni esatte sono in `pyproject.toml` e `uv.lock`.
