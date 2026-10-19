"""Small real PDF and .docx files for the extraction tests, built by hand so the tests need no extra libraries."""

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def write_pdf(path: Path, pages: list[list[str]]) -> Path:
    """A text PDF in Helvetica, one line per entry, 20pt apart from the top of an A4 page."""
    objects = ['<< /Type /Catalog /Pages 2 0 R >>', None, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for lines in pages:
        escaped = [line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') for line in lines]
        ops = ''.join(f'BT /F1 11 Tf 50 {780 - i * 20} Td ({line}) Tj ET\n' for i, line in enumerate(escaped))
        objects.append(f'<< /Length {len(ops.encode("latin-1"))} >>\nstream\n{ops}endstream')
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {len(objects)} 0 R >>')
        kids.append(f'{len(objects)} 0 R')
    objects[1] = f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {len(kids)} >>'

    out = b'%PDF-1.4\n'
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f'{n} 0 obj\n{body}\nendobj\n'.encode('latin-1')
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    out += ''.join(f'{o:010d} 00000 n \n' for o in offsets).encode()
    out += f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    path.write_bytes(out)
    return path


def write_docx(path: Path, paragraphs: list[str]) -> Path:
    """A minimal Word document with one paragraph per entry."""
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    body = ''.join(f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>' for p in paragraphs)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>'
        ))
        archive.writestr('_rels/.rels', (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        ))
        archive.writestr('word/document.xml', f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return path
