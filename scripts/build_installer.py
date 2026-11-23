"""Costruisce l'installatore di Jarvis (build/Jarvis-Setup-<versione>.exe) con Inno Setup.

Mette insieme in build/installer: il codice (solo i file del repository, con `git archive`: niente dati, modelli o
cartelle private), uv e le immagini della procedura in stile Monolite, disegnate da Chrome dall'icona. Poi compila
installer/jarvis.iss. Serve Inno Setup 6 (ISCC.exe) e, per le immagini, il gruppo dev (Playwright e Chrome).
Uso: python scripts/build_installer.py [--iscc percorso\\ISCC.exe]
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
STAGING = BUILD / "installer"
# niente da lasciare fuori: la copia installata diventa un repository git (jarvis/localrepo.py) e deve coincidere con il
# commit, o risulterebbe sempre "modificata" (fino al 23/11 restava fuori design/, 191 KB di prove di grafica)
LEAVE_OUT: tuple[str, ...] = ()
BUNDLE = "jarvis.bundle"  # la storia del progetto fino a questa versione: nella cartella di Jarvis e nella release
ISCC_PLACES = (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
               Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"), Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"))
# misure delle immagini della procedura (stile modern di Inno Setup) a 100, 125 e 150 %
WIZARD = {"100": (164, 314), "125": (205, 393), "150": (246, 471)}
SMALL = {"100": (55, 55), "125": (69, 69), "150": (83, 83)}


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def code_files(dest: Path) -> int:
    """I file del repository all'ultimo commit (git archive), tranne quelli che non servono a chi installa."""
    data = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for m in tar.getmembers():
            if not m.isfile() or (LEAVE_OUT and m.name.startswith(LEAVE_OUT)):
                continue
            out = dest / m.name
            if not out.resolve().is_relative_to(dest.resolve()):  # un archivio di git non lo fa, ma non si sa mai
                raise RuntimeError(f"percorso non ammesso nell'archivio: {m.name}")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tar.extractfile(m).read())
            n += 1
    return n


def history(dest: Path) -> Path:
    """La storia git fino all'ultimo commit in un file solo (git bundle, ~3,5 MB): la copia installata ne fa il suo
    repository, e gli aggiornamenti arrivano allo stesso modo, così si uniscono alle modifiche fatte da Jarvis."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "bundle", "create", str(dest), "HEAD"], cwd=ROOT, capture_output=True, check=True)
    subprocess.run(["git", "bundle", "verify", str(dest)], cwd=ROOT, capture_output=True, check=True)
    return dest


def find_uv() -> Path:
    found = os.environ.get("JARVIS_UV") or shutil.which("uv")
    if not found:
        raise SystemExit("uv non trovato: installalo o indica JARVIS_UV")
    return Path(found)


def images(dest: Path) -> None:
    """Le immagini della procedura: nero, l'icona e la scritta, come la Monolite."""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    icon = base64.b64encode((ROOT / "jarvis" / "ui" / "web" / "icona.svg").read_bytes()).decode()

    def page_html(w: int, h: int, big: bool) -> str:
        side = round(w * (0.62 if big else 0.78))
        text = (f'<div style="margin-top:{round(h * 0.06)}px;font:600 {max(9, round(w * 0.065))}px Bahnschrift,'
                f'Segoe UI,sans-serif;letter-spacing:.5em;color:#f3f2ee;padding-left:.5em">JARVIS</div>') if big else ""
        return (f'<html><body style="margin:0;width:{w}px;height:{h}px;background:#000;display:flex;'
                f'flex-direction:column;align-items:center;justify-content:center">'
                f'<img src="data:image/svg+xml;base64,{icon}" width="{side}" height="{side}">{text}</body></html>')

    dest.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(device_scale_factor=1)
        for group, big, prefix in ((WIZARD, True, "wizard"), (SMALL, False, "small")):
            for scale, (w, h) in group.items():
                page.set_viewport_size({"width": w, "height": h})
                page.set_content(page_html(w, h, big))
                page.wait_for_function("document.images[0].complete")
                png = page.screenshot(clip={"x": 0, "y": 0, "width": w, "height": h})
                Image.open(io.BytesIO(png)).convert("RGB").save(dest / f"{prefix}-{scale}.bmp")
        browser.close()


def find_iscc(given: str | None) -> Path:
    for p in ([Path(given)] if given else []) + list(ISCC_PLACES):
        if p.is_file():
            return p
    raise SystemExit("Inno Setup 6 non trovato (ISCC.exe): installalo o indica --iscc")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iscc")
    a = ap.parse_args(argv)
    iscc = find_iscc(a.iscc)
    if STAGING.exists():
        shutil.rmtree(STAGING)
    n = code_files(STAGING / "app")
    bundle = history(STAGING / "app" / BUNDLE)
    (STAGING / "tools").mkdir(parents=True)
    shutil.copy2(find_uv(), STAGING / "tools" / "uv.exe")
    images(STAGING / "images")
    ver = version()
    subprocess.run([str(iscc), f"/DAppVersion={ver}", f"/DStaging={STAGING}", f"/DOutputDir={BUILD}",
                    str(ROOT / "installer" / "jarvis.iss")], check=True)
    out = BUILD / f"Jarvis-Setup-{ver}.exe"
    shutil.copy2(bundle, BUILD / f"Jarvis-{ver}.bundle")  # il secondo file della release: gli aggiornamenti via git
    print(f"{out} ({out.stat().st_size // 2**20} MB, {n} file del codice)")
    print(f"{BUILD / f'Jarvis-{ver}.bundle'} ({bundle.stat().st_size // 2**10} KB di storia git)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
