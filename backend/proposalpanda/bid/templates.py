"""
Standard proformas found in Indian two-bid tenders: CPWD / state PWD works, and goods (GeM / store purchase) and
services (manpower, security, O&M) tenders that use the same technical-bid / price-bid split.
Filled from the company profile; anything the bidder must supply stays a [placeholder].
Form names vary between departments, so these follow the common wording, not one tender.
"""

from datetime import date

import regex

from ..engine.profile import bid_capacity as compute_bid_capacity, format_lakh
from ..fmt import date_en_in, js_number


def _signatory(company: dict) -> str:
    return (company.get('signatoryName') or '').strip() or '[name of authorised signatory]'


def _designation(company: dict) -> str:
    return (company.get('signatoryDesignation') or '').strip() or '[designation]'


def _registration(company: dict) -> str:
    number = company.get('registrationNumber')
    parts = [company.get('registrationAuthority'), company.get('registrationClass'), number and f'No. {number}']
    valid = f", valid up to {company['registrationValidUpto']}" if company.get('registrationValidUpto') else ''
    return f"{', '.join(p for p in parts if p)}{valid}"


def _work_lines(works: list | None, blank: str, describe) -> list[str]:
    """Numbered lines for the similar works the company file lists, padded to three with blanks."""
    filled = [describe(w) for w in works or [] if (w.get('name') or '').strip()]
    while len(filled) < 3:
        filled.append(blank)
    return [f'{i + 1}. {line}' for i, line in enumerate(filled)]


def _value(lakh) -> str:
    return format_lakh(lakh) if lakh is not None else '₹ [value]'


def _today() -> str:
    return date_en_in(date.today())


KIND_CHECKLIST = {
    'works': lambda company: [
        '5. EPF and ESI registration [attach]',
        f'6. Contractor registration with Government department ({_registration(company)}) [attach]',
        '7. Financial information: turnover for the last 5 years, CA certified with UDIN [attach]',
        '8. Solvency certificate from a scheduled bank or net worth certificate from a CA [attach]',
        '9. Details of similar works completed, with completion certificates [attach]',
        '10. Performance reports of works executed [attach]',
        '11. Works in hand / under execution (for bid capacity) [attach]',
        '12. Structure and organisation, key technical staff, plant and machinery [attach]',
        '13. Affidavit: not blacklisted or debarred [attach]',
        '14. Declaration of site inspection [attach]',
    ],
    'supply': lambda company: [
        '5. MSE / Udyam registration, if claiming EMD or other exemptions [attach]',
        "6. OEM authorisation (manufacturer's authorisation form), if the bidder is not the OEM [attach]",
        '7. Financial information: turnover for the years the bid document asks, CA certified with UDIN [attach]',
        '8. Past supply orders of similar items with completion / installation certificates [attach]',
        '9. Technical compliance sheet: each specification in the bid document against the offered make and model [attach]',
        '10. Product datasheets, brochures and certificates the bid document asks for (BIS, ISO, test reports) [attach]',
        '11. Make in India / local content declaration, where required [attach]',
        '12. Affidavit: not blacklisted or debarred [attach]',
    ],
    'services': lambda company: [
        '5. EPF and ESI registration, and labour licence under the CLRA Act where applicable [attach]',
        '6. Licences specific to the service the bid document asks for (e.g. PSARA licence for security agencies) [attach]',
        '7. Financial information: turnover for the years the bid document asks, CA certified with UDIN [attach]',
        '8. Past orders for similar services with satisfactory performance certificates [attach]',
        '9. Manpower deployment and supervision plan [attach]',
        '10. Quality certificates the bid document asks for (e.g. ISO 9001) [attach]',
        '11. Affidavit: not blacklisted or debarred [attach]',
    ],
}


def document_checklist(company: dict, kind: str = 'works', **_) -> str:
    items = [
        '1. EMD / Bid Security (scanned DD, BG, FDR or online receipt) or Bid Security Declaration where permitted [attach]',
        '2. Tender fee / e-processing fee receipt [attach]',
        f"3. GST registration certificate (GSTIN {company.get('gstin')}) [attach]",
        f"4. PAN card ({company.get('panNumber')}) [attach]",
        *KIND_CHECKLIST[kind](company),
        'Letter of transmittal and tender acceptance letter (signed) [attach]',
        'Integrity Pact, signed, if required by the NIT [attach]',
    ]
    return '\n'.join(
        [
            'Upload as one PDF in this order, unless the NIT specifies another order:',
            '',
            *[regex.sub(r'^(?:\d+\.\s*)?', f'{i + 1}. ', item, count=1) for i, item in enumerate(items)],
        ]
    )


def declarations(company: dict, nit_ref: str, tender_title: str, kind: str = 'works', **_) -> str:
    works = kind == 'works'
    lines = [
        '**Tender Acceptance Letter**',
        '',
        f"We, {company['legalName']}, have read all the terms and conditions of NIT No. {nit_ref} ({tender_title}), including the",
        'NIT, general and special conditions, specifications, drawings and BOQ, and accept them unconditionally.'
        if works
        else 'NIT / bid document, general and special conditions, specifications and price schedule, and accept them unconditionally.',
        'We confirm that our bid shall remain valid for the period stated in the NIT.',
        '',
    ]
    if works:
        lines += [
            '**Declaration of Site Inspection**',
            '',
            'We have inspected the site of work and acquainted ourselves with site conditions, access, availability of',
            'materials, water and power, and have taken these into account in our bid. Date of inspection: [dd/mm/yyyy].',
            '',
        ]
    lines += [
        '**Affidavit (on non-judicial stamp paper of value [₹ ___], duly notarised)**',
        '',
        f"I, {_signatory(company)}, {_designation(company)} of {company['legalName']}, do hereby solemnly affirm that the",
        'firm has not been blacklisted, debarred or suspended by any Central/State Government department, PSU or',
        'local body, and that all information and documents submitted with this bid are true and correct.',
        'If any information is found false, the EMD may be forfeited and the firm may be debarred.',
        '',
        f'Place: [city]    Date: {_today()}',
        'Signature and seal of the bidder',
    ]
    if company.get('signatoryName'):
        designation = f", {company['signatoryDesignation']}" if company.get('signatoryDesignation') else ''
        lines.append(f"{company['signatoryName']}{designation}")
    return '\n'.join(lines)


def past_experience(company: dict, kind: str = 'supply', **_) -> str:
    """Eligibility proforma for goods and services tenders: past orders and turnover instead of bid capacity."""
    noun = 'services' if kind == 'services' else 'supplies'
    turnover = [t for t in company.get('turnover') or [] if t.get('lakh') is not None]
    return '\n'.join(
        [
            f'**Similar {noun} completed in the period the bid document specifies**',
            '',
            *_work_lines(
                company.get('similarWorks'),
                f'[Order / contract no.], [buyer], [description of {noun}], ₹ [value], [order date] to [completion date], performance certificate attached: [yes/no]',
                lambda w: f"{w['name']}, {w.get('client') or '[buyer]'}, {_value(w.get('valueLakh'))}, {w.get('start') or '[order date]'} to {w.get('completion') or '[completion date]'}, performance certificate attached: [yes/no]",
            ),
            '',
            '**Average annual turnover** (CA certified, for the financial years the bid document asks)',
            '',
            *(
                [f"- FY {t['year']}: {format_lakh(t['lakh'])}" for t in turnover]
                if turnover
                else ['- FY [____]: ₹ [___]', '- FY [____]: ₹ [___]', '- FY [____]: ₹ [___]']
            ),
            '',
            'Check each figure against the minimum the bid document sets before submitting.',
        ]
    )


def bid_capacity(company: dict, months: int | None = None, as_of: date | None = None, **_) -> str:
    figures = compute_bid_capacity(company, months, as_of)
    if figures:
        formula = [
            f"- **A** = {format_lakh(figures['a'])}: the highest year (FY {figures['aYear']}) from the company file, brought to the",
            '  current price level at 7% per year. Check it against the CA certificate.',
            f"- **N** = {js_number(figures['n'])} years ({months} months, the completion period in the NIT)",
            f"- **B** = {format_lakh(figures['b'])}: works in hand to be completed within N, from the company file",
            '',
            f"Bid capacity = ({js_number(figures['a'])} × {js_number(figures['n'])} × 2) − {js_number(figures['b'])} = **{format_lakh(figures['capacity'])}**, which must be at least the",
            'estimated cost put to tender.',
        ]
    else:
        formula = [
            '- **A** = maximum value of civil works executed in any one year during the last 5 years, updated to the current',
            '  price level at 7% per year (from audited, CA-certified figures): ₹ [___]',
            '- **N** = number of years prescribed for completion of this work: [___]',
            '- **B** = value at current price level of existing commitments and works in hand to be completed within N: ₹ [___]',
            '',
            'Bid capacity = ([A] × [N] × 2) − [B] = ₹ [___], which must be at least the estimated cost put to tender.',
        ]
    return '\n'.join(
        [
            'Assessed available bid capacity = (A × N × 2) − B, where:',
            '',
            *formula,
            '',
            '**Similar works completed in the last 7 years** (as defined in the NIT)',
            '',
            *_work_lines(
                company.get('similarWorks'),
                '[Name of work], [client], ₹ [value], [start date] to [completion date], certificate attached: [yes/no]',
                lambda w: f"{w['name']}, {w.get('client') or '[client]'}, {_value(w.get('valueLakh'))}, {w.get('start') or '[start date]'} to {w.get('completion') or '[completion date]'}, certificate attached: [yes/no]",
            ),
            *_key_staff_and_plant(company),
        ]
    )


def _years(years) -> str:
    return f", {js_number(years)} years' experience" if years is not None else ''


def _key_staff_and_plant(company: dict) -> list[str]:
    staff = [s for s in company.get('keyStaff') or [] if (s.get('name') or '').strip()]
    machinery = (company.get('machinery') or '').strip()
    if not staff and not machinery:
        return []
    return [
        '',
        '**Key technical staff and plant**',
        '',
        *[f"- {s['name']}, {s.get('qualification') or '[qualification]'}{_years(s.get('years'))}" for s in staff],
        *['- ' + regex.sub(r'^[-•]\s*', '', line.strip()) for line in machinery.split('\n') if line.strip()],
    ]


def financial_bid(company: dict, nit_ref: str, tender_title: str, kind: str = 'works', **_) -> str:
    if kind != 'works':
        return '\n'.join(
            [
                f'**Financial Bid (Price Bid) for {nit_ref}: {tender_title}**',
                '',
                'Quote in the price schedule / BOQ on the portal (on GeM, in the bid itself). Do not reveal prices anywhere in',
                'the technical bid, or the bid may be rejected.',
                '',
                f"We, {company['legalName']}, offer the {'services' if kind == 'services' else 'items'} at the rates entered in the price schedule, as per the",
                'specifications, quantities and terms of the bid document.',
                '',
                '**Before quoting, check:**',
                '- GST treatment of quoted rates (inclusive or extra) as the bid document states',
                '- Minimum wages, EPF/ESI, bonus and other statutory payments built into the manpower rate, and how revisions are paid'
                if kind == 'services'
                else '- Freight, insurance, installation, commissioning and warranty included in the unit price as the bid document states',
                '- Delivery / contract period and the liquidated damages or penalties for delay',
                '- Payment terms and any price variation or rate revision clause',
            ]
        )
    return '\n'.join(
        [
            f'**Financial Bid (Cover II) for NIT No. {nit_ref}: {tender_title}**',
            '',
            'Fill in the BOQ / percentage-rate sheet on the e-procurement portal. Do not reveal prices anywhere in Cover I,',
            'or the bid may be rejected.',
            '',
            '*If percentage rate tender:*',
            '',
            f"We, {company['legalName']}, offer to execute the work at [___]% [below / above / at par] the estimated cost put to",
            'tender of ₹ [___], inclusive of all taxes, duties, royalties, labour cess and incidental charges, as per the',
            'tender conditions. A blank or nil quote makes the bid invalid.',
            '',
            '*If item rate tender:* quote a rate in figures and words for every BOQ item. Unquoted items are taken as zero.',
            '',
            '**Before quoting, check:**',
            '- GST treatment of quoted rates (inclusive or extra) as the NIT states',
            '- Price variation / escalation clause, if any',
            '- Royalty, labour cess, testing charges and insurance included in rates',
            '- Rebate offered, if allowed (only in the format the portal provides)',
        ]
    )


TEMPLATE_SECTIONS = [
    {'id': 'document-checklist', 'title': 'Document Checklist', 'cover': 'technical', 'source': 'template', 'render': document_checklist},
    {'id': 'declarations', 'title': 'Tender Acceptance Letter, Site Inspection and Affidavit', 'cover': 'technical', 'source': 'template', 'render': declarations},
    {'id': 'bid-capacity', 'title': 'Eligibility: Similar Works and Bid Capacity', 'cover': 'technical', 'source': 'template', 'render': bid_capacity},
    {'id': 'past-experience', 'title': 'Eligibility: Past Orders and Turnover', 'cover': 'technical', 'source': 'template', 'render': past_experience},
    {'id': 'financial-bid', 'title': 'Price Bid', 'cover': 'financial', 'source': 'template', 'render': financial_bid},
]


def template(id: str) -> dict:
    return next(t for t in TEMPLATE_SECTIONS if t['id'] == id)
