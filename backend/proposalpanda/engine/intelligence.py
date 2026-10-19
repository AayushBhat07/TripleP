"""
The analysis pipeline: summarise the tender (summarizer.py), then score compliance on that summary (compliance.py).
Coordination only: no model logic, scoring or parsing of its own.
"""

import logging
import time

from ..fmt import iso_now
from ..progress import STEP_LABELS, track_step
from .compliance import analyze_compliance
from .summarizer import summarize_file
from .text_extractor import UnreadableDocumentError

log = logging.getLogger('proposalpanda')


async def run_pipeline(file_path: str, tender_id: str, tender_title: str, on_progress=None, verbose: bool = False) -> dict:
    """{report: {summary, compliance, metadata}, diagnostics}."""
    started = time.monotonic()
    warnings: list[str] = []
    errors: list[str] = []
    try:
        if verbose:
            log.info('Analysing tender %s (%s) from %s', tender_title, tender_id, file_path)
        phase_a = time.monotonic()
        summarised = await summarize_file(file_path, tender_id, tender_title, on_progress)
        phase_a_ms = int((time.monotonic() - phase_a) * 1000)
        warnings += [f'[Phase 4A] {w}' for w in summarised['diagnostics']['warnings']]
        errors += [f'[Phase 4A] {e}' for e in summarised['diagnostics']['errors']]

        phase_b = time.monotonic()
        compliance = await track_step(
            on_progress,
            'compliance',
            STEP_LABELS['compliance'],
            lambda: analyze_compliance(summarised['summary'], verbose=verbose),
            lambda r: f"Risk level: {r['score']['riskLevel']}, score {r['score']['complianceScore']}/100",
        )
        phase_b_ms = int((time.monotonic() - phase_b) * 1000)
        warnings += [f'[Phase 4B] {w}' for w in compliance['diagnostics']['warnings']]
        errors += [f'[Phase 4B] {e}' for e in compliance['diagnostics']['errors']]

        total_ms = int((time.monotonic() - started) * 1000)
        report = {
            'summary': summarised['summary'],
            'compliance': compliance['score'],
            'metadata': {'generatedAt': iso_now(), 'pipelineVersion': '4A+4B', 'executionTimeMs': total_ms},
        }
        if verbose:
            log.info(
                'Pipeline complete in %dms (4A %dms, 4B %dms): score %d/100, risk %s',
                total_ms, phase_a_ms, phase_b_ms, report['compliance']['complianceScore'], report['compliance']['riskLevel'],
            )
        return {
            'report': report,
            'diagnostics': {
                'phase4ATimeMs': phase_a_ms,
                'phase4BTimeMs': phase_b_ms,
                'totalTimeMs': total_ms,
                'executionOrder': ['Phase 4A', 'Phase 4B'],
                'warnings': warnings,
                'errors': errors,
            },
        }
    except UnreadableDocumentError:
        # The route shows this one to the user as is.
        raise
    except Exception as error:
        errors.append(f'[Pipeline] {error}')
        raise RuntimeError(
            f"Intelligence pipeline failed: {error}\nWarnings: {'; '.join(warnings)}\nErrors: {'; '.join(errors)}"
        ) from error
