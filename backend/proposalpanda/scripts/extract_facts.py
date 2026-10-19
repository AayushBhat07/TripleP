"""
Dev tool: run text extraction and the deterministic fact finders over a folder of tenders, one JSON line each.
    cd backend && python -m proposalpanda.scripts.extract_facts <dir-with-tenders> [<dir-to-write-text>]
"""

import json
import sys
from pathlib import Path

from ..engine import facts as f
from ..engine.text_extractor import extract_text


def main(args: list[str]) -> int:
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 1
    folder, text_dir = Path(args[0]), Path(args[1]) if len(args) > 1 else None
    for path in sorted(folder.iterdir()):
        try:
            text = extract_text(str(path))
        except Exception as error:  # noqa: BLE001 - report and carry on with the next file
            print(json.dumps({'file': path.name, 'error': str(error)}))
            continue
        if text_dir:
            (text_dir / f'{path.stem}.txt').write_text(text, encoding='utf-8')
        name = f.find_name_of_work(text)
        print(json.dumps({
            'file': path.name,
            'chars': len(text),
            'nit': f.find_nit_reference(text),
            'months': f.find_completion_months(text),
            'emd': f.find_emd_amount(text),
            'cost': f.find_estimated_cost(text),
            'delay': f.find_delay_compensation(text),
            'office': f.find_inviting_office(text),
            'kind': f.find_tender_kind(text),
            'name': name,
            'worksType': f.find_works_type(name, text),
            'keyTerms': [f"{t['label']}: {t['text'][:90]}" for t in f.find_key_terms(text)],
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
