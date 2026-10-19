"""
Foundation bid generation. Follows the Indian two-bid structure (CPWD / state PWD): Cover I technical bid with letter
of transmittal, document checklist, declarations, eligibility and bid capacity, scope, methodology, compliance and
pre-bid queries; Cover II price bid. Goods (GeM / store purchase) and services tenders get the same split with their
own methodology, eligibility proforma and queries, and no construction programme. Narrative sections come from the
local Llama 3 model (AI_CONFIG.MODEL_NAME); standard proformas come from templates.py.
"""

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Callable

import regex

from .. import llm
from ..config import AI_CONFIG
from ..engine.profile import experience_line
from ..fmt import iso_now
from ..progress import STEP_LABELS, track_step, update
from .templates import TEMPLATE_SECTIONS, template

log = logging.getLogger('proposalpanda')
I = regex.I

# GSTIN and PAN stay in the browser's vault: the bid carries these tokens, and the page fills in the real values at
# download time (lib/vault/bidVault.ts).
GSTIN_TOKEN = '{GSTIN}'
PAN_TOKEN = '{PAN}'


@dataclass
class DraftFacts:
    """Tender facts the draft checks compare against."""

    clauses: str
    nit_ref: str
    tender_title: str
    pan: str
    gstin: str
    months: int | None = None
    emd_amount: str | None = None
    delay_compensation: str | None = None
    inviting_office: str | None = None
    # Whether the scope includes a road, so the programme must schedule it
    has_road: bool = False
    # IS code numbers that appear in the tender text or analysis
    is_codes: set = field(default_factory=set)
    # Key contract terms quoted from the tender text
    key_terms: list = field(default_factory=list)
    source_text: str = ''
    kind: str = 'works'


# Methodology items in build order, each kept only when the tender text names it.
METHOD_ITEMS = [
    ('earthwork and foundations', r'earth ?work|excavation|foundation|footing|pile|embankment'),
    ('RCC superstructure (concrete grade, steel grade)', r'\bRCC\b|reinforced cement concrete|superstructure|column|slab'),
    ('masonry', r'masonry|brick ?work|block ?work|\bAAC\b'),
    ('flooring and finishes', r'flooring|tiles?\b|plaster|painting|false ceiling|finishes'),
    ('waterproofing', r'water ?proofing'),
    ('water supply', r'water supply'),
    ('sanitary installations', r'sanitary|plumbing|manhole'),
    ('electrical installations', r'electrical|wiring'),
    ('roads', r'\broad\b|carriageway|\bGSB\b|\bWMM\b|\bDBM\b|bituminous|paver'),
    ('bridges and culverts', r'bridge|\bROB\b|\bRUB\b|culvert|girder|\bpier\b|abutment'),
    ('drainage', r'drain|hume pipe|\bNP-?\s?[234]\b|cross drainage'),
    ('GRIHA / green-building measures', r'GRIHA|green[- ]building'),
]
# When the text names no item (a one-page notice), the common building items; GRIHA only if the tender asks.
METHOD_LIST = '; '.join(item for item, _ in METHOD_ITEMS if not item.startswith('GRIHA'))

# Items that belong to roads, bridges, drains and walls; a building's items there come from general conditions.
INFRASTRUCTURE_ITEMS = {'earthwork and foundations', 'roads', 'bridges and culverts', 'drainage', 'GRIHA / green-building measures'}


def methodology_items(tender_text: str, works_type: str | None = None, name_of_work: str = '') -> str:
    """The methodology items this tender names, in build order (the common ones when the text names none)."""
    named = [
        item
        for item, pattern in METHOD_ITEMS
        if regex.search(pattern, tender_text, I)
        and (works_type != 'infrastructure' or item in INFRASTRUCTURE_ITEMS or (item == 'masonry' and regex.search(r'\bwall\b', name_of_work, I)))
    ]
    return '; '.join(named) if named else METHOD_LIST


def fit_brief_to_tender(brief: str, tender_text: str) -> str:
    """GRIHA, RMC and road terms appear in the briefs only when the tender itself mentions them."""
    out = brief
    if not regex.search(r'GRIHA|green[- ]building', tender_text, I):
        out = regex.sub(r';?\s*GRIHA / green-building measures', '', out)
        out = regex.sub(r', GRIHA(?= and|,)', '', out)
        out = regex.sub(r'\bGRIHA, ', '', out)
    if not regex.search(r'\bRMC\b|ready[- ]mix', tender_text, I):
        out = regex.sub(r',? ?RMC plant approval', '', out)
    if not regex.search(r'\broad\b|carriageway|\bGSB\b|\bWMM\b', tender_text, I):
        out = regex.sub(
            r' ?"Earthwork and foundations" means the building foundations, not the road; GSB, WMM, DBM and BC belong only to the road\.',
            '',
            out,
            count=1,
        )
    out = regex.sub(r'\s*\(e\.g\.\s*\)', '', out)
    out = regex.sub(r'\s+\(e\.g\.\s*,\s*', ' (e.g. ', out)
    return regex.sub(r'\s+,', ',', out)


def drop_lines(content: str, pattern) -> str:
    """Removes lines that match, then renumbers the numbered ones 1, 2, 3..."""
    n = 0

    def renumber(m) -> str:
        nonlocal n
        n += 1
        return f'{m[1]}{n}{m[2]}'

    kept = [line for line in content.split('\n') if not regex.search(pattern, line)]
    return '\n'.join(regex.sub(r'^(\s*)\d+([.)]\s)', renumber, line, count=1) for line in kept)


# Contract terms the compliance block quotes from the tender, so the model's own lines on them are dropped.
QUOTED_TERMS = regex.compile(
    r'security deposit|mobili[sz]ation|secured advance|compensation for delay|10\s*CC|price variation|escalation|defect liability|\bcess\b|^\W*\[as per enclosed registration certificates\]\W*$',
    I,
)

# Pre-bid queries that misquote the tender or ask what it already answers.
BAD_QUERY = regex.compile(
    r'^(?=.*10\s*CC)(?!.*(?:price|escalat|variation))|security deposit.{0,60}recover|recover.{0,60}security deposit|late fee|provision for (?:any )?(?:price variation|escalation)|how (?:will|shall|should) the contractor|absence of|\black(?:s|ing)? (?:a|any|clear)|not (?:clearly )?(?:mentioned|specified|provided|defined)|does not (?:include|contain|mention|specify|provide)',
    I,
)

IS_CODE = regex.compile(r'\bIS[:\s]*(\d{3,5})(?:\s*\(?\s*(?:Part|Pt\.?)\s*\d+\s*\)?)?(?:\s*[:\-–]\s*\d{4})?')


def is_codes_in(text: str) -> set[str]:
    """IS code numbers mentioned in a text, e.g. "IS 2185 (Part 3)" -> "2185"."""
    return {m[1] for m in IS_CODE.finditer(text)}


GRADE = regex.compile(r'\b(?:M\s?[-‐–]?\s?(\d{2})|Fe\s?[-‐–]?\s?(\d{3})\s?(?:D)?)\b')


def grades_in(text: str) -> set[str]:
    """Concrete and steel grades mentioned in a text, e.g. "M25" -> "M25", "Fe 500D" -> "Fe500"."""
    return {f'M{m[1]}' if m[1] else f'Fe{m[2]}' for m in GRADE.finditer(text)}


def redact_unknown_grades(content: str, known: set[str]) -> str:
    """Replaces concrete / steel grades the tender never mentions ("M25", "Fe 500D" in a tender that has neither)."""

    def swap(m) -> str:
        grade = f'M{m[1]}' if m[1] else f'Fe{m[2]}'
        if grade in known:
            return m[0]
        return '[concrete grade as per tender]' if m[1] else '[steel grade as per tender]'

    return GRADE.sub(swap, content)


def redact_unknown_standards(content: str, known: set[str]) -> str:
    """Replaces IS codes the tender never mentions, so a plausible but wrong standard can't slip into the bid."""
    return IS_CODE.sub(lambda m: m[0] if m[1] in known else '[IS code as per tender]', content)


# Lines of the clause summary the compliance statement must carry verbatim.
KEY_CLAUSE = regex.compile(r'clause\s*2\b|compensation for delay|10\s*CC|price variation|escalation|\badvance\b|security deposit', I)


def key_clause_lines(clauses: str, delay_compensation: str | None = None) -> list[str]:
    """
    Key contract clauses copied from the analysis, so the compliance statement can't drop or flip them.
    A heading keeps its value lines: indented lines, or the line after a heading that ends with ":".
    """
    groups: list[dict] = []
    for raw in clauses.split('\n'):
        if not raw.strip():
            continue
        indent = len(regex.match(r'\s*', raw)[0])
        text = regex.sub(r'^\s*(?:[-*•]|\d+[.)])\s*', '', raw, count=1).strip()
        current = groups[-1] if groups else None
        if current and (indent > current['indent'] or (current['open'] and not text.endswith(':'))):
            current['parts'].append(text)
            current['open'] = False
        else:
            groups.append({'indent': indent, 'parts': [text], 'open': text.endswith(':')})
    lines = [line for line in (' '.join(g['parts']) for g in groups) if KEY_CLAUSE.search(line)]
    if delay_compensation and not any(delay_compensation.split(' ')[0] in line for line in lines):
        lines.insert(0, f'Compensation for delay (Clause 2): {delay_compensation}')
    return lines


# Order the trades must start in; each must not begin before the one listed before it.
BUILD_ORDER = [
    ('foundations', r'foundation|footing|excavation'),
    ('RCC frame', r'rcc|frame|superstructure|slab'),
    ('masonry', r'\baac\b|block ?work|brick ?work|masonry (?:walls?|work)'),
    ('flooring and finishes', r'flooring|tiling|tiles|finish|painting'),
]

ROAD_WORK = regex.compile(r'road|carriageway|pavement|bituminous', I)


def _first_month(plan: dict[int, str], pattern: str) -> int | None:
    """First month in the programme whose activities match."""
    months = [m for m, a in plan.items() if regex.search(pattern, a, I)]
    return min(months) if months else None


def parse_programme(draft: str) -> dict[int, str]:
    """
    Month -> activities from a draft programme. Accepts "Month 3:", "Months 2-4:", "Month 16 to 18 -" and table rows
    like "| Month 1-3 | ... |"; ranges are expanded and the defect liability period is dropped.
    """
    by_month: dict[int, str] = {}
    for line in draft.split('\n'):
        m = regex.search(r'Months?\s*(\d{1,2})(?:\s*(?:-|–|to)\s*(?:Months?\s*)?(\d{1,2}))?\s*[:|\-–]\s*(.+)', line, I)
        if not m:
            continue
        activities = regex.sub(r'\s*\((?:approx\w*|about)[^)]*\)', '', m[3], flags=I)
        activities = regex.sub(r'\s*\|\s*', '; ', activities)
        activities = regex.sub(r'[,;]?\s*(?:and )?defect liability period[^,;.]*', '', activities, flags=I)
        activities = regex.sub(r'^[,;]\s*|[,;]$', '', activities.strip())
        if not activities:
            continue
        start = int(m[1])
        end = int(m[2]) if m[2] else start
        for month in range(start, end + 1):
            by_month.setdefault(month, activities)
    return by_month


def render_programme(draft: str, months: int | None, has_road: bool = False) -> str:
    """One line per month from Month 1 to the completion month; months the draft skipped are left to plan."""
    by_month = parse_programme(draft)
    last = months if months is not None else max([0, *by_month.keys()])
    # Llama 3 tends to drop the external works; schedule them in the last three months rather than lose them.
    if has_road and not any(ROAD_WORK.search(a) for a in by_month.values()):
        for month in range(max(1, last - 2), last + 1):
            current = by_month.get(month)
            prefix = regex.sub(r'\.$', '', current) + '; ' if current else ''
            by_month[month] = f'{prefix}approach road and drainage works (outside monsoon)'
    lines = [f"Month {i + 1}: {by_month.get(i + 1, '[activities to be planned]')}" for i in range(last)]
    return '\n'.join([*lines, '', 'The defect liability period runs from the date of completion and is not part of this programme.'])


@dataclass
class ModelSection:
    """
    A section the model drafts. needs_source: also give it the selected raw tender text. clean: deterministic cleanup
    before the checks. check: returns a problem description, or None. finish: turns the checked draft into the text.
    """

    id: str
    title: str
    cover: str
    brief: str
    needs_source: bool = False
    max_tokens: int | None = None
    clean: Callable[[str], str] | None = None
    check: Callable[[str, DraftFacts], str | None] | None = None
    finish: Callable[[str, DraftFacts], str] | None = None


def _transmittal_check(content: str, facts: DraftFacts) -> str | None:
    emd = facts.emd_amount
    if emd and emd.startswith('Rs.') and regex.sub(r'^Rs\.\s*', '', emd, count=1) not in regex.sub(r'\s', '', content):
        return 'it does not state the EMD amount'
    return None


def _transmittal_finish(content: str, facts: DraftFacts) -> str:
    if facts.inviting_office:
        parts = regex.split(r',\s*', f'The {facts.inviting_office}')
        address = [f'{part},' if i < len(parts) - 1 else part for i, part in enumerate(parts)]
    elif facts.kind == 'works':
        address = ['The Executive Engineer,', '[Division and address as per NIT]']
    else:
        address = ['[Designation of the tender inviting authority],', '[Address as per NIT]']
    body = regex.sub(r'^\s*(?:To,?[\s\S]*?)?(?=(?:Dear )?Sir)', '', content, count=1, flags=I)
    return '\n'.join(['To,', *address, '', f'Sub: Submission of bid for "{facts.tender_title}" against NIT No. {facts.nit_ref}', '', body])


def _methodology_check(content: str, facts: DraftFacts) -> str | None:
    depth = regex.search(r'depth of (\d+(?:\.\d+)?)\s*m', content, I)
    return f'it invents a depth ("{depth[0]}")' if depth and depth[1] not in facts.source_text else None


def _plans_too_few(plan: dict, months: int | None) -> str | None:
    if len(plan) < math.ceil((months if months is not None else 2) / 2):
        return f'it plans only {len(plan)} month(s)'
    return None


def _programme_check(content: str, facts: DraftFacts) -> str | None:
    plan = parse_programme(content)
    if problem := _plans_too_few(plan, facts.months):
        return problem
    idle = len(
        [
            a
            for a in plan.values()
            if regex.search(r'^[^;]*\b(?:testing|handover|commissioning)\b[^;]*$', a, I) and not regex.search(r'finish|work|install', a, I)
        ]
    )
    if idle > 2:
        return f'it leaves {idle} months for testing and handover only'
    previous = None
    for trade, pattern in BUILD_ORDER:
        start = _first_month(plan, pattern)
        if start is None:
            continue
        if previous and start < previous[1]:
            return f'it starts {trade} before {previous[0]}'
        previous = (trade, start)
    return None


def _compliance_check(content: str, facts: DraftFacts) -> str | None:
    for m in regex.finditer(r'Clause\s*(\d+[A-Z]*)\b[^\n]{0,80}?\bdoes not apply', content, I):
        clause = m[1]
        if not regex.search(rf'\b{regex.escape(clause)}\b[^\n]{{0,80}}?does not apply', facts.clauses, I):
            return f'it says Clause {clause} does not apply, which the tender analysis does not'
    return None


def _compliance_finish(content: str, facts: DraftFacts) -> str:
    if facts.key_terms:
        quoted = '\n'.join(f"- {t['label']}: \"{t['text']}\"" for t in facts.key_terms)
        return f'{content}\n\nKey contract terms (quoted from the NIT; accepted as stated):\n{quoted}'
    lines = key_clause_lines(facts.clauses, facts.delay_compensation)
    if not lines:
        return content
    listed = '\n'.join(f'- {line}' for line in lines)
    return f'{content}\n\nKey contract clauses (from the tender analysis; verify against the NIT):\n{listed}'


def _queries_check(content: str, facts: DraftFacts) -> str | None:
    count = len(regex.findall(r'^\s*\d+[.)]\s', content, regex.M))
    # A small job may only have three or four real questions; withholding those helps nobody.
    return f'it has only {count} usable numbered queries' if count < 3 else None


MODEL_SECTIONS = {
    'transmittal': ModelSection(
        id='letter-of-transmittal',
        title='Letter of Transmittal',
        cover='technical',
        brief=(
            'The body of a formal letter of transmittal to the Executive Engineer, in the CPWD style, starting at '
            '"Sir," (the address and subject line are added separately): list the documents enclosed in Cover I (EMD, registration, financial '
            'information, similar works, affidavits), confirm the financial bid is submitted separately in Cover II, and '
            'confirm acceptance of all tender conditions. Use the NIT reference from KEY FIGURES exactly, and state the '
            'EMD amount from KEY FIGURES in figures. Do not cite clause numbers and do not restate eligibility thresholds. '
            "Do not describe the bidder's capabilities or experience. Do not mention any quoted price. End with a "
            'signatory block: Yours faithfully, [Name], [Designation], for <company name>.'
        ),
        check=_transmittal_check,
        finish=_transmittal_finish,
    ),
    'scope': ModelSection(
        id='scope-understanding',
        title='Understanding of Scope of Work',
        cover='technical',
        brief='Restate the scope of work, location, completion period and key deliverables to show the tender was understood.',
    ),
    'methodology': ModelSection(
        id='methodology',
        title='Construction Methodology',
        cover='technical',
        brief=(
            'Construction methodology, one short paragraph per item, in this order: {methodItems}. In each paragraph name only the specifications and standards the tender '
            'text gives for that item, copied as written with their figures (layer names and thicknesses, widths, grades), '
            'the sequence of work, and the physical or laboratory tests that apply to that item. Attach a standard only to '
            'the item the tender text uses it for. '
            'Never invent materials or methods the tender does not state, and never expand an abbreviation unless the '
            'tender text does. "Earthwork and foundations" means the building foundations, not the road; GSB, WMM, DBM and '
            'BC belong only to the road. Give no dimensions, depths or thicknesses unless the tender text states them. '
            'Then one paragraph on safety and one line '
            '"Key technical staff and plant: [to be listed]". Do not write a work programme. '
            'Do not repeat text between items and do not end with a note or disclaimer.'
        ),
        needs_source=True,
        max_tokens=1600,
        check=_methodology_check,
    ),
    'programme': ModelSection(
        id='work-programme',
        title='Work Programme',
        cover='technical',
        brief=(
            'A month-by-month work programme. Write exactly one line per month, from "Month 1:" to "Month {completionMonths}:", '
            'each followed by the activities in that month. Follow the build order: mobilisation, foundations, RCC frame '
            'floor by floor, masonry, services rough-in, '
            'waterproofing, flooring and finishes, services fixtures, then testing and handover. Spread the building work '
            'across the whole period, so finishes run into the last third and testing and handover take only the last '
            'month. Schedule any external '
            'road and drainage works in their own months outside the monsoon (June to September), alongside the building '
            'work. Output only those lines.'
        ),
        check=_programme_check,
        finish=lambda content, facts: render_programme(content, facts.months, facts.has_road),
    ),
    'compliance': ModelSection(
        id='compliance-statement',
        title='Compliance with Tender Conditions',
        cover='technical',
        brief=(
            'A numbered compliance statement: for each submission, EMD, eligibility, technical, quality, GRIHA, labour and '
            'legal requirement found in the analysis, one line stating how the bidder complies. Leave out compensation '
            'for delay, price variation, advances, security deposit, defect liability and cess: those terms are quoted '
            'from the NIT separately. '
            'Do not restate the bidder\'s GSTIN, PAN or any registration; write "[as per enclosed registration certificates]". '
            'For eligibility criteria (similar works, turnover, no-loss, solvency, bid capacity) never assert the bidder '
            'meets them: write "Supporting documents enclosed at [Annexure __]; to be confirmed from company records." '
            'Never mark a clause as not applicable. Never comment on the estimated cost, budget or price: the price is '
            'quoted only in Cover II. Address each flagged risk and submission trap factually. Do not comment on clauses the tender does not contain.'
        ),
        needs_source=True,
        check=_compliance_check,
        clean=lambda content: drop_lines(content, QUOTED_TERMS),
        finish=_compliance_finish,
    ),
    'queries': ModelSection(
        id='pre-bid-queries',
        title='Pre-bid Queries and Clarifications',
        cover='technical',
        brief=(
            'Between 8 and 10 numbered pre-bid queries (1., 2., ...) the contractor should raise with the department, and nothing else. '
            'Never ask about anything the tender already states clearly (deadlines, EMD, forms, how security deposit is '
            'recovered). Do not repeat a query. Cite a clause number only if the analysis gives that number for the very term you ask about. Focus on ambiguous or '
            'onerous conditions: delay compensation and its cap, price variation / escalation, advances, third-party '
            'approvals or certification costs (e.g. GRIHA, RMC plant approval), site access and utilities. '
            'Never claim the tender lacks something; only ask about what is unclear. '
            'Keep each query to one or two sentences.'
        ),
        clean=lambda content: drop_lines(content, BAD_QUERY),
        check=_queries_check,
    ),
}

# Statements a draft must never make: they are eligibility facts only company records can supply.
INVENTED_CLAIMS = regex.compile(
    r"\b(?:our|the) (?:company|firm)\b[^.]{0,40}\b(?:has|have|had) (?:successfully )?(?:completed|executed)|\bturnover\b[^.]{0,80}\bwas (?:Rs|INR|₹)|\b(?:our|its) [^.]{0,30}\bturnover\b[^.]{0,60}\b(?:is|of) (?:Rs|INR|₹)|has not (?:incurred|suffered) (?:any )?loss|(?:have|has|possess) (?:the )?(?:necessary|requisite|adequate) (?:technical |financial )?(?:capabilit|capacit|experience)|we (?:meet|fulfil|fulfill|satisfy) (?:all )?(?:the )?eligibility|\bour (?:technical )?(?:capabilit\w*|experience|expertise|track record)\b|\bideal candidate\b|\bwell[- ]equipped\b|\b(?:has|have|holds?|possess(?:es)?) (?:a |the )?(?:valid )?[^.]{0,30}\b(?:EPF|ESI|ESIC|PF)\b|within the [^.]{0,30}\b(?:budget|estimated cost)\b|\bbid meets all (?:the )?requirements\b|detailed project reports|good strength base|well manufactured macadam|chartered accountant[^.]{0,40}\b(?:concrete|test|cube|quality)|(?:concrete|test|cube|quality)[^.]{0,60}chartered accountant",
    I,
)

PAN_SHAPE = regex.compile(r'\b[A-Z]{5}\s?\d{4}\s?[A-Z]\b|\b[A-Z]{4}\s[A-Z]\d{4}[A-Z]\b')
GSTIN_SHAPE = regex.compile(r'\b\d{2}\s?[A-Z]{5}\s?\d{4}[A-Z]\d[A-Z][A-Z\d]\b')


def find_draft_problem(section: ModelSection, content: str, facts: DraftFacts) -> str | None:
    """Why a model draft can't be used, or None if it passes."""
    claim = INVENTED_CLAIMS.search(content)
    if claim:
        return f'it states company facts not in the profile ("{claim[0]}")'
    ids = [m[0] for m in PAN_SHAPE.finditer(content)] + [m[0] for m in GSTIN_SHAPE.finditer(content)]
    wrong = next((id for id in ids if id != facts.pan and id != facts.gstin and id not in facts.gstin), None)
    if wrong:
        return f'it misquotes a tax ID ("{wrong}")'
    return section.check(content, facts) if section.check else None


def tidy_draft(content: str) -> str:
    """Drops the model's "Here is the ... section:" preamble and a closing "Note:" disclaimer."""
    content = regex.sub(r'^\s*Here is[^\n]*:\s*\n', '', content, count=1, flags=I)
    content = regex.sub(r'\n\s*\**Note\b[^\n]*(?:\n(?!\s*\n)[^\n]*)*\s*\Z', '', content, count=1, flags=I)
    content = regex.sub(
        r'\n\s*(?:Please note|Note that|This section (?:only )?provides|The above (?:is|are|methodology))[^\n]*(?:\n(?!\s*\n)[^\n]*)*\s*\Z',
        '',
        content,
        count=1,
        flags=I,
    )
    return content.strip()


KIND_NOUN = {'works': 'work', 'supply': 'supply', 'services': 'services'}


def sections_for(kind: str) -> dict[str, ModelSection]:
    """The works sections reworded for goods and services tenders; anything not listed is shared."""
    if kind == 'works':
        return MODEL_SECTIONS
    noun = KIND_NOUN[kind]
    supply = kind == 'supply'
    base = MODEL_SECTIONS
    return {
        **base,
        'transmittal': replace(
            base['transmittal'],
            brief=base['transmittal']
            .brief.replace('to the Executive Engineer, in the CPWD style,', 'to the tender inviting authority,', 1)
            .replace(
                '(EMD, registration, financial information, similar works, affidavits)',
                '(EMD or bid security declaration, registrations, financial information, past orders, declarations)',
                1,
            ),
        ),
        'scope': replace(
            base['scope'],
            title=f"Understanding of Scope of {'Supply' if supply else 'Services'}",
            brief=f"Restate the scope of {noun}, the place of {'delivery / consignees' if supply else 'service'}, the {'delivery' if supply else 'contract'} period and the key deliverables, as the tender states them, to show the tender was understood.",
        ),
        'methodology': ModelSection(
            id='supply-plan',
            title='Supply, Delivery and Quality Plan',
            cover='technical',
            brief=(
                'How the bidder will supply the items, one short paragraph each: sourcing of the items to the '
                'specifications the tender text gives (write the make and model as [make / model]); inspection and '
                'testing before dispatch, as the tender states; packing and delivery to the consignee(s) within the '
                'delivery period; installation, commissioning and training only if the tender asks for them; warranty '
                'and after-sales support for the period the tender states. Copy specifications and periods exactly; '
                'never invent a brand, model, certificate or period. Do not end with a note or disclaimer.'
            ),
            needs_source=True,
            max_tokens=1200,
        )
        if supply
        else ModelSection(
            id='service-methodology',
            title='Service Delivery Methodology',
            cover='technical',
            brief=(
                'How the bidder will deliver the services, one short paragraph each: mobilisation and deployment by the '
                'start date; manpower by category and number exactly as the tender states (or [number] if it does not); '
                'shifts, supervision and attendance; statutory obligations the tender names (minimum wages, EPF, ESI, '
                'licences) stated as obligations the bidder will meet; reporting to the department; meeting the service '
                'levels and penalties the tender states; replacement and leave reserve. Never invent numbers, rates or '
                'licences. Do not end with a note or disclaimer.'
            ),
            needs_source=True,
            max_tokens=1200,
        ),
        'compliance': replace(
            base['compliance'],
            brief=base['compliance']
            .brief.replace(
                'technical, quality, GRIHA, labour and legal requirement',
                f"technical, {'inspection, warranty' if supply else 'manpower, statutory'} and legal requirement",
                1,
            )
            .replace(
                '(similar works, turnover, no-loss, solvency, bid capacity)',
                f"(similar {'supplies' if supply else 'services'}, turnover, OEM authorisation, registrations)",
                1,
            ),
        ),
        'queries': replace(
            base['queries'],
            brief=base['queries']
            .brief.replace('the contractor should raise', 'the bidder should raise', 1)
            .replace(
                'delay compensation and its cap, price variation / escalation, advances, third-party approvals or certification costs (e.g. GRIHA, RMC plant approval), site access and utilities.',
                'specifications that are ambiguous or point to one brand, delivery period and consignee locations, inspection and acceptance, warranty start and scope, liquidated damages, and payment terms.'
                if supply
                else 'manpower numbers and categories, how minimum wage and statutory revisions are paid, penalties and service levels, equipment or consumables the bidder must provide, contract extension, and payment cycle.',
                1,
            ),
        ),
    }


def _maintenance_check(content: str, facts: DraftFacts) -> str | None:
    plan = parse_programme(content)
    if problem := _plans_too_few(plan, facts.months):
        return problem
    invented = next((a for a in plan.values() if regex.search(r'\bRCC frame\b|\bsuperstructure\b|floor by floor', a, I)), None)
    return f'it schedules new construction ("{invented}") in a maintenance contract' if invented else None


def _infrastructure_check(content: str, facts: DraftFacts) -> str | None:
    plan = parse_programme(content)
    if problem := _plans_too_few(plan, facts.months):
        return problem
    invented = next((a for a in plan.values() if regex.search(r'\bRCC frame\b|floor by floor|\bmasonry\b|\bflooring\b', a, I)), None)
    return f'it schedules building work ("{invented}") the tender does not name' if invented else None


# Maintenance and repair works: work orders over the period, so no foundation-to-handover build sequence.
MAINTENANCE_PROGRAMME = replace(
    MODEL_SECTIONS['programme'],
    brief=(
        'A month-by-month plan for this maintenance / repair contract. Write exactly one line per month, from "Month 1:" '
        'to "Month {completionMonths}:", each listing the maintenance or repair activities the tender names, taken up as '
        'the department issues work orders, with mobilisation in Month 1 and any monsoon-sensitive work (roofing, '
        'external painting, road patching) outside June to September. Do not add new construction stages (foundations, '
        'RCC frame) that the tender does not name. Output only those lines.'
    ),
    check=_maintenance_check,
)

# Roads, bridges, drains and walls: the activities the tender names in a buildable order, no building stages.
INFRASTRUCTURE_PROGRAMME = replace(
    MODEL_SECTIONS['programme'],
    brief=(
        'A month-by-month work programme. Write exactly one line per month, from "Month 1:" to "Month {completionMonths}:", '
        'each listing only activities the tender itself names, in the order they can be built, with mobilisation in '
        'Month 1, work affected by rain kept outside June to September, and testing and handover only in the last '
        'month. Do not add building stages (RCC frame, masonry, flooring) or road layers the tender does not name. '
        'Output only those lines.'
    ),
    check=_infrastructure_check,
)

# Final order of the bid, mixing model-drafted sections and standard proformas.
BID_ORDER = [
    MODEL_SECTIONS['transmittal'],
    template('document-checklist'),
    template('declarations'),
    template('bid-capacity'),
    MODEL_SECTIONS['scope'],
    MODEL_SECTIONS['methodology'],
    MODEL_SECTIONS['programme'],
    MODEL_SECTIONS['compliance'],
    MODEL_SECTIONS['queries'],
    template('financial-bid'),
]


def bid_order(kind: str, works_type: str | None = None) -> list:
    """Final order of the bid for a tender kind, mixing model-drafted sections and standard proformas."""
    if kind == 'works' and works_type and works_type != 'building':
        programme = MAINTENANCE_PROGRAMME if works_type == 'maintenance' else INFRASTRUCTURE_PROGRAMME
        return [programme if section is MODEL_SECTIONS['programme'] else section for section in BID_ORDER]
    if kind == 'works':
        return BID_ORDER
    sections = sections_for(kind)
    return [
        sections['transmittal'],
        template('document-checklist'),
        {**template('declarations'), 'title': 'Tender Acceptance Letter and Affidavit'},
        template('past-experience'),
        sections['scope'],
        sections['methodology'],
        sections['compliance'],
        sections['queries'],
        template('financial-bid'),
    ]


ROLE = {
    'works': 'an Indian civil-works contractor responding to a CPWD / state PWD tender',
    'supply': 'an Indian supplier responding to a government goods tender (GeM or an e-procurement portal)',
    'services': 'an Indian service provider responding to a government services tender',
}


def edit_needed(problem: str) -> str:
    return f"[Edit needed: the local model's draft of this section was withheld because {problem}. Write this section manually.]"


def system_prompt(kind: str) -> str:
    return (
        f'You draft bid documents for {ROLE[kind]} '
        'under the two-bid system (Cover I technical, Cover II financial). '
        'Use only the facts in the tender analysis, tender text and company profile provided. '
        'Copy figures that appear there (EMD, estimated cost, periods, percentages, clause numbers) exactly. '
        'Only for facts that are absent, write a bracketed placeholder such as [insert value]; never invent figures, dates, '
        'certificates or past projects. Always refer to the bidder by its company name, never a placeholder. '
        'Never add commitments beyond the tender conditions, and never state acceptance of risks or of clauses the tender lacks. '
        'Never state that the bidder has any experience, completed works, turnover, profit, solvency or bid capacity '
        'unless it is in the company profile; a false eligibility declaration gets a bid rejected and the contractor debarred. '
        'Do not write calendar dates; use [dd/mm/yyyy]. For a period the text does not give, write [insert period]. '
        'Write in formal English, ready for the contractor to edit. Output only the section body, without a heading.'
    )


class BidModelUnavailableError(Exception):
    pass


def _key_figures(report: dict, nit_ref: str, kind: str) -> str:
    metadata = report['summary']['metadata']
    period = 'Completion period' if kind == 'works' else 'Delivery period' if kind == 'supply' else 'Contract period'
    months = metadata.get('completionMonths')
    return '\n'.join(
        [
            'KEY FIGURES (copy exactly):',
            f'- NIT reference: {nit_ref}',
            f"- Name of {KIND_NOUN[kind]}: {metadata.get('nameOfWork') or metadata['tenderTitle']}",
            f"- Estimated cost: {metadata.get('estimatedCost') or '[insert value]'}",
            f"- EMD: {metadata.get('emdAmount') or '[insert value]'}",
            f"- {period}: {f'{months} months' if months else '[insert period]'}",
        ]
    )


def _context(report: dict, company: dict) -> str:
    summary, compliance = report['summary'], report['compliance']
    risks = '; '.join(f"{r['category']}: {r['description']}" for r in compliance['identifiedRisks']) or 'none'
    missing = '; '.join(f"{c['clause']} ({c['reason']})" for c in compliance['missingOrWeakClauses']) or 'none'
    experience = experience_line(company)
    return '\n'.join(
        [
            f"BIDDER: {company['legalName']}",
            f"Executive summary: {summary['executiveSummary']}",
            f"Commercial terms: {summary['commercialTerms']}",
            f"Dates and obligations: {summary['datesAndObligations']}",
            f"Technical scope: {summary['technicalScope']}",
            f"Legal highlights: {summary['legalHighlights']}",
            f"Attention points: {summary['attentionPoints']}",
            f"Eligibility and key clauses: {summary.get('eligibilityAndClauses') or 'not extracted'}",
            f"Risk level: {compliance['riskLevel']}; identified risks: {risks}",
            f'Missing or weak clauses: {missing}',
            f"Submission traps: {'; '.join(compliance['submissionTraps']) or 'none'}",
            '',
            f"CONTRACTOR: {company['legalName']}, registration class {company.get('registrationClass')}, GSTIN {company['gstin']}, PAN {company['panNumber']}, address {company.get('registeredAddress')}",
            *([f'CONTRACTOR EXPERIENCE: {experience}'] if experience else []),
        ]
    )


def _text_of(*parts) -> str:
    """Joins texts the way JavaScript's [a, b, c].join('\\n') does, where a missing one is an empty line."""
    return '\n'.join(p if p is not None else '' for p in parts)


async def generate_foundation_bid(report: dict, profile: dict, on_progress=None) -> dict:
    # GSTIN and PAN stay in the browser's vault: the bid carries tokens the page fills in at download time.
    company = {**profile, 'gstin': GSTIN_TOKEN, 'panNumber': PAN_TOKEN}
    model = AI_CONFIG.MODEL_NAME

    async def check():
        health = await llm.check_health(model)
        if not health['available']:
            raise BidModelUnavailableError(f"Local bid model is not available: {health.get('errorMessage')}")
        return health

    health = await track_step(on_progress, 'model-check', STEP_LABELS['model-check'], check, lambda _: 'Local model ready')

    summary = report['summary']
    metadata = summary['metadata']
    tender_id = metadata['tenderId']
    # The name of work as the NIT prints it beats whatever label the tender was uploaded under.
    tender_title = metadata.get('nameOfWork') or metadata['tenderTitle']
    months = metadata.get('completionMonths')
    # The internal tenderId never goes into the bid; only the reference printed on the NIT does.
    nit_ref = metadata.get('nitReference') or '[NIT No.]'
    kind = metadata.get('tenderKind') or 'works'
    key_figures = _key_figures(report, nit_ref, kind)
    source_text = summary.get('sourceText')
    tender_text = _text_of(source_text, summary.get('technicalScope'), summary.get('eligibilityAndClauses'))
    facts = DraftFacts(
        months=months,
        emd_amount=metadata.get('emdAmount'),
        clauses=summary.get('eligibilityAndClauses') or '',
        delay_compensation=metadata.get('delayCompensation'),
        inviting_office=metadata.get('invitingOffice'),
        has_road=kind == 'works' and regex.search(r'\broad\b', f"{summary.get('technicalScope')} {summary.get('executiveSummary')}", I) is not None,
        key_terms=metadata.get('keyTerms') or [],
        source_text=source_text or '',
        # Codes found anywhere in the tender at analysis time; older reports only have the excerpt and summaries.
        is_codes={*((metadata.get('standards') or {}).get('isCodes') or []), *is_codes_in(tender_text)},
        nit_ref=nit_ref,
        tender_title=tender_title,
        kind=kind,
        pan=company['panNumber'],
        gstin=company['gstin'],
    )
    context = _context(report, company)
    source_excerpt = f'\n\nTENDER TEXT (selected extracts):\n{source_text}' if source_text else ''
    known_grades = {*((metadata.get('standards') or {}).get('grades') or []), *grades_in(tender_text)}
    sections: list[dict] = []

    # Sequential: a local Ollama serves one generation at a time.
    for section in bid_order(kind, metadata.get('worksType')):
        if isinstance(section, dict):
            meta = {k: v for k, v in section.items() if k != 'render'}

            def render(section=section, meta=meta):
                content = section['render'](company=company, nit_ref=nit_ref, tender_title=tender_title, kind=kind, months=months)
                sections.append({**meta, 'content': content})

            await track_step(on_progress, f"section:{meta['id']}", meta['title'], render)
            continue

        fitted = fit_brief_to_tender(section.brief, tender_text).replace(
            '{methodItems}', methodology_items(f'{tender_title}\n{tender_text}', metadata.get('worksType'), tender_title), 1
        )
        months_text = str(months) if months is not None else 'N (the completion period in the tender)'
        task = f"Write the \"{section.title}\" section of the bid for {company['legalName']}. {fitted.replace('{completionMonths}', months_text, 1)}"
        # The task goes before and after the long context: Llama 3 drifts when it only comes last.
        user_prompt = f"{task}\n\n{key_figures}\n\n{context}{source_excerpt if section.needs_source else ''}\n\nREMINDER: {task}"

        step = f'section:{section.id}'
        if on_progress:
            on_progress(update(step, 'started', section.title))
        content = ''
        problem = None
        try:
            for temperature in (0.3, 0.1):
                response = await llm.generate(
                    model=model,
                    system_prompt=system_prompt(kind),
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=section.max_tokens or 900,
                )
                content = tidy_draft(response['content'])
                if section.clean:
                    content = section.clean(content)
                problem = find_draft_problem(section, content, facts)
                if not problem:
                    break
                log.warning('[bid] %s draft rejected at temperature %s: %s\n%s', section.id, temperature, problem, content)
                if temperature == 0.3 and on_progress:
                    on_progress(update(step, 'retry', section.title, f'the first draft was rejected because {problem}'))
        except BaseException:
            if on_progress:
                on_progress(update(step, 'failed', section.title))
            raise
        if on_progress:
            on_progress(
                update(step, 'withheld', section.title, f'the draft was withheld because {problem}')
                if problem
                else update(step, 'done', section.title)
            )
        if not problem:
            content = redact_unknown_standards(content, facts.is_codes)
            content = redact_unknown_grades(content, known_grades)
        sections.append(
            {
                'id': section.id,
                'title': section.title,
                'cover': section.cover,
                'source': 'model',
                'content': edit_needed(problem) if problem else section.finish(content, facts) if section.finish else content,
            }
        )

    return {
        'tenderId': tender_id,
        'tenderTitle': tender_title,
        'generatedAt': iso_now(),
        'modelUsed': health.get('modelName') or model,
        'sections': sections,
    }
