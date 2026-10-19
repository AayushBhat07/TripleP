"""The office access log. Insert-only: nothing in the app updates or deletes these rows."""

from ...fmt import iso_now
from .. import Db

# What the vault page reports; the server stamps who and when.
VAULT_ACTIONS = [
    'unlocked',
    'uploaded',
    'opened',
    'deleted',
    'saved identifiers',
    'exported bid',
    'built bundle',
    'moved to server',
    'erased',
]


def record(db: Db, user_email: str, action: str, target: str | None = None, detail: str | None = None) -> None:
    db.run(
        'INSERT INTO audit_log (at, user_email, action, target, detail) VALUES (?, ?, ?, ?, ?)',
        (iso_now(), user_email, action, target, detail[:300] if detail is not None else None),
    )


def recent(db: Db, actions: list[str] | None = None, limit: int = 200) -> list[dict]:
    where = f"WHERE action IN ({', '.join('?' * len(actions))})" if actions else ''
    rows = db.all(
        f'SELECT id, at, user_email, action, target, detail FROM audit_log {where} ORDER BY id DESC LIMIT ?',
        (*(actions or []), limit),
    )
    return [{**{k: v for k, v in r.items() if k != 'user_email'}, 'userEmail': r['user_email']} for r in rows]
