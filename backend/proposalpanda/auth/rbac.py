"""
Roles and permissions, mirrored from lib/auth/rbac.ts (which the pages and proxy.ts use).
The API enforces these; tests/test_auth.py checks the two copies agree.
"""

ROLES = ['Admin', 'BidWriter', 'TenderAnalyst', 'Viewer']

ROLE_PERMISSIONS = {
    'Admin': ['tender.view', 'tender.upload', 'bid.view', 'bid.generate', 'vault.use', 'user.manage', 'audit.view'],
    'BidWriter': ['tender.view', 'tender.upload', 'bid.view', 'bid.generate', 'vault.use'],
    'TenderAnalyst': ['tender.view', 'tender.upload', 'bid.view'],
    'Viewer': ['tender.view', 'bid.view'],
}


def is_role(value) -> bool:
    return isinstance(value, str) and value in ROLES


def can(role: str | None, permission: str) -> bool:
    return bool(role) and permission in ROLE_PERMISSIONS.get(role, [])
