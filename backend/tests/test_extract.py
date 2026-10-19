"""Reading text out of uploaded tender files."""

import re

import pytest

from files import write_docx, write_pdf

from proposalpanda.engine.facts import find_emd_amount, find_nit_reference
from proposalpanda.engine.text_extractor import UnreadableDocumentError, extract_pdf_text, extract_text


def test_keeps_the_line_breaks_of_a_text_pdf_so_label_value_rows_stay_readable(tmp_path):
    file = write_pdf(tmp_path / 'nit.pdf', [
        ['NOTICE INVITING TENDER', 'NIT No: 50/Civil/D1/2026-27', 'Earnest Money Deposit (Rs.) : Rs. 33446/-'],
        ['Second page'],
    ])
    text = extract_pdf_text(str(file))
    assert re.search(r'NIT No: 50/Civil/D1/2026-27\n', text)
    assert text.endswith('\n\nSecond page')
    assert find_nit_reference(text) == '50/Civil/D1/2026-27'
    assert find_emd_amount(text) == 'Rs. 33446'


def test_rejects_a_docx_with_no_readable_text_instead_of_summarising_nothing(tmp_path):
    with pytest.raises(UnreadableDocumentError):
        extract_text(str(write_docx(tmp_path / 'empty.docx', [''])))


def test_accepts_a_short_but_non_empty_docx_such_as_a_corrigendum(tmp_path):
    text = extract_text(str(write_docx(tmp_path / 'short.docx', ['Corrigendum 1: bid due date extended to 20/10/2026.'])))
    assert 'Corrigendum 1' in text


def test_a_pdf_with_no_text_layer_is_unreadable(tmp_path):
    with pytest.raises(UnreadableDocumentError):
        extract_text(str(write_pdf(tmp_path / 'scan.pdf', [[]])))
