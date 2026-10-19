"""Encrypted vault records, stored as the browser sealed them. The server never holds a key."""

import re

from ...fmt import iso_now
from .. import Db

_KEY = re.compile(r'(?:header|identifiers|log|doc:[A-Za-z0-9_-]{1,64}|file:[A-Za-z0-9_-]{1,64})')


def is_vault_key(key: str) -> bool:
    """Slot names the vault uses (lib/vault/wire.ts); anything else is refused."""
    return _KEY.fullmatch(key) is not None


def keys(db: Db) -> list[str]:
    return [r['key'] for r in db.all('SELECT key FROM vault_entries')]


def get(db: Db, key: str) -> str | None:
    row = db.one('SELECT value FROM vault_entries WHERE key = ?', (key,))
    return row['value'] if row else None


def put(db: Db, key: str, value: str) -> None:
    db.run(
        'INSERT INTO vault_entries (key, value, updated_at) VALUES (?, ?, ?) '
        'ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
        (key, value, iso_now()),
    )


def delete(db: Db, key: str) -> None:
    db.run('DELETE FROM vault_entries WHERE key = ?', (key,))


def clear(db: Db) -> None:
    db.run('DELETE FROM vault_entries')
