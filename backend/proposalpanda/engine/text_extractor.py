"""Text from the uploaded tender: a PDF's text layer (or OCR for a scan), a .docx, or plain text."""

import logging
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import regex
from pypdf import PdfReader

log = logging.getLogger('proposalpanda')

# Below this many letters/digits a PDF is treated as having no text layer.
MIN_TEXT_CHARS = 200
# OCR is slow (a few seconds a page); NIT, eligibility and key conditions sit in the first pages.
MAX_OCR_PAGES = 40


def meaningful_chars(text: str) -> int:
    return len(regex.findall(r'[\p{L}\p{N}]', text))


class UnreadableDocumentError(Exception):
    """The uploaded document has no usable text. The message is safe to show the user."""


def extract_pdf_text(file_path: str) -> str:
    """
    PDF text with its line and page breaks kept. The patterns that find the NIT number, EMD, office and contract
    clauses work line by line, so flattening a PDF into one line breaks them.
    """
    pages = []
    for page in PdfReader(file_path).pages:
        text = page.extract_text() or ''
        pages.append(re.sub(r'[ \t]+\n', '\n', text).strip())
    return '\n\n'.join(pages)


def _ocr_pdf(file_path: str) -> str | None:
    """OCR a scanned PDF with poppler + tesseract when both are installed; None when they aren't."""
    if not shutil.which('pdftoppm') or not shutil.which('tesseract'):
        return None
    langs_out = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True).stdout.split('\n')
    langs = '+'.join(lang for lang in ('eng', 'hin') if lang in langs_out) or 'eng'
    with tempfile.TemporaryDirectory(prefix='tender-ocr-') as folder:
        subprocess.run(
            ['pdftoppm', '-r', '200', '-gray', '-png', '-l', str(MAX_OCR_PAGES), file_path, str(Path(folder) / 'p')],
            check=True,
            timeout=300,
            capture_output=True,
        )
        images = sorted(p for p in Path(folder).iterdir() if p.suffix == '.png')
        pages = [
            subprocess.run(['tesseract', str(image), '-', '-l', langs], check=True, timeout=120, capture_output=True, text=True).stdout.strip()
            for image in images
        ]
    log.info('OCR read %d page(s) with tesseract (%s)', len(images), langs)
    return '\n\n'.join(pages)


W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def extract_docx_text(file_path: str) -> str:
    """Raw text of a .docx, one paragraph per block (table cells included), as mammoth's extractRawText gives it."""
    with zipfile.ZipFile(file_path) as archive:
        root = ElementTree.fromstring(archive.read('word/document.xml'))
    paragraphs = []
    for paragraph in root.iter(f'{W}p'):
        parts = []
        for node in paragraph.iter():
            if node.tag == f'{W}t':
                parts.append(node.text or '')
            elif node.tag == f'{W}tab':
                parts.append('\t')
            elif node.tag in (f'{W}br', f'{W}cr'):
                parts.append('\n')
        paragraphs.append(''.join(parts) + '\n\n')
    return ''.join(paragraphs)


def extract_text(file_path: str) -> str:
    """Text of a .pdf, .docx, .txt or .md tender."""
    path = Path(file_path)
    try:
        if not path.exists():
            raise FileNotFoundError(f'File not found: {file_path}')
        ext = path.suffix.lower()
        if ext in ('.txt', '.md'):
            return path.read_text(encoding='utf-8')
        if ext == '.docx':
            text = extract_docx_text(file_path)
            # A short notice or corrigendum is still a valid tender document; only an empty one is not.
            if meaningful_chars(text) == 0:
                raise UnreadableDocumentError(
                    'No readable text found in the .docx. Check that it is the tender document and not empty.'
                )
            log.info('Extracted %d characters from .docx', len(text))
            return text
        if ext == '.pdf':
            text = extract_pdf_text(file_path)
            if meaningful_chars(text) >= MIN_TEXT_CHARS:
                return text
            ocr = _ocr_pdf(file_path)
            if ocr is None:
                raise UnreadableDocumentError(
                    'No text layer found in PDF (it looks scanned). Install poppler and tesseract (brew install poppler tesseract) '
                    'to read scans, or upload a text PDF.'
                )
            if meaningful_chars(ocr) < MIN_TEXT_CHARS:
                raise UnreadableDocumentError('No readable text found in PDF, even with OCR. Upload a clearer scan or a text PDF.')
            return ocr
        raise ValueError(f'Unsupported file format: {ext}')
    except UnreadableDocumentError:
        raise
    except Exception as error:
        raise RuntimeError(f'Failed to extract text: {error}') from error


def extract_chapters(full_text: str) -> list[dict]:
    """Chapter-wise text when the tender has markers like "CHAPTER 01: TITLE"; else the whole text as one."""
    matches = list(re.finditer(r'(?:CHAPTER|Ch\.?)\s+(\d{1,2})[:\-]\s*([^\n]+)', full_text, re.I))
    if not matches:
        return [{'chapterId': '00', 'title': 'Full Document', 'content': full_text}]
    chapters = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i < len(matches) - 1 else len(full_text)
        chapters.append(
            {'chapterId': match[1].rjust(2, '0'), 'title': match[2].strip(), 'content': full_text[match.end() : end].strip()}
        )
    return chapters


def clean_text(text: str) -> str:
    """Normalise line endings, collapse runs of blank lines, tabs to spaces, no trailing spaces."""
    text = text.replace('\r\n', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.replace('\t', '    ')
    # /\s+$/gm: trailing whitespace before each line end, blank lines included
    text = regex.sub(r'\s+$', '', text, flags=regex.M)
    return text.strip()
