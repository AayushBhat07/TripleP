"""The deterministic fact finders, on layouts copied from real tenders."""

import re

import pytest

from proposalpanda.engine.facts import (
    SCHEDULE_WISE_EMD,
    find_completion_months,
    find_delay_compensation,
    find_eligibility,
    find_emd_amount,
    find_estimated_cost,
    find_guarantee_conflict,
    find_inviting_office,
    find_key_terms,
    find_name_of_work,
    find_nit_reference,
    find_risk_clauses,
    find_tender_kind,
    find_works_type,
    is_maintenance_work,
)
from proposalpanda.engine.summarizer import TenderSummarizer, ground_section, pick_chunks, select_source_text
from proposalpanda.engine.text_extractor import UnreadableDocumentError


def by_label(items):
    return {t['label']: t['text'] for t in items}


def test_finds_the_nit_reference_as_printed():
    assert find_nit_reference('CPWD\nNIT No. 14/EE/PCD-II/2026-27 for construction of') == '14/EE/PCD-II/2026-27'
    assert find_nit_reference('N.I.T. No: 05/SE/2025-26.') == '05/SE/2025-26'
    assert find_nit_reference('e-Tender No. PWD-MH/2026/112 dated') == 'PWD-MH/2026/112'
    assert find_nit_reference('NOTICE INVITING e-TENDER (NIT) No. 14/EE/PCD-II/2026-27') == '14/EE/PCD-II/2026-27'


def test_returns_none_when_no_reference_is_present():
    assert find_nit_reference('Public Works Department invites bids for road works.') is None


def test_finds_the_completion_period_in_months():
    assert find_completion_months('Period of completion: 18 (Eighteen) months including monsoon') == 18
    assert find_completion_months('Time allowed for completion of work 12 months') == 12
    assert find_completion_months('Completion period 18 months including monsoon.') == 18
    assert find_completion_months('No period here') is None


def test_finds_emd_and_estimated_cost_as_printed():
    nit = 'Estimated cost put to tender: Rs. 14,90,00,000\nEarnest Money Deposit (EMD): Rs.27,24,900.'
    assert find_emd_amount(nit) == 'Rs. 27,24,900'
    assert find_estimated_cost(nit) == 'Rs. 14,90,00,000'
    assert find_emd_amount('No deposit is mentioned here') is None


def test_does_not_repeat_the_document_head_in_the_source_excerpt():
    head = 'Name of Work:\n\nConstruction  of  hostel. ' + 'x ' * 2100
    full_text = head + 'Concrete grade M25 specification. ' + 'y ' * 500
    chunks = [
        {'index': 0, 'text': ' '.join(re.split(r'\s+', full_text)[:300]), 'tokenCount': 0, 'source': 'Full Text'},
        {'index': 1, 'text': 'Concrete grade M25 specification. ' + ('y ' * 50).strip(), 'tokenCount': 0, 'source': 'Full Text'},
    ]
    excerpt = select_source_text(full_text, chunks)
    assert excerpt.count('Name of Work') == 1
    assert re.search(r'\.\.\.\nConcrete grade M25', excerpt)


def test_finds_delay_compensation_and_the_inviting_office():
    assert (
        find_delay_compensation('compensation @ 1.5 % per month of delay, subject to a maximum of 10% of the tendered value')
        == '1.5% per month, maximum 10%'
    )
    assert (
        find_inviting_office('Office of the Executive Engineer, Pune Central Division-II, Nirman Bhawan, Pune - 411001\nNIT')
        == 'Executive Engineer, Pune Central Division-II, Nirman Bhawan, Pune - 411001'
    )


def test_quotes_key_contract_terms_from_the_tender_text():
    nit = '\n'.join(
        [
            'The Security Deposit shall be collected as per Clause 1A.',
            'Security Deposit @ 2.5% of the tendered value shall be recovered from running bills. It shall be refunded after the defect liability period of 12 months.',
            'Compensation for delay under Clause 2 shall be levied @ 1.5% per month of delay, subject to a maximum of 10% of the tendered value.',
            'Clause 10CC (price variation) shall not be applicable to this work.',
            'Mobilisation advance up to 10% of the tendered value at 10% simple interest against a BG of 110%.',
            'Performance Guarantee of 5% of tendered value within 15 days, extendable by 7 days with late fee of 0.1% per day.',
        ]
    )
    terms = by_label(find_key_terms(nit))
    assert terms['Price variation (Clause 10CC)'] == 'Clause 10CC (price variation) shall not be applicable to this work.'
    assert re.match(r'Compensation for delay under Clause 2 .*1\.5% per month.*10%', terms['Compensation for delay (Clause 2)'])
    assert (
        terms['Security deposit']
        == 'Security Deposit @ 2.5% of the tendered value shall be recovered from running bills. It shall be refunded after the defect liability period of 12 months.'
    )
    assert '10% simple interest' in terms['Mobilisation advance']
    assert '7 days' in terms['Performance guarantee']


def test_quotes_risk_clauses_from_the_tender_text_and_skips_absent_ones():
    text = '\n'.join(
        [
            'Bids shall be submitted online through the CPWD e-tendering portal.',
            'The performance guarantee shall be an unconditional bank guarantee. The EMD shall be forfeited if the bidder withdraws.',
            'Disputes shall be referred to arbitration under Clause 25.',
        ]
    )
    assert [c['label'] for c in find_risk_clauses(text)] == ['Unconditional guarantee', 'Forfeiture', 'Dispute resolution', 'Online submission']


# Layouts copied from real tenders (IIT Kanpur, AIIMS Raipur, PRL/ISRO, DFCCIL, BMC, GeM, East Central Railway).
def test_reads_nit_numbers_in_the_forms_real_tenders_print_them():
    assert find_nit_reference('NIT No: 50/Civil/D1/2026-27\n1 Name of work') == '50/Civil/D1/2026-27'
    assert find_nit_reference('N.I.T. NO. 21/EE/AIIMS/RPR/2018-19 (2nd Call) Date: - 24/11/2018') == '21/EE/AIIMS/RPR/2018-19'
    assert find_nit_reference('E-Tender Notice No.: PRL/CMG/e-Tender 19/2024-25 dated 07-11-2024') == 'PRL/CMG/e-Tender 19/2024-25'
    assert find_nit_reference('Tender NO: TDL/EN/RPF POST BUILDING/2020/01 DATED: 21.10.2020') == 'TDL/EN/RPF POST BUILDING/2020/01'
    assert find_nit_reference('Bid No. - 2026_MCGM_1286135_1\nSubject: Supply') == '2026_MCGM_1286135_1'
    assert find_nit_reference('Bid Number/बोली क्रमांक (बिड संख्या):\nGEM/2024/B/4869384\nDated') == 'GEM/2024/B/4869384'
    assert find_nit_reference('No.EE/NIRD/2016-17/NIT/14 F.No. :EE/CMU/2015-16/259') == 'EE/NIRD/2016-17/NIT/14'
    assert find_nit_reference('as required in the NIT at CPP portal') is None


def test_reads_completion_periods_written_in_words_days_and_table_rows():
    assert find_completion_months('4 Duration of contract : Twelve (12) months') == 12
    assert find_completion_months('4 Duration of contract : Two (02) Months') == 2
    assert find_completion_months('TIME ALLOWED: 90 (Ninety) Days') == 3
    assert find_completion_months('(b) Completion Period 12 Months') == 12
    assert find_completion_months('1.4 Duration of Contract 06 months') == 6
    assert find_completion_months('Duration of 30 Minutes') is None


def test_reads_amounts_from_rows_next_line_cells_and_lakh_figures_but_not_thresholds_or_rules():
    assert find_emd_amount('3 Earnest Money Deposit (Rs.) : Rs. 33446/-') == 'Rs. 33446'
    assert find_estimated_cost('2 Estimated cost (including GST) : Rs. 1672324/-, The cost is for 12 months.') == 'Rs. 1672324'
    assert find_estimated_cost('ESTIMATED COST\nPUT TO TENDER: Rs. 38, 32,203/-\nEARNEST MONEY: Rs. 76,700/-') == 'Rs. 38,32,203'
    assert find_emd_amount('ESTIMATED COST\nPUT TO TENDER: Rs. 38, 32,203/-\nEARNEST MONEY: Rs. 76,700/-') == 'Rs. 76,700'
    isro = '\n'.join(
        [
            'Estimated cost put to tender रु',
            '19.66 लाख',
            '₹ 19.66 Lakhs',
            'Earnest Money Deposit (EMD)',
            '₹ 39,320 /-',
            'similar works each costing not less than the amount equal to 40% of the estimated cost (i.e. ₹ 7.86 lakhs)',
        ]
    )
    assert find_emd_amount(isro) == 'Rs. 39,320'
    assert find_estimated_cost('\n'.join(isro.split('\n')[3:])) is None
    assert find_estimated_cost(isro) == 'Rs. 19.66 lakh'
    assert find_emd_amount('b) EMD will be Rs. 50 lakh for tenders valuing above Rs. 50 Cr.') is None
    assert find_estimated_cost('Works having estimated value of Rs. 10 lakhs and above.') is None
    assert find_emd_amount('5.2.1 Amount of EMD (rounded off to nearest higher Rs. 10 (ten))') is None


def test_reads_clause_10cc_from_cpwd_schedule_f_tables():
    iitk = 'Clause 10 CA NOT APPLICABLE\nClause 10 CC Increase/Decrease in Price of\nmaterials/wages\nNOT APPLICABLE\nClause 11 CPWD Specifications of'
    assert by_label(find_key_terms(iitk))['Price variation (Clause 10CC)'] == 'Clause 10 CC Increase/Decrease in Price of materials/wages NOT APPLICABLE.'
    aiims = (
        'Clause10CC\nClause 10CC to be applicable in contracts\nwith sipulated period of compensation\n'
        'Exceeding the period shown in next column : Not Applicable\nMaterial covered under this'
    )
    assert by_label(find_key_terms(aiims))['Price variation (Clause 10CC)'].endswith(': Not Applicable.')


def test_skips_unfilled_template_blanks_and_quotes_wrapped_sentences_whole():
    text = '\n'.join(
        [
            'of Contract, Mobilization Advance up to ___% (___ percent) of the original contract.',
            'Mobilisation advance – This shall be limited to 10% of the contract value and payable',
            'in two instalments.',
        ]
    )
    assert (
        by_label(find_key_terms(text))['Mobilisation advance']
        == 'Mobilisation advance – This shall be limited to 10% of the contract value and payable in two instalments.'
    )


def test_tells_works_supply_and_services_tenders_apart():
    assert find_tender_kind('NOTICE INVITING TENDER\nName of work: Construction of boundary wall. Percentage rate tender.') == 'works'
    assert find_tender_kind('Bid Number: GEM/2024/B/1\nItem Category: Laptop\nConsignee details. Warranty 3 years.') == 'supply'
    assert find_tender_kind('Subject: Supply, Installation, Testing, Commissioning and Maintenance of Computers') == 'supply'
    assert find_tender_kind('Hiring of agencies for security personnel to safeguard municipal offices. Manpower.') == 'services'


def test_picks_the_chunks_most_about_a_topic_not_just_the_first_that_mention_it():
    def chunk(index, text):
        return {'index': index, 'text': text, 'tokenCount': 0, 'source': 'Full Text'}

    chunks = [
        chunk(0, 'Index. EMD scan copy. Notice.'),
        chunk(1, 'General instructions. EMD to be paid online.'),
        chunk(2, 'Schedule: Earnest money Rs 33446. Performance guarantee 5%. Security deposit 2.5%. Time allowed 12 months.'),
    ]
    picked = pick_chunks(chunks, ['emd', 'earnest money', 'performance guarantee', 'security deposit', 'time allowed'], 1)
    assert [c['index'] for c in picked] == [2]


def test_reads_the_name_of_work_including_wrapped_lines_and_spots_maintenance_contracts():
    assert (
        find_name_of_work(
            'NIT No: 50/Civil/D1/2026-27\n1 Name of work : Carrying out minor maintenance civil\nworks of Out reach Centre Noida.\n2 Estimated cost : Rs. 1672324/-'
        )
        == 'Carrying out minor maintenance civil works of Out reach Centre Noida'
    )
    assert (
        find_name_of_work(
            'NAME OF WORK: - “Raising of boundry wall height by cocertiana coil and\nmiscellaneous work at Residential Complex, AIIMS\nRaipur.”\nESTIMATED COST'
        )
        == 'Raising of boundry wall height by cocertiana coil and miscellaneous work at Residential Complex, AIIMS Raipur'
    )
    assert find_name_of_work('Sub: Submission of Technical Proposal.\nDear Sir,') is None
    assert find_name_of_work('No. Name of Work\nNature\nof\nWork') is None
    assert is_maintenance_work('Carrying out minor maintenance civil works of Out reach Centre Noida', '') is True
    assert is_maintenance_work('Construction of RPF Post building at New Khurja', '') is False


def test_gem_bids_with_schedule_wise_emd_report_no_single_figure():
    gem = 'Schedule 1 EMD Amount/ईएमडी राशि (In INR) 8454\nSchedule 2 EMD Amount/ईएमडी राशि (In INR) 7477'
    assert find_emd_amount(gem) == SCHEDULE_WISE_EMD


def test_tells_building_infrastructure_and_maintenance_works_apart():
    assert find_works_type('Construction of RPF Post building at New Khurja & New Ekdil', '') == 'building'
    assert find_works_type('Construction of Service road and Diversion road at LC No. 66', '') == 'infrastructure'
    assert find_works_type('Raising of boundry wall height by concertina coil', '') == 'infrastructure'
    assert (
        find_works_type('Raising of boundry wall height by cocertiana coil and miscellaneous work at Residential Complex, AIIMS Raipur', '')
        == 'infrastructure'
    )
    assert find_works_type('Carrying out minor maintenance civil works of Out reach Centre Noida', '') == 'maintenance'
    assert find_works_type(None, 'Construction of G+4 RCC framed quarters') == 'building'


@pytest.mark.parametrize('full_text', ['', '   \n\t '])
async def test_rejects_blank_text_before_any_model_call(full_text):
    with pytest.raises(UnreadableDocumentError):
        await TenderSummarizer().summarize_tender(full_text, 'EMPTY', 'Empty')


def test_chunking_still_moves_forward_when_the_overlap_is_as_large_as_the_chunk():
    chunks = TenderSummarizer(max_tokens_per_chunk=4, overlap_tokens=8).chunk_text('a b c d e f')
    assert 0 < len(chunks) <= 6


def test_quotes_eligibility_rules_with_their_or_options_skipping_the_document_checklist():
    text = '\n'.join(
        [
            '16. Copy of Financial turnover from CA along with audited balance sheets.',
            '',
            'The eligible contractors mean registered in appropriate class and category with CPWD, BSNL, MES.',
            'Average annual financial turnover of civil works should be at least 50% of the estimated cost put to tender during the last 3 years.',
            'i) One similar work of 80% value of the estimated cost put to tender Or ii) Two similar works of 60% value of the estimated cost put to tender Or iii) Three similar works of 40% value of the estimated cost put to tender.',
        ]
    )
    rules = by_label(find_eligibility(text))
    assert 'appropriate class and category with CPWD' in rules['Enlistment']
    assert 'at least 50%' in rules['Turnover']
    assert re.search(r'80%.*60%.*40%', rules['Similar works'])


def test_drops_invented_figures_and_clause_numbers_and_states_10cc_as_the_tender_does():
    tender = 'EMD Rs. 1,21,797/-. Clause 10 CC Increase/Decrease in Price of materials/wages NOT APPLICABLE. Clause 2 applies.'
    model = '\n'.join(['- EMD: Rs. 1,21,797', '- EMD (Earnest Money Deposit) is Rs. 12,179.70.', '- Clause number: 2.10A', '- Price variation (10CC): applies.'])
    assert (
        ground_section(model, tender, 'Clause 10 CC Increase/Decrease in Price of materials/wages NOT APPLICABLE.')
        == '- EMD: Rs. 1,21,797\n- Price variation (Clause 10CC): does not apply. The tender: "Clause 10 CC Increase/Decrease in Price of materials/wages NOT APPLICABLE."'
    )
    assert ground_section('- 10CC applies.', 'No escalation clause here.') == '- Price variation (Clause 10CC): Not specified in the tender.'
    assert (
        ground_section('- Similar works thresholds: Not specified in the tender.', '', None, [{'label': 'Similar works', 'text': 'Three similar works of 40%.'}])
        == '- Similar works: "Three similar works of 40%."'
    )


def test_reads_cost_and_emd_from_a_scanned_press_notice_table_row():
    scan = 'Sl. Tender Estimated Cost Earnest Money Eligibility of\nput to tender\nNo No Name of Work (Rs.) Deposit (Rs .) contractor\nN/2 025 · and Rs.6 ,65 , 42, 704 /· Rs.13, 30 ,854/·'
    assert find_estimated_cost(scan) == 'Rs. 6,65,42,704'
    assert find_emd_amount(scan) == 'Rs. 13,30,854'


def test_flags_a_performance_guarantee_stated_on_two_different_bases():
    text = '\n'.join(
        [
            'The contractor whose bid is accepted will be required to furnish performance guarantee of 5% of tendered value within the period specified in Schedule F.',
            '',
            'Performance Guarantee PG shall be 5% of the Estimated cost put to tender (ECPT) or contract amount whichever is higher.',
        ]
    )
    assert len(find_guarantee_conflict(text)) == 2
    assert (
        find_guarantee_conflict('Performance guarantee of 5% of tendered value. The performance guarantee of 5% of the contract amount shall be valid.')
        is None
    )
