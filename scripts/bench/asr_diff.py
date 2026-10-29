import asyncio, json, sys
from pathlib import Path
import soundfile as sf
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.speech.stt import transcribe_file
sys.path.insert(0, str(Path(__file__).parent))
from asr_bench import wer, DATA
async def main():
    meta = json.load(open(DATA/"meta.json", encoding="utf-8"))[:40]
    for item in meta:
        a,_ = sf.read(DATA/item["file"], dtype="float32")
        p = (await transcribe_file("127.0.0.1",8790,"parakeet",a))["text"]
        n = (await transcribe_file("127.0.0.1",8790,"nemotron-off",a,language="it-IT"))["text"]
        ep,_ = wer(item["text"],p); en,_ = wer(item["text"],n)
        if ep or en:
            print(f"--- {item['file']} P={ep} N={en}\nR: {item['text']}\nP: {p}\nN: {n}")
asyncio.run(main())
