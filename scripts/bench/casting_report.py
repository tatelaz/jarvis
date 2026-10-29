import asyncio, json, sys, soundfile as sf, soxr
sys.path.insert(0,"."); sys.path.insert(0,"scripts/bench")
from jarvis.speech.stt import transcribe_file
from asr_bench import wer
folder = sys.argv[1]
m = json.load(open(f"{folder}/metrics.json", encoding="utf-8"))
man = json.load(open(f"{folder}/manifest.json", encoding="utf-8"))
texts = {it["file"]: it.get("text", man.get("text")) for it in man["items"]}
async def main():
    for r in m:
        a, sr = sf.read(f"{folder}/"+r["file"], dtype="float32")
        h = (await transcribe_file("127.0.0.1",8790,"pk-tiny",soxr.resample(a, sr, 16000)))["text"]
        e, n = wer(texts.get(r["file"], man.get("text")), h); r["wer"] = round(100*e/n,1); r["hyp"] = h
asyncio.run(main())
json.dump(m, open(f"{folder}/metrics.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
m.sort(key=lambda r: (-(r["sisdr"] or 0)))
print(f"{'file':30s} {'SISDR':>5} {'PESQ':>4} {'F0':>5} {'p10':>5} {'sprd':>5} {'dur':>5} {'WER':>5}")
for r in m:
    print(f"{r['file']:30s} {r['sisdr']:5.1f} {r['pesq']:4.2f} {r['f0_med']:5.1f} {r['f0_p10']:5.1f} {r['f0_spread']:5.1f} {r['dur_s']:5.2f} {r['wer']:5.1f}")
