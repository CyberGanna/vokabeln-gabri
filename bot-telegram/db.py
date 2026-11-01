"""Database SQLite del vocabolario: un solo file, nessuna installazione."""

import sqlite3
import unicodedata

SCHEMA = """
CREATE TABLE IF NOT EXISTS parole (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chiave      TEXT NOT NULL UNIQUE,   -- per i duplicati: articolo + forma base, normalizzati
    forma_base  TEXT NOT NULL,
    articolo    TEXT NOT NULL DEFAULT '',
    plurale     TEXT NOT NULL DEFAULT '',
    categoria   TEXT NOT NULL DEFAULT '',
    traduzione  TEXT NOT NULL,
    esempio     TEXT NOT NULL DEFAULT '',
    livello     TEXT NOT NULL DEFAULT '',
    utente_id   INTEGER,
    creata_il   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def chiave(forma_base: str, articolo: str) -> str:
    """Normalizza per il confronto: minuscole, ß = ss, spazi compattati."""
    testo = f"{articolo} {forma_base}".strip().lower().replace("ß", "ss")
    testo = unicodedata.normalize("NFC", " ".join(testo.split()))
    return testo


class Vocabolario:
    def __init__(self, percorso: str):
        self.conn = sqlite3.connect(percorso, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def aggiungi(self, parole: list[dict], utente_id: int | None) -> tuple[list[sqlite3.Row], list[dict]]:
        """Inserisce le parole nuove. Restituisce (inserite, già presenti)."""
        nuove, doppie = [], []
        with self.conn:
            for p in parole:
                k = chiave(p["forma_base"], p.get("articolo", ""))
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO parole (chiave, forma_base, articolo, plurale, categoria,"
                    " traduzione, esempio, livello, utente_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (k, p["forma_base"], p.get("articolo", ""), p.get("plurale", ""), p.get("categoria", ""),
                     p["traduzione"], p.get("esempio", ""), p.get("livello", ""), utente_id),
                )
                if cur.rowcount:
                    nuove.append(self.conn.execute("SELECT * FROM parole WHERE id=?", (cur.lastrowid,)).fetchone())
                else:
                    doppie.append(p)
        return nuove, doppie

    def elimina(self, parola_id: int) -> sqlite3.Row | None:
        riga = self.conn.execute("SELECT * FROM parole WHERE id=?", (parola_id,)).fetchone()
        if riga:
            with self.conn:
                self.conn.execute("DELETE FROM parole WHERE id=?", (parola_id,))
        return riga

    def ultime(self, quante: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM parole ORDER BY id DESC LIMIT ?", (quante,)).fetchall()

    def tutte(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM parole ORDER BY id").fetchall()

    def conta(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM parole").fetchone()[0]
