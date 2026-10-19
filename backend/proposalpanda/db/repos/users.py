"""People who can sign in, and what an Admin can change about them."""

import re
import uuid

from ...auth.password import MIN_PASSWORD_LENGTH, hash_password, verify_password
from ...auth.rbac import is_role
from ...fmt import iso_now
from .. import Db


class UserInputError(Exception):
    pass


def to_public(row: dict) -> dict:
    """What the API and pages see: never the password hash."""
    return {
        'id': row['id'],
        'email': row['email'],
        'name': row['name'],
        'role': row['role'],
        'disabledAt': row['disabled_at'],
        'createdAt': row['created_at'],
    }


def _normalise(email: str) -> str:
    return email.strip().lower()


def find_by_id(db: Db, id: str) -> dict | None:
    return db.one('SELECT * FROM users WHERE id = ?', (id,))


def list_users(db: Db) -> list[dict]:
    return [to_public(r) for r in db.all('SELECT * FROM users ORDER BY created_at')]


def count(db: Db) -> int:
    return db.one('SELECT count(*) AS n FROM users')['n']


def verify_credentials(db: Db, email: str, password: str) -> dict | None:
    """The signed-in user for these credentials, or None. Disabled users can't sign in."""
    row = db.one('SELECT * FROM users WHERE email = ?', (_normalise(email),))
    if not row or row['disabled_at'] or not verify_password(password, row['password_hash']):
        return None
    return row


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserInputError(f'Passwords need at least {MIN_PASSWORD_LENGTH} characters.')


def create(db: Db, data: dict, skip_password_check: bool = False) -> dict:
    email = _normalise(data['email'])
    name = data['name'].strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+', email):
        raise UserInputError('Enter a valid email address.')
    if not name:
        raise UserInputError('Enter a name.')
    if not is_role(data['role']):
        raise UserInputError('Pick a role.')
    if not skip_password_check:
        _check_password(data['password'])
    if db.one('SELECT id FROM users WHERE email = ?', (email,)):
        raise UserInputError('Someone with that email already has an account.')
    id = str(uuid.uuid4())
    db.run(
        'INSERT INTO users (id, email, name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        (id, email, name, data['role'], hash_password(data['password']), iso_now()),
    )
    return to_public(find_by_id(db, id))


def _active_admins(db: Db) -> int:
    return db.one("SELECT count(*) AS n FROM users WHERE role = 'Admin' AND disabled_at IS NULL")['n']


def update(db: Db, id: str, name=None, role=None, disabled=None, password=None) -> dict:
    """
    Change a user's role, name, password or disabled state. Any change that affects access bumps
    session_version, so their current cookie stops working on its next request.
    """
    row = find_by_id(db, id)
    if not row:
        raise UserInputError('No such user.')
    if role is not None and not is_role(role):
        raise UserInputError('Pick a role.')
    if password is not None:
        _check_password(password)

    loses_admin = row['role'] == 'Admin' and not row['disabled_at'] and ((role and role != 'Admin') or disabled)
    if loses_admin and _active_admins(db) <= 1:
        raise UserInputError('Keep at least one active Admin, or nobody can manage users.')

    access_changed = (
        (role is not None and role != row['role'])
        or (disabled is not None and disabled != bool(row['disabled_at']))
        or password is not None
    )
    changes: dict = {}
    if name and name.strip():
        changes['name'] = name.strip()
    if role is not None:
        changes['role'] = role
    if disabled is not None:
        changes['disabled_at'] = (row['disabled_at'] or iso_now()) if disabled else None
    if password is not None:
        changes['password_hash'] = hash_password(password)
    if access_changed:
        changes['session_version'] = row['session_version'] + 1
    if changes:
        assignments = ', '.join(f'{column} = ?' for column in changes)
        db.run(f'UPDATE users SET {assignments} WHERE id = ?', (*changes.values(), id))
    return to_public(find_by_id(db, id))
