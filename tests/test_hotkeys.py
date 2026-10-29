import pytest

from jarvis.ui.hotkeys import MOD_NOREPEAT, parse


def test_parse_combinations():
    assert parse("ctrl+alt+j") == (0x2 | 0x1 | MOD_NOREPEAT, ord("J"))
    assert parse("Ctrl + Shift + F5") == (0x2 | 0x4 | MOD_NOREPEAT, 0x74)
    assert parse("win+space")[1] == 0x20


@pytest.mark.parametrize("bad", ["", "ctrl+", "hyper+j", "ctrl+alt+invio"])
def test_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse(bad)
