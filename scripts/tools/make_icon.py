"""Icona di Windows (`jarvis/ui/jarvis.ico`) dalle due sorgenti SVG in `jarvis/ui/web`.

Ogni misura è disegnata da Chrome alla sua grandezza vera (non rimpicciolita da una grande): fino a 32 px dalla
versione piccola, che ha l'anello più spesso e niente alone. Il file `.ico` contiene le immagini in PNG, come
fanno le icone di Windows da Vista in poi.
Uso: python scripts/tools/make_icon.py  (serve Chrome installato e il gruppo dev, per Playwright)
"""

from __future__ import annotations

import base64
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "jarvis" / "ui" / "web"
ICO = ROOT / "jarvis" / "ui" / "jarvis.ico"
SMALL_UP_TO = 32
# le misure che Windows chiede davvero (Esplora risorse, barra delle applicazioni, Start, schermi al 125-200 %)
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(png: bytes) -> tuple[int, int]:
    """Larghezza e altezza scritte nell'intestazione di un PNG."""
    if not png.startswith(PNG_SIGNATURE):
        raise ValueError("non è un PNG")
    return struct.unpack(">II", png[16:24])


def pack_ico(images: list[bytes]) -> bytes:
    """Un file .ico con le immagini PNG date (quadrate, da 1 a 256 px), in ordine di grandezza."""
    sized = []
    for png in images:
        w, h = png_size(png)
        if w != h or not 1 <= w <= 256:
            raise ValueError(f"misura non valida per un'icona: {w}x{h}")
        sized.append((w, png))
    sized.sort(key=lambda s: s[0])
    out = struct.pack("<HHH", 0, 1, len(sized))
    offset = 6 + 16 * len(sized)
    for w, png in sized:
        side = 0 if w == 256 else w  # nel formato ICO 256 si scrive 0: il campo è di un byte
        out += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    return out + b"".join(png for _, png in sized)


def read_ico(data: bytes) -> list[tuple[int, bytes]]:
    """Le immagini di un .ico come (lato in pixel, PNG). Per i test e per controllare il file prodotto."""
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or kind != 1:
        raise ValueError("non è un file .ico")
    out = []
    for i in range(count):
        side, _, _, _, _, _, size, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        out.append((side or 256, data[offset:offset + size]))
    return out


def render(svg: Path, side: int, page) -> bytes:
    src = "data:image/svg+xml;base64," + base64.b64encode(svg.read_bytes()).decode()
    page.set_viewport_size({"width": side, "height": side})
    page.set_content(f'<html><body style="margin:0;background:transparent">'
                     f'<img src="{src}" width="{side}" height="{side}" style="display:block"></body></html>')
    page.wait_for_function("document.images[0].complete")
    return page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": side, "height": side})


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(device_scale_factor=1)
        images = [render(WEB / ("icona-piccola.svg" if s <= SMALL_UP_TO else "icona.svg"), s, page) for s in SIZES]
        browser.close()
    ICO.write_bytes(pack_ico(images))
    print(f"{ICO} ({len(images)} misure, {ICO.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
