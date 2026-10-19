"""Analysed tenders, their bids and pipeline stages: the office register."""

import json
import re

from ...fmt import iso_now
from .. import Db


def is_tender_id(id) -> bool:
    """Ids are made by the browser (TENDER-<time>); keep them safe to put in URLs and file names."""
    return isinstance(id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', id) is not None


def exists(db: Db, id: str) -> bool:
    return db.one('SELECT id FROM tenders WHERE id = ?', (id,)) is not None


def list_register(db: Db) -> dict:
    rows = db.all('SELECT id, report, stage FROM tenders ORDER BY created_at')
    return {
        'reports': [json.loads(r['report']) for r in rows],
        'bids': [json.loads(b['bid']) for b in db.all('SELECT bid FROM bids')],
        'stages': {r['id']: r['stage'] for r in rows if r['stage']},
    }


def get(db: Db, id: str) -> dict | None:
    row = db.one('SELECT report, stage FROM tenders WHERE id = ?', (id,))
    if not row:
        return None
    bid = db.one('SELECT bid FROM bids WHERE id = ?', (id,))
    has_file = db.one('SELECT id FROM tender_files WHERE id = ?', (id,)) is not None
    return {
        'report': json.loads(row['report']),
        'bid': json.loads(bid['bid']) if bid else None,
        'stage': row['stage'],
        'hasFile': has_file,
    }


def add(db: Db, report: dict, by: str, file: dict | None = None) -> bool:
    """Adds a new tender, with the file it came from if given. False if the id is taken (nothing is overwritten)."""
    with db.transaction() as conn:
        changed = conn.execute(
            'INSERT INTO tenders (id, report, uploaded_by, created_at) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING',
            (report['id'], json.dumps(report, ensure_ascii=False), by, report['uploadedAt']),
        ).rowcount
        if not changed:
            return False
        if file:
            conn.execute(
                'INSERT INTO tender_files (id, name, type, data) VALUES (?, ?, ?, ?)',
                (report['id'], file['name'], file['type'], file['data']),
            )
        return True


def get_file(db: Db, id: str) -> dict | None:
    return db.one('SELECT name, type, data FROM tender_files WHERE id = ?', (id,))


def update_report(db: Db, id: str, change) -> bool:
    row = db.one('SELECT report FROM tenders WHERE id = ?', (id,))
    if not row:
        return False
    db.run('UPDATE tenders SET report = ? WHERE id = ?', (json.dumps(change(json.loads(row['report'])), ensure_ascii=False), id))
    return True


def set_stage(db: Db, id: str, stage: str | None) -> bool:
    return db.run('UPDATE tenders SET stage = ? WHERE id = ?', (stage, id)) > 0


def save_bid(db: Db, id: str, bid: dict, by: str) -> bool:
    """Saves the tender's bid, replacing any earlier one. False if the tender doesn't exist."""
    if not exists(db, id):
        return False
    value = json.dumps({**bid, 'id': id}, ensure_ascii=False)
    now = iso_now()
    db.run(
        'INSERT INTO bids (id, bid, updated_by, updated_at) VALUES (?, ?, ?, ?) '
        'ON CONFLICT (id) DO UPDATE SET bid = excluded.bid, updated_by = excluded.updated_by, updated_at = excluded.updated_at',
        (id, value, by, now),
    )
    return True
