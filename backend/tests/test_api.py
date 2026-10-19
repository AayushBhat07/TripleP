"""The office API through HTTP: who may read and change what, and what is stored."""

from conftest import cookie_for, user_for

from proposalpanda.auth.session import SESSION_COOKIE
from proposalpanda.db import get_db
from proposalpanda.db.repos import tenders


def report(id: str) -> dict:
    return {
        'id': id,
        'fileName': f'{id}.pdf',
        'uploadedAt': '2026-10-01T10:00:00.000Z',
        'summary': {'metadata': {'tenderTitle': f'Works {id}', 'keyDates': []}},
        'compliance': {'riskLevel': 'Low', 'complianceScore': 80},
    }


def bid(id: str, content: str = 'GSTIN {GSTIN}') -> dict:
    return {'id': id, 'tenderId': id, 'sections': [{'id': 's1', 'content': content}]}


# Sign-in and users

def test_login_sets_the_cookie_and_me_reads_it(as_role):
    client = as_role(None)
    assert client.post('/api/auth/login', json={'email': 'viewer@proposalpanda.dev', 'password': 'wrong'}).status_code == 401
    response = client.post('/api/auth/login', json={'email': 'viewer@proposalpanda.dev', 'password': 'password'})
    assert response.status_code == 200
    assert SESSION_COOKIE in response.cookies
    assert client.get('/api/auth/me').json()['user']['role'] == 'Viewer'
    client.post('/api/auth/logout')
    assert client.get('/api/auth/me').status_code == 401


def test_only_admin_can_list_and_add_users(as_role):
    assert as_role('BidWriter').get('/api/users').status_code == 403
    assert as_role(None).get('/api/users').status_code == 401
    listed = as_role('Admin').get('/api/users')
    assert listed.status_code == 200
    found = listed.json()['users']
    assert len(found) >= 4 and all('passwordHash' not in u and 'password_hash' not in u for u in found)

    admin = as_role('Admin')
    created = admin.post('/api/users', json={'email': 'new@firm.in', 'name': 'New', 'role': 'Viewer', 'password': 'long enough pw'})
    assert created.status_code == 201
    assert admin.post('/api/users', json={'email': 'x'}).status_code == 400


def test_a_role_change_applies_on_the_next_request_and_turning_someone_off_signs_them_out(as_role):
    old = as_role(None)
    old.cookies.set(SESSION_COOKIE, cookie_for('TenderAnalyst'))
    analyst = user_for('TenderAnalyst')
    admin = as_role('Admin')

    assert old.get('/api/auth/me').status_code == 200
    admin.patch(f"/api/users/{analyst['id']}", json={'disabled': True})
    assert old.get('/api/auth/me').status_code == 401
    admin.patch(f"/api/users/{analyst['id']}", json={'disabled': False})
    # Still 401: the cookie was issued before the change.
    assert old.get('/api/auth/me').status_code == 401
    assert as_role('TenderAnalyst').get('/api/auth/me').status_code == 200


# Tenders, company file and answers

def test_what_one_person_adds_everyone_else_reads(as_role):
    tenders.add(get_db(), report('T-SHARED'), 'analyst@proposalpanda.dev')
    listed = as_role('Viewer').get('/api/tenders')
    assert listed.status_code == 200
    assert any(r['id'] == 'T-SHARED' for r in listed.json()['reports'])

    assert as_role('BidWriter').put('/api/tenders/T-SHARED/bid', json={'bid': bid('T-SHARED')}).status_code == 200
    one = as_role('Viewer').get('/api/tenders/T-SHARED')
    assert one.json()['bid']['sections'][0]['content'] == 'GSTIN {GSTIN}'


def test_viewers_cannot_change_anything(as_role):
    tenders.add(get_db(), report('T-RO'), 'analyst@proposalpanda.dev')
    viewer = as_role('Viewer')
    assert viewer.put('/api/tenders/T-RO/bid', json={'bid': bid('T-RO')}).status_code == 403
    assert viewer.patch('/api/tenders/T-RO', json={'stage': 'won'}).status_code == 403
    assert viewer.put('/api/company', json={'companyProfile': {'legalName': 'X'}}).status_code == 403
    assert viewer.put('/api/answers', json={'answers': {'a': 'b'}}).status_code == 403
    assert as_role(None).get('/api/tenders').status_code == 401


def test_stages_and_corrected_dates_are_saved_and_unknown_stages_refused(as_role):
    tenders.add(get_db(), report('T-STAGE'), 'analyst@proposalpanda.dev')
    analyst = as_role('TenderAnalyst')
    assert analyst.patch('/api/tenders/T-STAGE', json={'stage': 'submitted'}).status_code == 200
    assert analyst.patch('/api/tenders/T-STAGE', json={'stage': 'bribed'}).status_code == 400
    dates = [{'kind': 'submission', 'date': '2026-11-01'}]
    updated = analyst.patch('/api/tenders/T-STAGE', json={'keyDates': dates}).json()
    assert updated['stage'] == 'submitted'
    assert updated['report']['summary']['metadata']['keyDates'] == dates
    assert updated['report']['summary']['metadata']['tenderTitle'] == 'Works T-STAGE'
    assert as_role('Admin').patch('/api/tenders/NOPE', json={'stage': 'won'}).status_code == 404


def test_the_company_file_never_stores_gstin_or_pan(as_role):
    response = as_role('BidWriter').put('/api/company', json={
        'companyProfile': {'legalName': 'Acme', 'gstin': '07AAAAA0000A1Z5', 'panNumber': 'AAAAA0000A'}, 'isComplete': True,
    })
    assert response.status_code == 200
    stored = as_role('Viewer').get('/api/company').json()
    assert stored['companyProfile']['legalName'] == 'Acme'
    assert stored['isComplete'] is True
    assert 'gstin' not in stored['companyProfile'] and 'panNumber' not in stored['companyProfile']


def test_saved_answers_merge(as_role):
    writer = as_role('BidWriter')
    writer.put('/api/answers', json={'answers': {'bank': 'SBI'}})
    writer.put('/api/answers', json={'answers': {'signatory': 'R. Kumar'}})
    answers = as_role('Viewer').get('/api/answers').json()['answers']
    assert answers['bank'] == 'SBI'
    assert answers['signatory'] == 'R. Kumar'


def test_browser_import_adds_only_what_is_new_and_what_the_role_may_add(as_role):
    body = {
        'reports': [report('T-IMP1'), report('T-IMP2'), {'id': '../evil', 'uploadedAt': 'x'}],
        'bids': [bid('T-IMP1'), bid('T-MISSING')],
        'stages': {'T-IMP1': 'won', 'T-IMP2': 'nonsense'},
        'answers': {'fresh': 'yes'},
    }
    first = as_role('TenderAnalyst').post('/api/import', json=body).json()
    assert first['imported'] == {'reports': 2, 'stages': 1}
    assert sorted(first['refused']) == ['answers', 'bids']

    second = as_role('BidWriter').post('/api/import', json=body).json()
    assert second['imported']['reports'] == 0, 'same ids are not added twice'
    assert second['imported']['bids'] == 1, 'only the bid whose tender exists'
    assert second['imported']['stages'] == 0, 'a stage already set stays'
    assert second['refused'] == []


def test_the_uploaded_nit_is_kept_with_its_tender_and_served_back_as_stored(as_role):
    data = b'%PDF-1.4 original nit'
    tenders.add(get_db(), report('T-FILE'), 'analyst@proposalpanda.dev', {'name': 'NIT 12.pdf', 'type': 'application/pdf', 'data': data})
    viewer = as_role('Viewer')
    assert viewer.get('/api/tenders/T-FILE').json()['hasFile'] is True

    file = viewer.get('/api/tenders/T-FILE/file')
    assert file.status_code == 200
    assert file.headers['content-type'] == 'application/pdf'
    assert file.content == data
    assert as_role(None).get('/api/tenders/T-FILE/file').status_code == 401
    assert viewer.get('/api/tenders/T-NONE/file').status_code == 404


# Vault and audit log

def test_the_vault_stores_opaque_records_for_writers(as_role):
    writer, admin = as_role('BidWriter'), as_role('Admin')
    assert writer.put('/api/vault/entries/header', json={'value': 'b64:ciphertext'}).status_code == 200
    assert writer.put('/api/vault/entries/doc:3f2a-b', json={'value': 'b64:more'}).status_code == 200
    assert sorted(admin.get('/api/vault/entries').json()['keys']) == ['doc:3f2a-b', 'header']
    assert admin.get('/api/vault/entries/header').json()['value'] == 'b64:ciphertext'
    writer.delete('/api/vault/entries/doc:3f2a-b')
    assert writer.get('/api/vault/entries/doc:3f2a-b').status_code == 404
    assert admin.delete('/api/vault/entries').status_code == 200
    assert admin.get('/api/vault/entries').json()['keys'] == []


def test_viewers_and_analysts_cannot_reach_the_vault_and_only_admin_erases_it(as_role):
    for role in ('Viewer', 'TenderAnalyst'):
        assert as_role(role).get('/api/vault/entries').status_code == 403
        assert as_role(role).get('/api/vault/entries/header').status_code == 403
    writer = as_role('BidWriter')
    assert writer.delete('/api/vault/entries').status_code == 403
    assert writer.put('/api/vault/entries/users', json={'value': 'x'}).status_code == 400


def test_vault_actions_go_to_the_server_log_stamped_by_the_server(as_role):
    writer = as_role('BidWriter')
    assert writer.post('/api/audit', json={'action': 'unlocked', 'by': 'someone-else@x'}).status_code == 201
    assert writer.post('/api/audit', json={'action': 'deleted the log'}).status_code == 400
    assert as_role('Viewer').post('/api/audit', json={'action': 'unlocked'}).status_code == 403

    entries = writer.get('/api/audit?scope=vault').json()['entries']
    assert entries[0]['userEmail'] == 'writer@proposalpanda.dev'
    assert entries[0]['action'] == 'unlocked'
    assert writer.get('/api/audit').status_code == 403
    assert as_role('Admin').get('/api/audit').status_code == 200


def test_bad_json_is_a_400_not_a_crash(as_role):
    response = as_role('BidWriter').put('/api/answers', content=b'{not json', headers={'content-type': 'application/json'})
    assert response.status_code == 400
