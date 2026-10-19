"""Every test gets a fresh in-memory office database with the demo accounts, and a client signed in by role."""

import pytest
from fastapi.testclient import TestClient

from proposalpanda.auth.session import SESSION_COOKIE, sign_session
from proposalpanda.db import get_db, reset_db
from proposalpanda.db.repos import users
from proposalpanda.main import app

DEMO = {
    'Admin': 'admin@proposalpanda.dev',
    'BidWriter': 'writer@proposalpanda.dev',
    'TenderAnalyst': 'analyst@proposalpanda.dev',
    'Viewer': 'viewer@proposalpanda.dev',
}


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', ':memory:')
    monkeypatch.delenv('DEMO_PASSWORD', raising=False)
    reset_db()
    yield
    reset_db()


def user_for(role: str) -> dict:
    return users.verify_credentials(get_db(), DEMO[role], 'password')


def cookie_for(role: str) -> str:
    user = user_for(role)
    return sign_session({'uid': user['id'], 'ver': user['session_version'], 'email': user['email'], 'name': user['name'], 'role': role})


@pytest.fixture
def as_role():
    """as_role('Viewer') is a client carrying that demo user's cookie; as_role(None) is signed out."""

    def client(role: str | None) -> TestClient:
        c = TestClient(app)
        if role:
            c.cookies.set(SESSION_COOKIE, cookie_for(role))
        return c

    return client
