"""La finestra vera in un browser vero (il Chrome installato, senza finestra), con la vetrina al posto di Jarvis.

Si controlla quello che si vede e si usa: entrambe le grafiche in ogni stato senza errori, il cambio di grafica a
voce senza ricaricare, i sottotitoli che seguono la voce, i comandi che arrivano a Jarvis, le animazioni fluide.
"""

import asyncio
import contextlib
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
SCENES = ["attesa", "ascolto", "ragiono", "parla", "compiti", "conferma", "riposo"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Showcase:
    """La vetrina in un processo a parte, come il Jarvis vero: il test la vede solo dal browser e dalla rete."""

    def __init__(self, theme: str, scene: str) -> None:
        self.theme, self.scene = theme, scene
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.start()

    def start(self) -> None:
        """Avvia (o riavvia, sulla stessa porta) e aspetta che risponda."""
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "vetrina.py"), "--porta", str(self.port),
                                      "--tema", self.theme, "--scena", self.scene],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.url + "/", timeout=1):
                    return
            except OSError:
                time.sleep(0.1)
        self.close()
        raise RuntimeError("la vetrina non è partita")

    @property
    def commands(self) -> list[dict]:
        with urllib.request.urlopen(self.url + "/comandi", timeout=3) as r:
            return json.loads(r.read())

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait(10)


@contextlib.asynccontextmanager
async def window(theme="mark3", scene="attesa"):
    v = Showcase(theme, scene)
    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # senza Chrome questi test non hanno senso
            v.close()
            pytest.skip(f"Chrome non disponibile: {e}")
        page = await browser.new_page(viewport={"width": 1060, "height": 720})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await page.goto(v.url + "/")
        await page.wait_for_function(f"document.body.dataset.theme === '{theme}' && window.JarvisCore.J.connected")
        try:
            yield page, v, errors
        finally:
            await browser.close()
            v.close()


async def cmd(page, **kw):
    await page.evaluate("(c) => JarvisCore.api.send(c)", kw)


async def lit_pixels(page) -> int:
    """Quanti punti del disegno non sono neri: una scena vuota o rotta ne ha zero."""
    return await page.evaluate("""() => {
      const c = document.querySelector('#stage canvas'); const x = c.getContext('2d');
      const d = x.getImageData(0, 0, c.width, c.height).data; let n = 0;
      for (let i = 0; i < d.length; i += 16) if (d[i] + d[i + 1] + d[i + 2] > 60) n++;
      return n; }""")


@pytest.mark.parametrize("theme", ["mark3", "cinema"])
async def test_every_state_draws_without_errors(theme):
    async with window(theme) as (page, _v, errors):
        for scene in SCENES:
            await cmd(page, cmd="vetrina", scena=scene)
            await asyncio.sleep(0.25)
            await page.evaluate("JarvisCore.advance(1.2)")
            assert await lit_pixels(page) > 20, f"{theme}/{scene}: la scena è vuota"
        assert errors == []
        assert await page.locator("#stage canvas").count() == 1


async def test_theme_changes_live_with_a_crossfade():
    async with window("mark3") as (page, _v, errors):
        await page.evaluate("JarvisCore.advance(1)")
        await cmd(page, cmd="vetrina", tema="cinema")  # come quando Jarvis cambia l'impostazione a voce
        await page.wait_for_function("document.getElementById('stage').classList.contains('leaving')", timeout=2000)
        await page.wait_for_function("document.body.dataset.theme === 'cinema'", timeout=3000)
        assert await page.locator("#stage canvas").count() == 1  # la scena vecchia non resta sotto
        assert await page.locator(".ci-sub").count() == 1 and await page.locator(".m3-say").count() == 0
        await cmd(page, cmd="vetrina", tema="mark3")
        await page.wait_for_function("document.body.dataset.theme === 'mark3'", timeout=3000)
        assert errors == []


@pytest.mark.parametrize("theme", ["mark3", "cinema"])
async def test_subtitles_follow_the_voice_not_the_text(theme):
    async with window(theme, "parla") as (page, _v, errors):
        await page.wait_for_function("JarvisCore.J.reply.length > 50")
        first = await page.evaluate("JarvisCore.derive.spoken()")
        await asyncio.sleep(1.2)
        await page.evaluate("JarvisCore.advance(0.1)")
        later = await page.evaluate("JarvisCore.derive.spoken()")
        total = await page.evaluate("JarvisCore.J.reply.length")
        assert 0 <= first < later < total  # il testo è già tutto arrivato, ma si illumina al ritmo della voce
        if theme == "mark3":  # il detto pieno, il resto attenuato
            unspoken = await page.locator(".m3-say .un").inner_text()
            assert 0 < len(unspoken) < total
        else:  # parola per parola: quelle ancora da dire aspettano sfocate
            assert await page.locator(".ci-sub .w:not(.wait)").count() > 0
            assert await page.locator(".ci-sub .w.wait").count() > 0
        assert errors == []


async def test_commands_reach_jarvis():
    async with window("mark3", "conferma") as (page, v, errors):
        await page.evaluate("JarvisCore.advance(1)")
        await page.locator(".m3-ask .yes").click()
        await page.keyboard.type("ciao jarvis")  # scrivi per parlare: il campo compare da solo
        await page.keyboard.press("Enter")
        await page.locator("#c-rest").click()
        await asyncio.sleep(0.3)
        got = [c for c in v.commands if c.get("cmd") != "vetrina"]
        assert {"cmd": "confirm", "id": 2, "approved": True} in got
        assert {"cmd": "text", "text": "ciao jarvis"} in got
        assert {"cmd": "rest"} in got
        assert errors == []


async def test_what_you_write_while_jarvis_restarts_is_not_lost():
    """Jarvis si riavvia (a voce, o il guardiano lo rialza): quello che scrivi nel frattempo gli arriva lo stesso."""
    async with window("mark3") as (page, v, errors):
        v.close()
        await page.wait_for_function("!JarvisCore.J.connected", timeout=5000)
        assert await page.locator("#offline").is_visible()
        await cmd(page, cmd="text", text="sei ancora lì?")
        await cmd(page, cmd="stop")  # uno "stop" vecchio invece non ha senso dopo: non va recapitato
        v.start()
        await page.wait_for_function("JarvisCore.J.connected", timeout=10000)
        await asyncio.sleep(0.3)
        got = v.commands
        assert {"cmd": "text", "text": "sei ancora lì?"} in got
        assert {"cmd": "stop"} not in got
        assert await page.locator("#offline").is_hidden()
        # mentre Jarvis era giù Chrome segnala i tentativi di ricollegamento falliti: sono attesi, nient'altro
        assert [e for e in errors if "WebSocket connection" not in e] == []


async def test_missing_audio_is_shown_and_clears_by_itself():
    """Senza audio Jarvis non può dirlo: lo dice la finestra, e l'avviso sparisce da solo quando l'audio torna."""
    async with window("cinema") as (page, _v, errors):
        assert await page.locator("#noaudio").is_hidden()
        await cmd(page, cmd="vetrina", audio=False)
        await page.wait_for_function("!document.getElementById('noaudio').hidden", timeout=3000)
        await page.reload()  # chi apre la finestra dopo lo vede lo stesso
        await page.wait_for_function("JarvisCore.J.connected")
        assert await page.locator("#noaudio").is_visible()
        await cmd(page, cmd="vetrina", audio=True)
        await page.wait_for_function("document.getElementById('noaudio').hidden", timeout=3000)
        log = await page.evaluate("JarvisCore.J.history.map((h) => h.text).join(' | ')")
        assert "audio: Headphones (vetrina) · Microphone (vetrina)" in log  # nel registro resta dove si è spostato
        assert errors == []


@pytest.mark.parametrize("theme", ["mark3", "cinema"])
async def test_animation_is_light_enough_to_stay_fluid(theme):
    async with window(theme, "parla") as (page, _v, errors):
        await page.evaluate("JarvisCore.advance(1)")  # riscaldamento
        ms = await page.evaluate("() => { const t = performance.now(); JarvisCore.advance(2); return performance.now() - t; }")
        assert ms / 120 < 16, f"{theme}: {ms / 120:.1f} ms a fotogramma, troppi per 60 al secondo"
        assert errors == []


@pytest.mark.parametrize("theme", ["mark3", "cinema"])
async def test_window_can_be_moved_from_the_top_strip(theme):
    """26/10: la finestra è senza bordi e non c'era nessun punto da cui trascinarla (restava dove si apriva)."""
    import webview

    selector = webview.settings["DRAG_REGION_SELECTOR"]  # quello che pywebview usa davvero
    async with window(theme) as (page, _v, errors):
        await page.evaluate("JarvisCore.advance(1)")
        on_top = await page.evaluate("""(sel) => {
            const at = (x, y) => document.elementFromPoint(x, y);
            const ctl = document.getElementById('c-rest').getBoundingClientRect();
            return {left: at(10, 8).matches(sel), middle: at(innerWidth / 2, 20).matches(sel),
                    right: at(innerWidth - 10, 8).matches(sel),
                    controls: at(ctl.x + ctl.width / 2, ctl.y + ctl.height / 2).id};
        }""", selector)
        assert on_top == {"left": True, "middle": True, "right": True, "controls": "c-rest"}
        await page.locator("#c-log").click()  # il registro aperto resta cliccabile anche lì in alto
        await page.wait_for_timeout(500)
        box = await page.locator("#drawer-close").bounding_box()
        hit = await page.evaluate("([x, y]) => document.elementFromPoint(x, y).id",
                                  [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        assert hit == "drawer-close"
        assert errors == []


def test_page_files_are_signed_so_no_stale_copy_is_used():
    v = Showcase("mark3", "attesa")
    try:
        with urllib.request.urlopen(v.url + "/", timeout=3) as r:
            html = r.read().decode("utf-8")
            assert r.headers["Cache-Control"] == "no-store"
        assert "/static/core.js?v=" in html and "/static/themes/cinema.js?v=" in html
    finally:
        v.close()


async def test_voice_and_security_section():
    """La parola d'ordine si imposta dalla finestra: il campo non perde i tasti per la chat e si svuota subito."""
    async with window("mark3") as (page, v, errors):
        await page.locator("#c-log").click()
        await page.locator("#security summary").click()
        assert "riconoscimento acceso" in await page.locator("#sec-status").inner_text()
        await page.locator("#pass-input").click()
        await page.keyboard.type("aquila reale")  # prima ogni tasto spostava il cursore nella chat di Jarvis
        assert await page.evaluate("document.activeElement.id") == "pass-input"
        assert await page.evaluate("!document.getElementById('composer').classList.contains('on')")
        await page.keyboard.press("Enter")
        await page.wait_for_function("document.getElementById('sec-msg').textContent.includes('salvata')")
        assert await page.locator("#pass-input").input_value() == ""
        assert {"cmd": "passphrase", "value": "aquila reale"} in v.commands
        assert not any(c.get("cmd") == "text" for c in v.commands)  # niente finito alla chat
        await cmd(page, cmd="vetrina", utente={"text": "posso mettere la musica?", "speaker": "Marco", "owner": False})
        await page.wait_for_function("document.getElementById('log').textContent.includes('posso mettere la musica')")
        assert "marco" in (await page.locator("#log .entry.guest .who").last.inner_text()).lower()
        assert errors == []
