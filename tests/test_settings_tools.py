"""Jarvis cambia da solo ogni sua impostazione (richiesta di Andrea: "ogni cosa la può modificare anche Jarvis")."""

import contextlib
import json
import sys
import uuid

import pytest

from jarvis import autostart, health
from jarvis.config import Config, ConfigError, ConfigStore, is_live, settings_catalog
from jarvis.core.bus import Bus
from jarvis.memory.store import Memory
from jarvis.supervisor import Policy, Supervisor
from jarvis.tools.front import Services, build_front_tools


@pytest.fixture
def tools(tmp_path):
    applied = []

    def fake_autostart(on):
        applied.append(on)
        return "avvio automatico attivato" if on else "avvio automatico disattivato"

    store = ConfigStore(tmp_path / "config.yaml")
    s = Services(Bus(), store, Memory(tmp_path / "m.db"), None, None, restart=lambda: "riavvio in arrivo",
                 appliers={"system.autostart": fake_autostart})
    return build_front_tools(s), store, applied


async def test_every_setting_is_listed_with_value_and_meaning(tools):
    reg, _, _ = tools
    text = await reg.call("impostazioni", {})
    for row in settings_catalog(Config()):
        assert row["chiave"] in text and row["descrizione"]  # nessuna impostazione senza spiegazione
    assert "system.autostart = False" in text and "[vale dal riavvio]" in text
    only = await reg.call("impostazioni", {"filtro": "orecchie"})
    assert "speech.ears_device" in only and "brain.provider" not in only


async def test_any_key_can_be_changed_with_honest_timing(tools, tmp_path):
    reg, store, _ = tools
    out = await reg.call("impostazione", {"chiave": "speech.tts_speed", "valore": "0,9"})
    assert store.get("speech.tts_speed") == 0.9 and "vale subito" in out
    out = await reg.call("impostazione", {"chiave": "speech.ears_device", "valore": "cpu"})
    assert store.get("speech.ears_device") == "cpu" and "dal prossimo riavvio" in out
    out = await reg.call("impostazione", {"chiave": "audio.input_device", "valore": "nessuno"})
    assert store.get("audio.input_device") is None
    await reg.call("impostazione", {"chiave": "ui.wake_word", "valore": "no"})
    assert store.get("ui.wake_word") is False
    assert ConfigStore(tmp_path / "config.yaml").get("speech.ears_device") == "cpu"  # salvata davvero


async def test_echo_reference_can_be_said_in_plain_words(tools):
    reg, store, _ = tools
    out = await reg.call("impostazione", {"chiave": "audio.echo_reference", "valore": "solo la tua voce"})
    assert store.get("audio.echo_reference") == "jarvis" and "vale subito" in out
    await reg.call("impostazione", {"chiave": "audio.echo_reference", "valore": "Tutto ciò che suona"})
    assert store.get("audio.echo_reference") == "system"
    out = await reg.call("impostazione", {"chiave": "audio.echo_reference", "valore": "a volte"})
    assert out.startswith("Errore") and store.get("audio.echo_reference") == "system"


async def test_whatsapp_settings_in_plain_words(tools):
    reg, store, _ = tools
    assert store.get("apps.whatsapp") == "su richiesta" and store.get("apps.whatsapp_avvisi") is False
    out = await reg.call("impostazione", {"chiave": "whatsapp", "valore": "sempre acceso"})
    assert store.get("apps.whatsapp") == "sempre" and "vale subito" in out
    await reg.call("impostazione", {"chiave": "apps.whatsapp", "valore": "Quando serve"})
    assert store.get("apps.whatsapp") == "su richiesta"
    await reg.call("impostazione", {"chiave": "avvisi_whatsapp", "valore": "sì"})
    assert store.get("apps.whatsapp_avvisi") is True
    out = await reg.call("impostazione", {"chiave": "whatsapp", "valore": "a metà"})
    assert out.startswith("Errore") and "su richiesta, sempre o spento" in out
    assert store.get("apps.whatsapp") == "su richiesta"


async def test_shortcuts_still_work(tools):
    reg, store, _ = tools
    await reg.call("impostazione", {"chiave": "cervello", "valore": "locale"})
    assert store.get("brain.provider") == "local"
    await reg.call("impostazione", {"chiave": "risposta_senza_nome", "valore": "sì"})
    assert store.get("ui.wake_word") is False


async def test_opencode_settings_by_voice(tools):
    """OpenCode (modelli gratis quando Claude è esaurito): tutto cambiabile a voce, e vale subito."""
    reg, store, _ = tools
    for said in ("opencode", "Zen", "open code"):
        out = await reg.call("impostazione", {"chiave": "cervello", "valore": said})
        assert store.get("brain.provider") == "opencode" and "vale subito" in out
    out = await reg.call("impostazione", {"chiave": "opencode", "valore": "no"})
    assert store.get("brain.opencode") is False and "vale subito" in out
    await reg.call("impostazione", {"chiave": "privacy_modelli_gratis", "valore": "Solo privati"})
    assert store.get("brain.opencode_privacy") == "senza addestramento"
    await reg.call("impostazione", {"chiave": "brain.opencode_privacy", "valore": "tutti i modelli"})
    assert store.get("brain.opencode_privacy") == "tutti"
    out = await reg.call("impostazione", {"chiave": "privacy_modelli_gratis", "valore": "a volte"})
    assert out.startswith("Errore") and "senza addestramento" in out and store.get("brain.opencode_privacy") == "tutti"
    await reg.call("impostazione", {"chiave": "modello_gratis", "valore": "GLM 5.3 flash free"})
    assert store.get("brain.opencode_model") == "glm-5.3-flash-free"
    await reg.call("impostazione", {"chiave": "modello_gratis", "valore": "Automatico"})
    assert store.get("brain.opencode_model") == "auto"
    await reg.call("impostazione", {"chiave": "brain.opencode_worker_model", "valore": "opencode/big-pickle"})
    assert store.get("brain.opencode_worker_model") == "big-pickle"
    out = await reg.call("impostazione", {"chiave": "compiti_su_opencode", "valore": "no"})
    assert store.get("brain.opencode_workers") is False and "vale subito" in out
    out = await reg.call("impostazione", {"chiave": "brain.opencode_exam_hours", "valore": "24"})
    assert store.get("brain.opencode_exam_hours") == 24.0 and "vale subito" in out
    listed = await reg.call("impostazioni", {"filtro": "opencode"})
    for key in ("brain.opencode ", "brain.opencode_model", "brain.opencode_worker_model", "brain.opencode_privacy",
                "brain.opencode_exam_hours", "brain.opencode_workers"):
        assert key in listed
    assert "senza addestramento" in listed and "[vale dal riavvio]" not in listed
    await reg.call("impostazione", {"chiave": "memory.keeper_brain", "valore": "opencode"})
    assert store.get("memory.keeper_brain") == "opencode"
    with pytest.raises(ConfigError):  # anche scritta a mano nel file: solo le due scelte
        store.set("brain.opencode_privacy", "a metà")
    with pytest.raises(ConfigError):
        store.set("brain.provider", "chatgpt")


async def test_free_models_tool(tmp_path):
    class Kit:
        def describe(self):
            return "OpenCode acceso, senza chiave. In uso: glm-5.3-flash-free."

        def start_exam(self):
            return "esame dei modelli gratis avviato"

    mem = Memory(tmp_path / "m.db")
    reg = build_front_tools(Services(Bus(), ConfigStore(tmp_path / "c.yaml"), mem, free_models=Kit()))
    assert (await reg.call("modelli_gratis", {})).startswith("OpenCode acceso")
    assert (await reg.call("modelli_gratis", {"azione": "elenco"})).startswith("OpenCode acceso")
    assert "avviato" in await reg.call("modelli_gratis", {"azione": "esamina"})
    assert reg.get("modelli_gratis").group == "sistema"  # solo Andrea
    assert "modelli_gratis" not in build_front_tools(Services(Bus(), ConfigStore(tmp_path / "d.yaml"), mem)).names()
    mem.close()


@pytest.mark.parametrize("key, value, hint", [
    ("speech.ears_devise", "cpu", "sconosciuta"),  # refuso
    ("speech.ears_device", "gpu", "non valido"),  # valore fuori dalle scelte
    ("speech", "x", "sezione"),
    ("speech.tts_speed", "veloce", "Errore"),
])
async def test_bad_requests_are_refused_clearly(tools, key, value, hint):
    reg, store, _ = tools
    out = await reg.call("impostazione", {"chiave": key, "valore": value})
    assert out.startswith("Errore") and hint in out
    assert store.get("speech.ears_device") == "cuda" and store.get("speech.tts_speed") == 1.0  # niente cambiato


async def test_autostart_is_a_setting_with_a_real_effect(tools):
    reg, store, applied = tools
    out = await reg.call("impostazione", {"chiave": "system.autostart", "valore": "sì"})
    assert applied == [True] and store.get("system.autostart") is True and "attivato" in out
    await reg.call("impostazione", {"chiave": "system.autostart", "valore": "no"})
    assert applied == [True, False]


async def test_failed_effect_leaves_the_setting_unchanged(tmp_path):
    def broken(_on):
        raise PermissionError("registro bloccato")

    store = ConfigStore(tmp_path / "config.yaml")
    reg = build_front_tools(Services(Bus(), store, Memory(tmp_path / "m.db"), appliers={"system.autostart": broken}))
    out = await reg.call("impostazione", {"chiave": "system.autostart", "valore": "sì"})
    assert out.startswith("Errore") and store.get("system.autostart") is False


async def test_restart_tool(tools):
    reg, _, _ = tools
    assert await reg.call("riavvia", {}) == "riavvio in arrivo"


def test_live_settings_are_declared_honestly():
    assert is_live("speech.tts_speed") and is_live("hands.desktop")
    assert not is_live("speech.tts_model") and not is_live("persona.address_as")


@pytest.fixture
def keys():
    """Chiavi di prova al posto delle due vere di Windows (Run e StartupApproved): il PC di Andrea non viene toccato.

    Attenzione: la shell di Claude Code può vedere un registro isolato, diverso da quello vero. Queste prove sono
    valide lo stesso (usano solo chiavi proprie), ma lo stato reale del PC va letto da un processo esterno.
    """
    import winreg

    base = rf"Software\JarvisTest\{uuid.uuid4().hex}"
    run, approved = base + r"\Run", base + r"\Approved"
    try:
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, run).Close()
    except PermissionError:  # l'8/10 la sandbox di Codex aveva messo "integrità bassa" sulla cartella di Jarvis
        pytest.skip("scritture nel registro rifiutate: controllare l'etichetta di integrità della cartella di Jarvis "
                    "(vedi docs/STATO.md)")
    yield run, approved
    for key in (run, approved, base):
        with contextlib.suppress(FileNotFoundError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    with contextlib.suppress(OSError):  # anche la chiave madre, se è rimasta vuota: il registro resta com'era
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\JarvisTest")


def _switch_off_in_windows(approved):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, approved) as k:  # come fa Gestione attività
        winreg.SetValueEx(k, autostart.NAME, 0, winreg.REG_BINARY, bytes.fromhex("03000000E3E758940C57DD01"))


def _switch_on_in_windows(approved):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, approved) as k:
        winreg.SetValueEx(k, autostart.NAME, 0, winreg.REG_BINARY, bytes.fromhex("020000000000000000000000"))


def test_autostart_registry_round_trip(keys, tmp_path):
    run, approved = keys
    assert autostart.current(run) is None
    assert "attivato" in autostart.apply(True, run, root=tmp_path, approved_key=approved)
    cmd = autostart.current(run)
    assert cmd.endswith('avvia_jarvis.pyw"') and "pythonw" in cmd
    assert "già attivo" in autostart.apply(True, run, root=tmp_path, approved_key=approved)
    assert autostart.apply(False, run, root=tmp_path, approved_key=approved) == "avvio automatico disattivato"
    assert autostart.current(run) is None


def test_autostart_sees_and_undoes_the_windows_startup_switch(keys, tmp_path):
    """L'8/10 Andrea aveva spento Jarvis dalle App di avvio di Windows e Jarvis si credeva ancora "attivo"."""
    run, approved = keys
    autostart.apply(True, run, root=tmp_path, approved_key=approved)
    assert not autostart.blocked(approved)  # nessun valore: Windows lo considera acceso
    _switch_off_in_windows(approved)
    assert autostart.blocked(approved) and autostart.current(run)  # la voce in Run c'è ancora
    assert "attivato" in autostart.apply(True, run, root=tmp_path, approved_key=approved)
    assert not autostart.blocked(approved)  # chiederlo a Jarvis lo riaccende davvero
    assert "già attivo" in autostart.apply(True, run, root=tmp_path, approved_key=approved)


def test_startup_follows_the_windows_switch_without_removing_jarvis_from_it(keys, tmp_path):
    """Il 26/10 Jarvis, trovato l'interruttore spento, aveva tolto la voce: così spariva dalle App di avvio."""
    run, approved = keys
    autostart.apply(True, run, root=tmp_path, approved_key=approved)
    _switch_off_in_windows(approved)
    assert autostart.reconcile(True, run, root=tmp_path, approved_key=approved) == (
        False, "spento dalle App di avvio di Windows")
    assert autostart.current(run) and autostart.blocked(approved)  # niente di toccato: decide Andrea da Windows
    assert autostart.reconcile(False, run, root=tmp_path, approved_key=approved) == (False, None)  # già allineato
    assert autostart.current(run)
    _switch_on_in_windows(approved)  # Andrea lo riaccende dalle App di avvio
    assert autostart.reconcile(False, run, root=tmp_path, approved_key=approved) == (
        True, "acceso dalle App di avvio di Windows")
    assert autostart.reconcile(True, run, root=tmp_path, approved_key=approved) == (True, None)


def test_autostart_repairs_itself_when_the_folder_moves(keys, tmp_path):
    run, approved = keys
    autostart.apply(True, run, root=tmp_path / "vecchia", approved_key=approved)
    on, fixed = autostart.reconcile(True, run, root=tmp_path / "nuova", approved_key=approved)
    assert on and fixed  # comando sbagliato: corretto
    assert str(tmp_path / "nuova") in autostart.current(run)
    assert autostart.reconcile(True, run, root=tmp_path / "nuova", approved_key=approved) == (True, None)


def test_setting_on_recreates_a_missing_entry_and_its_windows_switch(keys, tmp_path):
    """Acceso nelle impostazioni ma senza voce in Run (lo stato del PC di Andrea il 26/10): si ricrea tutto."""
    run, approved = keys
    _switch_off_in_windows(approved)
    assert autostart.reconcile(False, run, root=tmp_path, approved_key=approved) == (False, None)
    assert autostart.current(run) is None  # spento resta spento
    on, fixed = autostart.reconcile(True, run, root=tmp_path, approved_key=approved)
    assert on and "attivato" in fixed and autostart.current(run) and not autostart.blocked(approved)


def test_requested_restart_is_not_a_crash(tmp_path):
    counter = tmp_path / "giri.txt"
    script = (f"import pathlib, sys\np = pathlib.Path(r'{counter}')\nn = int(p.read_text()) if p.exists() else 0\n"
              f"p.write_text(str(n + 1))\nsys.exit({health.RESTART_CODE} if n == 0 else 0)\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    sup = Supervisor([], root=tmp_path, state_dir=tmp_path / "state", logs_dir=logs,
                     policy=Policy(backoff_s=(30.0,), poll_s=0.05), child_cmd=lambda r: [sys.executable, "-c", script])
    assert sup.run() == 0 and sup.restarts == 1
    assert not (logs / "cadute").exists() and not sup.crashes  # nessun rapporto, nessuna caduta contata
    note = json.loads((tmp_path / "state" / health.RECOVERY).read_text(encoding="utf-8"))
    assert health.recovery_sentence(note, "signore") == "Eccomi di nuovo, signore: riavvio completato."


def test_only_one_jarvis_at_a_time():
    """Con l'avvio automatico, un doppio clic su "Avvia Jarvis" non deve far partire una seconda copia."""
    import subprocess

    from jarvis import single

    name = rf"Local\JarvisTest-{uuid.uuid4().hex}"
    assert single.acquire(name) is True
    assert single.acquire(name) is False  # nello stesso processo
    probe = subprocess.run([sys.executable, "-c", f"from jarvis import single; print(single.acquire({name!r}))"],
                           capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "False"  # e da un altro processo
    free = rf"Local\JarvisTest-{uuid.uuid4().hex}"
    probe = subprocess.run([sys.executable, "-c", f"from jarvis import single; print(single.acquire({free!r}))"],
                           capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "True"
    single.release(name)  # restituito: chi viene dopo (anche nello stesso processo) può prenderlo
    assert single.acquire(name) is True
    single.release(name)


async def test_shutdown_tool_only_when_wired(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    mem = Memory(tmp_path / "m.db")
    reg = build_front_tools(Services(Bus(), store, mem, shutdown=lambda: "spegnimento in arrivo"))
    assert await reg.call("spegniti", {}) == "spegnimento in arrivo"
    bare = build_front_tools(Services(Bus(), store, mem))
    assert "spegniti" not in bare.names() and "riavvia" not in bare.names()
