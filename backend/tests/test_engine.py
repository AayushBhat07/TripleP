"""Compliance scoring, key dates, the schedule of quantities and the company proformas."""

from datetime import date

from proposalpanda.bid.templates import bid_capacity as bid_capacity_proforma
from proposalpanda.bid.templates import declarations, document_checklist, past_experience
from proposalpanda.engine.boq import find_boq, rupees
from proposalpanda.engine.compliance import analyze_compliance, analyze_contract_terms, overall_risk_level
from proposalpanda.engine.dates import dates_on_portal, days_until, find_key_dates, format_key_date
from proposalpanda.engine.facts import find_risk_clauses
from proposalpanda.engine.profile import bid_capacity, format_lakh, last_five_years
from proposalpanda.fmt import round_half_up


def summary(metadata: dict, prose: str = '') -> dict:
    sections = ('executiveSummary', 'commercialTerms', 'datesAndObligations', 'technicalScope', 'legalHighlights', 'attentionPoints')
    return {
        **{s: prose for s in sections},
        'metadata': {'tenderId': 't', 'tenderTitle': 't', 'generatedAt': '2026-10-16T00:00:00.000Z', 'modelUsed': 'm',
                     'totalChunks': 20, 'processingTimeMs': 1, **metadata},
    }


# Compliance

def test_overall_risk_follows_the_category_levels():
    assert overall_risk_level({'a': 'Low', 'b': 'Low', 'c': 'Low', 'd': 'Low'}) == 'Low'
    assert overall_risk_level({'a': 'Medium', 'b': 'Medium', 'c': 'Low', 'd': 'Low'}) == 'Medium'
    assert overall_risk_level({'a': 'High', 'b': 'Low', 'c': 'Low', 'd': 'Low'}) == 'Medium'
    assert overall_risk_level({'a': 'High', 'b': 'High', 'c': 'Low', 'd': 'Low'}) == 'High'


def test_flags_no_price_variation_over_a_long_contract():
    result = analyze_contract_terms(summary({
        'completionMonths': 18,
        'keyTerms': [
            {'label': 'Price variation (Clause 10CC)', 'text': 'Price variation under Clause 10CC shall not be applicable for this work.'},
            {'label': 'Compensation for delay (Clause 2)', 'text': 'Compensation for delay under Clause 2 shall be levied at 1.5% per month.'},
        ],
    }))
    assert result['level'] == 'High'
    assert 'over the 18-month completion period' in result['risks'][0]['description']
    assert len(result['risks']) == 2


def test_model_prose_alone_raises_no_risk():
    prose = 'EMD of Rs. 29,64,000. Specific and multiple works over 23 months. Online portal. Arbitration. Guarantee.'
    score = analyze_compliance(summary({'riskClauses': []}, prose))['score']
    assert score['identifiedRisks'] == []
    assert score['submissionTraps'] == []
    assert score['complianceScore'] == 80
    assert score['riskLevel'] == 'Low'


def test_risks_quote_the_tender_and_quoted_should_passes_the_language_check():
    risk_clauses = [
        {'label': 'Unconditional guarantee', 'text': 'The bidder should furnish an unconditional bank guarantee of 5%.'},
        {'label': 'Final and binding', 'text': 'The decision of the Engineer-in-Charge shall be final and binding.'},
        {'label': 'Online submission', 'text': 'Bids shall be submitted online on the CPWD e-tendering portal.'},
        {'label': 'Dispute resolution', 'text': 'Disputes shall be settled by arbitration at New Delhi.'},
    ]
    score = analyze_compliance(summary({'riskClauses': risk_clauses, 'completionMonths': 18}))['score']
    assert [r['sourceSection'] for r in score['identifiedRisks']] == ['tender text', 'tender text']
    assert 'unconditional bank guarantee of 5%' in score['identifiedRisks'][0]['description']
    assert len(score['submissionTraps']) == 2
    assert [c['clause'] for c in score['missingOrWeakClauses']] == ['Time Extension Provisions', 'Force Majeure Clause']


def test_arbitration_excluded_and_a_30_month_period_are_flagged():
    risk_clauses = [{'label': 'Dispute resolution', 'text': 'Arbitration shall not be applicable to this contract.'}]
    score = analyze_compliance(summary({'riskClauses': risk_clauses, 'completionMonths': 30}))['score']
    assert score['riskCategories']['legal'] == 'Medium'
    assert score['riskCategories']['technical'] == 'Medium'


def test_older_reports_without_quoted_clauses_report_no_missing_clauses():
    assert analyze_compliance(summary({}))['score']['missingOrWeakClauses'] == []


# Risk clauses

def test_risk_clauses_skip_contents_pages_and_stop_at_the_next_numbered_heading():
    text = '\n'.join([
        'Index 1 Instructions for bidders for e-tendering 3-5 2 CPWD-6 6-11 3 Scope of work 14-15 4 Forms 16-24.',
        'Bids shall be submitted online on the CPPP portal only.',
        'If he does not do so within 3 days of receipt of such notice, the work shall be got done at his cost 4.0 Special Conditions for Steel apply here.',
    ])
    quotes = {c['label']: c['text'] for c in find_risk_clauses(text)}
    assert quotes['Online submission'].startswith('Bids shall be submitted online')
    assert 'Special Conditions' not in quotes['Short notice']


# Key dates

def pick(text: str) -> dict:
    return {d['kind']: f"{d['date']} {d['time']}" if d.get('time') else d['date'] for d in find_key_dates(text)}


def test_cpwd_nit_table_with_wrapped_label_and_time_before_date():
    text = '\n'.join([
        'Stipulated Period of Completion', 'of work (in months)', '15 Months',
        'Last time & date of submission', 'of online bid, copy of receipt of', 'deposition of original EMD and',
        'other documents as specified in', 'Notice Inviting e-Tender.', '03:00 PM on 01.09.2026',
        'Time date of opening of bid 03:30 PM on 01.09.2026',
    ])
    assert pick(text) == {'submission': '2026-09-01 15:00', 'opening': '2026-09-01 15:30'}


def test_portal_rows_24_hour_times_pre_bid_and_noon():
    assert pick(
        'Bid Submission Last Date 01-12-2018 till 03:00 PM\nBid Opening Date 01-12-2018 at 03:30 PM\n'
        'Pre-Bid meeting Date & Time Dt. 20.03.2026 at 12.00 noon'
    ) == {'submission': '2018-12-01 15:00', 'opening': '2018-12-01 15:30', 'preBid': '2026-03-20 12:00'}
    assert pick('Last date of online submission\nof e-Tender 08/01/2024 up to\n15.00 hours\nOpening of online technical Bid\n09/01/2024 at 16.00 hours') == {
        'submission': '2024-01-08 15:00', 'opening': '2024-01-09 16:00',
    }


def test_a_range_gives_its_end_as_the_deadline():
    assert pick('5 Bid clarification From 20-12-2024 at 11:00 hrs to 06-01-2025 at 14:30 hrs') == {'queries': '2025-01-06 14:30'}


def test_dates_from_other_columns_and_unlabelled_dates_are_ignored():
    assert pick('Time and date of opening of tenders 1. Renovation of toilets 7,04,913/- 2months 14,100/- 30.6.16 up to 4.00 PM') == {}
    assert pick('Turnover in the last five financial years ending 31.03.2026 shall be at least Rs. 5 crore.') == {}


def test_days_until_and_formatting():
    assert days_until('2026-10-20', date(2026, 10, 16)) == 4
    assert days_until('2026-09-01', date(2026, 10, 16)) == -45
    assert format_key_date({'date': '2026-09-01', 'time': '15:00'}) == '01 Sept 2026, 15:00'


def test_skips_portal_rows_reads_tender_opening_and_ocr_split_times():
    portal = '13 Date & time of pre-bid meeting : As per CPP portal\n14 Venue of pre-bid meeting : As per CPP portal\n16 Tentative date of technical presentation : 3rd June 2026'
    assert find_key_dates(portal) == []
    assert dates_on_portal(portal) is True
    spa = '• Date and Place of Tender Opening: 14:00 Hours on 11th May 2026 at Conference Room'
    assert [(d['kind'], d['date'], d['time']) for d in find_key_dates(spa)] == [('opening', '2026-05-11', '14:00')]
    scan = 'Pre- bi d Mee ting Date, Time & Pl ace At. 11.00. A.M on 19.02.2026\nDate and time of opening of bid on 11 .00 A.M at 25.02.2026'
    assert [(d['kind'], d['time']) for d in find_key_dates(scan)] == [('opening', '11:00'), ('preBid', '11:00')]


# Schedule of quantities. Shaped like CPWD schedules in real tender PDFs: wrapped descriptions, figures on the row's
# last line, page furniture between rows, rows split over short lines, and numbers inside descriptions.

CIVIL = """Schedule of Quantity
for
Civil Work

Correction – Nil Insertion – Nil Deletion – Nil AE(P) EE (P) Page 104
SCHEDUE OF QUANTITY
Name of Work : Construction of Office building.
Item
No. Description of item Qty. Unit Rate
(in Rs.)
Amount
(in Rs.)
1 Earth Work
1.1
Earth work in excavation by
mechanical means.
1.1.1 All kinds of soil 4,190.00 Cum 219.34 9,19,035
1.2
Filling available excavated earth
in trenches.
4,190.00 Cum 242.2 10,14,818
3 R.C.C. Work
3.2
Add for using extra cement in the items
of design mix.
754
Quintal
906.39 6,83,418
Total 26,17,271

PART-C
SCHEDULE OF QUANTITY (E&M WORK)
Item
No. Description of Item Qty. Unit Rate Amount
SUB HEAD - I (Point Wiring)
1
Wiring for light point with 1.5 sq.mm
FRLSH copper conductor cable.
(a) Group C 226 Points 2590.53 585459.78
2
Wiring for group controlled point with
1.5 sq. mm FRLSH copper conductor cable.
(a) Group C 128 Points 1536.32 196648.96
3 PANEL 1 (GROUND FLOOR PANEL)
Design, manufacture and supply of a panel.
complete panel as above and complete 1.00 No. 158217.00 158217.00
Total 9,40,325.74"""


def test_reads_a_cpwd_schedule_of_quantities_row_by_row():
    civil, em = find_boq(CIVIL)
    assert civil['title'] == 'Civil work'
    assert [i['no'] for i in civil['items']] == ['1.1.1', '1.2', '3.2']
    assert civil['items'][0]['description'] == 'Earth work in excavation by mechanical means. — All kinds of soil'
    assert civil['items'][0]['section'] == 'Earth Work'
    item = civil['items'][2]
    assert [item['quantity'], item['unit'], item['amount']] == [754, 'Quintal', 683418]
    assert civil['statedTotal'] == 2617271
    assert sum(i['amount'] for i in civil['items']) == civil['statedTotal']

    assert em['title'] == 'Electrical and mechanical work'
    # "1.5 sq. mm" inside item 2 is text, not item 1.5; "3 PANEL 1" is an item, not a heading.
    assert [i['no'] for i in em['items']] == ['1 (a)', '2 (a)', '3']
    assert em['items'][2]['description'].startswith('PANEL 1 (GROUND FLOOR PANEL) Design')
    assert em['items'][0]['section'] == 'I (Point Wiring)'
    assert round_half_up(sum(i['amount'] for i in em['items']) * 100) / 100 == em['statedTotal']


def test_no_schedule_no_parts():
    assert find_boq('Notice inviting tender. Estimated cost Rs. 10,00,000. Time allowed 6 months.') == []
    assert rupees('Rs. 15,64,86,955/-') == 156486955


def test_reads_per_letter_units_wrapped_units_and_the_grand_total():
    text = """SCHEDULE OF QUANTITIES
2.5 Lettering with black Japan paint of approved brand
20760 per
letter
per cm
height
6.15 127674.00
2.6 Providing and applying white cement based putty 665 sqm 156.05 103773.00
3 Providing services of manpower
3.1 IP-PBX system complete as required.
1 Each 1537488.1
2 1537488.12
a) Khallasi 24
Per
Person
Per
Month
29607.00 7,10,568.00
Total of Sub Head I 7,10,568.00
Grand Total
(I+II)
2479503.12"""
    [part] = find_boq(text)
    assert [[i['quantity'], i['unit'], i['amount']] for i in part['items']] == [
        [20760, 'per letter per cm height', 127674],
        [665, 'sqm', 103773],
        [1, 'Each', 1537488.12],
        [24, 'Per Person Per Month', 710568],
    ]
    assert part['statedTotal'] == 2479503.12


# Company file and proformas

AS_OF = date(2026, 10, 16)
COMPANY = {
    'legalName': 'Himgiri Buildcon Private Limited',
    'registrationClass': 'Class I (Civil)',
    'registeredAddress': 'Parwanoo',
    'registrationAuthority': 'CPWD',
    'registrationNumber': 'ENL/HP/CIV/2019/0482',
    'signatoryName': 'Rajesh Kumar Thakur',
    'signatoryDesignation': 'Director',
    'turnover': [
        {'year': '2025-26', 'lakh': 6894},
        {'year': '2024-25', 'lakh': 6127},
        {'year': '2023-24', 'lakh': 5280},
        {'year': '2022-23', 'lakh': None},
        {'year': '2021-22', 'lakh': 3842},
    ],
    'worksInHandLakh': 4200,
    'similarWorks': [{'name': 'Administrative Block, Sundernagar', 'client': 'HP PWD', 'valueLakh': 2208, 'start': '04/2021', 'completion': '09/2022'}],
    'keyStaff': [{'name': 'A. Sharma', 'qualification': 'B.E. Civil', 'years': 14}],
    'machinery': 'Batching plant 30 cum/hr\nTower crane',
}


def test_financial_years_run_april_to_march():
    assert last_five_years(AS_OF) == ['2025-26', '2024-25', '2023-24', '2022-23', '2021-22']
    assert last_five_years(date(2026, 2, 1))[:2] == ['2024-25', '2023-24']


def test_lakh_amounts_format_the_indian_way():
    assert format_lakh(82.5) == '₹ 82.50 lakh'
    assert format_lakh(1564.87) == '₹ 1,564.87 lakh (₹ 15.65 crore)'


def test_bid_capacity_is_2an_minus_b_with_a_at_current_prices():
    result = bid_capacity(COMPANY, 15, AS_OF)
    # 2025-26 is one year back: 6894 × 1.07 = 7376.58, beating 6127 × 1.14 = 6984.78
    assert result['aYear'] == '2025-26'
    assert result['a'] == 7376.58
    assert result['n'] == 1.25
    assert result['capacity'] == round_half_up((2 * 7376.58 * 1.25 - 4200) * 100) / 100
    assert bid_capacity({**COMPANY, 'worksInHandLakh': None}, 15, AS_OF) is None
    assert bid_capacity(COMPANY, None, AS_OF) is None


def test_proformas_use_the_company_file_and_keep_blanks_only_where_it_is_empty():
    given = {'nit_ref': '05/2026-27', 'tender_title': 'Office building', 'company': COMPANY, 'kind': 'works', 'months': 15, 'as_of': AS_OF}
    capacity = bid_capacity_proforma(**given)
    assert '**A** = ₹ 7,376.58 lakh' in capacity
    assert '1. Administrative Block, Sundernagar, HP PWD, ₹ 2,208.00 lakh' in capacity
    assert '\n2. [Name of work]' in capacity
    assert 'A. Sharma, B.E. Civil, 14 years' in capacity
    assert '- Tower crane' in capacity
    assert 'I, Rajesh Kumar Thakur, Director of Himgiri' in declarations(**given)
    assert 'CPWD, Class I (Civil), No. ENL/HP/CIV/2019/0482' in document_checklist(**given)
    assert '- FY 2025-26: ₹ 6,894.00 lakh' in past_experience(**{**given, 'kind': 'supply'})

    bare = {**given, 'company': {'legalName': 'X', 'registrationClass': 'Class II', 'registeredAddress': 'Y'}}
    assert '**A** = maximum value' in bid_capacity_proforma(**bare)
    assert '[name of authorised signatory], [designation]' in declarations(**bare)
