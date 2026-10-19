"""
First Admin for a production install (demo accounts are off there):
    ADMIN_PASSWORD='a long passphrase' npm run db:create-admin -- owner@firm.in "Owner Name"
"""

import os
import sys

from ..config import database_path
from ..db import open_db
from ..db.repos.users import UserInputError, create


def main(args: list[str]) -> int:
    password = os.environ.get('ADMIN_PASSWORD')
    if len(args) != 2 or not password:
        print('Usage: ADMIN_PASSWORD=... npm run db:create-admin -- <email> "<name>"', file=sys.stderr)
        return 1
    email, name = args
    try:
        user = create(open_db(database_path()), {'email': email, 'name': name, 'role': 'Admin', 'password': password})
    except UserInputError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Created Admin {user['email']}. Sign in and add everyone else on the Users page.")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
