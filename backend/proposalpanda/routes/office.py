"""Sign-in, users, the company file, saved answers, the register, the vault and the activity log."""

from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..auth.deps import get_current_user, get_session, require_permission, set_session_cookie
from ..auth.rbac import can
from ..auth.session import SESSION_COOKIE
from ..db import get_db
from ..db.repos import audit, company, tenders, users, vault
from ..engine.pipeline import STAGE_LABELS

router = APIRouter(prefix='/api')


async def body_of(request: Request, default=None):
    try:
        return await request.json()
    except ValueError:
        return default


def error(message: str, status: int) -> JSONResponse:
    return JSONResponse({'error': message}, status_code=status)


# ── Auth ─────────────────────────────────────────────────────────────


@router.post('/auth/login')
async def login(request: Request):
    body = await body_of(request, {})
    body = body if isinstance(body, dict) else {}
    db = get_db()
    user = users.verify_credentials(db, str(body.get('email') or ''), str(body.get('password') or ''))
    if not user:
        return error('Invalid email or password.', 401)
    audit.record(db, user['email'], 'signed in')
    response = JSONResponse({'user': {'email': user['email'], 'name': user['name'], 'role': user['role']}})
    set_session_cookie(response, user)
    return response


@router.post('/auth/logout')
async def logout():
    response = JSONResponse({'ok': True})
    response.delete_cookie(SESSION_COOKIE, path='/')
    return response


@router.get('/auth/me')
async def me(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({'user': None}, status_code=401)
    response = JSONResponse({'user': {'email': user['email'], 'name': user['name'], 'role': user['role']}})
    # Re-issue the cookie when the name or role changed, so proxy.ts gates pages on the current role.
    cookie = get_session(request)
    if cookie and (cookie['role'] != user['role'] or cookie['name'] != user['name']):
        row = users.find_by_id(get_db(), user['uid'])
        if row:
            set_session_cookie(response, row)
    return response


# ── Users ────────────────────────────────────────────────────────────


@router.get('/users')
async def list_users(request: Request):
    require_permission(request, 'user.manage')
    return {'users': users.list_users(get_db())}


@router.post('/users')
async def add_user(request: Request):
    auth = require_permission(request, 'user.manage')
    body = await body_of(request, {})
    body = body if isinstance(body, dict) else {}
    db = get_db()
    try:
        user = users.create(
            db,
            {k: str(body.get(k) if body.get(k) is not None else '') for k in ('email', 'name', 'role', 'password')},
        )
    except users.UserInputError as e:
        return error(str(e), 400)
    audit.record(db, auth['email'], 'added user', user['email'], user['role'])
    return JSONResponse({'user': user}, status_code=201)


@router.patch('/users/{id}')
async def change_user(id: str, request: Request):
    auth = require_permission(request, 'user.manage')
    body = await body_of(request, {})
    body = body if isinstance(body, dict) else {}
    pick = lambda key, kind: body.get(key) if isinstance(body.get(key), kind) else None  # noqa: E731
    changes = {
        'name': pick('name', str),
        'role': pick('role', str),
        'disabled': pick('disabled', bool),
        'password': pick('password', str),
    }
    db = get_db()
    try:
        user = users.update(db, id, **changes)
    except users.UserInputError as e:
        return error(str(e), 400)
    what = [
        changes['role'] is not None and f"role {user['role']}",
        changes['disabled'] is not None and ('turned off' if changes['disabled'] else 'turned on'),
        changes['password'] is not None and 'password reset',
        changes['name'] is not None and 'renamed',
    ]
    audit.record(db, auth['email'], 'changed user', user['email'], ', '.join(w for w in what if w))
    return {'user': user}


# ── Company file and saved answers ──────────────────────────────────


@router.get('/company')
async def get_company(request: Request):
    require_permission(request)
    return company.get(get_db())


@router.put('/company')
async def put_company(request: Request):
    """Admins and Bid Writers keep the company file; GSTIN and PAN are dropped (they live in the vault)."""
    auth = require_permission(request, 'bid.generate')
    body = await body_of(request)
    profile = body.get('companyProfile') if isinstance(body, dict) else None
    if not isinstance(body, dict) or (
        profile is not None and (not isinstance(profile, dict) or not isinstance(profile.get('legalName'), str))
    ):
        return error('A company profile is required.', 400)
    return company.save(get_db(), {'companyProfile': profile, 'isComplete': bool(body.get('isComplete'))}, auth['email'])


@router.get('/answers')
async def get_answers(request: Request):
    """Answers to bid blanks remembered for the next bid (bank, signatory and the like)."""
    require_permission(request, 'bid.view')
    return {'answers': company.get_answers(get_db())}


@router.put('/answers')
async def put_answers(request: Request):
    auth = require_permission(request, 'bid.generate')
    body = await body_of(request)
    answers = body.get('answers') if isinstance(body, dict) else None
    if not isinstance(answers, (dict, list)):
        return error('answers is required.', 400)
    if isinstance(answers, list):
        answers = {str(i): v for i, v in enumerate(answers)}
    return {'answers': company.save_answers(get_db(), answers, auth['email'])}


# ── The register ─────────────────────────────────────────────────────


@router.get('/tenders')
async def register(request: Request):
    """Every analysed tender with its bid and stage, for the register and dashboard."""
    require_permission(request, 'tender.view')
    return tenders.list_register(get_db())


@router.get('/tenders/{id}')
async def get_tender(id: str, request: Request):
    require_permission(request, 'tender.view')
    tender = tenders.get(get_db(), id)
    return tender if tender else error('No such tender.', 404)


@router.patch('/tenders/{id}')
async def patch_tender(id: str, request: Request):
    """Corrected key dates (from a corrigendum) and the stage set by hand on the dashboard."""
    require_permission(request, 'tender.upload')
    body = await body_of(request, {})
    body = body if isinstance(body, dict) else {}
    db = get_db()

    if 'stage' in body:
        stage = body['stage']
        if stage is not None and not (isinstance(stage, str) and stage in STAGE_LABELS):
            return error('Unknown stage.', 400)
        if not tenders.set_stage(db, id, stage):
            return error('No such tender.', 404)

    if 'keyDates' in body:
        if not isinstance(body['keyDates'], list):
            return error('keyDates must be a list.', 400)

        def change(report: dict) -> dict:
            summary = report.get('summary') or {}
            return {**report, 'summary': {**summary, 'metadata': {**(summary.get('metadata') or {}), 'keyDates': body['keyDates']}}}

        if not tenders.update_report(db, id, change):
            return error('No such tender.', 404)

    return tenders.get(db, id)


@router.put('/tenders/{id}/bid')
async def put_bid(id: str, request: Request):
    """Saves edits to a drafted bid (sections edited, blanks filled, vault documents attached)."""
    auth = require_permission(request, 'bid.generate')
    body = await body_of(request, {})
    bid = body.get('bid') if isinstance(body, dict) else None
    if not isinstance(bid, dict) or not isinstance(bid.get('sections'), list):
        return error('A bid with sections is required.', 400)
    if not tenders.save_bid(get_db(), id, bid, auth['email']):
        return error('No such tender.', 404)
    return {'ok': True}


@router.get('/tenders/{id}/file')
async def tender_file(id: str, request: Request):
    """The NIT file the tender was analysed from."""
    require_permission(request, 'tender.view')
    file = tenders.get_file(get_db(), id)
    if not file:
        return error('No file is stored for this tender.', 404)
    return Response(
        content=bytes(file['data']),
        media_type=file['type'],
        headers={
            'Content-Disposition': f"inline; filename*=UTF-8''{quote(file['name'], safe='')}",
            'X-Content-Type-Options': 'nosniff',
        },
    )


@router.post('/import')
async def import_browser_data(request: Request):
    """
    One-time move of what a browser stored before the database existed. Rows keep their ids and nothing already
    in the database is overwritten, so sending the same data twice is harmless. Each part needs the permission that
    would let this user create it; parts they can't create come back as refused, and the browser keeps them.
    """
    auth = require_permission(request)
    body = await body_of(request)
    if not isinstance(body, dict):
        return error('Nothing to import.', 400)

    db = get_db()
    allowed = {
        'reports': can(auth['role'], 'tender.upload'),
        'stages': can(auth['role'], 'tender.upload'),
        'bids': can(auth['role'], 'bid.generate'),
        'company': can(auth['role'], 'bid.generate'),
        'answers': can(auth['role'], 'bid.generate'),
    }
    imported: dict = {}
    present = lambda part: body.get(part) is not None  # noqa: E731
    refused = [part for part in allowed if present(part) and not allowed[part]]

    if present('reports') and allowed['reports'] and isinstance(body['reports'], list):
        imported['reports'] = 0
        for report in body['reports']:
            if not isinstance(report, dict) or not tenders.is_tender_id(report.get('id')) or not isinstance(report.get('uploadedAt'), str):
                continue
            if tenders.add(db, report, auth['email']):
                imported['reports'] += 1

    if present('bids') and allowed['bids'] and isinstance(body['bids'], list):
        imported['bids'] = 0
        for bid in body['bids']:
            if not isinstance(bid, dict) or not tenders.is_tender_id(bid.get('id')) or not isinstance(bid.get('sections'), list):
                continue
            tender = tenders.get(db, bid['id'])
            # Only into a tender that exists and has no bid yet.
            if tender and not tender['bid'] and tenders.save_bid(db, bid['id'], bid, auth['email']):
                imported['bids'] += 1

    if present('stages') and allowed['stages'] and isinstance(body['stages'], dict):
        imported['stages'] = 0
        for id, stage in body['stages'].items():
            tender = tenders.get(db, id)
            if tender and not tender['stage'] and isinstance(stage, str) and stage in STAGE_LABELS and tenders.set_stage(db, id, stage):
                imported['stages'] += 1

    if present('company') and allowed['company'] and isinstance(body['company'], dict) and body['company'].get('companyProfile') and not company.exists(db):
        company.save(db, body['company'], auth['email'])
        imported['company'] = 1

    if present('answers') and allowed['answers'] and isinstance(body['answers'], dict):
        existing = company.get_answers(db)
        fresh = {k: v for k, v in body['answers'].items() if k not in existing}
        company.save_answers(db, fresh, auth['email'])
        imported['answers'] = len(fresh)

    return {'imported': imported, 'refused': refused}


# ── Vault and activity log ───────────────────────────────────────────

# A sealed 10 MB file grows by a third in base64; leave room for the JSON around it.
MAX_RECORD_CHARS = 15 * 1024 * 1024


@router.get('/vault/entries')
async def vault_keys(request: Request):
    require_permission(request, 'vault.use')
    return {'keys': vault.keys(get_db())}


@router.delete('/vault/entries')
async def erase_vault(request: Request):
    """Erasing the office vault loses every certificate in it, so only an Admin can."""
    auth = require_permission(request, 'user.manage')
    db = get_db()
    vault.clear(db)
    audit.record(db, auth['email'], 'erased', 'vault')
    return {'ok': True}


def _vault_guard(request: Request, key: str):
    require_permission(request, 'vault.use')
    return None if vault.is_vault_key(key) else error('Unknown vault record.', 400)


@router.get('/vault/entries/{key}')
async def vault_get(key: str, request: Request):
    if (refused := _vault_guard(request, key)) is not None:
        return refused
    value = vault.get(get_db(), key)
    return error('Not found.', 404) if value is None else {'value': value}


@router.put('/vault/entries/{key}')
async def vault_put(key: str, request: Request):
    if (refused := _vault_guard(request, key)) is not None:
        return refused
    body = await body_of(request)
    value = body.get('value') if isinstance(body, dict) else None
    if not isinstance(value, str) or len(value) > MAX_RECORD_CHARS:
        return error('A sealed record of 10 MB or less is required.', 400)
    vault.put(get_db(), key, value)
    return {'ok': True}


@router.delete('/vault/entries/{key}')
async def vault_delete(key: str, request: Request):
    if (refused := _vault_guard(request, key)) is not None:
        return refused
    vault.delete(get_db(), key)
    return {'ok': True}


@router.get('/audit')
async def audit_log(request: Request):
    """?scope=vault: the vault's own log, for anyone who can open the vault. Otherwise everything, for Admins."""
    vault_only = request.query_params.get('scope') == 'vault'
    require_permission(request, 'vault.use' if vault_only else 'audit.view')
    db = get_db()
    return {'entries': audit.recent(db, actions=audit.VAULT_ACTIONS) if vault_only else audit.recent(db, limit=500)}


@router.post('/audit')
async def audit_report(request: Request):
    """The vault page reports what it did; the server stamps who and when, and nobody can remove the row."""
    auth = require_permission(request, 'vault.use')
    body = await body_of(request)
    action = body.get('action') if isinstance(body, dict) else None
    if action not in audit.VAULT_ACTIONS:
        return error('Unknown action.', 400)
    detail = body.get('detail')
    audit.record(get_db(), auth['email'], action, 'vault', detail if isinstance(detail, str) else None)
    return JSONResponse({'ok': True}, status_code=201)
