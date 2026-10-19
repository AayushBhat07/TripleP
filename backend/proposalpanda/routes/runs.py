"""The two long runs, streamed as progress: analysing an uploaded tender, and drafting its foundation bid."""

import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from ..auth.deps import require_permission
from ..bid.generator import BidModelUnavailableError, generate_foundation_bid
from ..db import get_db
from ..db.repos import audit, company, tenders
from ..engine.intelligence import run_pipeline
from ..engine.text_extractor import UnreadableDocumentError
from ..fmt import compact, iso_now
from ..progress import stream_progress

log = logging.getLogger('proposalpanda')
router = APIRouter(prefix='/api')

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def error(message: str, status: int) -> JSONResponse:
    return JSONResponse({'error': message}, status_code=status)


def _pipeline_error(e: BaseException) -> dict:
    if isinstance(e, UnreadableDocumentError):
        return {'error': str(e), 'status': 422}
    # Internal details stay in the server log, not the response.
    log.error('[API] Intelligence pipeline error: %s', e)
    return {'error': 'Failed to process tender document. Please try again.', 'status': 500}


@router.post('/intelligence/run')
async def analyse(request: Request):
    auth = require_permission(request, 'tender.upload')
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - anything unparseable is the same 400
        return error('Expected a multipart form upload.', 400)
    file, tender_id, tender_title = form.get('file'), form.get('tenderId'), form.get('tenderTitle')

    # A text field named "file" is not an upload.
    if not isinstance(file, UploadFile):
        return error('No file provided. Please upload a tender document.', 400)
    if not isinstance(tender_id, str) or not isinstance(tender_title, str) or not tender_id.strip() or not tender_title.strip():
        return error('Missing tender metadata (tenderId or tenderTitle).', 400)
    if not tenders.is_tender_id(tender_id):
        return error('Invalid tender id.', 400)
    db = get_db()
    if tenders.exists(db, tender_id):
        return error('A tender with this id is already on the register.', 409)

    name = file.filename or ''
    lower = name.lower()
    if not lower.endswith('.docx') and not lower.endswith('.pdf'):
        return error('Invalid file type. Only .docx and .pdf files are supported.', 400)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return error('File must be 50MB or smaller.', 413)

    # Never build the path from client input (tenderId / file name could contain ../).
    folder = Path(tempfile.gettempdir()) / 'tender-uploads'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{uuid.uuid4()}{Path(lower).suffix}'
    path.write_bytes(data)
    log.info('[API] Processing tender: %s (%s)', tender_title, tender_id)

    async def job(on_progress):
        try:
            result = await run_pipeline(str(path), tender_id, tender_title, on_progress, verbose=True)
            log.info('[API] Pipeline completed in %dms', result['diagnostics']['totalTimeMs'])
            # On the office register before the browser hears it finished, so a closed tab loses nothing.
            stored = compact({'id': tender_id, 'fileName': name, 'uploadedAt': iso_now(), **result['report']})
            # The type comes from the checked extension, never the browser, so the file can't be served back as HTML.
            kind = 'application/pdf' if lower.endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            if not tenders.add(db, stored, auth['email'], {'name': name, 'type': kind, 'data': data}):
                raise RuntimeError(f'Tender {tender_id} was added twice')
            audit.record(db, auth['email'], 'analysed tender', tender_id)
            return stored
        finally:
            try:
                os.unlink(path)
            except OSError as e:
                log.warning('[API] Failed to delete temp file: %s', e)

    return stream_progress(job, _pipeline_error)


@router.post('/bid/generate')
async def generate_bid(request: Request):
    """Foundation bid generation: analysed tender + company profile -> bid draft from the local model."""
    auth = require_permission(request, 'bid.generate')
    try:
        body = await request.json()
    except ValueError:
        body = None
    tender_id = str((body or {}).get('tenderId') or '') if isinstance(body, dict) else ''
    db = get_db()
    # Both come from the office database, not the browser.
    tender = tenders.get(db, tender_id)
    report = tender['report'] if tender else None
    # The generator swaps GSTIN and PAN for tokens itself; the profile in the database has neither anyway.
    profile = company.get(db).get('companyProfile')

    if not report or not (report.get('summary') or {}).get('metadata') or not report.get('compliance'):
        return error('A tender analysis report is required.', 400)
    if not profile or not profile.get('legalName'):
        return error('Complete the company profile before generating a bid.', 400)

    async def job(on_progress):
        bid = await generate_foundation_bid(report, profile, on_progress)
        stored = {**bid, 'id': tender_id}
        tenders.save_bid(db, tender_id, stored, auth['email'])
        audit.record(db, auth['email'], 'drafted bid', tender_id)
        return stored

    def to_error(e: BaseException) -> dict:
        log.error('[API] Bid generation failed: %s', e)
        # Only the model-unavailable message is meant for the user; other internals stay in the server log.
        if isinstance(e, BidModelUnavailableError):
            return {'error': str(e), 'status': 503}
        return {'error': 'Failed to generate the foundation bid.', 'status': 500}

    return stream_progress(job, to_error)
