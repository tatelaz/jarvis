"""Il filtro delle frasi di servizio in inglese (jarvis/core/narration.py)."""

import pytest

from jarvis.core.narration import NarrationFilter


def run(pieces):
    """pieces: testo, oppure None = parte uno strumento. -> ciò che si pronuncia."""
    f, out = NarrationFilter(), ""
    for p in pieces:
        if p is None:
            f.tool()
        else:
            out += f.feed(p)
    return out + f.flush()


@pytest.mark.parametrize("pieces,said", [
    (["Let me check the status.", None, "Direi di sì."], "Direi di sì."),
    (["I'll ", "look that up", None, "Ecco."], "Ecco."),
    (["Now let me read them.", None, "Hai due messaggi."], "Hai due messaggi."),
    (["Subito, ", "signore."], "Subito, signore."),
    (["I", "l collegamento è attivo."], "Il collegamento è attivo."),
    (["Le ", "ho aperto WhatsApp."], "Le ho aperto WhatsApp."),
    (["Let me know if you need anything."], "Let me know if you need anything."),  # inglese chiesto: niente strumento
    (["Controllo.", None, "Let me see.", None, "Fatto."], "Controllo.Fatto."),  # dopo ogni strumento si ricontrolla
])
def test_narration(pieces, said):
    assert run(pieces) == said


def test_italian_passes_at_once():
    f = NarrationFilter()
    assert f.feed("Certamente, ") == "Certamente, " and f.feed("Let me") == "Let me"  # a metà frase non si tocca
