"""Guardare lo schermo (jarvis/media/screen.py, strumento guarda_schermo): cosa si cattura, quanto grande, chi può.

Nessuno screenshot vero qui: cattura, finestra in primo piano e monitor sono finti. Quello vero ha il marcatore desktop.
"""

import io
import sys

import pytest
from PIL import Image as PILImage

from jarvis.brain.tools import ToolOutput
from jarvis.config import ConfigStore
from jarvis.core.bus import Bus
from jarvis.identity.guard import BASE, OWNER_ONLY, Identity, Voice
from jarvis.media import screen
from jarvis.memory.store import Memory
from jarvis.tools.front import TOOL_GROUPS, Services, build_front_tools

LEFT = ((0, 0, 1920, 1080), 1)  # il principale
RIGHT = ((1920, 0, 4480, 1440), 0)  # 2560x1440, a destra


def picture(w, h, color=(10, 120, 200)):
    return PILImage.new("RGB", (w, h), color)


class FakeScreen:
    def __init__(self, fg=((2000, 100, 3000, 900), "Errore di compilazione - Visual Studio Code")):
        self.fg = fg
        self.grabbed = []

    def grab(self, bbox):
        self.grabbed.append(bbox)
        if bbox is None:
            return picture(4480, 1440)
        return picture(bbox[2] - bbox[0], bbox[3] - bbox[1])

    def capture(self, where="schermo", max_px=1568):
        return screen.capture(where, max_px, grab=self.grab, window=lambda: self.fg,
                              monitor=lambda r: screen.pick_monitor(r, [LEFT, RIGHT]))


def test_the_monitor_is_the_one_with_the_front_window():
    assert screen.pick_monitor((2000, 100, 3000, 900), [LEFT, RIGHT]) == ((1920, 0, 4480, 1440), 2, 2)
    assert screen.pick_monitor((1800, 0, 2000, 500), [LEFT, RIGHT])[1] == 1  # a cavallo: dove ce n'è di più
    assert screen.pick_monitor(None, [RIGHT, LEFT]) == ((0, 0, 1920, 1080), 2, 2)  # nessuna finestra: il principale
    assert screen.pick_monitor((9000, 9000, 9100, 9100), [LEFT, RIGHT])[1] == 1  # fuori da tutti: il principale


def test_the_picture_is_a_small_jpeg_never_enlarged():
    data, size = screen.encode(picture(3840, 2160), 1568)
    assert size == (1568, 882) and data[:2] == b"\xff\xd8"
    assert PILImage.open(io.BytesIO(data)).size == (1568, 882)
    assert screen.encode(picture(800, 600), 1568)[1] == (800, 600)


@pytest.mark.parametrize("where,bbox,what", [
    ("schermo", (1920, 0, 4480, 1440), "dello schermo 2 di 2"),
    ("finestra", (2000, 100, 3000, 900), "della finestra «Errore di compilazione - Visual Studio Code»"),
    ("tutti", None, "di tutti gli schermi"),
    ("boh", (1920, 0, 4480, 1440), "dello schermo 2 di 2"),  # parola sconosciuta: lo schermo
])
def test_what_is_captured(where, bbox, what):
    fake = FakeScreen()
    shot = fake.capture(where)
    assert fake.grabbed == [bbox] and shot.what == what
    assert shot.front == "«Errore di compilazione - Visual Studio Code»"
    assert max(shot.size) <= 1568


def test_taskbar_and_desktop_are_not_a_window():
    assert not screen.is_real_window("Shell_TrayWnd") and not screen.is_real_window("Progman")
    assert not screen.is_real_window("WorkerW") and screen.is_real_window("Chrome_WidgetWin_1")


def test_without_a_front_window():
    fake = FakeScreen(fg=None)
    assert fake.capture("finestra").what == "dello schermo 1 di 2 (nessuna finestra in primo piano)"
    assert fake.grabbed == [(0, 0, 1920, 1080)] and fake.capture().front == ""


def test_size_limits_are_kept_even_if_the_file_says_otherwise():
    assert max(FakeScreen().capture("schermo", 99999).size) == 2560  # il massimo (lo schermo è largo 2560)
    assert max(FakeScreen().capture("schermo", 10).size) == 512  # il minimo


@pytest.fixture
def tools(tmp_path):
    fake = FakeScreen()
    store = ConfigStore(tmp_path / "config.yaml")
    mem = Memory(tmp_path / "m.db")

    def make(screenshot=fake.capture):
        return build_front_tools(Services(Bus(), store, mem, screenshot=screenshot))

    yield make(), store, fake, make
    mem.close()


async def test_the_brain_gets_the_picture_and_a_line_of_text(tools):
    reg, _store, fake, _make = tools
    out = await reg.get("guarda_schermo").run({"dove": "finestra"})
    assert isinstance(out, ToolOutput) and len(out.images) == 1 and out.images[0].mime == "image/jpeg"
    assert out.text.startswith("Screenshot della finestra «Errore di compilazione - Visual Studio Code» alle ")
    assert "In primo piano: «Errore di compilazione - Visual Studio Code»" in out.text
    blind = out.as_text()  # OpenCode, Ollama: niente immagine, e lo si dice
    assert "Visual Studio Code" in blind and "non vede le immagini" in blind and "parlato" not in blind
    assert fake.grabbed == [(2000, 100, 3000, 900)]


async def test_it_can_be_switched_off_and_resized_by_voice(tools):
    reg, store, fake, _make = tools
    await reg.call("impostazione", {"chiave": "vedere_schermo", "valore": "no"})
    assert store.config.media.screen is False
    out = await reg.call("guarda_schermo", {})
    assert out.startswith("Errore: guardare lo schermo è spento") and fake.grabbed == []
    await reg.call("impostazione", {"chiave": "vedere_schermo", "valore": "sì"})
    await reg.call("impostazione", {"chiave": "risoluzione_schermo", "valore": "1024"})
    assert store.config.media.screen_px == 1024
    assert "fuori misura" in await reg.call("impostazione", {"chiave": "risoluzione_schermo", "valore": "9000"})
    out = await reg.get("guarda_schermo").run({})
    assert "ridotto a 1024x576" in out.text


async def test_a_failed_capture_is_said(tools):
    *_, make = tools

    def broken(_where, _px):
        raise OSError("schermo bloccato")  # per esempio Windows bloccato o la sessione remota chiusa

    out = await make(broken).call("guarda_schermo", {})
    assert out == "Errore: non riesco a fare lo screenshot (OSError: schermo bloccato)"


def test_only_the_owner_can_look_at_the_screen():
    assert TOOL_GROUPS["guarda_schermo"] == "schermo" and "schermo" in OWNER_ONLY  # mai concedibile a un'altra voce
    guest = Voice(None)
    assert "guardare lo schermo" in Identity.refusal("schermo", guest) and "schermo" not in guest.groups()
    assert BASE in guest.groups() and Identity.refusal("schermo", Voice(None, via="tastiera")) is None


@pytest.mark.desktop
@pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")
def test_a_real_screenshot_has_the_right_shape():
    """Su questo PC, per davvero: solo forma e misure (l'immagine non si guarda né si salva)."""
    for where in ("schermo", "finestra", "tutti"):
        shot = screen.capture(where, 1568)
        assert shot.jpeg[:2] == b"\xff\xd8" and 0 < max(shot.size) <= 1568 and min(shot.original) > 100
