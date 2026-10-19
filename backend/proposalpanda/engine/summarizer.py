"""
Tender summarisation. Each summary section is produced by the local analysis model through Ollama
(AI_CONFIG.ANALYSIS_MODEL). If Ollama is unreachable, a keyword-based extractive fallback runs instead and
metadata.modelUsed says so. The facts (amounts, dates, clauses, BOQ) are read deterministically by facts.py.

Rules: faithful summarisation only; no hallucination, interpretation, judgment or scoring.
"""

import asyncio
import logging
import math
import time
from pathlib import Path

import regex

from .. import llm
from ..config import AI_CONFIG
from ..fmt import format_in, iso_now
from ..progress import STEP_LABELS, track_step, update
from . import facts as f
from .boq import find_boq
from .dates import KEY_DATE_LABELS, dates_on_portal, find_key_dates, format_key_date
from .text_extractor import UnreadableDocumentError, clean_text, extract_chapters, extract_text

log = logging.getLogger('proposalpanda')

# How much raw tender text the report keeps for bid drafting (~2.5k tokens; Llama 3 has an 8k context).
SOURCE_TEXT_CHARS = 10000
SOURCE_HEAD_CHARS = 4000
SPEC_KEYWORDS = ['specification', 'grade', 'm20', 'm25', 'm30', 'fe500', 'fe 500', 'is:', 'is ', 'morth',
                 'cpwd spec', 'rmc', 'griha', 'quality', 'testing', 'technical staff', 'plant', 'machinery']


def select_source_text(full_text: str, chunks: list[dict]) -> str:
    """NIT head (key data) plus the chunks most about specs and resources, capped for Llama 3's 8k context."""
    text = full_text[:SOURCE_HEAD_CHARS]
    # Chunks are rebuilt with single spaces, so locate them in a whitespace-collapsed copy.
    flat = regex.sub(r'\s+', ' ', full_text)
    head_end = len(regex.sub(r'\s+', ' ', text))
    for chunk in chunks:
        if len(text) >= SOURCE_TEXT_CHARS:
            break
        lower = chunk['text'].lower()
        position = flat.find(chunk['text'][:200])
        if position != -1 and position < head_end:
            continue  # starts inside the head
        if any(k in lower for k in SPEC_KEYWORDS):
            text += f"\n...\n{chunk['text']}"
    return text[:SOURCE_TEXT_CHARS]


# How many chunks fit one summary call: ~4.5k characters each against the ~21k-character prompt budget.
CHUNKS_PER_CALL = 4


def pick_chunks(chunks: list[dict], keywords: list[str], max: int = CHUNKS_PER_CALL) -> list[dict]:
    """
    The chunks most about a topic, in document order. A long tender mentions "EMD" or "date" on almost every page, so
    taking the first chunks that mention a keyword only ever reads the first pages; ranking by how many distinct
    keywords a chunk carries (and how often) finds the schedule or clause that actually sets the term.
    """
    scored = []
    for chunk in chunks:
        lower = chunk['text'].lower()
        counts = [lower.count(k) for k in keywords]
        distinct = len([c for c in counts if c > 0])
        total = sum(min(c, 5) for c in counts)
        score = distinct * 3 + total
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda s: (-s[0], s[1]['index']))
    picked = sorted((chunk for _, chunk in scored[:max]), key=lambda c: c['index'])
    return picked if picked else chunks[:max]


def _join_chunks(chunks: list[dict]) -> str:
    return '\n\n'.join(c['text'] for c in chunks)


def facts_block(facts: dict) -> str:
    """The facts the text rules already read, as a block the model is told to take as given instead of re-deriving."""
    lines = [
        facts.get('estimatedCost') and f"Estimated cost: {facts['estimatedCost']}",
        facts.get('emdAmount') and f"EMD: {facts['emdAmount']}",
        facts.get('completionMonths') and f"Completion period: {facts['completionMonths']} months",
        *[f"{KEY_DATE_LABELS[d['kind']]}: {format_key_date(d)}" for d in facts.get('keyDates') or []],
        not facts.get('keyDates') and facts.get('datesOnPortal') and 'Key dates: as per the CPP portal (not printed in the document)',
        *[f"{t['label']}: \"{t['text'][:300]}\"" for t in [*(facts.get('keyTerms') or []), *(facts.get('eligibility') or [])]],
    ]
    lines = [line for line in lines if line]
    if not lines:
        return ''
    joined = '\n- '.join(lines)
    return (
        'Facts already read from this tender. They are correct: never contradict them or call them "not specified". '
        f'Use one only where the task asks for it, and do not copy this list into the answer:\n- {joined}\n\n'
    )


PRICE_VARIATION_LINE = regex.compile(r'10\s*CC|price variation|escalation', regex.I)

# Which quoted eligibility rule a model line is about.
ELIGIBILITY_TOPIC = [
    (regex.compile(r'similar\s+work', regex.I), 'Similar works'),
    (regex.compile(r'turn\s?over', regex.I), 'Turnover'),
    (regex.compile(r'solvency', regex.I), 'Solvency'),
    (regex.compile(r'enlist|registration|registered', regex.I), 'Enlistment'),
]
# Clause numbers ("2.10A"), Indian-grouped amounts ("1,21,797") and long figures; not lists like "No. 2, 5, 12".
FIGURE = regex.compile(r'\d+(?:\.\d+)+[A-Z]?\b|\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?')
BULLET = regex.compile(r'^\s*(?:[-*•]|\d+\.)?\s*')


def _digits(s: str) -> str:
    return regex.sub(r'(\d)\s*,\s*(?=\d)', r'\1', s)


def ground_section(section: str, full_text: str, price_variation: str | None = None, eligibility: list[dict] | None = None) -> str:
    """
    Removes what a small model makes up. A line is dropped when it quotes an amount, decimal or clause number that the
    tender text does not contain ("Rs. 12,179.70" for a printed "1,21,797"; "Clause 2.10A"). Lines about Clause 10CC
    are replaced by what the tender states, or by "not specified" when it never mentions 10CC.
    """
    eligibility = eligibility or []
    source = _digits(full_text)

    def supported(figure: str) -> bool:
        escaped = _digits(figure).replace('.', r'\.')
        return regex.search(rf'(?<!\d)(?<!\d\.){escaped}(?!\d)', source) is not None

    applies = price_variation and not regex.search(r'\bnot\s+(?:be\s+)?applicable\b', price_variation, regex.I)
    out = []
    for line in section.split('\n'):
        if PRICE_VARIATION_LINE.search(line) and regex.search(r'\bappl(?:y|ies|icable)\b', line, regex.I):
            bullet = BULLET.match(line)[0] or '- '
            stated = (
                f"{'applies' if applies else 'does not apply'}. The tender: \"{price_variation}\""
                if price_variation
                else 'Not specified in the tender.'
            )
            out.append(f'{bullet}Price variation (Clause 10CC): {stated}')
            continue
        # "Similar works thresholds: Not specified" when the text rules quoted the rule.
        topic = regex.search(r'not specified', line, regex.I) and next((t for t in ELIGIBILITY_TOPIC if t[0].search(line)), None)
        rule = next((e for e in eligibility if e['label'] == topic[1]), None) if topic else None
        if rule:
            out.append(f"{BULLET.match(line)[0] or '- '}{rule['label']}: \"{rule['text']}\"")
            continue
        if all(supported(m[0]) for m in FIGURE.finditer(line)):
            out.append(line)
    return '\n'.join(out)


INSTRUCTION_KEYWORDS = [
    'EMD', 'earnest money', 'performance', 'security', 'completion', 'duration', 'period', 'defect', 'liability',
    'submission', 'validity', 'date', 'deadline', 'work', 'specification', 'technical', 'bond', 'guarantee',
    'authority', 'jurisdiction', 'contract', 'value', 'scope',
]


def _keywords_from_instruction(instruction: str) -> list[str]:
    out = []
    for keyword in INSTRUCTION_KEYWORDS:
        match = regex.search(regex.escape(keyword), instruction, regex.I)
        if match:
            out.append(match[0])
    return out


def extractive_summary(text: str, instruction: str) -> str:
    """Keyword-based extraction used when the local model is unavailable."""
    lines = [line for line in text.split('\n') if line.strip()]
    keywords = _keywords_from_instruction(instruction)
    relevant = [line for line in lines if any(k.lower() in line.lower() for k in keywords)]
    summary = ' '.join(relevant[:8])[:500]
    if not summary:
        return ' '.join(lines[:5])[:500]
    return summary


def _split_into_chunks(text: str, source: str, max_tokens: int, overlap_tokens: int) -> list[dict]:
    """Split text into manageable chunks with overlap (1 token ≈ 0.75 words)."""
    words = [w for w in regex.split(r'\s+', text) if w]
    words_per_chunk = math.floor(max_tokens * 0.75)
    overlap_words = math.floor(overlap_tokens * 0.75)
    chunks = []
    start = 0
    while start < len(words):
        chunk_words = words[start : min(start + words_per_chunk, len(words))]
        chunks.append({'index': len(chunks), 'text': ' '.join(chunk_words), 'tokenCount': math.ceil(len(chunk_words) / 0.75), 'source': source})
        # An overlap as large as the chunk would never move forward.
        start += max(1, words_per_chunk - overlap_words)
    return chunks


class TenderSummarizer:
    def __init__(self, model: str | None = None, max_tokens_per_chunk: int = 1024, overlap_tokens: int = 100):
        self.model = model or AI_CONFIG.ANALYSIS_MODEL
        self.max_tokens_per_chunk = max_tokens_per_chunk
        self.overlap_tokens = overlap_tokens
        self.use_local_model = False
        self.fallback_sections = 0
        # facts_block() for the tender being summarised, put ahead of every section's text
        self.facts = ''

    def chunk_text(self, full_text: str, chapters: list[dict] | None = None) -> list[dict]:
        """Chunk text so each model call stays inside the context window."""
        if not chapters:
            return _split_into_chunks(full_text, 'Full Text', self.max_tokens_per_chunk, self.overlap_tokens)
        chunks: list[dict] = []
        for chapter in chapters:
            parts = _split_into_chunks(chapter['content'], f"Chapter {chapter['chapterId']}: {chapter['title']}", self.max_tokens_per_chunk, self.overlap_tokens)
            base = len(chunks)
            chunks.extend([{**c, 'index': base + i} for i, c in enumerate(parts)])
        return chunks

    async def summarize_tender(self, full_text: str, tender_id: str, tender_title: str, chapters=None, on_progress=None) -> dict:
        start_time = time.monotonic()
        diagnostics = {
            'inputLength': len(full_text),
            'chunksProcessed': 0,
            'averageChunkLength': 0,
            'totalTokensProcessed': 0,
            'processingTimeMs': 0,
            'errors': [],
            'warnings': [],
        }
        try:
            chunks = self.chunk_text(full_text, chapters)
            diagnostics['chunksProcessed'] = len(chunks)
            diagnostics['totalTokensProcessed'] = sum(c['tokenCount'] for c in chunks)
            if not chunks:
                diagnostics['errors'].append('No text chunks generated from input')
                raise UnreadableDocumentError('No readable text found in the document.')
            diagnostics['averageChunkLength'] = diagnostics['totalTokensProcessed'] / len(chunks)

            # Check the local model once, then generate each section. Sequential on purpose: a local Ollama serves
            # one request at a time. An offline model fails the check but not the run: summaries fall back.
            label = STEP_LABELS['model-check']
            if on_progress:
                on_progress(update('model-check', 'started', label))
            health = await llm.check_health(self.model)
            if on_progress:
                on_progress(
                    update('model-check', 'done', label, 'Local model ready')
                    if health['available']
                    else update('model-check', 'failed', label, 'Local model offline; using extractive summaries')
                )
            self.use_local_model = health['available']
            self.fallback_sections = 0
            if not health['available']:
                diagnostics['warnings'].append(
                    f"Local model unavailable ({health.get('errorMessage')}); used extractive fallback summaries"
                )

            # The deterministic finders are CPU work over a long text; keep the event loop free while they run.
            facts = await asyncio.to_thread(_read_facts, full_text)
            self.facts = facts_block(facts)
            price_variation = next((t['text'] for t in facts['keyTerms'] if t['label'] == 'Price variation (Clause 10CC)'), None)

            def ground(section: str) -> str:
                return ground_section(section, full_text, price_variation, facts['eligibility']) if self.use_local_model else section

            async def step(id: str, run, detail=None) -> str:
                return await track_step(on_progress, id, STEP_LABELS[id], run, detail)

            executive_summary = await step('executive-summary', lambda: self._executive_summary(chunks))
            commercial_terms = await step(
                'commercial-terms',
                lambda: self._commercial_terms(chunks),
                lambda _: f"EMD: {facts['emdAmount']}" if facts['emdAmount'] else 'EMD not found',
            )
            dates_and_obligations = await step(
                'dates',
                lambda: self._dates_and_obligations(chunks),
                lambda _: f"Completion period: {facts['completionMonths']} months" if facts['completionMonths'] else 'Completion period not found',
            )
            technical_scope = await step('technical-scope', lambda: self._technical_scope(chunks))
            legal_highlights = await step('legal', lambda: self._legal_highlights(chunks))
            attention_points = await step('attention-points', lambda: self._attention_points(chunks))
            eligibility_and_clauses = await step('eligibility-clauses', lambda: self._eligibility_and_clauses(chunks))

            processing_time_ms = int((time.monotonic() - start_time) * 1000)
            diagnostics['processingTimeMs'] = processing_time_ms
            if not self.use_local_model:
                model_used = 'extractive-fallback'
            elif self.fallback_sections > 0:
                model_used = f'{self.model} (+{self.fallback_sections} extractive)'
            else:
                model_used = self.model

            summary = {
                'executiveSummary': ground(executive_summary),
                'commercialTerms': ground(commercial_terms),
                'datesAndObligations': ground(dates_and_obligations),
                'technicalScope': ground(technical_scope),
                'legalHighlights': ground(legal_highlights),
                'attentionPoints': ground(attention_points),
                'eligibilityAndClauses': ground(eligibility_and_clauses),
                'sourceText': select_source_text(full_text, chunks),
                'metadata': {
                    'tenderId': tender_id,
                    'tenderTitle': tender_title,
                    'generatedAt': iso_now(),
                    'nitReference': facts['nitReference'],
                    'completionMonths': facts['completionMonths'],
                    'emdAmount': facts['emdAmount'],
                    'estimatedCost': facts['estimatedCost'],
                    'delayCompensation': facts['delayCompensation'],
                    'invitingOffice': facts['invitingOffice'],
                    'keyTerms': facts['keyTerms'],
                    'eligibility': facts['eligibility'],
                    'guaranteeConflict': facts['guaranteeConflict'],
                    'riskClauses': facts['riskClauses'],
                    'tenderKind': facts['tenderKind'],
                    'standards': facts['standards'],
                    'nameOfWork': facts['nameOfWork'],
                    'boq': facts['boq'],
                    'keyDates': facts['keyDates'],
                    'datesOnPortal': facts['datesOnPortal'],
                    'worksType': facts['worksType'],
                    'modelUsed': model_used,
                    'totalChunks': len(chunks),
                    'processingTimeMs': processing_time_ms,
                },
            }
            return {'summary': summary, 'diagnostics': diagnostics}
        except Exception as error:
            diagnostics['errors'].append(str(error))
            raise

    async def _executive_summary(self, chunks: list[dict]) -> str:
        relevant = [c for c in chunks if any(f'Chapter 0{n}' in c['source'] for n in (1, 2, 3))][:5]
        text = '\n\n'.join(c['text'] for c in (relevant or chunks[:3]))
        return await self._summarize(
            text, 'Extract: project name, scope of work, executing authority, location, contract value, duration. Be factual and concise.'
        )

    async def _commercial_terms(self, chunks: list[dict]) -> str:
        text = _join_chunks(pick_chunks(chunks, ['emd', 'earnest money', 'bid security', 'estimated cost', 'performance guarantee',
                                                 'performance security', 'security deposit', 'completion', 'time allowed', 'defect liability']))
        return await self._summarize(
            text, 'Extract: EMD amount, performance guarantee, completion period, defect liability, tender type. List factually.'
        )

    async def _dates_and_obligations(self, chunks: list[dict]) -> str:
        text = _join_chunks(pick_chunks(chunks, ['last date', 'due date', 'submission', 'opening', 'bid validity', 'validity',
                                                 'pre-bid', 'deadline', 'hrs', 'time']))
        return await self._summarize(text, 'Extract: submission requirements, bid validity, extension obligations, key dates. Be specific.')

    async def _technical_scope(self, chunks: list[dict]) -> str:
        # The name of work (first chunk) plus the chunks most about what is to be built or supplied.
        picked = pick_chunks(chunks, ['scope of work', 'name of work', 'specification', 'schedule of quantities', 'bill of quantities',
                                      'boq', 'supply of', 'grade', 'is:', 'is code', 'make', 'installation'], CHUNKS_PER_CALL - 1)
        text = _join_chunks([chunks[0], *[c for c in picked if c is not chunks[0]]])
        return await self._summarize(
            text,
            'Extract: nature of the work, service or supply; the major items or work categories the tender itself names (do not add '
            'categories it does not name); quantities, grades and specifications as printed; execution scope. Describe factually, do not judge.',
        )

    async def _legal_highlights(self, chunks: list[dict]) -> str:
        text = _join_chunks(pick_chunks(chunks, ['bank guarantee', 'guarantee', 'bond', 'indemnity', 'jurisdiction', 'arbitration',
                                                 'dispute', 'termination', 'blacklist', 'debar', 'force majeure', 'penalty']))
        return await self._summarize(
            text, 'Extract: bond requirements, guarantee obligations, authority hierarchy, jurisdiction references. State what the tender requires.'
        )

    async def _attention_points(self, chunks: list[dict]) -> str:
        """FACTUAL ONLY: not a risk assessment."""
        text = _join_chunks(pick_chunks(chunks, ['shall be liable', 'forfeit', 'penalty', 'liquidated damages', 'compensation',
                                                 'at his own cost', 'no claim', 'not be entertained', 'rejected', 'debar', 'mandatory', 'monsoon']))
        return await self._summarize(
            text,
            'Extract: long execution periods, high security requirements, extensive technical scope, any repeated obligations. FACTUAL ONLY. '
            'Use phrasing like "The tender specifies..." or "The contractor is obligated to...". Do NOT use "should" or "may be risky".',
        )

    async def _eligibility_and_clauses(self, chunks: list[dict]) -> str:
        """Eligibility criteria and the contract clauses that decide whether and how to bid."""
        keywords = ['eligib', 'similar work', 'turnover', 'solvency', 'bid capacity', 'compensation for delay', 'clause 2', '10cc',
                    '10 cc', 'price variation', 'escalation', 'mobilisation', 'mobilization', 'secured advance', 'security deposit',
                    'performance guarantee', 'arbitration', 'dispute']
        return await self._summarize(
            _join_chunks(pick_chunks(chunks, keywords)),
            'Extract as bullet points: eligibility criteria (similar works thresholds, average annual turnover, solvency, bid capacity formula); '
            'compensation for delay and its cap; price variation / escalation clause (e.g. 10CC) and whether it applies; '
            'mobilisation or secured advance; security deposit (how it is recovered, and separately when it is refunded); '
            'performance guarantee (amount, deadline and any extension period); dispute resolution, arbitration and venue. '
            'Quote amounts, percentages and clause numbers exactly as written. For each clause, write "applies" or '
            '"does not apply" exactly as the tender states. Do not list documents to upload. Keep it under 200 words.',
            800,
        )

    async def _summarize(self, text: str, instruction: str, max_tokens: int = 600) -> str:
        """Summarise one section with the local model; fall back to extraction on any failure."""
        if self.use_local_model:
            # ~4 chars per token; leave room in the context window for the prompt and answer.
            budget = (AI_CONFIG.CONTEXT_TOKENS - 1024) * 3 - len(self.facts)
            result = await llm.generate_safe(
                model=self.model,
                system_prompt=(
                    'You summarise Indian public-works tender documents for a contractor. '
                    'Only state facts found in the provided text. If something is not in the text, say "Not specified in the tender." '
                    'Quote every amount, percentage, period, clause number and named specification (e.g. M25, Fe500D, MoRTH) exactly as written. '
                    'Answer in plain prose or short bullet points, under 250 words, with no preamble.'
                ),
                user_prompt=f'Task: {instruction}\n\n{self.facts}Tender text:\n{text[: max(0, budget)]}',
                temperature=0.1,
                max_tokens=max_tokens,
            )
            if result['success'] and result['data']['content']:
                return result['data']['content']
            self.fallback_sections += 1
            log.warning(
                '[Summarization] Local model call failed, using extractive fallback: %s',
                'empty response' if result['success'] else result['error']['message'],
            )
        return extractive_summary(text, instruction)


def _read_facts(text: str) -> dict:
    name_of_work = f.find_name_of_work(text)
    return {
        'estimatedCost': f.find_estimated_cost(text),
        'emdAmount': f.find_emd_amount(text),
        'completionMonths': f.find_completion_months(text),
        'keyDates': find_key_dates(text),
        'datesOnPortal': dates_on_portal(text),
        'keyTerms': f.find_key_terms(text),
        'eligibility': f.find_eligibility(text),
        'nitReference': f.find_nit_reference(text),
        'delayCompensation': f.find_delay_compensation(text),
        'invitingOffice': f.find_inviting_office(text),
        'guaranteeConflict': f.find_guarantee_conflict(text),
        'riskClauses': f.find_risk_clauses(text),
        'tenderKind': f.find_tender_kind(text),
        'standards': f.find_standards(text),
        'nameOfWork': name_of_work,
        'boq': find_boq(text),
        'worksType': f.find_works_type(name_of_work, text),
    }


async def summarize_text(full_text: str, tender_id: str, tender_title: str, on_progress=None) -> dict:
    cleaned = clean_text(full_text)
    chapters = extract_chapters(cleaned)
    return await TenderSummarizer().summarize_tender(cleaned, tender_id, tender_title, chapters if len(chapters) > 1 else None, on_progress)


async def summarize_file(file_path: str, tender_id: str, tender_title: str, on_progress=None) -> dict:
    """Summarise a .pdf, .docx, .txt or .md tender."""
    ext = Path(file_path).suffix.lower()

    def read() -> dict:
        if ext not in ('.docx', '.pdf', '.txt', '.md'):
            raise ValueError(f'Unsupported file format: {ext}')
        text = clean_text(extract_text(file_path))
        return {'fullText': text, 'chapters': extract_chapters(text)}

    extracted = await track_step(
        on_progress,
        'extract',
        STEP_LABELS['extract'],
        lambda: asyncio.to_thread(read),
        lambda r: format_in(len([w for w in regex.split(r'\s+', r['fullText']) if w])) + ' words read',
    )
    chapters = extracted['chapters']
    log.info('Extracted %d chapters from %s', len(chapters), tender_title)
    return await TenderSummarizer().summarize_tender(
        extracted['fullText'], tender_id, tender_title, chapters if len(chapters) > 1 else None, on_progress
    )
