"""
Signed session cookie: base64url(JSON) "." base64url(HMAC-SHA256). The same format lib/auth/session.ts verifies
in proxy.ts, so the pages and the API accept the same cookie. The role lives here, set at login; the browser
cannot change it.
"""

import base64
import hashlib
import hmac
import json
import os
import time

from ..config import production
from .rbac import is_role

SESSION_COOKIE = 'pp_session'
SESSION_TTL_SECONDS = 60 * 60 * 8

DEV_SECRET = 'proposal-panda-dev-secret-change-me'


def _secret() -> bytes:
    secret = os.environ.get('AUTH_SECRET')
    if secret:
        return secret.encode()
    if production():
        raise RuntimeError('AUTH_SECRET must be set in production')
    return DEV_SECRET.encode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _from_b64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def sign_session(data: dict) -> str:
    """data: {uid, ver, email, name, role}; adds exp."""
    session = {**data, 'exp': int(time.time()) + SESSION_TTL_SECONDS}
    payload = _b64url(json.dumps(session, separators=(',', ':'), ensure_ascii=False).encode())
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64url(signature)}"


def verify_session(token: str | None) -> dict | None:
    if not token:
        return None
    payload, _, signature = token.partition('.')
    if not payload or not signature:
        return None
    try:
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _from_b64url(signature)):
            return None
        session = json.loads(_from_b64url(payload))
    except (ValueError, TypeError):
        return None
    if not isinstance(session, dict) or not is_role(session.get('role')):
        return None
    exp, uid, ver = session.get('exp'), session.get('uid'), session.get('ver')
    if not isinstance(exp, (int, float)) or not isinstance(uid, str) or not isinstance(ver, (int, float)):
        return None
    if exp < int(time.time()):
        return None
    return session
