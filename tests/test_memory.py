"""La memoria: fatti e conversazioni, ricerca per parole e per significato, dimenticare con prudenza."""

import sqlite3
import time
from typing import ClassVar

import numpy as np
import pytest

from jarvis.memory.embed import Embedder
from jarvis.memory.store import Memory


class ConceptEmbedder:
    """Significato finto e prevedibile: ogni parola nota spinge verso un concetto (cibo, computer, animali)."""

    CONCEPTS: ClassVar[dict[str, int]] = {"pizza": 0, "margherita": 0, "mangiare": 0, "cena": 0, "fame": 0,
                "gpu": 1, "scheda": 1, "video": 1, "computer": 1,
                "gatto": 2, "micio": 2, "animale": 2, "cane": 2}

    def __init__(self):
        self.calls = 0

    def _vec(self, text):
        v = np.full(4, 0.05, np.float32)  # un po' di "rumore di fondo" uguale per tutti
        for w in text.lower().split():
            i = self.CONCEPTS.get(w.strip(".,?!"))
            if i is not None:
                v[i] += 1
        return v

    def queries(self, texts):
        self.calls += 1
        return np.stack([self._vec(t) for t in texts])

    def documents(self, texts):
        self.calls += 1
        return np.stack([self._vec(t) for t in texts])


@pytest.fixture
def mem(tmp_path):
    m = Memory(tmp_path / "m.db")
    yield m
    m.close()


@pytest.fixture
def smem(tmp_path):
    m = Memory(tmp_path / "s.db", embedder=ConceptEmbedder())
    yield m
    m.close()


def test_remember_and_search_ignoring_accents(mem):
    mem.remember("Il colore preferito di Andrea è il blu notte", "preferenze", importance=2)
    mem.remember("Andrea usa Comet come browser predefinito", "pc")
    mem.remember("La sorella di Andrea si chiama Giulia", "persone")
    hits = mem.search("qual è il mio colore preferito?")
    assert hits and "blu notte" in hits[0].text
    assert mem.search("perché e il di") == []  # solo parole vuote: nessun risultato, nessun errore
    assert "Giulia" in mem.search("sorella")[0].text
    assert "Comet" in mem.search("browser")[0].text


def test_near_duplicates_update_instead_of_duplicating(mem):
    fid, new = mem.remember("Andrea preferisce il caffè senza zucchero")
    fid2, new2 = mem.remember("Andrea preferisce il caffè senza zucchero.")
    assert new and not new2 and fid == fid2 and mem.count() == 1
    _, new3 = mem.remember("Andrea preferisce il tè verde la sera")
    assert new3 and mem.count() == 2


@pytest.mark.parametrize("a, b", [
    ("Andrea ha un gatto di nome Briciola", "Andrea ha un gatto di nome Tigre"),  # prima: Tigre cancellava Briciola
    ("Andrea va in palestra il martedì", "Andrea va in palestra il giovedì"),
    ("La sorella di Andrea vive a Bologna", "La sorella di Andrea vive a Bolzano"),
    ("Andrea ha 2 fratelli", "Andrea ha 3 fratelli"),  # le cifre singole venivano ignorate
])
def test_similar_but_different_facts_are_both_kept(mem, a, b):
    mem.remember(a)
    mem.remember(b)
    assert mem.count() == 2


def test_a_fact_with_more_detail_keeps_the_detail(mem):
    fid, _ = mem.remember("Andrea ha un gatto")
    assert mem.remember("Andrea ha un gatto nero di nome Tigre") == (fid, False)
    assert mem.get(fid).text == "Andrea ha un gatto nero di nome Tigre"
    assert mem.remember("Andrea ha un gatto") == (fid, False)  # ridetto in breve: il dettaglio non si perde
    assert mem.get(fid).text == "Andrea ha un gatto nero di nome Tigre" and mem.count() == 1


def test_forget_a_clear_match(mem):
    mem.remember("Il codice del garage è nel cassetto")
    mem.remember("Andrea tifa Inter")
    res = mem.forget("codice del garage")
    assert res.removed == ["Il codice del garage è nel cassetto"] and not res.candidates
    assert mem.count() == 1


def test_forget_asks_which_when_ambiguous(mem):
    """Prima cancellava il primo che capitava: con due gatti, quello sbagliato."""
    mem.remember("Andrea ha un gatto di nome Briciola")
    mem.remember("Andrea ha un gatto di nome Tigre")
    res = mem.forget("il gatto")
    assert not res.removed and {f.text for f in res.candidates} == {"Andrea ha un gatto di nome Briciola",
                                                                    "Andrea ha un gatto di nome Tigre"}
    assert mem.count() == 2
    assert mem.forget("andrea ha un gatto di nome tigre").removed == ["Andrea ha un gatto di nome Tigre"]  # testo esatto
    assert mem.forget("astronomia").removed == [] and mem.count() == 1


def test_update_get_delete(mem):
    fid, _ = mem.remember("Andrea guida una Panda")
    assert mem.update(fid, "Andrea guida una Golf", importance=2)
    f = mem.get(fid)
    assert f.text == "Andrea guida una Golf" and f.importance == 2
    assert mem.search("Golf")[0].id == fid and mem.search("Panda") == []  # l'indice delle parole segue
    assert not mem.update(999, "niente") and not mem.update(fid, "   ")
    assert mem.delete(fid) and not mem.delete(fid) and mem.get(fid) is None


def test_use_counter_only_counts_memories_really_used(mem):
    """Le ricerche interne (es. il controllo dei doppioni) gonfiavano il conteggio che ordina il profilo."""
    fid, _ = mem.remember("Andrea ama la montagna")
    mem.remember("Andrea ama la montagna d'inverno")  # controllo doppioni: ricerca interna
    mem.search("montagna")
    uses = lambda: mem._db.execute("SELECT uses FROM facts WHERE id=?", (fid,)).fetchone()[0]  # noqa: E731
    assert uses() == 0
    mem.search("montagna", touch=True)
    assert uses() == 1


def test_profile_orders_by_importance(mem):
    mem.remember("fatto minore", importance=1)
    mem.remember("fatto fondamentale", importance=3)
    assert mem.profile()[0] == "fatto fondamentale"


def test_turn_log_and_search(mem):
    mem.log_turn("user", "Jarvis, prenota un tavolo per sabato")
    mem.log_turn("jarvis", "Fatto, signore: sabato alle otto da Gino.")
    mem.log_turn("user", "   ")
    assert len(mem.recent_turns()) == 2
    hits = mem.search_turns("Gino")
    assert len(hits) == 1 and "Gino" in hits[0][2]
    assert any("tavolo" in t[2] for t in mem.search_turns("tavolo sabato"))
    assert mem.last_turn_id() == 2 and [t[3] for t in mem.turns_after(1)] == ["Fatto, signore: sabato alle otto da Gino."]


def test_semantic_layer_finds_meaning_without_shared_words(smem):
    smem.remember("Ama la pizza margherita")
    smem.remember("Ha una scheda video RTX 4060")
    hit = smem.search("cosa vuole per cena?")[0]  # nessuna parola in comune con il fatto giusto
    assert "pizza" in hit.text


def test_warm_up_leaves_only_the_question_for_the_first_turn(smem, mem):
    """26/10: modello e frasi di sfondo al primo turno rallentavano l'audio. Dopo warm() la prima ricerca
    calcola solo la domanda (una chiamata), niente frasi di sfondo."""
    smem.remember("Ama la pizza margherita")
    smem.warm()
    before = smem.embedder.calls
    assert "pizza" in smem.search("cosa vuole per cena?")[0].text
    assert smem.embedder.calls - before == 1
    mem.warm()  # senza modello del significato non c'è niente da preparare (e nessun errore)


async def test_jarvis_preloads_the_meaning_model_at_startup(smem):
    import asyncio

    from jarvis.app import Jarvis

    j = Jarvis.__new__(Jarvis)
    j._bg, j.identity, j.memory = set(), None, smem
    j._preload_models()
    await asyncio.gather(*j._bg)
    assert smem._background is not None  # frasi di sfondo già pronte prima della prima frase


def test_unrelated_question_brings_no_memories(smem):
    smem.remember("Ama la pizza margherita")
    smem.remember("Il gatto si chiama Briciola")
    assert smem.search("che ore sono adesso?") == []  # niente che spicchi: niente rumore nel contesto


def test_past_conversations_found_by_meaning(smem):
    smem.log_turn("user", "stasera ho una fame da lupi, ordino una margherita")
    smem.log_turn("jarvis", "Ottima scelta, signore.")
    smem.log_turn("user", "il micio ha rotto un vaso")
    assert smem.embed_pending() == 3 and smem.embed_pending() == 0  # una volta sola
    assert "micio" in smem.search_turns("cosa ha combinato l'animale?")[0][2]


def test_old_database_gets_the_new_column(tmp_path):
    db = tmp_path / "vecchio.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, ts REAL NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, "
                "session TEXT)")
    con.execute("INSERT INTO turns(ts, role, text) VALUES (?, 'user', 'frase di prima')", (time.time(),))
    con.commit()
    con.close()
    m = Memory(db, embedder=ConceptEmbedder())
    assert m.recent_turns()[0][2] == "frase di prima"
    assert m.embed_pending() == 1
    m.close()


def test_deleted_lines_leave_the_search_index(mem):
    mem.log_turn("user", "prova da cancellare")
    mem._db.execute("DELETE FROM turns")
    assert mem._db.execute("SELECT COUNT(*) FROM turns_fts").fetchone()[0] == 0


def test_meta_values(mem):
    assert mem.get_meta("x") is None and mem.get_meta("x", "0") == "0"
    mem.set_meta("x", "1")
    mem.set_meta("x", "2")
    assert mem.get_meta("x") == "2"


def test_empty_fact_rejected(mem):
    with pytest.raises(ValueError):
        mem.remember("   ")


# ------------------------------------------------------------------ il modello vero (EmbeddingGemma 2)

REAL = Embedder()
needs_model = pytest.mark.skipif(not REAL.available, reason="modello EmbeddingGemma 2 non scaricato")
FACTS = ["Giulia è la fidanzata di Andrea", "Andrea preferisce il caffè senza zucchero",
         "La macchina di Andrea è una Fiat Panda grigia", "Andrea lavora a un gioco chiamato FERRO",
         "Il gatto di Andrea si chiama Briciola", "Andrea va in palestra il martedì e il giovedì",
         "Il compleanno di Andrea è il 14 marzo", "Andrea usa cuffie con il microfono integrato",
         "La password del Wi-Fi è scritta sul frigorifero", "Andrea odia le riunioni di mattina presto",
         "Andrea ha una RTX 4060 con 8 GB di memoria video", "La sorella di Andrea si chiama Sara e vive a Bologna",
         "Andrea è allergico alle arachidi", "Andrea tifa per la Fiorentina", "Andrea studia giapponese il sabato"]
QUESTIONS = [("chi è la mia ragazza?", 0), ("come prendo il caffè?", 1), ("che auto ho?", 2),
             ("a che progetto sto lavorando?", 3), ("come si chiama il mio animale?", 4), ("quando mi alleno?", 5),
             ("quando sono nato?", 6), ("che cuffie uso?", 7), ("dove trovo la chiave della rete?", 8),
             ("a che ora non vuoi che mi fissi appuntamenti?", 9), ("che scheda video ho?", 10),
             ("dove abita mia sorella?", 11), ("posso mangiare il burro di noccioline?", 12),
             ("che squadra di calcio seguo?", 13), ("che lingua sto imparando?", 14)]


@pytest.fixture(scope="module")
def real_mem(tmp_path_factory):
    m = Memory(tmp_path_factory.mktemp("vera") / "m.db", embedder=REAL)
    for f in FACTS:
        m.remember(f)
    yield m
    m.close()


@needs_model
def test_real_model_finds_italian_memories_by_meaning(real_mem):
    found = [bool(h := real_mem.search(q, k=3)) and h[0].text == FACTS[i] for q, i in QUESTIONS]
    assert sum(found) >= 13, [q for (q, _), ok in zip(QUESTIONS, found, strict=True) if not ok]  # misurato: 13-14 su 15


@needs_model
def test_real_model_brings_nothing_for_unrelated_questions(real_mem):
    for q in ["qual è la capitale dell'Australia?", "quanto fa 17 per 23?", "come si fa la carbonara?"]:
        assert real_mem.search(q) == [], q


@needs_model
def test_real_model_is_fast_enough_for_every_turn(real_mem):
    real_mem.search("riscaldamento")
    t = time.perf_counter()
    for _ in range(5):
        real_mem.search("dove ho lasciato le chiavi della macchina?")
    assert (time.perf_counter() - t) / 5 < 0.15  # misurato ~30 ms; il contesto di ogni turno non deve rallentare
