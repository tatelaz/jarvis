"""Le clip della gara del modello dei suoni (scripts/bench/omni_race.py), da fonti con licenza libera.

Suoni da ESC-50 (solo clip CC0 o CC BY, lette nel file LICENSE del progetto), musica, parlato e video da Wikimedia
Commons; una clip di parlato con il piano sotto è un missaggio fatto qui. Scrive in data/prove/omni_clips le clip
(audio in WAV mono 16 kHz, tagliato; video originali), attese.json e FONTI.md (provenienza e licenza di ognuna).
I file scaricati sono solo dati: si leggono con PyAV, non si esegue niente. Uso: python scripts/bench/omni_clips.py
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis import paths  # noqa: E402
from jarvis.media import watch as W  # noqa: E402

OUT = paths.DATA_DIR / "prove" / "omni_clips"
RAW = paths.DATA_DIR / "prove" / "omni_clips_scaricati"  # gli originali, a parte
UA = {"User-Agent": "JarvisClipPicker/1.0 (uso privato) python-urllib"}
ESC = "https://raw.githubusercontent.com/karolpiczak/ESC-50/master/"

SOUNDS = {"dog": ["dog|bark"], "rain": ["rain"], "siren": ["siren"], "clapping": ["clap|applau"],
          "church_bells": ["bell"], "crying_baby": ["baby|infant|child", "cry"], "thunderstorm": ["thunder"]}
COMMONS = [  # (file, nome della clip, inizio, durata, parole attese); durata None = video originale
    ("Moonlight Sonata (Sharp c minor Sonata) 2nd Movement Beethoven JMC,Han.ogg", "piano.wav", 0, 40, ["piano"]),
    ("Jazz-Sax.ogg", "jazz_sax.wav", 0, 40, ["sax", "jazz|blues"]),
    ("Double tracked distorted electric guitar playing chords.ogg", "chitarra_distorta.wav", 0, 40,
     ["guitar", "distort|rock|electric|heavy"]),
    # "Pipe Organ and choir" secondo la sua pagina: organo e coro (alla gara del 29/10 tutti sentivano il coro)
    ("Hartford City Presbyterian Pipe Organ.ogg", "organo.wav", 0, 40, ["organ|choir"]),
    ("Train passing liminka station.webm", "video_treno.webm", 0, None, ["train"]),
    ("Juggling with swing poi.webm", "video_giocoliere.webm", 0, None, ["juggl|poi|spin|swing"]),
    ("Test Kitchen- Pork Roast with Fresh Applesauce -shorts.webm", "video_cucina.webm", 0, None,
     ["cook|kitchen|food|roast|pork|meat"]),
]
SPEECH = "JFK inaugural address.ogg"  # pubblico dominio: 30 s di parlato con il piano sotto


def get(url: str) -> bytes:
    """Con pazienza: Wikimedia risponde 429 a chi chiede troppo in fretta."""
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                data = r.read()
            time.sleep(3)
            return data
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            print(f"429: aspetto {20 * (attempt + 1)} s")
            time.sleep(20 * (attempt + 1))
    raise RuntimeError("irraggiungibile")


def fetch(url: str, name: str) -> Path:
    dest = RAW / name
    if not (dest.is_file() and dest.stat().st_size):
        dest.write_bytes(get(url))
    return dest


def commons(title: str) -> dict:
    q = urllib.parse.urlencode({"action": "query", "titles": "File:" + title, "prop": "imageinfo", "format": "json",
                                "iiprop": "url|extmetadata", "iiextmetadatafilter": "LicenseShortName|Artist"})
    page = next(iter(json.loads(get("https://commons.wikimedia.org/w/api.php?" + q))["query"]["pages"].values()))
    ii = page["imageinfo"][0]
    md = ii.get("extmetadata") or {}

    def clean(k: str) -> str:
        return re.sub(r"<[^>]+>", "", html.unescape((md.get(k) or {}).get("value", ""))).strip()

    return {"url": ii["url"], "page": ii["descriptionurl"], "license": clean("LicenseShortName"),
            "author": clean("Artist")[:120]}


def save_wav(path: Path, x: np.ndarray) -> None:
    peak = float(np.max(np.abs(x))) or 1.0
    sf.write(str(path), (x * (0.9 / peak)).astype(np.float32), W.SR, subtype="PCM_16")


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    expected: dict[str, list[str]] = {}
    notes = ["# Clip della gara del modello dei suoni: provenienza e licenze", "",
             f"Preparate il {time.strftime('%d/%m/%Y')} da scripts/bench/omni_clips.py, per uso privato di prova. Audio "
             "convertito in WAV mono 16 kHz e tagliato; video originali.", ""]
    lic = get(ESC + "LICENSE").decode()
    meta = list(csv.DictReader(io.StringIO(get(ESC + "meta/esc50.csv").decode())))
    for cat, words in SOUNDS.items():
        for row in (r for r in meta if r["category"] == cat):
            stem = row["filename"].rsplit("-", 1)[0]  # 1-100032-A-0.wav -> 1-100032-A
            m = re.search(r"- \[" + re.escape(stem) + r"\.ogg\]: (.+?) \[(CC0|CC-BY)\]", lic)
            if not m:
                continue  # niente clip non commerciali (NC)
            name = f"esc_{cat}.wav"
            save_wav(OUT / name, W.load_audio(fetch(ESC + "audio/" + row["filename"], row["filename"]), 0.0, 5.0))
            expected[name] = words
            notes.append(f"- **{name}**: ESC-50 ({row['filename']}), {m.group(1)} [{m.group(2)}]. "
                         "https://github.com/karolpiczak/ESC-50")
            break
    for title, name, start, length, words in COMMONS:
        info = commons(title)
        raw = fetch(info["url"], title.replace(" ", "_"))
        if length is None:
            shutil.copyfile(raw, OUT / name)
            how = "originale"
        else:
            save_wav(OUT / name, W.load_audio(raw, float(start), float(start + length)))
            how = f"primi {length} s, mono 16 kHz"
        expected[name] = words
        notes.append(f"- **{name}**: Wikimedia Commons, «{title}» di {info['author'] or '?'} [{info['license']}] "
                     f"({how}). {info['page']}")
    sp, piano = commons(SPEECH), commons(COMMONS[0][0])
    speech = W.load_audio(fetch(sp["url"], SPEECH.replace(" ", "_")), 60.0, 90.0)
    music = W.load_audio(RAW / COMMONS[0][0].replace(" ", "_"), 40.0, 70.0)
    n = min(len(speech), len(music))
    save_wav(OUT / "parlato_con_piano.wav", speech[:n] / (np.max(np.abs(speech[:n])) or 1)
             + 0.3 * music[:n] / (np.max(np.abs(music[:n])) or 1))
    expected["parlato_con_piano.wav"] = ["speech|speak|talk|voice|man|address", "piano|music"]
    notes.append(f"- **parlato_con_piano.wav**: missaggio fatto qui di «{SPEECH}» (1:00-1:30) [{sp['license']}] "
                 f"{sp['page']} e del piano qui sopra (0:40-1:10, al 30 %) [{piano['license']}]; il missaggio segue "
                 "la licenza del piano.")
    (OUT / "attese.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "FONTI.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    print(f"{len(expected)} clip in {OUT}")


if __name__ == "__main__":
    main()
