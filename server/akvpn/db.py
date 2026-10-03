"""SQLite: konta, urzadzenia, kody z maili, licznik prob (limity)."""
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    pw_hash     TEXT NOT NULL,
    -- unverified -> pending -> active / blocked
    status      TEXT NOT NULL DEFAULT 'unverified',
    created_at  REAL NOT NULL,
    approved_at REAL,
    last_login  REAL
);
CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    device_key  TEXT NOT NULL,          -- staly identyfikator komputera z aplikacji
    name        TEXT NOT NULL,
    uuid        TEXT NOT NULL UNIQUE,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    up_bytes    INTEGER NOT NULL DEFAULT 0,
    down_bytes  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (account_id, device_key)
);
CREATE TABLE IF NOT EXISTS codes (
    email       TEXT NOT NULL,
    purpose     TEXT NOT NULL,          -- verify / reset
    code_hash   TEXT NOT NULL,
    expires_at  REAL NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (email, purpose)
);
CREATE TABLE IF NOT EXISTS hits (
    kind TEXT NOT NULL,
    key  TEXT NOT NULL,
    ts   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS hits_idx ON hits (kind, key, ts);
"""


def now() -> float:
    return time.time()


class Database:
    def __init__(self, path: str):
        self.path = path
        c = sqlite3.connect(path, timeout=15)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
        finally:
            c.close()

    @contextmanager
    def tx(self):
        """Jedna transakcja: commit na koncu, rollback przy wyjatku."""
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=15000")
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
        finally:
            c.close()
