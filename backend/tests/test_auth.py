"""Passwords, sessions and user accounts."""

import base64
import json
import re
from pathlib import Path

import pytest

from proposalpanda.auth.password import hash_password, verify_password
from proposalpanda.auth.rbac import ROLE_PERMISSIONS, ROLES, can
from proposalpanda.auth.session import sign_session, verify_session
from proposalpanda.db import open_db
from proposalpanda.db.repos import tenders
from proposalpanda.db.repos.users import UserInputError, create, find_by_id, update, verify_credentials
from proposalpanda.db.seed import seed_demo_users

# Made by the earlier Node server (scryptSync, and the cookie lib/auth/session.ts signs), so both keep working.
NODE_HASH = 'scrypt$MDEyMzQ1Njc4OWFiY2RlZg==$0LupyHkzAIgDr9UDsriAAf87vMbCbcaUfDvFOAHk0D0j5FDm643A031eTgoXAgCtB9917OPAizRUNx9itOkmEA=='
NODE_COOKIE = (
    'eyJ1aWQiOiJ1MSIsInZlciI6MSwiZW1haWwiOiJ2aWV3ZXJAcHJvcG9zYWxwYW5kYS5kZXYiLCJuYW1lIjoiViIsInJvbGUiOiJWaWV3ZXIiLCJleHAiOjQxMDI0NDQ4MDB9'
    '.pJfopRIxrOVXtf_x1VmfJP4O3mjY5Z8USWF0KibGQlA'
)


def seeded():
    db = open_db(':memory:')
    seed_demo_users(db, 'password')
    return db


def test_roles_and_permissions():
    assert [r for r in ROLES if can(r, 'bid.generate')] == ['Admin', 'BidWriter']
    assert [r for r in ROLES if can(r, 'user.manage')] == ['Admin']
    assert all(can(r, 'tender.view') and can(r, 'bid.view') for r in ROLES)
    assert not can('Viewer', 'tender.upload')
    assert not can(None, 'tender.view')


def test_session_round_trips_and_rejects_tampering():
    token = sign_session({'uid': 'u1', 'ver': 1, 'email': 'viewer@proposalpanda.dev', 'name': 'V', 'role': 'Viewer'})
    assert verify_session(token)['role'] == 'Viewer'
    payload, signature = token.split('.')
    data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    forged = base64.urlsafe_b64encode(json.dumps({**data, 'role': 'Admin'}).encode()).decode().rstrip('=')
    assert verify_session(f'{forged}.{signature}') is None
    assert verify_session('garbage') is None
    assert verify_session(None) is None


def test_cookies_and_hashes_from_the_node_server_still_verify():
    assert verify_session(NODE_COOKIE)['email'] == 'viewer@proposalpanda.dev'
    assert verify_password('correct horse', NODE_HASH)
    assert not verify_password('wrong horse', NODE_HASH)


def test_passwords_are_salted_scrypt_hashes():
    a = hash_password('correct horse')
    assert a != hash_password('correct horse')
    assert verify_password('correct horse', a)
    assert not verify_password('wrong horse', a)
    assert not verify_password('x', 'garbage')


def test_demo_login_checks_the_password():
    db = seeded()
    assert verify_credentials(db, 'Writer@proposalpanda.dev ', 'password')['role'] == 'BidWriter'
    assert verify_credentials(db, 'writer@proposalpanda.dev', 'wrong') is None
    assert verify_credentials(db, 'nobody@proposalpanda.dev', 'password') is None


def test_no_demo_accounts_without_a_password_and_none_once_someone_exists():
    db = open_db(':memory:')
    seed_demo_users(db, None)
    assert verify_credentials(db, 'admin@proposalpanda.dev', 'password') is None
    create(db, {'email': 'owner@firm.in', 'name': 'Owner', 'role': 'Admin', 'password': 'long enough pw'})
    seed_demo_users(db, 'password')
    assert verify_credentials(db, 'admin@proposalpanda.dev', 'password') is None


def test_access_changes_bump_the_session_version_and_disabled_users_cannot_sign_in():
    db = seeded()
    writer = verify_credentials(db, 'writer@proposalpanda.dev', 'password')
    update(db, writer['id'], name='Bina W')
    assert find_by_id(db, writer['id'])['session_version'] == writer['session_version']
    update(db, writer['id'], role='Viewer')
    assert find_by_id(db, writer['id'])['session_version'] == writer['session_version'] + 1
    update(db, writer['id'], disabled=True)
    assert verify_credentials(db, 'writer@proposalpanda.dev', 'password') is None
    update(db, writer['id'], disabled=False, password='a new password')
    assert verify_credentials(db, 'writer@proposalpanda.dev', 'a new password')


def test_the_last_active_admin_cannot_be_demoted_or_turned_off():
    db = seeded()
    admin = verify_credentials(db, 'admin@proposalpanda.dev', 'password')
    with pytest.raises(UserInputError):
        update(db, admin['id'], role='Viewer')
    with pytest.raises(UserInputError):
        update(db, admin['id'], disabled=True)
    create(db, {'email': 'two@firm.in', 'name': 'Two', 'role': 'Admin', 'password': 'long enough pw'})
    assert update(db, admin['id'], role='Viewer')['role'] == 'Viewer'


def test_new_users_need_a_valid_email_role_unique_address_and_long_password():
    db = open_db(':memory:')
    ok = {'email': 'a@firm.in', 'name': 'A', 'role': 'Viewer', 'password': 'long enough pw'}
    create(db, ok)
    with pytest.raises(UserInputError, match='already'):
        create(db, ok)
    with pytest.raises(UserInputError, match='10 characters'):
        create(db, {**ok, 'email': 'b@firm.in', 'password': 'short'})
    with pytest.raises(UserInputError, match='email'):
        create(db, {**ok, 'email': 'nope'})
    with pytest.raises(UserInputError, match='role'):
        create(db, {**ok, 'email': 'c@firm.in', 'role': 'Boss'})


def test_a_fresh_database_migrates_and_stores_json():
    db = open_db(':memory:')
    assert tenders.add(db, {'id': 'T-1', 'uploadedAt': '2026-10-01T10:00:00.000Z', 'title': 'Road works'}, 'a@firm.in')
    assert tenders.get(db, 'T-1')['report']['title'] == 'Road works'
    assert not tenders.add(db, {'id': 'T-1', 'uploadedAt': 'x'}, 'a@firm.in')


def test_the_api_and_the_screens_share_the_same_roles():
    """lib/auth/rbac.ts drives proxy.ts and the screens; it must grant exactly what the API grants."""
    source = (Path(__file__).resolve().parents[2] / 'lib' / 'auth' / 'rbac.ts').read_text()
    table = re.search(r'ROLE_PERMISSIONS[^=]*=\s*\{(.*?)\};', source, re.S).group(1)
    screens = {role: re.findall(r"'([a-z]+\.[a-z]+)'", perms) for role, perms in re.findall(r'(\w+):\s*\[([^\]]*)\]', table)}
    assert screens == ROLE_PERMISSIONS
    assert re.findall(r"'(\w+)'", re.search(r'ROLES = \[(.*?)\]', source).group(1)) == ROLES
