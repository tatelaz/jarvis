"""Dal testo del cervello al testo da pronunciare: segmentazione in streaming e lettura in italiano."""

import pytest

from jarvis.speech.text import SentenceChunker, number_it, speakable


def stream(chunker: SentenceChunker, pieces: list[str]) -> list[str]:
    out = []
    for p in pieces:
        out += chunker.feed(p)
    return out + chunker.flush()


def letters(text: str) -> list[str]:
    return list(text)  # streaming carattere per carattere: il caso più severo


def test_first_chunk_leaves_early_at_a_clause():
    out = stream(SentenceChunker(), letters("Ho controllato il meteo, domani piove su Milano per tutto il giorno "
                                            "e anche sabato. Domenica torna il sole."))
    assert out[0] == "Ho controllato il meteo,"  # parte alla prima pausa naturale, dopo almeno tre parole
    assert " ".join(out[1:]) == "domani piove su Milano per tutto il giorno e anche sabato. Domenica torna il sole."


def test_two_word_clause_is_not_enough_to_start():
    out = stream(SentenceChunker(), letters("Certamente, signore, ho trovato tre voli per domani mattina. Bene."))
    assert out[0] == "Certamente, signore, ho trovato tre voli per domani mattina."


def test_short_first_sentence_waits_unless_its_audio_is_ready():
    text = "Subito. Diciannove per ventitré fa trecentonovantuno."
    assert stream(SentenceChunker(), letters(text)) == [text]  # "Subito." da solo sarebbe troppo corto
    ready = {"Subito."}
    out = stream(SentenceChunker(instant=lambda s: s in ready), letters(text))
    assert out == ["Subito.", "Diciannove per ventitré fa trecentonovantuno."]  # suona subito, dalla cache


def test_instant_applies_only_to_the_first_chunk():
    ready = {"Fatto."}
    out = stream(SentenceChunker(instant=lambda s: s in ready),
                 letters("Ho finito il lavoro richiesto stamattina. Fatto. Altro?"))
    assert "Fatto." not in out  # a metà risposta le frasi corte restano unite per la prosodia


def test_numbers_and_abbreviations_do_not_cut():
    out = stream(SentenceChunker(), letters("La temperatura è di 3.5 gradi, sig. Rossi, e domani sale ancora un po'. "
                                            "Poi piove."))
    assert not any(o.endswith("3.") or o.endswith("sig.") for o in out)


def test_code_blocks_are_never_spoken():
    out = stream(SentenceChunker(), ["Ecco lo script richiesto, signore. ", "```py", "thon\nprint(1)\n", "```",
                                     " L'ho anche salvato sul desktop."])
    assert all("print" not in o for o in out)
    assert any("salvato" in o for o in out)


def test_long_text_without_punctuation_is_split():
    out = stream(SentenceChunker(max_chars=80), letters("parola " * 60))
    assert len(out) > 1 and all(len(o) <= 81 for o in out)


@pytest.mark.parametrize("raw, spoken", [
    ("alle 20:20", "alle venti e venti"),
    ("alle 8:30", "alle otto e mezza"),
    ("4.892", "quattromilaottocentonovantadue"),
    ("3,5", "tre virgola cinque"),
    ("17 per 23 fa 391", "diciassette per ventitré fa trecentonovantuno"),
    ("il 5/10", "il cinque ottobre"),  # era letto "cinquecentodieci": "/10" scambiato per un percorso
    ("a 50 km/h", "a cinquanta chilometri orari"),
    ("il 12/03/2026", "il dodici marzo duemilaventisei"),
    ("apri C:\\Users\\Andrea\\Desktop\\note.txt", "apri note.txt"),
    ("il file /home/andrea/a.py", "il file a.py"),
    ("30%", "trenta per cento"),
    ("1 km", "un chilometro"),
    ("**importante**", "importante"),
    ("vai su https://www.example.com/pagina", "vai su example punto com"),
    ("la GPU", "la gi pi u"),
])
def test_speakable(raw, spoken):
    assert speakable(raw) == spoken


def test_speakable_drops_unreadable_content():
    assert speakable("```\ncodice\n```") == ""
    assert "🚀" not in speakable("Partiamo 🚀")
    assert speakable("- primo\n- secondo") == "primo. secondo"


def test_number_it_forms():
    assert number_it("101") == "centuno" and number_it("108") == "centotto"
    assert number_it("-2") == "meno due"
    assert number_it("abc") == "abc"


def test_after_the_first_piece_long_sentences_are_split_at_commas():
    out = stream(SentenceChunker(), letters("Subito, signore mio caro. Ho controllato il meteo per tutta la settimana, "
                                            "domani piove su Milano per tutto il giorno, e anche sabato."))
    assert out[1:] == ["Ho controllato il meteo per tutta la settimana,", "domani piove su Milano per tutto il giorno,",
                       "e anche sabato."]


def test_short_clauses_are_not_split_off():
    out = stream(SentenceChunker(), letters("Ho controllato tutto quanto stamattina. Sì, certo, va bene così, signore."))
    assert out[-1] == "Sì, certo, va bene così, signore."  # pezzi sotto le 4 parole restano uniti


def test_e2e_long_pause_meter():
    """Il misuratore dei buchi della prova completa: conta solo i silenzi a metà risposta."""
    import importlib.util
    from pathlib import Path

    import numpy as np

    spec = importlib.util.spec_from_file_location("e2e", Path(__file__).resolve().parents[1] / "scripts" / "e2e.py")
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    voice, gap = np.full(16000, 0.1, np.float32), np.zeros(16000, np.float32)
    audio = np.concatenate([gap, voice, np.zeros(1600 * 3, np.float32), voice, gap[:8000], voice,
                            np.zeros(16000 * 5, np.float32), voice, gap])
    pauses = e2e.long_pauses(audio)
    assert pauses == []  # 0,3 s e 0,5 s sono pause naturali; 5 s è lo spazio tra due risposte
    audio2 = np.concatenate([voice, np.zeros(int(16000 * 1.2), np.float32), voice])
    assert e2e.long_pauses(audio2) == [1.2]


def test_no_stump_after_a_short_opening_clause():
    """Dal vivo: "A sua disposizione," e poi "signore." da solo, con un'intonazione spezzata."""
    out = stream(SentenceChunker(), letters("A sua disposizione, signore. Ho preparato tutto quello che serviva."))
    assert out[0] == "A sua disposizione, signore."
    out = stream(SentenceChunker(), letters("Ho controllato il meteo, domani piove su Milano per tutto il giorno."))
    assert out[0] == "Ho controllato il meteo,"  # qui dopo la virgola c'è una frase vera: si parte subito
