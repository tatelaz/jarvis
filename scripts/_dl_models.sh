set -e
cd "$(dirname "$0")/../models"
export HF_HUB_ENABLE_HF_TRANSFER=0
for pat in "Nemotron-3.5-ASR-Streaming-0.6B-GGUF/*q8_0*" "Parakeet-TDT-0.6B-v3-GGUF/*q8_0*" "Smart-Turn-v3-GGUF/*" "OmniVoice-GGUF/omnivoice-bf16.gguf" "Supertonic-3-GGUF/supertonic-3-orig.gguf" "Qwen3-TTS-12Hz-1.7B-Base-GGUF/*q8_0_v2*" "Qwen3-TTS-12Hz-1.7B-VoiceDesign-GGUF/*q8_0*"; do
  echo "== $pat"
  hf download audio-cpp/audio.cpp-gguf --include "$pat" --local-dir audiocpp 2>&1 | tail -1
done
echo MODELS_DONE
