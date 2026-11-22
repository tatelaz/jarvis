"""La pagina del primo avvio in un Chrome vero (senza finestra), con il server vero e un PC inventato.

Si percorre tutto come farebbe chi installa: nelle due lingue, con Claude da installare e da collegare, con un PC
che non va bene, con un download che si ferma. A ogni passo si controlla che niente esca dalla finestra o finisca
sotto l'anello, sia alla misura normale sia a quella minima della finestra di Jarvis.
"""

import asyncio
import contextlib
import threading

import numpy as np
import pytest
from test_setup_audio import FakeSD, find
from test_setup_flow import (
    KEY,
    RTX,
    FakeAnthropic,
    FakeClaude,
    FakeDownloader,
    facts,
    fake_keys,
    free_port,
    give_voice,
)

from jarvis.config import ConfigStore
from jarvis.setup import catalog, flow, pc
from jarvis.setup.audio_check import AudioCheck
from jarvis.setup.download import DownloadError
from jarvis.setup.server import SetupServer

pw = pytest.importorskip("playwright.async_api")

SIZES = [(1060, 720), (420, 560)]  # quella con cui si apre e la minima (jarvis/ui/window.py)

FITS = """() => {
  const step = document.querySelector("[data-step].on");
  const ring = document.querySelector(".halo");
  const halo = ring.getBoundingClientRect(), haloShown = Number(getComputedStyle(ring).opacity) > 0.05;
  const bottom = innerHeight - 48, top = 40;  // sotto la fascia in alto, sopra i passi
  const bad = [];
  for (const el of step.querySelectorAll("*")) {
    if (!el.offsetParent || el.closest("[hidden]")) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const name = (el.className || el.tagName) + " " + (el.textContent || "").trim().slice(0, 30);
    if (r.left < -1 || r.right > innerWidth + 1 || r.top < top - 1 || r.bottom > bottom + 1) bad.push("fuori: " + name);
    else if (haloShown && step.dataset.step !== "hello" && r.top < halo.bottom - 2 && r.bottom > halo.top + 2 &&
             r.left < halo.right - 2 && r.right > halo.left + 2) bad.push("sotto l'anello: " + name);
    if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).textOverflow !== "ellipsis" &&
        el.tagName !== "INPUT") bad.push("testo tagliato: " + name);
  }
  return bad;
}"""


class Harness:
    def __init__(self, tmp_path, **kw) -> None:
        self.store = ConfigStore(tmp_path / "config.yaml")
        self.claude = kw.pop("claude", FakeClaude())
        self.setup = flow.Setup(self.store, FakeDownloader(tmp_path), gather=kw.pop("gather", lambda: facts(RTX)),
                                claude=self.claude, voices_dir=tmp_path / "voices", done_path=tmp_path / "fatto.json",
                                bundled_voices=kw.pop("bundled", tmp_path / "incluse"),  # senza: si vede l'avviso
                                audio=AudioCheck(sd=FakeSD(), find=find), missing_tools=lambda: list(catalog.TOOLS),
                                keys=fake_keys(tmp_path / "claude"), check_key=FakeAnthropic())
        self.server = SetupServer(self.setup, port=free_port())
        self.url = f"http://127.0.0.1:{self.server.port}/"
        self.errors: list[str] = []


@contextlib.asynccontextmanager
async def opened(tmp_path, locale="it-IT", size=SIZES[0], **kw):
    h = Harness(tmp_path, **kw)
    await h.server.start()
    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:
            await h.server.stop()
            pytest.skip(f"Chrome non disponibile: {e}")
        ctx = await browser.new_context(locale=locale, viewport={"width": size[0], "height": size[1]},
                                        reduced_motion="reduce")
        page = await ctx.new_page()
        page.on("pageerror", lambda e: h.errors.append(str(e)))
        page.on("console", lambda m: h.errors.append(m.text) if m.type == "error" else None)
        await page.goto(h.url)
        await page.wait_for_selector('[data-step="hello"].on')
        try:
            yield h, page
        finally:
            await browser.close()
            await h.server.stop()


async def on(page, step: str) -> None:
    await page.wait_for_selector(f'[data-step="{step}"].on')
    await page.wait_for_timeout(80)  # le transizioni (ridotte al minimo) finiscono


async def check_fits(page) -> None:
    for w, hgt in SIZES:
        await page.set_viewport_size({"width": w, "height": hgt})
        await page.wait_for_timeout(60)
        assert await page.evaluate(FITS) == [], f"a {w}x{hgt}"
    await page.set_viewport_size({"width": SIZES[0][0], "height": SIZES[0][1]})


async def text(page, selector: str) -> str:
    return ((await page.locator(selector).text_content()) or "").strip()  # il testo, non le maiuscole dello stile


async def test_the_whole_setup_in_italian(tmp_path):
    async with opened(tmp_path) as (h, page):
        assert await text(page, '[data-step="hello"] h1') == "Ciao. Sono Jarvis."
        await check_fits(page)
        await page.click('[data-step="hello"] [data-next]')

        await on(page, "pc")
        await page.wait_for_selector('#rows .row[data-row="net"].ok')
        assert "RTX 4060 · 8 GB" in await text(page, '[data-row="gpu"] .v')
        assert await text(page, "#profile b") == "Profilo completo"
        assert await page.is_enabled('[data-step="pc"] [data-next]')
        await check_fits(page)
        await page.click('[data-step="pc"] [data-next]')

        await on(page, "brain")
        await page.wait_for_function("document.querySelector('#claude-action').textContent === 'Accedi con Claude'")
        assert await page.get_attribute('[data-choice="claude"]', "aria-checked") == "true"
        await check_fits(page)
        await page.click("#claude-action")
        await page.wait_for_function("document.querySelector('#claude-action').dataset.state === 'wait'")
        assert h.claude.calls == ["C:/fake/claude.exe auth login --claudeai"]
        h.claude.logged_in = True  # l'utente ha finito l'accesso nel browser
        await page.wait_for_function("document.querySelector('#claude-action').textContent === 'Collegato · Pro'",
                                     timeout=8000)
        await page.click('[data-step="brain"] [data-next]')

        await on(page, "dl")
        await page.wait_for_function("document.querySelector('#total').textContent === 'Tutto scaricato'")
        assert await page.locator("#dls .dl.done").count() == len(flow.GROUPS)
        assert await page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--progress')") == "1"
        await check_fits(page)
        await page.click('[data-step="dl"] [data-next]')

        await on(page, "test")
        await check_fits(page)
        await page.click('[data-step="test"] [data-next]')

        await on(page, "done")
        await check_fits(page)
        await page.fill("#name", "Andrea")
        await page.click("#go")
        await page.wait_for_selector("#done-error:not([hidden])")
        assert "voce di base" in await text(page, "#done-error")  # niente voce: Jarvis non partirebbe
        give_voice(h.setup)
        await page.click("#go")
        await on(page, "bye")
        assert await text(page, '[data-step="bye"] h1') == "A tra poco."
        assert h.setup.finished.is_set()
        c = h.store.config
        assert (c.persona.user_name, c.brain.provider, c.system.autostart, c.persona.language) == \
               ("Andrea", "claude", True, "it")
        assert h.errors == []


async def test_english_from_windows_and_switching_language(tmp_path):
    async with opened(tmp_path, locale="en-GB") as (h, page):
        assert await text(page, '[data-step="hello"] h1') == "Hello. I'm Jarvis."
        await page.wait_for_function("document.documentElement.lang === 'en'")
        for _ in range(50):
            if h.store.config.persona.language == "en":
                break
            await asyncio.sleep(0.02)
        assert h.store.config.persona.language == "en"
        await check_fits(page)
        await page.click('[data-step="hello"] [data-next]')
        await on(page, "pc")
        await page.wait_for_selector('#rows .row[data-row="net"].ok')
        assert await text(page, "#profile b") == "Full profile"
        await check_fits(page)
        await page.click("#lang")
        assert await text(page, "#profile b") == "Profilo completo"  # anche i testi già calcolati cambiano
        await page.click("#lang")
        await page.click('[data-step="pc"] [data-next]')
        await on(page, "brain")
        await page.wait_for_function("document.querySelector('#claude-action').textContent === 'Sign in with Claude'")
        await check_fits(page)
        await page.click('[data-choice="opencode"]')
        assert "free models" in await text(page, "#brain-hint")
        await page.click('[data-step="brain"] [data-next]')
        await on(page, "dl")
        await page.wait_for_function("document.querySelector('#total').textContent === 'All downloaded'")
        await check_fits(page)
        await page.click('[data-step="dl"] [data-next]')
        await on(page, "test")
        await page.wait_for_function("document.querySelector('#mic-select').options.length === 2")
        assert await text(page, "#mic-select option:first-child") == "Windows default"
        await check_fits(page)
        await page.click('[data-step="test"] [data-next]')
        await on(page, "done")
        await check_fits(page)
        assert h.store.config.brain.provider == "opencode"
        assert h.errors == []


async def test_claude_code_to_install_first(tmp_path):
    claude = FakeClaude(installed=False)
    async with opened(tmp_path, claude=claude) as (h, page):
        await page.click('[data-step="hello"] [data-next]')
        await page.wait_for_selector('#rows .row[data-row="net"].ok')
        await page.click('[data-step="pc"] [data-next]')
        await on(page, "brain")
        await page.wait_for_function("document.querySelector('#claude-action').textContent === 'Installa Claude Code'")
        await page.click("#claude-action")
        assert claude.calls == ["powershell -NoProfile -Command irm https://claude.ai/install.ps1 | iex"]
        assert await text(page, "#claude-action") == "Installazione nella finestra aperta…"
        claude.installed = True
        await page.wait_for_function("document.querySelector('#claude-action').textContent === 'Accedi con Claude'",
                                     timeout=8000)
        assert "Puoi accedere anche dopo" in await text(page, "#brain-hint")
        assert h.errors == []


async def test_a_pc_without_nvidia_cannot_go_on(tmp_path):
    async with opened(tmp_path, gather=lambda: facts(pc.Gpu("AMD Radeon RX 7800", "amd", 16384, "31.0"), mics=0)) \
            as (h, page):
        await page.click('[data-step="hello"] [data-next]')
        await page.wait_for_selector('#rows .row[data-row="gpu"].bad')
        assert await page.is_disabled('[data-step="pc"] [data-next]')
        assert await text(page, "#profile b") == "Così non posso continuare"
        notes = await text(page, "#profile .notes")
        assert "scheda video NVIDIA" in notes and "AMD e Intel" in notes and "microfono" in notes
        assert "molto lenta" not in notes  # detto già dal blocco
        assert await page.is_visible("#pc-again")
        await check_fits(page)
        h.setup.gather = lambda: facts(RTX)  # ha montato la scheda giusta
        await page.click("#pc-again")
        await page.wait_for_function("!document.querySelector('[data-step=\"pc\"] [data-next]').disabled")
        assert h.errors == []


async def test_a_stopped_download_can_be_retried(tmp_path):
    async with opened(tmp_path) as (h, page):
        h.setup.downloader.fail = DownloadError("voce: interrotto")
        await page.click('[data-step="hello"] [data-next]')
        await page.wait_for_selector('#rows .row[data-row="net"].ok')
        await page.click('[data-step="pc"] [data-next]')
        await on(page, "brain")
        await page.click('[data-step="brain"] [data-next]')
        await on(page, "dl")
        await page.wait_for_selector("#dl-error:not([hidden])")
        assert "Riprova: riparto da dove ero arrivato." in await text(page, "#dl-error")
        assert await page.is_disabled('[data-step="dl"] [data-next]')
        await check_fits(page)
        h.setup.downloader.fail = None
        await page.click("#dl-again")
        await page.wait_for_function("!document.querySelector('[data-step=\"dl\"] [data-next]').disabled")
        assert await page.is_hidden("#dl-error")
        assert h.errors == []


async def test_progress_and_time_left_while_downloading(tmp_path):
    async with opened(tmp_path) as (h, page):
        h.setup.downloader.gate.clear()
        await page.click('[data-step="hello"] [data-next]')
        await page.wait_for_selector('#rows .row[data-row="net"].ok')
        await page.click('[data-step="pc"] [data-next]')
        await on(page, "brain")
        await page.click('[data-step="brain"] [data-next]')
        await on(page, "dl")
        await page.wait_for_function("document.querySelector('#total').textContent.includes('rimanenti')")
        assert await page.locator("#dls .dl").count() == len(flow.GROUPS)
        assert await page.is_disabled('[data-step="dl"] [data-next]')
        await check_fits(page)
        h.setup.downloader.gate.set()
        await page.wait_for_function("document.querySelector('#total').textContent === 'Tutto scaricato'")
        assert h.errors == []


async def test_closing_asks_the_program_to_stop(tmp_path):
    async with opened(tmp_path) as (h, page):
        await page.click("#close")
        for _ in range(50):
            if h.setup.quit.is_set():
                break
            await asyncio.sleep(0.02)
        assert h.setup.quit.is_set()


async def test_checks_start_on_the_welcome_screen(tmp_path):
    """Nella finestra vera, a freddo, il controllo del PC e lo stato di Claude hanno impiegato 8 s ciascuno: partono
    già mentre si legge il benvenuto, così al passo sono pronti."""
    calls = []

    def gather():
        calls.append(1)
        return facts(RTX)

    async with opened(tmp_path, gather=gather) as (h, page):
        for _ in range(150):
            if calls and h.setup.profile is not None:
                break
            await asyncio.sleep(0.02)
        assert len(calls) == 1  # senza toccare niente
        await page.click('[data-step="hello"] [data-next]')
        await page.wait_for_selector('#rows .row[data-row="net"].ok')
        await page.click('[data-step="pc"] [data-next]')
        await on(page, "brain")
        await page.wait_for_function("document.querySelector('#claude-action').textContent === 'Accedi con Claude'")
        assert len(calls) == 1  # il controllo già fatto, non un secondo
        assert h.errors == []


async def test_claude_status_is_awaited_visibly(tmp_path):
    release = threading.Event()
    claude = FakeClaude()
    slow = claude.run

    def waiting(cmd, **kw):
        release.wait(10)
        return slow(cmd, **kw)

    claude.run = waiting
    async with opened(tmp_path, claude=claude) as (h, page):
        await page.click('[data-step="hello"] [data-next]')
        await page.wait_for_selector('#rows .row[data-row="net"].ok')
        await page.click('[data-step="pc"] [data-next]')
        await on(page, "brain")
        assert await text(page, "#claude-action") == "Controllo l'accesso…"
        assert await page.is_disabled("#claude-action")
        release.set()
        await page.wait_for_function("document.querySelector('#claude-action').textContent === 'Accedi con Claude'",
                                     timeout=8000)
        assert await page.is_enabled("#claude-action")
        assert h.errors == []


async def go_to_test(page) -> None:
    await page.click('[data-step="hello"] [data-next]')
    await page.wait_for_selector('#rows .row[data-row="net"].ok')
    await page.click('[data-step="pc"] [data-next]')
    await on(page, "brain")
    await page.click('[data-step="brain"] [data-next]')
    await on(page, "dl")
    await page.wait_for_function("!document.querySelector('[data-step=\"dl\"] [data-next]').disabled")
    await page.click('[data-step="dl"] [data-next]')
    await on(page, "test")


async def test_microphone_and_speakers(tmp_path):
    from jarvis.speech.engine import BUNDLED_VOICES

    async with opened(tmp_path, bundled=BUNDLED_VOICES) as (h, page):
        await go_to_test(page)
        sd = h.setup.audio.sd
        for _ in range(100):
            if sd.streams:
                break
            await asyncio.sleep(0.02)
        assert sd.streams and sd.streams[-1].kw["device"] == 0  # il microfono predefinito, aperto entrando
        options = await page.locator("#out-select option").all_text_contents()
        assert options == ["Predefinito di Windows", "Altoparlanti (Realtek)", "Cuffie (Bluetooth)"]
        for _ in range(40):
            sd.streams[-1].feed(np.full(480, 0.1, np.float32))  # qualcuno parla
        await page.wait_for_selector("#mic-meter.ok")
        assert await text(page, "#heard") == "Ti sento"
        await page.click("#play")
        await page.wait_for_function("document.querySelector('#test-line').textContent.length > 10")
        assert sd.played and sd.played[-1][2] == 1  # le casse predefinite
        await page.select_option("#out-select", "Cuffie (Bluetooth)")
        for _ in range(100):
            if h.store.config.audio.output_device == "Cuffie (Bluetooth)":
                break
            await asyncio.sleep(0.02)
        assert h.store.config.audio.output_device == "Cuffie (Bluetooth)"
        assert h.store.config.audio.input_device is None
        await page.click("#play")
        for _ in range(100):
            if sd.played[-1][2] == 2:
                break
            await asyncio.sleep(0.02)
        assert sd.played[-1][2] == 2  # ora sulle cuffie
        await check_fits(page)
        await page.click('[data-step="test"] [data-next]')
        await on(page, "done")
        assert sd.streams[-1].closed  # il microfono si chiude uscendo dalla prova
        assert h.errors == []


async def test_a_busy_microphone_is_explained(tmp_path):
    async with opened(tmp_path) as (h, page):
        h.setup.audio.sd.busy = True
        await go_to_test(page)
        await page.wait_for_selector("#test-error:not([hidden])")
        assert "microfono" in await text(page, "#test-error")
        await page.click('[data-step="test"] [data-next]')  # si può andare avanti lo stesso
        await on(page, "done")
        assert h.errors == []


@pytest.mark.parametrize("size", SIZES)
async def test_claude_with_an_api_key(tmp_path, size):
    async with opened(tmp_path, size=size) as (h, page):
        await page.click('[data-step="hello"] [data-next]')
        await on(page, "pc")
        await page.wait_for_function("!document.querySelector('[data-step=\"pc\"] [data-next]').disabled")
        await page.click('[data-step="pc"] [data-next]')
        await on(page, "brain")
        await page.click('[data-choice="key"]')
        assert await page.get_attribute('[data-choice="key"]', "aria-checked") == "true"
        assert await page.evaluate(FITS) == [], f"a {size}"
        assert await page.get_attribute("#claude-key", "type") == "password"
        await page.click('[data-step="brain"] [data-next]')  # vuota, e nessuna chiave salvata
        await page.wait_for_function("document.querySelector('#brain-hint').textContent.includes('Scrivi la chiave')")
        await page.fill("#claude-key", "una password qualsiasi")
        await page.press("#claude-key", "Enter")  # Invio nel campo vale come Avanti
        await page.wait_for_function("document.querySelector('#brain-hint').textContent.includes('sk-ant-')")
        h.setup.check_key.verdict = "rifiutata"
        await page.fill("#claude-key", KEY)
        await page.click('[data-step="brain"] [data-next]')
        await page.wait_for_function("document.querySelector('#brain-hint').textContent.includes('non accetta')")
        assert await page.locator('[data-step="brain"].on').count() == 1 and not h.setup.keys.stored()
        h.setup.check_key.verdict = "ok"
        await page.click('[data-step="brain"] [data-next]')
        await on(page, "dl")
        assert h.setup.keys.get() == KEY and h.store.config.brain.claude_access == "chiave"
        assert await page.input_value("#claude-key") == ""  # la chiave non resta nella pagina
        assert KEY not in await page.content()
        await page.click('[data-step="dl"] [data-prev]')
        await on(page, "brain")
        assert await page.get_attribute("#claude-key", "placeholder") == "chiave già salvata: lasciala vuota per tenerla"
        await page.click('[data-step="brain"] [data-next]')  # vuota: si tiene quella salvata
        await on(page, "dl")
        assert [e for e in h.errors if "409 (Conflict)" not in e] == []  # i rifiuti attesi, nient'altro
