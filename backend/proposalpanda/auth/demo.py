"""Demo accounts, one per role, so a fresh install can be tried."""

# Seeded into an empty database; in production only when DEMO_PASSWORD is set.
# Real people are added on the Users page.
DEMO_USERS = [
    {'email': 'admin@proposalpanda.dev', 'name': 'Asha Admin', 'role': 'Admin'},
    {'email': 'analyst@proposalpanda.dev', 'name': 'Tarun Analyst', 'role': 'TenderAnalyst'},
    {'email': 'writer@proposalpanda.dev', 'name': 'Bina Writer', 'role': 'BidWriter'},
    {'email': 'viewer@proposalpanda.dev', 'name': 'Vikram Viewer', 'role': 'Viewer'},
]
