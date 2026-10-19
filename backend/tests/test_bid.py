"""The foundation bid's checks on model drafts, and the goods and services variants."""

import re
from dataclasses import replace

from proposalpanda.bid.generator import (
    INFRASTRUCTURE_PROGRAMME,
    MAINTENANCE_PROGRAMME,
    MODEL_SECTIONS,
    DraftFacts,
    ModelSection,
    find_draft_problem,
    fit_brief_to_tender,
    grades_in,
    is_codes_in,
    key_clause_lines,
    methodology_items,
    parse_programme,
    redact_unknown_grades,
    redact_unknown_standards,
    render_programme,
    sections_for,
    tidy_draft,
)
from proposalpanda.bid.templates import declarations, document_checklist, financial_bid

SECTION = ModelSection(id='s', title='Section', cover='technical', brief='')
CLAUSES = '\n'.join([
    '- Compensation for delay (Clause 2): 1.5% per month, maximum 10%.',
    '- Price variation / escalation clause (10CC): Does not apply.',
    '- Similar works: three works of Rs. 7.45 crore each.',
])
FACTS = DraftFacts(
    months=18, emd_amount='Rs. 27,24,900', clauses=CLAUSES, nit_ref='14/EE/PCD-II/2026-27',
    tender_title='Hostel block', pan='ABCDE1234F', gstin='27ABCDE1234F1Z5',
)
SHORT = DraftFacts(months=3, clauses='', nit_ref='X', tender_title='X', pan='ABCDE1234F', gstin='27ABCDE1234F1Z5')


def test_rejects_drafts_that_invent_company_eligibility_facts():
    invented = [
        'Our average annual financial turnover during the last three consecutive financial years was Rs. 10.11 crore.',
        'Our company has completed similar works in the past, meeting the eligibility criteria.',
        'We confirm that our company has not incurred any loss in more than two years.',
        'We confirm that our company has the necessary technical capabilities.',
        'We are confident that our technical capabilities, experience, and resources make us an ideal candidate.',
        'The bidder has valid EPF/ESI registration.',
        'Quality tests will include cube tests and certification by a registered Chartered Accountant.',
        'GSTIN and PAN (ABCD E1234F) enclosed.',
    ]
    for text in invented:
        assert find_draft_problem(SECTION, f'1. {text}', FACTS), text


def test_accepts_drafts_that_only_restate_tender_requirements():
    ok = ('1. Average annual turnover of Rs. 7.45 crore is required; supporting documents enclosed at [Annexure __].\n'
          '2. Please clarify whether Clause 2 compensation is capped at 10%.')
    assert find_draft_problem(SECTION, ok, FACTS) is None


def test_parses_grouped_and_tabular_month_ranges():
    assert len(parse_programme('Month 1: Mobilisation\nMonth 16-18: Finishing')) == 4
    assert len(parse_programme('Months 1-3: Foundations\nMonths 16 to 18: Finishing')) == 6
    assert len(parse_programme('| Month 1-3 | Earthwork | Foundations |\n| Month 16-18 | Finishing |')) == 6
    assert parse_programme('| Month 1-3 | Earthwork | Foundations |')[2] == 'Earthwork; Foundations'


def test_programme_has_one_line_per_month_and_leaves_out_defect_liability():
    draft = 'Month 1: Mobilisation\nMonths 2-4: Foundations\nMonth 16-18: Finishing, defect liability period'
    lines = [line for line in render_programme(draft, 18).split('\n') if line.startswith('Month')]
    assert len(lines) == 18
    assert lines[2] == 'Month 3: Foundations'
    assert lines[17] == 'Month 18: Finishing'
    assert lines[5] == 'Month 6: [activities to be planned]'
    assert not re.search('defect liability', '\n'.join(lines), re.I)


def test_strips_the_model_preamble_and_closing_note():
    assert tidy_draft('Here is the "Letter" section of the bid for X:\n\nDear Sir,\nBody.\n\nNote: based on the specifications.') == 'Dear Sir,\nBody.'


def test_compliance_may_only_mark_clauses_not_applicable_where_the_analysis_does():
    compliance = MODEL_SECTIONS['compliance']
    assert 'Clause 2' in (find_draft_problem(compliance, 'Compensation for delay under Clause 2: Does not apply for this work.', FACTS) or '')
    assert find_draft_problem(compliance, 'Price variation under Clause 10CC: Does not apply.', FACTS) is None


def test_transmittal_must_state_the_emd_amount():
    letter = MODEL_SECTIONS['transmittal']
    assert find_draft_problem(letter, 'EMD of Rs. [insert value] enclosed.', FACTS)
    assert find_draft_problem(letter, 'EMD of Rs. 27,24,900/- enclosed.', FACTS) is None


def test_accepts_tax_ids_quoted_exactly():
    assert find_draft_problem(SECTION, 'GSTIN 27ABCDE1234F1Z5, PAN ABCDE1234F.', FACTS) is None


def test_drops_queries_that_misquote_the_tender_then_needs_enough_left():
    draft = '\n'.join([
        '1. Considering the absence of a clear dispute resolution mechanism, please clarify.',
        '2. Is delay compensation not covered by Clause 10CC?',
        '3. Will the price variation exclusion under Clause 10CC be reviewed for steel?',
        '4. How is the security deposit recovered from running bills?',
        '5. Who bears GRIHA certification costs?',
        '6. Is RMC plant approval by the department required?',
        '7. Will site access be available through the monsoon?',
    ])
    queries = MODEL_SECTIONS['queries']
    cleaned = queries.clean(draft)
    assert cleaned == '\n'.join([
        '1. Will the price variation exclusion under Clause 10CC be reviewed for steel?',
        '2. Who bears GRIHA certification costs?',
        '3. Is RMC plant approval by the department required?',
        '4. Will site access be available through the monsoon?',
    ])
    # Four good queries are kept; fewer than three is withheld.
    assert find_draft_problem(queries, cleaned, FACTS) is None
    assert 'only 2' in (find_draft_problem(queries, '\n'.join(cleaned.split('\n')[:2]), FACTS) or '')


def test_rejects_a_programme_that_lays_masonry_before_the_frame():
    programme = MODEL_SECTIONS['programme']
    draft = 'Months 1-3: Foundations\nMonths 4-6: AAC masonry\nMonths 7-12: RCC frame\nMonths 13-18: Flooring'
    assert 'masonry before RCC frame' in (find_draft_problem(programme, draft, FACTS) or '')
    ok = 'Months 1-3: Foundations\nMonths 4-9: RCC frame\nMonths 10-12: AAC masonry\nMonths 13-18: Flooring'
    assert find_draft_problem(programme, ok, FACTS) is None


def test_compliance_carries_the_key_clauses_from_the_analysis():
    out = MODEL_SECTIONS['compliance'].finish('1. EMD enclosed.', FACTS)
    assert '- Compensation for delay (Clause 2): 1.5% per month, maximum 10%.' in out
    assert '10CC): Does not apply' in out
    assert 'Similar works' not in out


def test_letter_gets_the_address_and_subject_line():
    out = MODEL_SECTIONS['transmittal'].finish('Sir,\nWe submit our bid.', FACTS)
    assert out.startswith('To,\nThe Executive Engineer,')
    assert 'Sub: Submission of bid for "Hostel block" against NIT No. 14/EE/PCD-II/2026-27\n\nSir,' in out


def test_key_clauses_keep_the_value_lines_under_their_headings():
    run6 = '\n'.join([
        '- Similar works:',
        '  - Three works of Rs. 7.45 crore each.',
        '- Compensation for delay:',
        '  - Applies under Clause 2, with a maximum cap of 10% of the tendered value.',
        '- Price variation / escalation clause (e.g. 10CC):',
        '  - Does not apply to this work.',
        '- Mobilisation or secured advance:',
        '  - 10% of the tendered value at 10% simple interest against a bank guarantee of 110%.',
        '- Security deposit and performance guarantee:',
        '  - Security Deposit: 2.5% of the tendered value, to be recovered from running bills.',
    ])
    lines = key_clause_lines(run6, '1.5% per month, maximum 10%')
    assert lines[0] == 'Compensation for delay (Clause 2): 1.5% per month, maximum 10%'
    assert any(re.search(r'10CC\): Does not apply', line) for line in lines)
    assert any(re.search(r'advance: 10% .*10% simple interest', line) for line in lines)
    assert not any('Similar works' in line for line in lines)
    # Unindented values directly under a heading are kept too
    assert key_clause_lines('Price variation (10CC):\nDoes not apply.\nSimilar works: three works.') == ['Price variation (10CC): Does not apply.']


def test_letter_is_addressed_to_the_inviting_office():
    facts = replace(FACTS, inviting_office='Executive Engineer, Pune Central Division-II, Nirman Bhawan, Pune - 411001')
    out = MODEL_SECTIONS['transmittal'].finish('Sir,', facts)
    assert out.startswith('To,\nThe Executive Engineer,\nPune Central Division-II,\nNirman Bhawan,\nPune - 411001\n')


def test_programme_schedules_the_road_when_the_draft_leaves_it_out():
    lines = render_programme('Months 1-3: Foundations\nMonths 4-9: RCC frame\nMonths 10-18: Finishes', 18, True).split('\n')
    assert lines[15] == 'Month 16: Finishes; approach road and drainage works (outside monsoon)'
    assert 'road' not in lines[14]


def test_queries_need_at_least_five_numbered_lines():
    assert 'only 1' in (find_draft_problem(MODEL_SECTIONS['queries'], '1. Please clarify RMC plant approval.', FACTS) or '')


def test_replaces_is_codes_the_tender_never_mentions():
    known = is_codes_in('Concrete to IS 10262:2019; AAC blocks to IS 2185 (Part 3).')
    assert redact_unknown_standards('APP membrane to IS 732:1973; mix to IS 10262:2019; blocks to IS 2185 Part 3.', known) == (
        'APP membrane to [IS code as per tender]; mix to IS 10262:2019; blocks to IS 2185 Part 3.'
    )


def test_rejects_price_commitments_and_wrong_road_terms():
    assert find_draft_problem(SECTION, 'We will execute the work within the specified budget.', FACTS)
    assert find_draft_problem(SECTION, 'GSB (Good Strength Base) shall be laid.', FACTS)


def test_compliance_drops_its_own_lines_on_quoted_terms_and_quotes_the_nit():
    compliance = MODEL_SECTIONS['compliance']
    draft = '1. EMD of Rs. 27,24,900 enclosed.\n2. The bidder shall recover the security deposit.\n3. GRIHA measures will be followed.'
    cleaned = compliance.clean(draft)
    assert cleaned == '1. EMD of Rs. 27,24,900 enclosed.\n2. GRIHA measures will be followed.'
    facts = replace(FACTS, key_terms=[{'label': 'Price variation (Clause 10CC)', 'text': 'Clause 10CC shall not be applicable.'}])
    out = compliance.finish(cleaned, facts)
    assert re.search(r'quoted from the NIT[^\n]*\n- Price variation \(Clause 10CC\): "Clause 10CC shall not be applicable\."\Z', out)


def test_rejects_a_programme_that_idles_in_testing_and_handover():
    draft = ('Months 1-3: Foundations\nMonths 4-9: RCC frame\nMonths 10-12: Flooring and finishes\n'
             'Months 13-15: Testing and handover preparation\nMonths 16-18: Final testing')
    assert 'testing and handover only' in (find_draft_problem(MODEL_SECTIONS['programme'], draft, FACTS) or '')


def test_goods_and_services_tenders_get_their_own_sections_and_proformas():
    supply = sections_for('supply')
    assert supply['methodology'].title == 'Supply, Delivery and Quality Plan'
    assert supply['scope'].title == 'Understanding of Scope of Supply'
    assert not re.search('GRIHA|RMC', supply['queries'].brief)
    assert sections_for('services')['methodology'].title == 'Service Delivery Methodology'
    assert sections_for('works') is MODEL_SECTIONS

    company = {'legalName': 'Panda Infra Pvt Ltd', 'registrationClass': 'Class I-A', 'gstin': '27ABCDE1234F1Z5',
               'panNumber': 'ABCDE1234F', 'registeredAddress': 'Pune'}
    given = {'nit_ref': 'GEM/2024/B/1', 'tender_title': 'Laptops', 'company': company}
    assert 'OEM authorisation' in document_checklist(**given, kind='supply')
    assert not re.search('site inspection|bid capacity', document_checklist(**given, kind='supply'), re.I)
    assert 'Site Inspection' not in declarations(**given, kind='services')
    assert 'Site Inspection' in declarations(**given, kind='works')
    assert 'Minimum wages' in financial_bid(**given, kind='services')
    assert re.search(r'^16\. Integrity Pact', document_checklist(**given, kind='works'), re.M)


def test_briefs_mention_griha_and_rmc_only_when_the_tender_does():
    brief = MODEL_SECTIONS['queries'].brief
    assert not re.search(r'GRIHA|RMC|\(e\.g\.\s*\)|\s,', fit_brief_to_tender(brief, 'minor maintenance civil works, paver blocks'))
    assert 'GRIHA, RMC plant approval' in fit_brief_to_tender(brief, 'achieve GRIHA 3-star; ready mix concrete from RMC plant')
    assert 'GRIHA' not in fit_brief_to_tender(MODEL_SECTIONS['methodology'].brief, 'boundary wall')


def test_a_maintenance_contract_programme_may_not_invent_new_construction():
    built = 'Month 1: Mobilisation\nMonth 2: Construction of RCC frame, floor by floor\nMonth 3: Testing'
    assert 'new construction' in (find_draft_problem(MAINTENANCE_PROGRAMME, built, SHORT) or '')
    ok = 'Month 1: Mobilisation and paver block repairs\nMonth 2: Sanitary repairs as per work orders\nMonth 3: Painting'
    assert find_draft_problem(MAINTENANCE_PROGRAMME, ok, SHORT) is None


def test_methodology_covers_only_named_items_and_unknown_grades_are_redacted():
    road = ('Construction of Service road and Diversion road at LC No. 66. Earthwork in embankment, GSB, WMM, '
            'bituminous surfacing. NP3 hume pipe culverts.')
    assert methodology_items(road) == 'earthwork and foundations; roads; bridges and culverts; drainage'
    assert re.search('RCC superstructure.*masonry.*flooring.*water supply', methodology_items('G+4 RCC framed quarters with AAC masonry, vitrified flooring, water supply'))
    assert methodology_items('nothing recognisable').startswith('earthwork and foundations; RCC superstructure')
    tender = grades_in('Concrete M-20 and steel Fe 415 as per MORTH.')
    assert redact_unknown_grades('Use M25 concrete and Fe500D bars; M20 for culverts with Fe 415.', tender) == (
        'Use [concrete grade as per tender] concrete and [steel grade as per tender] bars; M20 for culverts with Fe 415.'
    )


def test_road_building_and_maintenance_works_get_their_own_programmes():
    building = 'Month 1: Mobilisation\nMonth 2: RCC frame floor by floor\nMonth 3: Masonry and flooring'
    assert 'building work' in (find_draft_problem(INFRASTRUCTURE_PROGRAMME, building, SHORT) or '')
    road = 'Month 1: Mobilisation and survey\nMonth 2: Earthwork and GSB\nMonth 3: WMM, bituminous surfacing, handover'
    assert find_draft_problem(INFRASTRUCTURE_PROGRAMME, road, SHORT) is None


def test_a_one_page_notice_that_names_no_items_gets_the_common_items_without_griha():
    assert 'GRIHA' not in methodology_items('Tender notice. EMD by demand draft.')


def test_road_works_get_only_road_items_and_is_codes_with_a_year_are_redacted_whole():
    text = 'Service road. NP3 class RCC pipes. Earthwork. Bituminous macadam. Flooring as per general conditions.'
    assert methodology_items(text, 'infrastructure', 'Construction of Service road') == 'earthwork and foundations; roads; drainage'
    assert redact_unknown_standards('concrete as per IS 456-2000', set()) == 'concrete as per [IS code as per tender]'
    assert tidy_draft('Plan.\n\nPlease note that this section only provides a general outline.') == 'Plan.'


def test_grades_printed_with_a_unicode_hyphen_count_as_mentioned():
    assert grades_in('80 mm thick C.C. paver block of M‐35 grade') == {'M35'}
