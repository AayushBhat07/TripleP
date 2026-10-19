"""
Reads the schedule of quantities (BOQ) printed inside a tender PDF: item number, description, quantity, unit, the
department's rate and amount, grouped under the sub-heads the tender prints. Deterministic, like the other fact
finders, so the figures can be checked against the tender's own totals. (The analysis page sums and ranks these
items with boqInsights in features/summarization/services/boq.ts.)

CPWD-style layouts this follows: an item number on its own line or leading the description, the description
wrapped over several lines, and the figures on the row's last line as "<qty> <unit> <rate> <amount>".
"""

import regex

from ..fmt import num

START = regex.compile(r'^\s*SCHEDU?L?E\s+OF\s+QUANTIT(?:Y|IES)\b|^\s*BILL\s+OF\s+QUANTIT(?:Y|IES)\b|^\s*PRICE\s+SCHEDULE\b', regex.I)
NUMBER = r'\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?'
# "Cum", "Sq. m", or a "per …" unit like "per letter per cm height".
UNIT = r'[A-Za-z][A-Za-z.]{0,11}(?:\s?[A-Za-z.]{1,6})?|[Pp]er(?:\s+[A-Za-z.]{1,8}){1,4}'
# The figures at the end of a BOQ row: quantity, unit (one or two words), rate, amount.
ROW = regex.compile(rf'^(.*?)\s*({NUMBER})\s+({UNIT})\s+({NUMBER})\s+({NUMBER})\s*$')
ITEM_NO = regex.compile(r'^(\d{1,3}(?:\.\d{1,3}){0,4}|\(?[a-z]{1,2}\)|[ivx]{1,4}\))(?:\s+|$)(.*)$', regex.S)
TOTAL = regex.compile(rf'^\s*(?:grand\s+|sub[\s-]?)?total\b[^\d]*({NUMBER})\s*$', regex.I)
# A total whose figure wraps onto a later line ("Grand Total" / "(I+II+…+XI)" / "9402546.40").
TOTAL_LABEL = regex.compile(r'^\s*(?:grand\s+|sub[\s-]?)?total\b[^\d]*$', regex.I)
TRAILING_FIGURE = regex.compile(rf'^[^\d]*({NUMBER})\s*$')
# "Grand Total", "Total of ALL SUB HEAD": the schedule's own total, which later sub-head totals must not replace.
GRAND = regex.compile(r'\bgrand\s+total\b|\btotal\s+of\s+all\b', regex.I)
SUB_HEAD = regex.compile(r'^\s*SUB\s?-?HEAD\b\s*([IVXL]+)?\s*[:\-–]*\s*(.*)$', regex.I | regex.S)
# Page furniture repeated on every page of a tender; never part of an item.
NOISE = regex.compile(
    r'^\s*(?:correction\s*[–-]|page\s+\d+\s*$|item\s*$|no\.\s+description|description of item|\(in rs\.?\)|amount\s*$|qty\.?\s|rate\s*$)',
    regex.I,
)
NOT_UNITS = regex.compile(r'^(?:and|or|of|to|the|in|at|per|with|for|rs\.?)$', regex.I)
CUT = regex.compile(rf'^(.*?)({NUMBER})\s+({UNIT})\s+(\d[\d,]*\.\d+)$')
CUT_TAIL = regex.compile(rf'^(\d{{1,2}})\s+({NUMBER})$')
ENDS_IN_QTY = regex.compile(rf'\s(?:{NUMBER})$')
STARTS_WITH_NUMBER = regex.compile(rf'^(?:{NUMBER})(?:\s|$)')
UNIT_WORD = regex.compile(r'^(?:sq|mm|cm|m|kg|kv|kva|w|nos?|core|x)\b', regex.I)


def to_number(s: str) -> float | int:
    return num(float(s.replace(',', '')))


def tidy(s: str) -> str:
    return regex.sub(r'\s+([,.;:])', r'\1', regex.sub(r'\s+', ' ', s)).strip()


def _ancestors(no: str) -> list[str]:
    """"1.4.1.1" -> ["1", "1.4", "1.4.1"]: the items whose descriptions this one continues."""
    parts = no.split('.')
    return ['.'.join(parts[: i + 1]) for i in range(len(parts) - 1)]


def _is_digit_start(s: str) -> bool:
    return bool(s) and s[0].isdigit() and s[0].isascii()


def _parse_part(lines: list[str], title: str) -> dict:
    items: list[dict] = []
    texts: dict[str, str] = {}
    section: str | None = None
    pending_no: str | None = None
    last_top: str | None = None
    buffer: list[str] = []
    stated_total = None
    grand = False
    sub_head: list[str] | None = None
    heading: dict | None = None

    def plausible(no: str) -> bool:
        """Continues the current numbering (gaps of up to three); "1.5 sq. mm cable" inside item 2 is text, not 1.5."""
        if not pending_no and not last_top:
            return True
        top = int(no.split('.')[0])
        current = int((pending_no or last_top or '').split('.')[0])
        return current <= top <= current + 3

    clean = [line.strip() for line in lines]
    clean = [line for line in clean if line and not NOISE.search(line)]
    i = 0
    while i < len(clean):
        line = clean[i]
        # Figures split over short lines ("754" / "Quintal" / "906.39 6,83,418", or "a) Khallasi 24" / "Per" / "Person"
        # / "Per" / "Month" / "29607.00 7,10,568.00"): join them back into one row.
        # The rate's last digits wrapped under it ("1 Each 1537488.1" / "2 1537488.12"): rejoin when qty × rate = amount.
        cut = CUT.search(line)
        tail = CUT_TAIL.search(clean[i + 1]) if cut and i + 1 < len(clean) else None
        if cut and tail and abs(to_number(cut[2]) * to_number(cut[4] + tail[1]) - to_number(tail[2])) <= 1:
            line = f'{cut[1]}{cut[2]} {cut[3]} {cut[4]}{tail[1]} {tail[2]}'
            i += 1
        ends_in_qty = ENDS_IN_QTY.search(line) is not None
        if not ROW.search(line) and (len(line) <= 24 or ends_in_qty):
            take = 1
            while take <= 5 and i + take < len(clean):
                joined = ' '.join(clean[i : i + take + 1])
                if (
                    all(len(other) <= 24 for other in clean[i + 1 : i + take + 1])
                    and ROW.search(joined)
                    and (ends_in_qty or STARTS_WITH_NUMBER.search(line))
                ):
                    line = joined
                    i += take
                    break
                take += 1

        total = TOTAL.search(line)
        label = line
        if not total and TOTAL_LABEL.search(line):
            take = 1
            while take <= 3 and i + take < len(clean) and not total:
                total = TRAILING_FIGURE.search(clean[i + take])
                if total:
                    i += take
                else:
                    label += f' {clean[i + take]}'
                take += 1
        if total:
            if not grand:
                stated_total = to_number(total[1])
            grand = grand or GRAND.search(label) is not None
            i += 1
            continue

        head = SUB_HEAD.search(line)
        if head:
            sub_head = [head[2]]
            section = tidy(head[2]) or f"Sub head {head[1] or ''}".strip()
            buffer = []
            pending_no = None
            last_top = None
            i += 1
            continue
        # A sub-head title can wrap onto the next line before the first item.
        if sub_head is not None and not ITEM_NO.search(line) and not ROW.search(line) and len(sub_head) < 3:
            sub_head.append(line)
            section = tidy(regex.sub(r'^[:\-–\s]+', '', ' '.join(sub_head)))
            i += 1
            continue
        sub_head = None

        row = ROW.search(line)
        if row and not NOT_UNITS.search(row[3]) and to_number(row[5]) > 0:
            lead = row[1].strip()
            no = pending_no
            numbered = ITEM_NO.search(lead)
            if numbered and regex.search(r'\d|\)', numbered[1]) and (not _is_digit_start(numbered[1]) or plausible(numbered[1])):
                label = numbered[1]
                no = label if _is_digit_start(label) else f"{pending_no or last_top or ''} {label}".strip()
                lead = numbered[2]
                # The lines before a numbered row describe its parent item, not the row itself.
                if pending_no and buffer:
                    texts[pending_no] = tidy(' '.join(buffer))
                buffer = []
            if not no and heading:
                no = heading['no']
                buffer = [heading['text'], *buffer]
                section = heading['previous']
            if not no:
                buffer = []
                i += 1
                continue
            own = tidy(' '.join([*buffer, lead]))
            if _is_digit_start(no):
                context = [texts[a] for a in _ancestors(no) if texts.get(a)]
            else:
                parent = texts.get(pending_no or last_top or '')
                context = [parent] if parent else []
            description = tidy(' — '.join(part for part in [*context, own] if part)) or no
            if heading and heading['no'] == no:
                pending_no = no
                heading = None
            items.append(
                {
                    'no': no,
                    'description': f'{description[:317].rstrip()}…' if len(description) > 320 else description,
                    'quantity': to_number(row[2]),
                    'unit': regex.sub(r'\.$', '', row[3]),
                    'rate': to_number(row[4]),
                    'amount': to_number(row[5]),
                    'section': section,
                }
            )
            buffer = []
            i += 1
            continue

        numbered = ITEM_NO.search(line)
        if numbered and _is_digit_start(numbered[1]) and plausible(numbered[1]) and not UNIT_WORD.search(numbered[2]):
            no, text = numbered[1], numbered[2]
            # "1 Earth Work": a short heading for a group of items, not an item.
            if (
                '.' not in no
                and text
                and len(text) <= 40
                and regex.match(r'[A-Z]', text)
                and not regex.search(r'[,;&]|[.]$|\band$', text)
                and len(text.split(' ')) <= 5
            ):
                # Usually a heading for items 1.1, 1.2…; if a row follows directly, it was an item after all.
                heading = {'no': no, 'text': tidy(text), 'previous': section}
                section = tidy(text)
                texts.pop(no, None)
                pending_no = None
                last_top = no
                buffer = []
                i += 1
                continue
            if pending_no and buffer:
                texts[pending_no] = tidy(' '.join(buffer))
            heading = None
            pending_no = no
            if '.' not in no:
                last_top = no
            buffer = [text] if text else []
            i += 1
            continue
        # "(a) Group C" with its figures on the next line: a lettered sub-item of the pending item.
        if numbered and pending_no:
            if buffer:
                texts[pending_no] = tidy(' '.join(buffer))
            buffer = [line]
            i += 1
            continue

        buffer.append(line)
        if pending_no:
            texts[pending_no] = tidy(' '.join(buffer))
        i += 1
    return {'title': title, 'items': items, 'statedTotal': stated_total}


def find_boq(text: str) -> list[dict]:
    """Every schedule of quantities in the tender text; empty when the BOQ is a separate file."""
    lines = text.split('\n')
    starts = [i for i, line in enumerate(lines) if START.search(line)]
    parts: list[dict] = []
    # A cover page ("Schedule of Quantity for Civil Work") often comes just before the table's own heading.
    carried = ''
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        heading = tidy(' '.join(lines[start : start + 3]))
        heading = regex.sub(r'^.*?QUANTIT(?:Y|IES)\s*', '', heading, count=1, flags=regex.I)
        heading = regex.sub(r'^(?:for|applicable to)\s+', '', heading, count=1, flags=regex.I)
        heading = regex.sub(r'[()]', '', heading)[:80]
        part = _parse_part(lines[start + 1 : end], heading)
        if len(part['items']) >= 3:
            parts.append({**part, 'title': _title_for(f'{carried} {heading}', len(parts))})
            carried = ''
        else:
            carried = heading if end - start < 12 else ''
    return parts


def _title_for(heading: str, index: int) -> str:
    if regex.search(r'e\s*&\s*m|electric', heading, regex.I):
        return 'Electrical and mechanical work'
    if regex.search(r'civil', heading, regex.I):
        return 'Civil work'
    clean = regex.sub(r'\s*name of work.*$', '', heading, flags=regex.I | regex.S).strip()
    if regex.match(r'[A-Z]', clean) and len(clean) <= 40 and not regex.search(r'[:;]', clean):
        return clean
    return f'Schedule {index + 1}' if index else 'Schedule of quantities'


def rupees(printed: str | None) -> float | int | None:
    """"Rs. 15,64,86,955/-" -> 156486955"""
    match = regex.search(r'\d+(?:\.\d+)?', printed.replace(',', '')) if printed else None
    return num(float(match[0])) if match else None
