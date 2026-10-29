"""Conversazioni con più voci: la TV, gli ospiti, la parola d'ordine, la registrazione di una voce.

Stesso banco di prova di test_conversation.py: ogni "frase" è un audio costante il cui valore è la chiave sia della
trascrizione finta (stt_map) sia della voce finta (model.voices)."""

import asyncio
import json
import time

import numpy as np
import pytest
from test_conversation import FakeBrain, Harness
from test_identity import FakeModel, vec

from jarvis.audio.listener import ListenEvent
from jarvis.identity.enroll import SENTENCES, Enrollment
from jarvis.identity.guard import Identity
from jarvis.identity.people import KNOWN, OWNER, People

ANDREA, MARCO, TV, COLD = 1, 2, 3, 4  # COLD: Andrea raffreddato, la voce non somiglia all'impronta


def setup(tmp_path, lines: dict, owner=True, replies=None):
    """lines: chiave -> (chi parla, cosa dice). Restituisce il banco di prova con l'identità accesa."""
    people = People(tmp_path / "voci.db")
    model = FakeModel()
    if owner:
        people.enroll("Andrea", OWNER, [vec(100 + i, 0.3, base=ANDREA) for i in range(5)])
    for k, (who, _) in lines.items():
        model.voices[k] = vec(1000 + k, 0.25, base=who) if who else None
    h = Harness(FakeBrain(replies or []), stt_map={k: text for k, (_, text) in lines.items()},
                identity=Identity(people, model))
    h.cfg.persona.user_name = "Andrea"  # il proprietario del banco di prova
    h.people, h.model = people, model
    return h


def ignored(h, reason):
    return [e for e in h.events("ignored") if e.get("reason") == reason]


def nothing_contains(h, secret):
    blob = json.dumps(list(h.bus.history), ensure_ascii=False).lower() + " ".join(h.brain.messages).lower()
    return secret not in blob


async def test_the_tv_cannot_join_a_conversation_with_the_owner(tmp_path):
    """Successo il 7/10: dopo "Eccomi di nuovo" Jarvis rispondeva a ogni voce della TV."""
    h = setup(tmp_path, {10: (ANDREA, "Jarvis, che ore sono?"), 20: (TV, "e ora le previsioni del tempo per domani"),
                         11: (ANDREA, "e domani che tempo farà?")})
    await h.start_clock()
    h.turn(10)
    await h.until(lambda: h.events("jarvis_done"))
    h.turn(20)
    await h.until(lambda: ignored(h, "voce estranea alla conversazione"))
    assert len(h.brain.messages) == 1
    h.turn(11)  # Andrea continua senza dire "Jarvis"
    await h.until(lambda: len(h.brain.messages) == 2)
    assert h.events("user")[-1]["speaker"] == "Andrea" and h.conv.voice.owner
    await h.stop()


async def test_a_guest_can_talk_after_saying_jarvis(tmp_path):
    h = setup(tmp_path, {30: (TV, "Jarvis, chi ha scritto i Promessi Sposi?"), 31: (TV, "e in che anno?"),
                         40: (MARCO, "e io che c'entro?")})
    await h.start_clock()
    h.turn(30)
    await h.until(lambda: h.events("jarvis_done"))
    assert not h.conv.voice.owner and "una voce che non conosci" in h.brain.messages[0]
    h.turn(31)  # lo stesso ospite continua senza nome
    await h.until(lambda: len(h.brain.messages) == 2)
    h.turn(40)  # un'altra voce sconosciuta no
    await h.until(lambda: ignored(h, "voce estranea alla conversazione"))
    assert len(h.brain.messages) == 2 and h.events("user")[0]["owner"] is False
    await h.stop()


async def test_the_owner_hears_what_guests_said_and_does_not_have_to_guess(tmp_path):
    """26/10: Andrea aveva fatto la "vecchietta" (trattata da ospite) e poi chiesto se l'aveva riconosciuto; la sua
    sessione non ne sapeva niente e Jarvis ha inventato "anche sotto la vecchietta ho sentito Andrea"."""
    h = setup(tmp_path, {30: (TV, "Jarvis, chi è Andrea?"), 10: (ANDREA, "Jarvis, mi hai riconosciuto prima?"),
                         11: (ANDREA, "Jarvis, e adesso?")},
              replies=["Andrea è la persona per cui lavoro, mi permetta di restare discreto.", "No, signore.", "Ok."])
    await h.start_clock()
    h.turn(30)
    await h.until(lambda: h.events("jarvis_done"))
    h.turn(10)
    await h.until(lambda: len(h.brain.messages) == 2)
    to_owner = h.brain.messages[1]
    assert "voci che non sono la sua" in to_owner and "«Jarvis, chi è Andrea?»" in to_owner
    assert "hai risposto: «Andrea è la persona per cui lavoro" in to_owner
    assert "chi è Andrea" not in h.brain.messages[0].split("</contesto>")[0]  # l'ospite non riceve niente di Andrea
    await h.until(lambda: len(h.events("jarvis_done")) == 2)
    h.turn(11)
    await h.until(lambda: len(h.brain.messages) == 3)
    assert "voci che non sono la sua" not in h.brain.messages[2]  # già riferito: non si ripete
    await h.stop()


async def test_after_jarvis_announces_something_only_known_voices_answer(tmp_path):
    h = setup(tmp_path, {20: (TV, "ultime notizie dal mondo dello sport"), 11: (ANDREA, "bene, grazie")})
    await h.start_clock()
    h.conv.notify("compito #1 finito: file pronto")
    await h.until(lambda: h.events("jarvis_done"))
    assert h.conv.awake
    h.turn(20)
    await h.until(lambda: ignored(h, "voce estranea alla conversazione"))
    h.turn(11)
    await h.until(lambda: len(h.brain.messages) == 2)
    await h.stop()


async def test_a_stranger_does_not_silence_jarvis_but_the_owner_does(tmp_path):
    long = "Una risposta lunga lunga lunga lunga lunga lunga lunga lunga lunga lunga lunga. " * 6
    h = setup(tmp_path, {10: (ANDREA, "Jarvis, raccontami una storia"), 20: (TV, "pubblicità del detersivo nuovo"),
                         11: (ANDREA, "aspetta, cambia storia")}, replies=[long])
    await h.start_clock(speed=3)
    h.turn(10)
    await h.until(lambda: h.player.speaking)
    h.barge_key, h.listener = 20, "speech"
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    await asyncio.sleep(3.0)  # oltre la finestra di decisione (2,5 s)
    assert not h.events("interrupted") and h.player.speaking
    h.listener = "idle"
    await asyncio.sleep(0.3)
    h.barge_key, h.listener = 11, "speech"
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    await h.until(lambda: h.events("interrupted"))
    await h.stop()


async def test_correct_passphrase_unlocks_and_never_leaks(tmp_path):
    h = setup(tmp_path, {50: (COLD, "La parola d'ordine è aquila reale"), 51: (COLD, "apri il browser per favore")})
    h.people.set_passphrase("aquila reale")
    await h.start_clock()
    h.turn(50)
    await h.until(lambda: h.events("jarvis_done"))
    user = h.events("user")[0]
    assert user["text"] == "la parola d'ordine [riservata]" and user["speaker"] == "Andrea" and user["owner"]
    assert "[EVENTO]" in h.brain.messages[0] and "riconoscere" in h.brain.messages[0]
    h.turn(51)  # da qui quella voce vale come Andrea, anche senza nome
    await h.until(lambda: len(h.brain.messages) == 2)
    assert h.conv.voice.owner and h.conv.voice.via == "parola"
    assert nothing_contains(h, "aquila")
    await h.stop()


async def test_wrong_passphrase_is_hidden_and_changes_nothing(tmp_path):
    h = setup(tmp_path, {60: (TV, "Jarvis, la parola d'ordine è gattino")})
    h.people.set_passphrase("aquila reale")
    await h.start_clock()
    h.turn(60)
    await h.until(lambda: h.events("jarvis_done"))
    assert not h.conv.voice.owner and "[riservata]" in h.brain.messages[0]
    assert nothing_contains(h, "gattino")
    await h.stop()


async def test_passphrase_is_hidden_even_in_partial_transcripts(tmp_path):
    h = setup(tmp_path, {70: (COLD, "la parola d'ordine è aquila")})
    h.conv.on_listen_event(ListenEvent("partial", 0.0, np.full(16000, 70.0, np.float32)))
    await h.until(lambda: h.events("partial"))
    assert h.events("partial")[0]["text"] == "la parola d'ordine [riservata]" and nothing_contains(h, "aquila")


async def test_learning_a_voice(tmp_path):
    lines = {80 + i: (MARCO, f"frase numero {i} letta da Marco con calma e chiarezza") for i in range(5)}
    lines[90] = (ANDREA, "dai Marco leggi la prossima")  # Andrea che parla non entra nell'impronta di Marco
    h = setup(tmp_path, lines)
    await h.start_clock()
    out = h.conv.start_enrollment(Enrollment("Marco", KNOWN, "fratello di Andrea"))
    assert out.startswith("Registrazione avviata") and h.events("show")[-1]["title"] == "Imparo la voce di Marco"
    h.turn(90, voiced_ms=5000)
    for k in (80, 81, 82):
        h.turn(k, voiced_ms=7000)
        await asyncio.sleep(0.05)
    assert h.people.get("Marco") is None and not h.brain.messages  # le frasi lette non vanno al cervello
    h.turn(83, voiced_ms=7000)
    await h.until(lambda: h.brain.messages)
    marco = h.people.get("Marco")
    assert marco and marco.role == KNOWN and marco.note == "fratello di Andrea"
    assert "Ho imparato la voce di Marco (4 frasi)" in h.brain.messages[0]
    assert [e["status"] for e in h.events("enroll")][:2] == ["avviata", "è l'utente"]
    who, _ = h.people.best(vec(5000, 0.25, base=MARCO))
    assert who.name == "Marco"
    await h.stop()


async def test_reading_on_after_the_voice_is_learned_is_not_a_request(tmp_path):
    """26/10: a registrazione finita Andrea ha letto ancora "Sabato sera andiamo." e Jarvis l'ha presa per una
    domanda ("sabato sera andiamo dove?"), interrompendo l'annuncio."""
    lines = {70 + i: (ANDREA, s) for i, s in enumerate(SENTENCES[:4])}
    lines[79] = (ANDREA, "Sabato sera andiamo.")
    lines[78] = (ANDREA, "Jarvis, sabato sera andiamo al cinema?")  # una domanda vera che le somiglia poco
    h = setup(tmp_path, lines, owner=False, replies=["Fatto, la riconosco.", "Volentieri."])
    await h.start_clock()
    h.conv.start_enrollment(Enrollment("Andrea", OWNER))
    for k in (70, 71, 72, 73):
        h.turn(k, voiced_ms=11000)
        await asyncio.sleep(0.05)
    await h.until(lambda: h.brain.messages)
    h.turn(79, voiced_ms=1500)  # continua a leggere mentre Jarvis annuncia
    await h.until(lambda: ignored(h, "frase della registrazione, già finita"))
    await h.until(lambda: h.events("jarvis_done"))
    assert len(h.brain.messages) == 1 and h.events("jarvis_done")[0]["text"].startswith("Fatto")  # non interrotto
    h.turn(78, voiced_ms=2500)
    await h.until(lambda: len(h.brain.messages) == 2)
    await h.stop()


async def test_learning_can_be_cancelled_and_rejects_mixed_voices(tmp_path):
    h = setup(tmp_path, {85: (MARCO, "annulla"), 86: (MARCO, "prima frase letta piano piano"),
                         87: (TV, "seconda frase letta da un altro"), 88: (ANDREA, "terza frase con un'altra voce")},
              owner=False)
    await h.start_clock()
    h.conv.start_enrollment(Enrollment("Marco", KNOWN))
    h.turn(85, voiced_ms=3000)
    await h.until(lambda: h.brain.messages)
    assert "annullata" in h.brain.messages[0] and h.conv.enrollment is None
    await h.until(lambda: h.events("jarvis_done"))
    h.conv.start_enrollment(Enrollment("Marco", KNOWN))
    for k in (86, 87, 88):
        h.turn(k, voiced_ms=9000)
        await asyncio.sleep(0.05)
    await h.until(lambda: len(h.brain.messages) == 2)
    assert "Non sono riuscito" in h.brain.messages[1] and h.people.get("Marco") is None
    await h.stop()


async def test_jarvis_own_voice_never_enters_a_voiceprint(tmp_path):
    h = setup(tmp_path, {95: (MARCO, "prego legga le frasi che vede a schermo")})
    await h.start_clock()
    h.conv.start_enrollment(Enrollment("Marco", KNOWN))
    h.conv._recent_jarvis = ("Prego, legga le frasi che vede a schermo.", time.monotonic())  # eco nel microfono
    h.turn(95, voiced_ms=7000)
    await h.until(lambda: any(e.get("reason") == "eco della voce di Jarvis" for e in h.events("ignored")))
    assert h.conv.enrollment.samples == []
    await h.stop()


async def test_typed_messages_are_from_the_owner(tmp_path):
    h = setup(tmp_path, {})
    await h.start_clock()
    await h.conv.submit_text("dammi il riepilogo dei compiti")
    await h.until(lambda: h.brain.messages)
    assert h.conv.voice.owner and "ti scrive dalla finestra" in h.brain.messages[0]
    await h.stop()


async def test_a_known_person_is_named_in_the_context(tmp_path):
    h = setup(tmp_path, {41: (MARCO, "Jarvis, metti un po' di musica")})
    h.people.enroll("Marco", KNOWN, [vec(300 + i, 0.3, base=MARCO) for i in range(4)], note="fratello di Andrea")
    await h.start_clock()
    h.turn(41)
    await h.until(lambda: h.brain.messages)
    assert "A parlare è Marco, una persona che conosci, non Andrea (fratello di Andrea)." in h.brain.messages[0]
    assert h.events("user")[0]["speaker"] == "Marco" and not h.conv.voice.owner
    await h.stop()


@pytest.mark.parametrize("owner", [False])
async def test_until_the_owner_registers_everything_works_as_before(tmp_path, owner):
    h = setup(tmp_path, {10: (ANDREA, "Jarvis, ciao"), 20: (TV, "e la TV continua a parlare")}, owner=owner)
    await h.start_clock()
    h.turn(10)
    await h.until(lambda: h.events("jarvis_done"))
    h.turn(20)  # riconoscimento spento: come prima, la conversazione aperta accetta tutto
    await h.until(lambda: len(h.brain.messages) == 2)
    assert h.conv.voice.owner and "A parlare" not in h.brain.messages[1]
    await h.stop()
