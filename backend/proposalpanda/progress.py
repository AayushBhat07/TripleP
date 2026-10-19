"""
Progress events streamed by /api/intelligence/run and /api/bid/generate. The site office animation is driven
only by these; nothing moves on a timer.

Wire format: newline-delimited JSON. Zero or more `progress` lines, then exactly one `result` or `error` line.
The browser side (readProgressStream) lives in lib/progress/stream.ts.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import StreamingResponse

log = logging.getLogger('proposalpanda')

ANALYSIS_STEPS = [
    'extract',
    'model-check',
    'executive-summary',
    'commercial-terms',
    'dates',
    'technical-scope',
    'legal',
    'attention-points',
    'eligibility-clauses',
    'compliance',
]

STEP_LABELS = {
    'extract': 'Reading the tender file',
    'model-check': 'Checking the local model',
    'executive-summary': 'Executive summary',
    'commercial-terms': 'Commercial terms',
    'dates': 'Dates and obligations',
    'technical-scope': 'Technical scope',
    'legal': 'Legal highlights',
    'attention-points': 'Points to watch',
    'eligibility-clauses': 'Eligibility and clauses',
    'compliance': 'Compliance score',
}

# A progress update: {step, status, label?, detail?}. Statuses: started, retry, done, withheld, failed.
OnProgress = Callable[[dict], None] | None


def update(step: str, status: str, label: str | None = None, detail: str | None = None) -> dict:
    out = {'step': step, 'status': status}
    if label is not None:
        out['label'] = label
    if detail:
        out['detail'] = detail
    return out


async def track_step(
    on_progress: OnProgress,
    step: str,
    label: str,
    run: Callable[[], Any],
    detail: Callable[[Any], str | None] | None = None,
):
    """Emits started, then done (with an optional detail) or failed around one pipeline step."""
    if on_progress:
        on_progress(update(step, 'started', label))
    try:
        result = run()
        if asyncio.iscoroutine(result):
            result = await result
        line = detail(result) if detail else None
        if on_progress:
            on_progress(update(step, 'done', label, line))
        return result
    except BaseException:
        if on_progress:
            on_progress(update(step, 'failed', label))
        raise


# Jobs still running after their client went away; held so they aren't garbage-collected mid-run.
_running: set[asyncio.Task] = set()


def stream_progress(
    job: Callable[[Callable[[dict], None]], Awaitable[Any]],
    to_error: Callable[[BaseException], dict],
) -> StreamingResponse:
    """
    Runs `job` and streams its progress as NDJSON, then one result or error line. `to_error` maps a raised error
    to what the user may see ({error, status}); internal messages must not leak through it. The job finishes even
    if the client goes away.
    """
    queue: asyncio.Queue = asyncio.Queue()
    start = time.monotonic()

    def on_progress(u: dict) -> None:
        queue.put_nowait({**u, 'type': 'progress', 'at': int((time.monotonic() - start) * 1000)})

    async def run() -> None:
        try:
            data = await job(on_progress)
            queue.put_nowait({'type': 'result', 'data': data})
        except Exception as error:  # noqa: BLE001 - every failure becomes one error line
            queue.put_nowait({'type': 'error', **to_error(error)})

    task = asyncio.create_task(run())
    _running.add(task)
    task.add_done_callback(_running.discard)

    async def lines():
        while True:
            line = await queue.get()
            yield (json.dumps(line, ensure_ascii=False) + '\n').encode()
            if line['type'] != 'progress':
                return

    return StreamingResponse(
        lines(),
        media_type='application/x-ndjson; charset=utf-8',
        headers={'cache-control': 'no-cache, no-transform', 'x-accel-buffering': 'no'},
    )
