"""
The tender's key dates as the NIT prints them: last date for submitting the bid, bid opening, pre-bid meeting and
the last date for queries. Read from the labelled rows of the NIT's schedule, never guessed: a date counts only
when it follows its label closely, before the next label starts. The pages format and count down these dates with
features/summarization/services/dates.ts.
"""

from datetime import date

import regex

from ..fmt import date_en_in

MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

LABELS = [
    (
        'submission',
        r'\b(?:last|closing|end)\s+(?:time\s*(?:&|and)\s*)?(?:date|time)(?:\s*(?:&|and)\s*(?:time|date))?\s+(?:of|for)\s+(?:online\s+)?(?:submission|receipt|bid\s+submission|e-?tender)|\bbid\s+(?:submission\s+)?(?:end|last)\s+date|\blast\s+date\s*(?:&|and)\s*time\s+of\s+uploading|\bsubmission\s+end\b|\bdate\s*(?:&|and)\s*time\s+of\s+submission|\bclosing\s+date\b|\bdue\s+date\s+(?:of|for)\s+submission',
    ),
    (
        'opening',
        r'\b(?:date|time)(?:\s*(?:&|and)\s*(?:time|date))?\s+(?:of\s+)?(?:the\s+)?opening\b|\b(?:technical\s+)?(?:bid|tender)\s+opening(?:\s+date)?\b|\bopening\s+of\s+(?:the\s+)?(?:online\s+)?(?:technical\s+)?(?:bids?|tenders?|packet\s*a)\b',
    ),
    ('preBid', r'\bpre[\s-]*bi\s?d\s+(?:mee\s?ting|conference)'),
    ('queries', r'\b(?:bid\s+clarification|last\s+date\s+(?:for|of)\s+(?:seeking\s+clarification|(?:receipt\s+of\s+)?(?:pre[\s-]?bid\s+)?queries))'),
]
LABEL_RES = [(kind, regex.compile(source, regex.I)) for kind, source in LABELS]

# Any label, so a window stops where the next row begins.
ANY_LABEL = regex.compile('|'.join(source for _, source in LABELS), regex.I)

DATE = regex.compile(
    r'\b(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)?\d{2})\b'
    r'|\b(\d{1,2})(?:st|nd|rd|th)?[\s-]+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s,-]+((?:19|20)\d{2})\b',
    regex.I,
)
TIME = regex.compile(
    r'\b(\d{1,2})[:.](\d{2})(?::\d{2})?\.?\s*(a\.?\s?m\.?|p\.?\s?m\.?|hrs?\.?|hours?|noon)?|\b(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)|\b(noon)\b',
    regex.I,
)

KEY_DATE_LABELS = {
    'submission': 'Bid due',
    'opening': 'Bid opening',
    'preBid': 'Pre-bid meeting',
    'queries': 'Last date for queries',
}


def _to_iso(day: int, month: int, year: int) -> str | None:
    y = 2000 + year if year < 100 else year
    if month < 1 or month > 12 or day < 1 or day > 31 or y < 2000 or y > 2100:
        return None
    return f'{y}-{month:02d}-{day:02d}'


def _read_time(text: str) -> str | None:
    m = TIME.search(text)
    if not m:
        return None
    if m[6]:
        return '12:00'
    hour = int(m[1] if m[1] is not None else m[4])
    # "00 A.M" is the tail of an OCR-split "11 .00 A.M", not midnight.
    if m[4] and (hour < 1 or hour > 12):
        return None
    minute = int(m[2] or 0)
    meridiem = regex.sub(r'[\s.]', '', (m[3] or m[5] or '').lower())
    # A bare "3.30" with no AM/PM or hrs could be a clause number; only trust times with a unit or a colon.
    if not meridiem and ':' not in m[0]:
        return None
    if meridiem == 'noon':
        return '12:00'
    if meridiem.startswith('p') and hour < 12:
        hour += 12
    if meridiem.startswith('a') and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


AS_PER_PORTAL = regex.compile(r'\bas\s+per\s+(?:the\s+)?(?:CPPP?|e-?procurement|e-?tender(?:ing)?|online)?\s*(?:portal|website)', regex.I)


def dates_on_portal(text: str) -> bool:
    """Every date row says "As per CPP portal": the dates live on the portal, not in the document."""
    return regex.search(r'\b(?:date|time)\b[^:\n]{0,60}:?\s*as\s+per\s+(?:the\s+)?CPPP?\s*portal', text[:120_000], regex.I) is not None


# How far after a label its date may sit: NIT tables wrap the label over several short lines.
WINDOW = 240


def find_key_dates(text: str) -> list[dict]:
    # NIT schedules sit near the front; later mentions are conditions ("valid on the last date of submission").
    # Scans split times as "11 .00 A.M"; join them back.
    flat = regex.sub(r'(\d)\s+([.:])(?=\d{2}\b)', r'\1\2', regex.sub(r'\s+', ' ', text[:120_000]))
    found: dict[str, dict] = {}
    for kind, label in LABEL_RES:
        for match in label.finditer(flat):
            start = match.end()
            window = flat[start : start + WINDOW]
            next_label = ANY_LABEL.search(window)
            if next_label and next_label.start() > 0:
                window = window[: next_label.start()]
            dates = list(DATE.finditer(window))
            if not dates:
                continue
            first = dates[0]
            # A table row that reaches the date only through other figures ("7,04,913/- 2months 30.6.16") is another column.
            if regex.search(r'/-|\bRs\.?\s*\d', window[: first.start()]):
                continue
            # "pre-bid meeting : As per CPP portal 14 Venue … 16 Tentative date of presentation : 3rd June": not this row's.
            if AS_PER_PORTAL.search(window[: first.start()]):
                continue
            # "From 20-12-2024 at 10:00 hrs to 03-01-2025 at 14:30": the deadline is the end of the range.
            between = window[first.end() : dates[1].start()] if len(dates) > 1 else ''
            ranged = kind in ('submission', 'queries') and len(between) < 40 and regex.search(r'\bto\b|\btill\b|से', between, regex.I)
            d = dates[1] if ranged else first
            if d[1]:
                iso = _to_iso(int(d[1]), int(d[2]), int(d[3]))
            else:
                iso = _to_iso(int(d[4]), MONTHS.index(d[5].lower()) + 1, int(d[6]))
            if not iso:
                continue
            # The time is printed just before ("03:00 PM on 01.09.2026") or just after ("at 15:00 hrs").
            at = d.start()
            before = window[max(0, at - 22) : at]
            after = regex.split(r'\bfrom\b|\bto\s+\d{1,2}[./-]\d{1,2}[./-]\d{2}', window[d.end() : d.end() + 40], flags=regex.I)[0]
            time = _read_time(after) or _read_time(before)
            printed = regex.sub(r'\s+', ' ', f'{match[0]} {window[: d.end() + 30]}').strip()[:200]
            found[kind] = {'kind': kind, 'date': iso, 'time': time, 'printed': printed}
            break
    return [found[kind] for kind, _ in LABELS if kind in found]


def days_until(iso: str, today: date | None = None) -> int:
    """Whole days from `today` to the date, negative once it has passed."""
    y, m, d = (int(part) for part in iso.split('-'))
    return (date(y, m, d) - (today or date.today())).days


def format_key_date(k: dict) -> str:
    y, m, d = (int(part) for part in k['date'].split('-'))
    day = date_en_in(date(y, m, d))
    return f"{day}, {k['time']}" if k.get('time') else day
