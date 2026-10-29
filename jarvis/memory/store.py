"""Memoria a lungo termine di Jarvis (SQLite, un solo file in data/).

- fatti: ciò che Jarvis sa di te (preferenze, persone, progetti, abitudini, decisioni);
- turni: ogni scambio della conversazione, ricercabile;
- meta: piccoli valori di servizio (es. fin dove il custode della memoria ha già letto).

Ricerca ibrida: parole (FTS5, accenti ignorati, BM25) + significato (EmbeddingGemma 2, vedi embed.py).
Il modello dà somiglianze tutte alte anche tra frasi che non c'entrano (0,66-0,70), quindi una soglia fissa
non funziona: conta quanto un ricordo spicca rispetto a una serie di "ricordi di fondo" che non c'entrano
(tarato il 7/10: soglia 0,02 → 13 ricordi giusti su 15, nessuno per le domande senza risposta).
I fatti quasi identici non si duplicano: si aggiornano.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from .. import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY, text TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'generale',
    importance INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT 'conversazione',
    created REAL NOT NULL, updated REAL NOT NULL, uses INTEGER NOT NULL DEFAULT 0, embedding BLOB
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(text, content='facts', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE OF text ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text); END;

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, session TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(text, content='turns', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, text) VALUES ('delete', old.id, old.text); END;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

STOP = set("il lo la i gli le un uno una di a da in con su per tra fra e o ma che chi cosa come dove quando "
           "perche non si mi ti ci vi ne del dello della dei degli delle al allo alla ai agli alle nel nella "
           "è e' sono sei mio mia miei mie tuo tua suo sua io tu lui lei noi voi loro questo quello".split())

# "ricordi di fondo": frasi su altre persone e argomenti qualsiasi. Quanto un ricordo vero spicca su queste
# dice se c'entra davvero con la domanda (vedi la nota in cima).
BACKGROUND = [
    "Luca ha un cane di nome Rex", "Anna beve il tè verde la sera", "Paolo guida una moto rossa",
    "Marta lavora in banca a Milano", "Il vicino suona la chitarra", "Giorgio colleziona francobolli",
    "Elena va a nuotare il lunedì", "Il fratello di Carlo vive a Napoli", "Sofia ha paura dei ragni",
    "Davide gioca a scacchi online", "Il compleanno di Irene è ad agosto", "Franco usa un portatile vecchio",
    "Chiara tifa per la Juventus", "Roberto studia il tedesco", "La casa di Pietro ha un giardino",
    "Silvia preferisce la montagna al mare", "Il gatto di Bianca è nero", "Tommaso lavora di notte",
    "Laura ha comprato una bici elettrica", "Nicola suona il pianoforte"]
RELEVANT = 0.02  # spicco minimo per entrare nel contesto di un turno
WIDE = 0.0  # spicco minimo quando Jarvis cerca apposta nei ricordi (più largo: meglio un ricordo in più)


class Embedder(Protocol):
    def queries(self, texts: list[str]) -> np.ndarray: ...
    def documents(self, texts: list[str]) -> np.ndarray: ...


@dataclass
class Fact:
    id: int
    text: str
    category: str
    importance: int
    updated: float
    score: float = 0.0


@dataclass
class Forgotten:
    removed: list[str] = field(default_factory=list)
    candidates: list[Fact] = field(default_factory=list)  # più ricordi simili: niente cancellato, si chiede quale


def _fts_query(text: str) -> str:
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in STOP and len(w) > 1]
    return " OR ".join(f'"{w}"*' for w in words[:12])


def _content(s: str) -> set[str]:
    # le cifre contano sempre: "ha 2 figli" e "ha 3 figli" sono due cose diverse
    return {w for w in re.findall(r"\w+", s.lower()) if w not in STOP and (len(w) > 1 or w.isdigit())}


def _same_fact(old: str, new: str) -> bool:
    """Stesso ricordo detto di nuovo, o con un dettaglio in più. "un gatto di nome Briciola" e "un gatto di nome
    Tigre" sono due ricordi: prima la somiglianza del testo (81%) li fondeva e il secondo cancellava il primo."""
    a, b = _content(old), _content(new)
    if not a or not b:
        return False
    if a == b:
        return True
    # uno contiene l'altro con un dettaglio in più ("un gatto" -> "un gatto nero di nome Tigre"), purché non sia
    # una parola sola (il nome dell'utente starebbe dentro a tutto)
    return ((a <= b or b <= a) and min(len(a), len(b)) >= 2
            and difflib.SequenceMatcher(None, old.lower(), new.lower()).ratio() >= 0.5)


def _norm_text(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, np.float32)
    return v / max(float(np.linalg.norm(v)), 1e-9)


class Memory:
    def __init__(self, path: Path | None = None, embedder: Embedder | None = None) -> None:
        self.path = path or (paths.DATA_DIR / "jarvis.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        if "embedding" not in {r[1] for r in self._db.execute("PRAGMA table_info(turns)")}:
            self._db.execute("ALTER TABLE turns ADD COLUMN embedding BLOB")  # database di prima della memoria semantica
            self._db.commit()
        self._lock = threading.RLock()
        self.embedder = embedder
        self._background: np.ndarray | None = None

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------ significato

    def warm(self) -> None:
        """Modello del significato e frasi di sfondo pronti prima della prima frase. Il 26/10 si caricavano al primo
        turno: la CPU piena per un secondo e mezzo faceva restare indietro l'audio, e la frase dell'utente arrivava
        a pezzi ("A Jarvis." / "Have" / "Bis")."""
        if self.embedder is not None:
            self._baseline(self._query_vec("prova"))

    def _baseline(self, qv: np.ndarray) -> float:
        if self._background is None:
            self._background = np.stack([_unit(v) for v in self.embedder.documents(BACKGROUND)])
        return float(np.sort(self._background @ qv)[-3:].mean())

    def _query_vec(self, query: str) -> np.ndarray:
        return _unit(self.embedder.queries([query])[0])

    def _relevance(self, qv: np.ndarray, rows: list[tuple[int, bytes]]) -> dict[int, float]:
        if not rows:
            return {}
        mat = np.stack([_unit(np.frombuffer(blob, np.float32)) for _, blob in rows])
        rel = mat @ qv - self._baseline(qv)
        return {rid: float(r) for (rid, _), r in zip(rows, rel, strict=True)}

    def _set_embedding(self, table: str, rid: int, text: str) -> None:
        if self.embedder is not None:
            vec = _unit(self.embedder.documents([text])[0])
            self._db.execute(f"UPDATE {table} SET embedding=? WHERE id=?", (vec.tobytes(), rid))

    def embed_pending(self, limit: int = 64) -> int:
        """Dà un vettore ai fatti e ai turni che non l'hanno ancora (in sottofondo). Restituisce quanti."""
        if self.embedder is None:
            return 0
        done = 0
        for table in ("facts", "turns"):
            rows = self._db.execute(f"SELECT id, text FROM {table} WHERE embedding IS NULL ORDER BY id DESC LIMIT ?",
                                    (limit - done,)).fetchall()
            if not rows:
                continue
            vecs = self.embedder.documents([t for _, t in rows])
            with self._lock, self._db:
                self._db.executemany(f"UPDATE {table} SET embedding=? WHERE id=?",
                                     [(_unit(v).tobytes(), rid) for (rid, _), v in zip(rows, vecs, strict=True)])
            done += len(rows)
            if done >= limit:
                break
        return done

    # ------------------------------------------------------------ fatti

    def remember(self, text: str, category: str = "generale", importance: int = 1,
                 source: str = "conversazione") -> tuple[int, bool]:
        """Salva un fatto; se ne esiste uno quasi uguale lo aggiorna. Restituisce (id, nuovo?)."""
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise ValueError("fatto vuoto")
        now = time.time()
        for f in self._bm25_facts(text, k=5):
            if _same_fact(f.text, text):
                richer = text if len(_content(text)) >= len(_content(f.text)) else f.text  # mai perdere un dettaglio
                with self._lock, self._db:
                    self._db.execute("UPDATE facts SET text=?, updated=?, importance=MAX(importance, ?) WHERE id=?",
                                     (richer, now, importance, f.id))
                    if richer != f.text:
                        self._set_embedding("facts", f.id, richer)
                return f.id, False
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO facts(text, category, importance, source, created, updated) VALUES (?,?,?,?,?,?)",
                (text, category, importance, source, now, now))
            fid = int(cur.lastrowid)
            self._set_embedding("facts", fid, text)
        return fid, True

    def get(self, fid: int) -> Fact | None:
        r = self._db.execute("SELECT id, text, category, importance, updated FROM facts WHERE id=?", (fid,)).fetchone()
        return Fact(*r) if r else None

    def update(self, fid: int, text: str, importance: int | None = None) -> bool:
        text = re.sub(r"\s+", " ", text).strip()
        if not text or self.get(fid) is None:
            return False
        with self._lock, self._db:
            self._db.execute("UPDATE facts SET text=?, updated=?, importance=COALESCE(?, importance) WHERE id=?",
                             (text, time.time(), importance, fid))
            self._set_embedding("facts", fid, text)
        return True

    def delete(self, fid: int) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM facts WHERE id=?", (fid,)).rowcount > 0

    def forget(self, query: str) -> Forgotten:
        """Dimentica il fatto indicato. Se la richiesta è ambigua (più ricordi simili) non cancella niente e
        restituisce i candidati: meglio chiedere quale che cancellare quello sbagliato."""
        target = _norm_text(query)
        exact = [r for r in self._db.execute("SELECT id, text FROM facts").fetchall() if _norm_text(r[1]) == target]
        if exact:
            hits = [self.get(exact[0][0])]
        else:
            hits = self.search(query, k=5, min_relevance=WIDE)
            gap = 0.03 if self.embedder is not None else 0.3  # quanto il primo deve staccare il secondo
            if len(hits) > 1 and hits[0].score - hits[1].score < gap:
                return Forgotten(candidates=hits[:4])
            hits = hits[:1]
        for f in hits:
            self.delete(f.id)
        return Forgotten(removed=[f.text for f in hits])

    def _bm25_facts(self, query: str, k: int = 20) -> list[Fact]:
        q = _fts_query(query)
        if not q:
            return []
        rows = self._db.execute(
            "SELECT f.id, f.text, f.category, f.importance, f.updated, bm25(facts_fts) FROM facts_fts "
            "JOIN facts f ON f.id = facts_fts.rowid WHERE facts_fts MATCH ? ORDER BY bm25(facts_fts) LIMIT ?",
            (q, k)).fetchall()
        if not rows:
            return []
        best = min(r[5] for r in rows)
        return [Fact(*r[:5], score=(r[5] / best if best else 1.0)) for r in rows]  # 1 = il migliore

    def search(self, query: str, k: int = 5, min_relevance: float = RELEVANT, touch: bool = False) -> list[Fact]:
        """Ricerca ibrida. touch=True conta l'uso (solo quando il ricordo arriva davvero al cervello)."""
        lexical = {f.id: f.score for f in self._bm25_facts(query)}
        if self.embedder is None:
            scores = lexical
        else:
            rows = self._db.execute("SELECT id, embedding FROM facts WHERE embedding IS NOT NULL").fetchall()
            sem = self._relevance(self._query_vec(query), rows) if rows and query.strip() else {}
            # entra ciò che spicca per significato, o ciò che corrisponde bene per parole (anche un fatto
            # appena salvato e non ancora vettorizzato); le parole in comune contano un poco anche nell'ordine
            keep = {i for i, r in sem.items() if r >= min_relevance} | {i for i, b in lexical.items() if b >= 0.5}
            scores = {i: sem.get(i, min_relevance) + 0.03 * lexical.get(i, 0.0) for i in keep}
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        out = [Fact(*r, score=sc) for rid, sc in top
               if (r := self._db.execute("SELECT id, text, category, importance, updated FROM facts WHERE id=?",
                                         (rid,)).fetchone())]
        if out and touch:
            with self._lock, self._db:
                self._db.executemany("UPDATE facts SET uses = uses + 1 WHERE id=?", [(f.id,) for f in out])
        return out

    def related(self, texts: list[str], k: int = 20) -> list[Fact]:
        """I fatti già noti che riguardano questi testi (per il custode della memoria), senza contare l'uso."""
        seen: dict[int, Fact] = {}
        for t in texts:
            for f in self.search(t, k=6, min_relevance=WIDE):
                if f.id not in seen or f.score > seen[f.id].score:
                    seen[f.id] = f
        return sorted(seen.values(), key=lambda f: -f.score)[:k]

    def profile(self, limit: int = 25) -> list[str]:
        """I fatti più importanti e usati: entrano nel prompt di sistema."""
        rows = self._db.execute(
            "SELECT text FROM facts ORDER BY importance DESC, uses DESC, updated DESC LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in rows]

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    # ------------------------------------------------------------ conversazione

    def log_turn(self, role: str, text: str, session: str | None = None) -> None:
        if not text.strip():
            return
        with self._lock, self._db:
            self._db.execute("INSERT INTO turns(ts, role, text, session) VALUES (?,?,?,?)",
                             (time.time(), role, text, session))

    def recent_turns(self, n: int = 20) -> list[tuple[float, str, str]]:
        rows = self._db.execute("SELECT ts, role, text FROM turns ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return list(reversed(rows))

    def last_turn_id(self) -> int:
        return self._db.execute("SELECT COALESCE(MAX(id), 0) FROM turns").fetchone()[0]

    def turns_after(self, turn_id: int, limit: int = 80) -> list[tuple[int, float, str, str]]:
        return self._db.execute("SELECT id, ts, role, text FROM turns WHERE id > ? ORDER BY id LIMIT ?",
                                (turn_id, limit)).fetchall()

    def search_turns(self, query: str, k: int = 5) -> list[tuple[float, str, str]]:
        """Le frasi passate più pertinenti: per parole e, se c'è il modello, per significato."""
        scores: dict[int, float] = {}
        q = _fts_query(query)
        if q:
            rows = self._db.execute("SELECT rowid, bm25(turns_fts) FROM turns_fts WHERE turns_fts MATCH ? "
                                    "ORDER BY bm25(turns_fts) LIMIT 20", (q,)).fetchall()
            best = min((r[1] for r in rows), default=0)
            scores = {rid: 0.03 * (b / best if best else 1.0) for rid, b in rows}
        if self.embedder is not None and query.strip():
            rows = self._db.execute("SELECT id, embedding FROM turns WHERE embedding IS NOT NULL").fetchall()
            for rid, rel in self._relevance(self._query_vec(query), rows).items():
                if rel >= RELEVANT:
                    scores[rid] = scores.get(rid, 0.0) + rel
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        out = []
        for rid, _ in top:
            r = self._db.execute("SELECT ts, role, text FROM turns WHERE id=?", (rid,)).fetchone()
            if r:
                out.append(tuple(r))
        return out

    # ------------------------------------------------------------ valori di servizio

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        r = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, value))
