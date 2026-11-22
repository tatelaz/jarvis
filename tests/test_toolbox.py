"""Gli strumenti scaricati dal primo avvio (OpenCode, Node.js, Git): quando mancano, come si rendono visibili."""

import os
from pathlib import Path

import pytest

from jarvis import paths, toolbox
from jarvis.brain import opencode
from jarvis.setup import catalog


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ENGINE_DIR", tmp_path / "engine")
    return tmp_path / "engine"


def put(engine: Path, key: str) -> Path:
    """Lo strumento come lo lascia il primo avvio: l'eseguibile del catalogo al suo posto."""
    exe = toolbox.check_file(key)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    return exe


def test_each_tool_lives_in_the_engine_tools_folder(engine):
    assert set(toolbox.COMMAND) == set(catalog.TOOLS)
    for key in catalog.TOOLS:
        exe = toolbox.check_file(key)
        assert exe.is_relative_to(engine / "tools") and exe.stem == toolbox.COMMAND[key]
    assert toolbox.portable_bash() == engine / "tools" / "git" / "bin" / "bash.exe"


def test_a_pc_without_anything_needs_all_three(engine):
    assert toolbox.missing_tools(which=lambda name: None, opencode_found=lambda: False) == list(catalog.TOOLS)


def test_tools_on_the_pc_or_already_downloaded_are_not_downloaded_again(engine):
    on_pc = {"git": r"C:\Program Files\Git\cmd\git.exe"}
    assert toolbox.missing_tools(which=on_pc.get, opencode_found=lambda: True) == ["tool-node"]
    put(engine, "tool-node")
    assert toolbox.missing_tools(which=on_pc.get, opencode_found=lambda: True) == []
    put(engine, "tool-opencode")  # scaricato: non si chiede neanche dove sia OpenCode
    assert toolbox.missing_tools(which=on_pc.get, opencode_found=lambda: 1 / 0) == []


def test_downloaded_tools_go_to_the_end_of_the_path_once(engine):
    env = {"PATH": r"C:\Windows;C:\Tools"}
    assert toolbox.use_downloaded_tools(env) == []  # niente scaricato: niente cambia
    assert env == {"PATH": r"C:\Windows;C:\Tools"}
    node = put(engine, "tool-node")
    oc = put(engine, "tool-opencode")
    assert toolbox.use_downloaded_tools(env) == [oc.parent, node.parent]  # nell'ordine del catalogo
    assert env["PATH"].split(os.pathsep) == [r"C:\Windows", r"C:\Tools", str(oc.parent), str(node.parent)]
    assert toolbox.use_downloaded_tools(env) == []  # la seconda volta non si raddoppia
    assert env["PATH"].count(str(node.parent)) == 1
    assert toolbox.GIT_BASH_VAR not in env


def test_claude_code_gets_the_portable_bash_only_when_it_is_the_only_git(engine, tmp_path):
    git = put(engine, "tool-git")
    toolbox.portable_bash().parent.mkdir(parents=True)
    toolbox.portable_bash().write_bytes(b"MZ")
    env = {"PATH": ""}
    toolbox.use_downloaded_tools(env)
    assert env["PATH"] == str(git.parent)
    assert env[toolbox.GIT_BASH_VAR] == str(toolbox.portable_bash())

    system = tmp_path / "Git" / "cmd"
    system.mkdir(parents=True)
    (system / "git.exe").write_bytes(b"MZ")  # un Git installato sul PC viene prima: la bash la trova da solo
    env = {"PATH": str(system)}
    toolbox.use_downloaded_tools(env)
    assert toolbox.GIT_BASH_VAR not in env

    env = {"PATH": "", toolbox.GIT_BASH_VAR: r"D:\mia\bash.exe"}  # una scelta di chi usa il PC resta
    toolbox.use_downloaded_tools(env)
    assert env[toolbox.GIT_BASH_VAR] == r"D:\mia\bash.exe"


def test_without_bash_nothing_is_promised(engine):
    put(engine, "tool-git")  # git.exe sì, bash no (cartella rovinata)
    env = {"PATH": ""}
    toolbox.use_downloaded_tools(env)
    assert toolbox.GIT_BASH_VAR not in env


def test_opencode_downloaded_by_the_first_launch_is_found(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "casa")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(opencode.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        opencode.find_opencode()
    exe = put(engine, "tool-opencode")
    assert opencode.find_opencode() == str(exe)
