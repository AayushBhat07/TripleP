"""
The ProposalPanda API. The Next.js app serves the pages and forwards /api/* here (next.config.ts), so the browser
talks to one origin and the session cookie works for both.

    uvicorn proposalpanda.main:app --port 8000
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth.deps import Denied
from .routes import office, runs

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

app = FastAPI(title='ProposalPanda API', docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(office.router)
app.include_router(runs.router)


@app.exception_handler(Denied)
async def denied(_: Request, exc: Denied):
    return exc.response


@app.exception_handler(RequestValidationError)
async def invalid(_: Request, exc: RequestValidationError):
    return JSONResponse({'error': 'Invalid request.'}, status_code=400)


@app.get('/api/health')
async def health():
    return {'ok': True}
