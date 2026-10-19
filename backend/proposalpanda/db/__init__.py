"""
The office database: one SQLite file (DATABASE_PATH, default data/proposalpanda.db), migrated on open so a fresh
install needs no extra step. Reports and bids are stored whole as JSON: they are only ever loaded whole.

Migrations are the SQL files in migrations/, recorded in __drizzle_migrations the way the earlier Drizzle
migrator recorded them, so a database made by the Node server opens here without being migrated twice.
"""

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from ..config import database_path

MIGRATIONS = Path(__file__).parent / 'migrations'


class Db:
    """A connection shared by request threads; the lock keeps one statement sequence at a time."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.lock = threading.RLock()

    def all(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def run(self, sql: str, params=()) -> int:
        """Runs a write (autocommit outside transaction()); returns the number of rows changed."""
        with self.lock:
            return self.conn.execute(sql, params).rowcount

    def transaction(self):
        return _Transaction(self)


class _Transaction:
    def __init__(self, db: Db):
        self.db = db

    def __enter__(self) -> sqlite3.Connection:
        self.db.lock.acquire()
        self.db.conn.execute('BEGIN')
        return self.db.conn

    def __exit__(self, kind, error, trace):
        try:
            self.db.conn.execute('COMMIT' if kind is None else 'ROLLBACK')
        finally:
            self.db.lock.release()


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        'CREATE TABLE IF NOT EXISTS "__drizzle_migrations" (id INTEGER PRIMARY KEY AUTOINCREMENT, hash text NOT NULL, created_at numeric)'
    )
    last = conn.execute('SELECT created_at FROM "__drizzle_migrations" ORDER BY created_at DESC LIMIT 1').fetchone()
    journal = json.loads((MIGRATIONS / '_journal.json').read_text())
    for entry in journal['entries']:
        if last and int(last[0]) >= entry['when']:
            continue
        sql = (MIGRATIONS / f"{entry['tag']}.sql").read_text()
        conn.execute('BEGIN')
        for statement in sql.split('--> statement-breakpoint'):
            if statement.strip():
                conn.execute(statement)
        conn.execute(
            'INSERT INTO "__drizzle_migrations" (hash, created_at) VALUES (?, ?)',
            (hashlib.sha256(sql.encode()).hexdigest(), entry['when']),
        )
        conn.execute('COMMIT')


def open_db(path: str) -> Db:
    """Opens (creating if needed) and migrates a database. Pass ':memory:' in tests."""
    if path != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA foreign_keys = ON')
    _migrate(conn)
    return Db(conn)


_db: Db | None = None
_db_lock = threading.Lock()


def get_db() -> Db:
    global _db
    with _db_lock:
        if _db is None:
            from .seed import demo_password, seed_demo_users

            _db = open_db(database_path())
            seed_demo_users(_db, demo_password())
        return _db


def reset_db() -> None:
    """Forgets the open database (tests use a fresh in-memory one each run)."""
    global _db
    with _db_lock:
        _db = None
