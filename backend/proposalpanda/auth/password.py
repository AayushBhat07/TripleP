"""
Password hashing with scrypt, stored as scrypt$<salt>$<hash>. The parameters are Node's scryptSync defaults
(N=16384, r=8, p=1, 64-byte key), so hashes written by the earlier Node server still verify.
"""

import base64
import hashlib
import hmac
import os

KEY_LENGTH = 64
MIN_PASSWORD_LENGTH = 10


def _scrypt(password: str, salt: bytes, length: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=length, maxmem=64 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = _scrypt(password, salt, KEY_LENGTH)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def verify_password(password: str, stored: str) -> bool:
    scheme, _, rest = stored.partition('$')
    salt, _, digest = rest.partition('$')
    if scheme != 'scrypt' or not salt or not digest:
        return False
    try:
        expected = base64.b64decode(digest)
        actual = _scrypt(password, base64.b64decode(salt), len(expected))
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)
