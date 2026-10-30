"""L'icona di Windows: il formato .ico scritto a mano e il file prodotto dalle sorgenti SVG."""

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "tools"))

import make_icon


def png(side: int, height: int | None = None) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (side, height or side), (143, 216, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_pack_and_read_back_in_size_order():
    data = make_icon.pack_ico([png(48), png(16), png(256)])
    back = make_icon.read_ico(data)
    assert [side for side, _ in back] == [16, 48, 256]
    assert all(make_icon.png_size(p) == (side, side) for side, p in back)
    # il formato è quello che legge anche Pillow (come Windows)
    assert Image.open(io.BytesIO(data)).info["sizes"] == {(16, 16), (48, 48), (256, 256)}


def test_256_is_written_as_zero_in_the_directory():
    data = make_icon.pack_ico([png(256)])
    assert data[6] == 0 and data[7] == 0  # larghezza e altezza: un byte, 256 si scrive 0


def test_offsets_point_at_each_image():
    images = [png(16), png(32)]
    data = make_icon.pack_ico(images)
    assert [p for _, p in make_icon.read_ico(data)] == images


@pytest.mark.parametrize("bad", [png(32, 16), png(300)])
def test_refuses_sizes_windows_cannot_use(bad):
    with pytest.raises(ValueError, match="misura non valida"):
        make_icon.pack_ico([bad])


def test_refuses_what_is_not_a_png():
    with pytest.raises(ValueError, match="non è un PNG"):
        make_icon.pack_ico([b"GIF89a" + bytes(30)])
    with pytest.raises(ValueError, match=r"non è un file \.ico"):
        make_icon.read_ico(b"\x00\x00\x02\x00\x01\x00")


def test_the_shipped_icon_has_every_size_windows_asks_for():
    back = make_icon.read_ico(make_icon.ICO.read_bytes())
    assert tuple(side for side, _ in back) == make_icon.SIZES
    for side, p in back:
        im = Image.open(io.BytesIO(p))
        assert im.size == (side, side) and im.mode == "RGBA"
        assert im.getpixel((0, 0))[3] == 0  # angoli trasparenti: il quadrato è arrotondato
        assert im.getpixel((side // 2, side // 2))[3] == 255


def test_sources_are_in_the_window_folder():
    for name in ("icona.svg", "icona-piccola.svg"):
        assert (make_icon.WEB / name).read_text(encoding="utf-8").lstrip().startswith("<svg")
