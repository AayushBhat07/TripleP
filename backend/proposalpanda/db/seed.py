import os

from ..config import production
from . import Db
from .repos import users


def demo_password() -> str | None:
    if os.environ.get('DEMO_PASSWORD'):
        return os.environ['DEMO_PASSWORD']
    return None if production() else 'password'


def seed_demo_users(db: Db, password: str | None) -> None:
    """Adds the demo accounts, all with `password`, when nobody has an account yet. No password, no demo accounts."""
    from ..auth.demo import DEMO_USERS

    if not password or users.count(db) > 0:
        return
    for user in DEMO_USERS:
        users.create(db, {**user, 'password': password}, skip_password_check=True)
