"""Le persone che Jarvis riconosce dalla voce, con i loro permessi, e la parola d'ordine (solo come impronta).

File a parte (data/voci.db), non nella memoria: le impronte vocali sono dati biometrici e restano sul PC.
Ruoli: "proprietario" (l'utente: tutto) e "conosciuto" (amici, famiglia: saluto per nome, i permessi base più
quelli che l'utente concede a voce). Chi non è qui è un ospite.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import passphrase

OWNER, KNOWN = "proprietario", "conosciuto"
SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, role TEXT NOT NULL,
    groups TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '', voiceprint BLOB NOT NULL,
    samples INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Person:
    id: int
    name: str
    role: str
    groups: set[str] = field(default_factory=set)
    note: str = ""

    @property
    def owner(self) -> bool:
        return self.role == OWNER


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, np.float32)
    return v / max(float(np.linalg.norm(v)), 1e-9)


class People:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------ persone

    def enroll(self, name: str, role: str, embeddings: list[np.ndarray], note: str = "") -> Person:
        """Registra (o sostituisce) l'impronta di una persona dalla media di più frasi."""
        if not embeddings:
            raise ValueError("servono campioni di voce")
        print_ = _unit(np.mean([_unit(e) for e in embeddings], axis=0))
        now = time.time()
        with self._lock, self._db:
            if role == OWNER:  # un solo proprietario
                self._db.execute("UPDATE people SET role=? WHERE role=? AND name<>?", (KNOWN, OWNER, name))
            self._db.execute(
                "INSERT INTO people(name, role, note, voiceprint, samples, created, updated) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET role=excluded.role, voiceprint=excluded.voiceprint, "
                "samples=excluded.samples, updated=excluded.updated, note=CASE WHEN excluded.note<>'' "
                "THEN excluded.note ELSE people.note END",
                (name, role, note, print_.tobytes(), len(embeddings), now, now))
        return self.get(name)

    def get(self, name: str) -> Person | None:
        r = self._db.execute("SELECT id, name, role, groups, note FROM people WHERE name=?", (name,)).fetchone()
        return Person(r[0], r[1], r[2], set(json.loads(r[3])), r[4]) if r else None

    def all(self) -> list[Person]:
        rows = self._db.execute("SELECT id, name, role, groups, note FROM people ORDER BY role DESC, name").fetchall()
        return [Person(r[0], r[1], r[2], set(json.loads(r[3])), r[4]) for r in rows]

    def owner(self) -> Person | None:
        return next((p for p in self.all() if p.owner), None)

    def set_groups(self, name: str, groups: set[str]) -> bool:
        with self._lock, self._db:
            return self._db.execute("UPDATE people SET groups=?, updated=? WHERE name=?",
                                    (json.dumps(sorted(groups)), time.time(), name)).rowcount > 0

    def set_note(self, name: str, note: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("UPDATE people SET note=? WHERE name=?", (note, name)).rowcount > 0

    def forget(self, name: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM people WHERE name=?", (name,)).rowcount > 0

    # ------------------------------------------------------------ voce

    def best(self, embedding: np.ndarray) -> tuple[Person | None, float]:
        """La persona con l'impronta più vicina e quanto (coseno); (None, 0) se non c'è nessuno."""
        rows = self._db.execute("SELECT name, voiceprint FROM people").fetchall()
        if not rows:
            return None, 0.0
        e = _unit(embedding)
        scores = [float(e @ np.frombuffer(blob, np.float32)) for _, blob in rows]
        i = int(np.argmax(scores))
        return self.get(rows[i][0]), scores[i]

    def adapt(self, name: str, embedding: np.ndarray, weight: float = 0.05) -> None:
        """Avvicina un poco l'impronta a una frase sicuramente sua: la voce cambia (raffreddore, microfono)."""
        r = self._db.execute("SELECT voiceprint, samples FROM people WHERE name=?", (name,)).fetchone()
        if not r:
            return
        new = _unit((1 - weight) * np.frombuffer(r[0], np.float32) + weight * _unit(embedding))
        with self._lock, self._db:
            self._db.execute("UPDATE people SET voiceprint=?, samples=?, updated=? WHERE name=?",
                             (new.tobytes(), r[1] + 1, time.time(), name))

    # ------------------------------------------------------------ parola d'ordine

    def set_passphrase(self, text: str) -> None:
        if len(passphrase.normalize(text)) < 4:
            raise ValueError("parola d'ordine troppo corta (almeno 4 lettere)")
        salt, digest = passphrase.make_hash(text)
        with self._lock, self._db:
            self._db.execute("INSERT INTO meta(key, value) VALUES ('parola', ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"{salt}:{digest}",))

    def has_passphrase(self) -> bool:
        return self._db.execute("SELECT 1 FROM meta WHERE key='parola'").fetchone() is not None

    def check_passphrase(self, text: str) -> bool:
        r = self._db.execute("SELECT value FROM meta WHERE key='parola'").fetchone()
        if not r:
            return False
        salt, digest = r[0].split(":")
        return passphrase.check(text, salt, digest)
