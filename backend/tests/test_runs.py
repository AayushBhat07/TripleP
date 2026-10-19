"""The two streamed runs: progress events, the analysis pipeline and the bid draft, with Ollama stubbed out."""

import asyncio
import json

import httpx
import pytest

from files import write_pdf

from proposalpanda import llm
from proposalpanda.bid.generator import generate_foundation_bid
from proposalpanda.engine.intelligence import run_pipeline
from proposalpanda.engine.profile import last_five_years
from proposalpanda.progress import ANALYSIS_STEPS, stream_progress

TENDER = '\n'.join([
    'NOTICE INVITING TENDER',
    'NIT No: 50/Civil/D1/2026-27',
    'Name of work: Construction of hostel block including RCC frame, masonry and finishes',
    'Estimated cost put to tender: Rs. 1,56,48,695/-',
    'Earnest Money Deposit (Rs.) : Rs. 3,12,974/-',
    'Period of completion: 18 (Eighteen) months',
    'Compensation for delay (Clause 2): 1.5% per month, maximum 10%.',
    'Eligibility: three similar works of Rs. 62.59 lakh each in the last seven years.',
])

PROFILE = {
    'legalName': 'Himgiri Buildcon Private Limited',
    'registeredAddress': 'Parwanoo, District Solan, Himachal Pradesh',
    'registrationAuthority': 'CPWD',
    'registrationClass': 'Class I (Civil)',
    'registrationNumber': 'ENL/HP/CIV/2019/0482',
    'signatoryName': 'Rajesh Kumar Thakur',
    'signatoryDesignation': 'Director',
    'turnover': [{'year': y, 'lakh': lakh} for y, lakh in zip(last_five_years(), [6894, 6127, 5280, 4615, 3842])],
    'worksInHandLakh': 4200,
    'similarWorks': [{'name': 'Administrative Block, Sundernagar', 'client': 'HP PWD', 'valueLakh': 2208, 'start': '04/2021', 'completion': '09/2022'}],
    'keyStaff': [{'name': 'Anil Sharma', 'qualification': 'B.E. Civil', 'years': 18}],
    'machinery': 'Concrete batching plant 30 cum/hr',
}


@pytest.fixture
def ollama(monkeypatch):
    """Stands in for Ollama. Set .reply to a function of (prompt, n) answering the nth generate call; .models lists what is pulled."""
    state = type('Ollama', (), {})()
    state.reply = lambda prompt, n: '- Summary line.'
    state.models = ['llama3:latest', 'qwen2.5:3b-instruct']
    state.calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/api/tags'):
            return httpx.Response(200, json={'models': [{'name': m} for m in state.models]})
        body = json.loads(request.content)
        n, state.calls = state.calls, state.calls + 1
        return httpx.Response(200, json={'model': body['model'], 'response': state.reply(body['prompt'], n), 'done': True, 'eval_count': 1})

    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    return state


async def analyse(tmp_path, on_progress=None) -> dict:
    file = tmp_path / 'nit.txt'
    file.write_text(TENDER)
    return await run_pipeline(str(file), 'T-1', 'Hostel', on_progress)


async def read_stream(response) -> list[dict]:
    return [json.loads(line) async for chunk in response.body_iterator for line in chunk.decode().splitlines()]


async def test_stream_sends_progress_lines_then_the_result():
    async def job(on_progress):
        on_progress({'step': 'extract', 'status': 'started', 'label': 'Text extraction'})
        on_progress({'step': 'extract', 'status': 'done', 'detail': '10 words read'})
        return {'ok': 1, 'text': 'héllo ₹'}

    response = stream_progress(job, lambda e: {'error': 'x', 'status': 500})
    assert response.headers['content-type'] == 'application/x-ndjson; charset=utf-8'
    assert response.headers['cache-control'] == 'no-cache, no-transform'
    assert response.headers['x-accel-buffering'] == 'no'
    lines = await read_stream(response)
    assert [(e['type'], e.get('step'), e.get('status')) for e in lines] == [
        ('progress', 'extract', 'started'), ('progress', 'extract', 'done'), ('result', None, None),
    ]
    assert lines[1]['detail'] == '10 words read'
    assert all(isinstance(e['at'], int) and e['at'] >= 0 for e in lines[:2])
    assert lines[2]['data'] == {'ok': 1, 'text': 'héllo ₹'}


async def test_a_failed_job_becomes_one_error_line_without_internal_details():
    async def job(on_progress):
        raise RuntimeError('internal secret')

    response = stream_progress(job, lambda e: {'error': 'Failed to process tender document. Please try again.', 'status': 500})
    lines = await read_stream(response)
    assert lines == [{'type': 'error', 'error': 'Failed to process tender document. Please try again.', 'status': 500}]


async def test_the_analysis_pipeline_reports_every_step_in_order(tmp_path, ollama):
    ollama.reply = lambda prompt, n: '- The tender asks for an EMD of Rs. 3,12,974.'
    updates = []
    await analyse(tmp_path, updates.append)
    assert [f"{u['step']}:{u['status']}" for u in updates] == [f'{s}:{status}' for s in ANALYSIS_STEPS for status in ('started', 'done')]

    def detail(step):
        return next((u.get('detail') for u in updates if u['step'] == step and u['status'] == 'done'), None)

    assert detail('model-check') == 'Local model ready'
    assert detail('commercial-terms') == 'EMD: Rs. 3,12,974'
    assert detail('dates') == 'Completion period: 18 months'
    assert detail('compliance').startswith('Risk level: ')
    assert all(u.get('label') for u in updates)


async def test_the_bid_reports_each_section_retries_and_withheld_drafts(tmp_path, ollama):
    report = (await analyse(tmp_path))['report']
    # Every model draft is rejected twice: each model section retries once, then is withheld.
    ollama.reply = lambda prompt, n: 'Our company has completed similar works in the past, meeting the eligibility criteria.'
    updates = []
    bid = await generate_foundation_bid(report, PROFILE, updates.append)
    assert [f"{u['step']}:{u['status']}" for u in updates[:2]] == ['model-check:started', 'model-check:done']
    by_section: dict[str, list[str]] = {}
    for u in updates[2:]:
        by_section.setdefault(u['step'], []).append(u['status'])
    assert list(by_section) == [f"section:{s['id']}" for s in bid['sections']]
    for s in bid['sections']:
        expected = ['started', 'retry', 'withheld'] if s['source'] == 'model' else ['started', 'done']
        assert by_section[f"section:{s['id']}"] == expected, s['id']
    assert any(s['source'] == 'model' for s in bid['sections'])
    titles = {f"section:{s['id']}": s['title'] for s in bid['sections']}
    assert all(u['label'] == titles[u['step']] for u in updates[2:])

    # A model that is down fails the model check.
    ollama.models = []
    updates = []
    with pytest.raises(Exception, match='not available'):
        await generate_foundation_bid(report, PROFILE, updates.append)
    assert [f"{u['step']}:{u['status']}" for u in updates] == ['model-check:started', 'model-check:failed']


def stream_lines(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines()]


def test_upload_analyse_and_draft_through_the_api(tmp_path, ollama, as_role):
    analyst = as_role('TenderAnalyst')
    pdf = write_pdf(tmp_path / 'nit.pdf', [TENDER.split('\n')]).read_bytes()
    form = {'tenderId': 'T-API', 'tenderTitle': 'Hostel'}

    assert as_role('Viewer').post('/api/intelligence/run', data=form, files={'file': ('nit.pdf', pdf)}).status_code == 403
    assert analyst.post('/api/intelligence/run', data=form, files={'file': ('nit.exe', pdf)}).status_code == 400
    assert analyst.post('/api/intelligence/run', data={**form, 'tenderId': '../x'}, files={'file': ('nit.pdf', pdf)}).status_code == 400

    lines = stream_lines(analyst.post('/api/intelligence/run', data=form, files={'file': ('NIT 50.pdf', pdf)}))
    assert lines[-1]['type'] == 'result'
    assert lines[-1]['data']['fileName'] == 'NIT 50.pdf'
    assert [e['step'] for e in lines if e['type'] == 'progress' and e['status'] == 'done'] == ANALYSIS_STEPS
    stored = analyst.get('/api/tenders/T-API').json()
    assert stored['hasFile'] is True
    assert stored['report']['summary']['metadata']['emdAmount'] == 'Rs. 3,12,974'
    assert analyst.post('/api/intelligence/run', data=form, files={'file': ('nit.pdf', pdf)}).status_code == 409

    writer = as_role('BidWriter')
    assert writer.post('/api/bid/generate', json={'tenderId': 'T-API'}).status_code == 400, 'no company profile yet'
    writer.put('/api/company', json={'companyProfile': PROFILE, 'isComplete': True})
    assert analyst.post('/api/bid/generate', json={'tenderId': 'T-API'}).status_code == 403
    lines = stream_lines(writer.post('/api/bid/generate', json={'tenderId': 'T-API'}))
    assert lines[-1]['type'] == 'result'
    assert writer.get('/api/tenders/T-API').json()['bid']['id'] == 'T-API'

    ollama.models = []
    lines = stream_lines(writer.post('/api/bid/generate', json={'tenderId': 'T-API'}))
    assert lines[-1]['type'] == 'error' and lines[-1]['status'] == 503
