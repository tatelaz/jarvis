set -e
cd "$(dirname "$0")/../engine"
for a in audio-v0.9.0-bin-windows-x64-cuda13.3.zip audio-v0.9.0-cudart-windows-x64-cuda13.3.zip; do
  [ -f "$a" ] || gh release download v0.9.0 -R 0xShug0/audio.cpp -p "$a"
done
mkdir -p audiocpp
unzip -o -q audio-v0.9.0-bin-windows-x64-cuda13.3.zip -d audiocpp
unzip -o -q audio-v0.9.0-cudart-windows-x64-cuda13.3.zip -d audiocpp
echo ENGINE_DONE
