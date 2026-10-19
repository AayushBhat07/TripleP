"""The office's company file and the saved answers to bid blanks: one of each, shared by everyone."""

import json

from ...fmt import iso_now
from .. import Db

EMPTY = {'companyProfile': None, 'isComplete': False}


def _without_identifiers(state: dict) -> dict:
    """GSTIN and PAN belong in the vault, never in the database in the clear."""
    profile = state.get('companyProfile')
    if not profile:
        return state
    return {**state, 'companyProfile': {k: v for k, v in profile.items() if k not in ('gstin', 'panNumber')}}


def get(db: Db) -> dict:
    row = db.one('SELECT data FROM company_profile WHERE id = 1')
    return json.loads(row['data']) if row else dict(EMPTY)


def exists(db: Db) -> bool:
    return db.one('SELECT id FROM company_profile WHERE id = 1') is not None


def save(db: Db, state: dict, by: str) -> dict:
    data = _without_identifiers({'companyProfile': state.get('companyProfile'), 'isComplete': bool(state.get('isComplete'))})
    db.run(
        'INSERT INTO company_profile (id, data, updated_by, updated_at) VALUES (1, ?, ?, ?) '
        'ON CONFLICT (id) DO UPDATE SET data = excluded.data, updated_by = excluded.updated_by, updated_at = excluded.updated_at',
        (json.dumps(data, ensure_ascii=False), by, iso_now()),
    )
    return data


def get_answers(db: Db) -> dict:
    row = db.one("SELECT answers FROM blank_answers WHERE id = 'company'")
    return json.loads(row['answers']) if row else {}


def save_answers(db: Db, answers: dict, by: str) -> dict:
    """Merges new answers into the saved ones."""
    merged = {**get_answers(db), **{k: v for k, v in (answers or {}).items() if isinstance(v, str)}}
    db.run(
        "INSERT INTO blank_answers (id, answers, updated_by, updated_at) VALUES ('company', ?, ?, ?) "
        'ON CONFLICT (id) DO UPDATE SET answers = excluded.answers, updated_by = excluded.updated_by, updated_at = excluded.updated_at',
        (json.dumps(merged, ensure_ascii=False), by, iso_now()),
    )
    return merged
