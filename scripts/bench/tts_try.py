import asyncio, time, soundfile as sf, sys, json, subprocess
sys.path.insert(0, ".")
from jarvis.speech.tts import TtsClient, Voice
c = TtsClient("127.0.0.1", 8790)
T = "Buonasera, signore. Ho completato l'analisi: tre file risultano danneggiati, ma ho già provveduto a ripristinarli dal backup di ieri sera."
def mem():
    out = subprocess.run(["powershell","-NoProfile","-Command","(Get-Process audiocpp_server).PrivateMemorySize64/1MB"],capture_output=True,text=True).stdout.strip()
    v = subprocess.run(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader"],capture_output=True,text=True).stdout.strip()
    return f"RAM srv {out[:6]} MB, VRAM {v}"
async def run(name, model, voice, n=2, **kw):
    for i in range(n):
        t = time.perf_counter()
        try:
            a, sr = await c.synthesize(model, kw.pop("text", T), voice, **kw)
        except Exception as e:
            print(name, "ERRORE", str(e)[:300]); return
        dt = time.perf_counter()-t
        print(f"{name:22s} run{i} {dt*1000:6.0f} ms  audio {len(a)/sr:5.2f}s  RTF {dt/(len(a)/sr):.2f}  sr {sr}  | {mem()}", flush=True)
    sf.write(f"data/bench/tts/{name}.wav", a, sr)
async def main():
    for spec in sys.argv[1:]:
        name, model, vjson = spec.split("|")
        await run(name, model, Voice(**json.loads(vjson)))
asyncio.run(main())
