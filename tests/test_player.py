import numpy as np

from jarvis.audio.player import Player, Segment

SR = 48000


def tone(sec, amp=0.5):
    return np.full(int(SR * sec), amp, np.float32)


def collect(p, total, block=480):
    return np.concatenate([p.render(block) for _ in range(total // block)])


def test_plays_segments_in_order_with_events():
    ev = []
    p = Player(SR, on_event=lambda k, s: ev.append((k, s.text)))
    p.enqueue(Segment(tone(0.1, 0.2), "uno"))
    p.enqueue(Segment(tone(0.1, 0.4), "due"))
    out = collect(p, int(SR * 0.3))
    assert np.allclose(out[: int(SR * 0.1)], 0.2)
    assert np.allclose(out[int(SR * 0.1): int(SR * 0.2)], 0.4)
    assert np.allclose(out[int(SR * 0.2):], 0.0)
    assert ev == [("segment_start", "uno"), ("segment_end", "uno"), ("segment_start", "due"), ("segment_end", "due")]
    assert not p.speaking


def test_render_always_returns_requested_length():
    p = Player(SR)
    p.enqueue(Segment(tone(0.0123), "x"))
    for n in (1, 7, 480, 1000, 4096):
        assert p.render(n).shape == (n,)


def test_stop_fades_quickly_and_reports_spoken_text():
    p = Player(SR, fade_ms=15)
    seg = p.enqueue(Segment(tone(1.0), "Ho trovato tre voli per Londra domani mattina."))
    nxt = p.enqueue(Segment(tone(1.0), "Il più conveniente parte alle sette."))
    collect(p, int(SR * 0.5))
    dropped = p.stop()
    assert [s.id for s in dropped] == [seg.id, nxt.id]
    out = collect(p, int(SR * 0.1))
    fade = int(SR * 0.015)
    assert out[0] > 0.4  # la dissolvenza parte dal volume corrente (niente taglio netto, niente clic)
    assert 0.1 < out[fade // 2] < 0.4  # a metà è a metà strada
    assert np.all(np.diff(out[:fade]) <= 1e-6)  # scende senza risalire
    assert np.allclose(out[fade + 1:], 0.0)  # poi silenzio, e la frase dopo non parte
    assert seg.interrupted and nxt.interrupted
    spoken = seg.spoken_text()
    assert spoken and spoken != seg.text and seg.text.startswith(spoken)
    assert not spoken.endswith(" ")
    assert nxt.spoken_text() == ""  # mai iniziata


def test_stop_before_start_is_silent_and_complete_segment_reports_full_text():
    p = Player(SR)
    s = p.enqueue(Segment(tone(0.2), "Subito, signore."))
    p.stop()
    assert np.allclose(collect(p, 4800), 0.0)
    q = Player(SR)
    t = q.enqueue(Segment(tone(0.05), "Fatto."))
    collect(q, int(SR * 0.1))
    assert t.finished and not t.interrupted and t.spoken_text() == "Fatto."
    assert s.spoken_text() == ""


def test_duck_and_unduck_ramp_without_jumps():
    p = Player(SR, ramp_ms=30)
    p.enqueue(Segment(tone(1.0, 0.5), "x"))
    collect(p, 4800)
    p.duck(0.2)
    out = collect(p, int(SR * 0.06))
    ramp = int(SR * 0.03)
    assert out[0] < 0.5 and abs(out[ramp + 10] - 0.1) < 1e-3  # arriva a 0,5 × 0,2
    assert np.max(np.abs(np.diff(out))) < 0.5 / ramp * 2  # nessun salto brusco
    p.unduck()
    out = collect(p, int(SR * 0.06))
    assert abs(out[-1] - 0.5) < 1e-3


def test_stop_with_nothing_playing():
    assert Player(SR).stop() == []
