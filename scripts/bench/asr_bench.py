"""Misura accuratezza (WER) e latenza degli STT sul set italiano FLEURS."""
import asyncio, json, re, sys, time, unicodedata
from pathlib import Path
import numpy as np, soundfile as sf
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.speech.stt import LiveTranscription, transcribe_file

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "models/testdata/fleurs_it"
HOST, PORT = "127.0.0.1", 8790

def norm(t):
    t = unicodedata.normalize("NFC", t.lower())
    t = t.replace("’", "'").replace("'", " ")
    t = re.sub(r"[^\w\s]", " ", t)
    return t.split()

def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = np.zeros((len(r)+1, len(h)+1), dtype=int); d[:,0] = range(len(r)+1); d[0,:] = range(len(h)+1)
    for i in range(1,len(r)+1):
        for j in range(1,len(h)+1):
            d[i,j] = min(d[i-1,j]+1, d[i,j-1]+1, d[i-1,j-1]+(r[i-1]!=h[j-1]))
    return d[len(r),len(h)], len(r)

async def live(audio, chunk_ms=40, realtime=True):
    s = LiveTranscription(HOST, PORT, "nemotron-it", "it-IT")
    await s.start()
    step = 16000*chunk_ms//1000
    t0 = time.perf_counter()
    for i in range(0, len(audio), step):
        await s.send(audio[i:i+step])
        if realtime:
            target = t0 + (i+step)/16000
            await asyncio.sleep(max(0, target - time.perf_counter()))
    text = await s.finish()
    return text, (s.final_at - s.ended_at), (s.first_delta_at - s.opened_at) if s.first_delta_at else None

async def main(n=20, modes=("parakeet","nemotron-off","nemotron-it")):
    meta = json.load(open(DATA/"meta.json", encoding="utf-8"))[:n]
    res = {m: [0,0,[]] for m in modes}
    for k, item in enumerate(meta):
        audio, sr = sf.read(DATA/item["file"], dtype="float32")
        for m in modes:
            t = time.perf_counter()
            if m == "nemotron-it":
                hyp, fin, first = await live(audio, realtime=k < 3)
                lat = fin
            else:
                r = await transcribe_file(HOST, PORT, m, audio, language=None if m=="parakeet" else "it-IT")
                hyp, lat = r["text"], time.perf_counter()-t
            e, nw = wer(item["text"], hyp)
            res[m][0] += e; res[m][1] += nw; res[m][2].append(lat)
            if k < 3: print(f"[{m}] {hyp}  (lat {lat*1000:.0f} ms)")
        if k < 3: print("   RIF:", item["text"])
    for m,(e,nw,lats) in res.items():
        print(f"{m:14s} WER {100*e/nw:5.2f}%  latenza mediana {1000*np.median(lats[1:] or lats):.0f} ms  (p90 {1000*np.percentile(lats,90):.0f})")
if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20,
                     sys.argv[2].split(",") if len(sys.argv) > 2 else ("parakeet", "nemotron-off", "nemotron-it")))
