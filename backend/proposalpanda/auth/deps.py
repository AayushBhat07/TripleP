"""
The signed-in user for an API request. proxy.ts checks the cookie first; this is the authority because it
reads the user's row: a valid cookie is not enough if the user was disabled or their access changed since.
"""

from fastapi import Request
from fastapi.responses import JSONResponse

from ..config import production
from ..db import get_db
from ..db.repos import users
from .rbac import can
from .session import SESSION_COOKIE, SESSION_TTL_SECONDS, sign_session, verify_session


class Denied(Exception):
    """Raised by require_permission; the app turns it into the JSON response it carries."""

    def __init__(self, response: JSONResponse):
        self.response = response


def get_session(request: Request) -> dict | None:
    return verify_session(request.cookies.get(SESSION_COOKIE))


def get_current_user(request: Request) -> dict | None:
    session = get_session(request)
    if not session:
        return None
    user = users.find_by_id(get_db(), session['uid'])
    if not user or user['disabled_at'] or user['session_version'] != session['ver']:
        return None
    return {**session, 'email': user['email'], 'name': user['name'], 'role': user['role']}


def require_permission(request: Request, permission: str | None = None) -> dict:
    user = get_current_user(request)
    if not user:
        # The cookie is left alone: a slow 401 must not wipe a cookie a newer sign-in just set.
        raise Denied(JSONResponse({'error': 'Not signed in.'}, status_code=401))
    if permission and not can(user['role'], permission):
        raise Denied(JSONResponse({'error': 'Your role is not allowed to do this.'}, status_code=403))
    return user


def set_session_cookie(response, user: dict) -> None:
    token = sign_session(
        {'uid': user['id'], 'ver': user['session_version'], 'email': user['email'], 'name': user['name'], 'role': user['role']}
    )
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite='lax', secure=production(), path='/', max_age=SESSION_TTL_SECONDS
    )
