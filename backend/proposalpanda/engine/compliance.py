"""
Compliance and risk scoring over the tender summary: factual compliance checks, risk identification and a
structured, explainable score. Every rule reads the tender's own sentences or figures (summary.metadata), never the
model's prose, so a risk is only raised when the tender says it, and the same tender always scores the same.

This is analysis, not generation; facts, not advice.
"""

import logging
import time

import regex

log = logging.getLogger('proposalpanda')

RISK_ORDER = ['Low', 'Medium', 'High']


def _level_for(penalty: int, medium: int, high: int) -> str:
    return 'High' if penalty >= high else 'Medium' if penalty >= medium else 'Low'


def _clauses_of(summary: dict) -> dict:
    """Clauses quoted from the tender text, by label. Empty for reports analysed before quoting existed."""
    return {c['label']: c['text'] for c in summary['metadata'].get('riskClauses') or []}


def _quoted_risk(category: str, quote: str, consequence: str) -> dict:
    """A risk that quotes the tender sentence it comes from."""
    return {'category': category, 'description': f'The tender states: "{quote}" {consequence}', 'sourceSection': 'tender text'}


def _financial_risks(clauses: dict) -> dict:
    """Guarantees that can be encashed without consent, and deposits that can be forfeited. An EMD alone is not a risk."""
    risks, penalty = [], 0
    if clauses.get('Unconditional guarantee'):
        risks.append(_quoted_risk('Financial', clauses['Unconditional guarantee'], "The guarantee can be encashed without the contractor's consent."))
        penalty += 10
    if clauses.get('Forfeiture'):
        risks.append(_quoted_risk('Financial', clauses['Forfeiture'], 'The deposit or guarantee named here can be forfeited.'))
        penalty += 5
    return {'risks': risks, 'scorePenalty': penalty, 'level': _level_for(penalty, 8, 15)}


def _technical_risks(completion_months) -> dict:
    """A completion period of two years or more ties up plant, staff and guarantees for that long."""
    if completion_months is None or completion_months < 24:
        return {'risks': [], 'scorePenalty': 0, 'level': 'Low'}
    return {
        'risks': [
            {
                'category': 'Technical',
                'description': f'The tender specifies a completion period of {completion_months} months. Resources and guarantees stay committed for that period.',
                'sourceSection': 'tender text',
            }
        ],
        'scorePenalty': 5,
        'level': 'Medium',
    }


def _legal_risks(clauses: dict) -> dict:
    risks, penalty = [], 0
    if clauses.get('Final and binding'):
        risks.append(_quoted_risk('Legal', clauses['Final and binding'], 'Decisions covered by this clause bind the contractor.'))
        penalty += 5
    if clauses.get('Indemnity'):
        risks.append(_quoted_risk('Legal', clauses['Indemnity'], 'The contractor carries the liabilities named here.'))
        penalty += 5
    disputes = clauses.get('Dispute resolution')
    if disputes and regex.search(r'arbitrat', disputes, regex.I) and regex.search(r'\b(?:no|not|excluded)\b', disputes, regex.I):
        risks.append(_quoted_risk('Legal', disputes, 'Arbitration may not be available for disputes.'))
        penalty += 10
    return {'risks': risks, 'scorePenalty': penalty, 'level': _level_for(penalty, 8, 15)}


def _submission_risks(clauses: dict) -> dict:
    """Online submission is normal; a deadline of a few days after an event is not."""
    if not clauses.get('Short notice'):
        return {'risks': [], 'scorePenalty': 0, 'level': 'Low'}
    return {
        'risks': [_quoted_risk('Submission', clauses['Short notice'], 'The contractor has only this long to respond.')],
        'scorePenalty': 8,
        'level': 'Medium',
    }


def _higher_risk(a: str, b: str) -> str:
    return RISK_ORDER[max(RISK_ORDER.index(a), RISK_ORDER.index(b))]


def overall_risk_level(categories: dict) -> str:
    """
    Overall risk level from the category levels, so the headline never contradicts its own breakdown.
    The numeric score only summarises penalties and does not set the level.
    """
    levels = list(categories.values())
    high = levels.count('High')
    medium = levels.count('Medium')
    if high >= 2:
        return 'High'
    if high == 1 or medium >= 2:
        return 'Medium'
    return 'Low'


def analyze_contract_terms(summary: dict) -> dict:
    """Risks from the contract terms quoted word for word from the tender (summary.metadata.keyTerms)."""
    terms = {t['label']: t['text'] for t in summary['metadata'].get('keyTerms') or []}
    months = summary['metadata'].get('completionMonths')
    risks, penalty = [], 0

    price_variation = terms.get('Price variation (Clause 10CC)')
    if price_variation and regex.search(r'\bnot\b[^.]{0,30}\bappl', price_variation, regex.I):
        long = months is not None and months >= 12
        period = f' over the {months}-month completion period' if months else ''
        risks.append(
            {
                'category': 'Financial',
                'description': f'The tender states: "{price_variation}" The quoted rates must absorb any increase in material and labour costs{period}.',
                'sourceSection': 'tender text',
            }
        )
        penalty += 10 if long else 5

    delay = terms.get('Compensation for delay (Clause 2)')
    if delay:
        risks.append({'category': 'Financial', 'description': f'The tender states: "{delay}"', 'sourceSection': 'tender text'})
        penalty += 5

    advance = terms.get('Mobilisation advance')
    if advance and regex.search(r'bank guarantee|\bBG\b', advance, regex.I):
        risks.append(
            {
                'category': 'Financial',
                'description': f'The tender states: "{advance}" Drawing the advance needs bank guarantee limits.',
                'sourceSection': 'tender text',
            }
        )

    level = 'High' if penalty >= 15 else 'Medium' if penalty >= 5 else 'Low'
    return {'risks': risks, 'scorePenalty': penalty, 'level': level}


EXPECTED_CLAUSES = [
    ('Dispute resolution', 'Dispute Resolution Mechanism'),
    ('Extension of time', 'Time Extension Provisions'),
    ('Force majeure', 'Force Majeure Clause'),
]


def _missing_clauses(summary: dict) -> list[dict]:
    """Clauses absent from the tender text itself. Skipped for reports analysed before the text was checked."""
    conflict = summary['metadata'].get('guaranteeConflict')
    conflicting = (
        [
            {
                'clause': 'Performance guarantee (stated two ways)',
                'reason': f'The tender says "{conflict[0]}" and also "{conflict[1]}". Ask which applies in a pre-bid query.',
            }
        ]
        if conflict
        else []
    )
    if summary['metadata'].get('riskClauses') is None:
        return conflicting
    clauses = _clauses_of(summary)
    return conflicting + [
        {
            'clause': clause,
            'reason': 'Not found in the tender text. Check whether the General Conditions of Contract it refers to (e.g. CPWD GCC) cover it.',
        }
        for label, clause in EXPECTED_CLAUSES
        if not clauses.get(label)
    ]


def _submission_traps(clauses: dict) -> list[str]:
    """Procedural requirements quoted from the tender."""
    traps = []
    if clauses.get('Short notice'):
        traps.append(f"Short deadline: \"{clauses['Short notice']}\"")
    if clauses.get('Unconditional guarantee'):
        traps.append(f"Unconditional guarantee required: \"{clauses['Unconditional guarantee']}\"")
    if clauses.get('Online submission'):
        traps.append(f"Online submission: \"{clauses['Online submission']}\"")
    return traps


def _confidence_notes(score: int, risk_count: int, conservative_bias: bool) -> str:
    notes = f'Compliance score of {score}/100 based on factual analysis of {risk_count} identified risk factors. '
    if conservative_bias:
        notes += 'Conservative bias applied (government tenders favor issuing authority). '
    if score >= 75:
        notes += 'The tender presents manageable compliance requirements.'
    elif score >= 60:
        notes += 'The tender contains notable compliance requirements requiring careful attention.'
    else:
        notes += 'The tender contains significant compliance requirements and restrictions.'
    return notes


def _score(summary: dict, conservative_bias: bool, verbose: bool) -> dict:
    clauses = _clauses_of(summary)
    results = {
        'financialRisks': _financial_risks(clauses),
        'technicalRisks': _technical_risks(summary['metadata'].get('completionMonths')),
        'legalRisks': _legal_risks(clauses),
        'submissionRisks': _submission_risks(clauses),
        'contractRisks': analyze_contract_terms(summary),
    }
    if verbose:
        for name, r in results.items():
            log.info('[Compliance] %s: %d risks, penalty %d, %s', name, len(r['risks']), r['scorePenalty'], r['level'])

    identified = [
        *results['contractRisks']['risks'],
        *results['financialRisks']['risks'],
        *results['technicalRisks']['risks'],
        *results['legalRisks']['risks'],
        *results['submissionRisks']['risks'],
    ]
    penalty = sum(r['scorePenalty'] for r in results.values()) + (5 if conservative_bias else 0)
    # A one- or two-page NIT or press notice carries no contract conditions; few risks found is not low risk.
    thin = summary['metadata']['totalChunks'] <= 3
    compliance_score = max(0, min(60 if thin else 100, 80 - penalty))
    categories = {
        'financial': _higher_risk(results['financialRisks']['level'], results['contractRisks']['level']),
        'technical': results['technicalRisks']['level'],
        'legal': results['legalRisks']['level'],
        'submission': results['submissionRisks']['level'],
    }
    overall = overall_risk_level(categories)
    provisional = (
        'Only a short notice was analysed, so this score is provisional: the contract conditions are in the full bid document. Upload it to score them. '
        if thin
        else ''
    )
    return {
        'complianceScore': compliance_score,
        'riskLevel': 'Medium' if thin and overall == 'Low' else overall,
        'riskCategories': categories,
        'identifiedRisks': identified,
        'missingOrWeakClauses': _missing_clauses(summary),
        'submissionTraps': _submission_traps(clauses),
        'confidenceNotes': provisional + _confidence_notes(compliance_score, len(identified), conservative_bias),
    }


REQUIRED_FIELDS = ['executiveSummary', 'commercialTerms', 'datesAndObligations', 'technicalScope', 'legalHighlights', 'attentionPoints']

FORBIDDEN_WORDS = ['should', 'recommended', 'better to', 'unfair', 'biased', 'bad tender', 'good idea', 'bad idea']


def _validate_input(summary) -> None:
    if not summary:
        raise ValueError('Missing summary input')
    for field in REQUIRED_FIELDS:
        if field not in summary:
            raise ValueError(f'Missing required summary field: {field}')
    if not (summary.get('metadata') or {}).get('tenderId'):
        raise ValueError('Missing tender ID in summary metadata')


def _forbidden_language(score: dict) -> list[str]:
    text = ' '.join(
        [
            score['confidenceNotes'],
            *[r['description'] for r in score['identifiedRisks']],
            *[c['reason'] for c in score['missingOrWeakClauses']],
            *score['submissionTraps'],
        ]
    )
    # Quoted tender text is the tender's wording, not ours ("the bidder should submit...").
    text = regex.sub(r'"[^"]*"', '', text).lower()
    return [word for word in FORBIDDEN_WORDS if word in text]


def analyze_compliance(summary: dict, conservative_bias: bool = False, verbose: bool = False) -> dict:
    """Scores a tender summary: {score, diagnostics}. Deterministic rules; no model call."""
    start = time.monotonic()
    errors: list[str] = []
    try:
        _validate_input(summary)
        score = _score(summary, conservative_bias, verbose)
        if not isinstance(score['complianceScore'], int) or not 0 <= score['complianceScore'] <= 100:
            raise ValueError('Compliance score out of range (0-100)')
        if score['riskLevel'] not in RISK_ORDER:
            raise ValueError('Invalid risk level')
        violations = _forbidden_language(score)
        if violations:
            errors.append(f"Forbidden language detected: {', '.join(violations)}")
            raise ValueError('Language validation failed')
        return {
            'score': score,
            'diagnostics': {
                'processingTimeMs': int((time.monotonic() - start) * 1000),
                'modelUsed': 'rules',
                'tenderId': summary['metadata']['tenderId'],
                'summaryVersion': 'Phase 4A',
                'warnings': [],
                'errors': errors,
            },
        }
    except Exception as error:
        errors.append(str(error) or 'Unknown error')
        raise RuntimeError(f"Compliance analysis failed: {'; '.join(errors)}") from error
