"""
Deterministic tender facts. Real tenders print these as "Label : value" rows, in two-column tables where the value
sits on the next line, or as sentences that wrap across PDF lines, so each finder looks at the label's line and the
line or two after it. A finder returns None rather than guess: the bid then shows a placeholder.
"""

import math

import regex

I = regex.I

# A full stop ends a sentence unless it follows an abbreviation like "Rs." or "No.".
_ABBREVIATIONS = r'(?<!\b(?:Rs|No|Nos|Cl|viz|i\.e|e\.g|Dt|Govt|M/s))'


def labelled_value(text: str, label, value, lookahead: int = 2, skip=None) -> str | None:
    """Value of the first label match, searched in the rest of that line and then the next `lookahead` lines."""
    lines = text.split('\n')
    for i, line in enumerate(lines):
        for match in label.finditer(line):
            before = line[: match.start()]
            if skip and skip(before, line):
                continue
            rest = line[match.end() :]
            # Only a label that ends its line ("Earnest Money Deposit (EMD)" in a two-column table) has its value below.
            ends_line = len(regex.findall(r'\p{L}', rest)) <= 12
            found = value(rest)
            if not found and ends_line:
                for following in lines[i + 1 : i + 1 + lookahead]:
                    found = value(following)
                    if found:
                        break
            if found:
                return found
    return None


NIT_LABELS = [
    regex.compile(r'\b(?:N\.?\s?I\.?\s?T\.?|NIeT|Notice Inviting (?:e-?)?Tenders?)\)?\s*(?:No|Number|Ref(?:erence)?)\b\.?', I),
    regex.compile(r'\b(?:e-?\s?)?Tender Notice\s*(?:No|Number)\b\.?', I),
    regex.compile(r'\b(?:e-?\s?)?Tender\s*(?:ID|No|Number|Ref(?:erence)?|Document No)\b\.?', I),
    regex.compile(r'\b(?:Bid|RFP|Enquiry)\s*(?:No|Number|Ref(?:erence)?)\b\.?', I),
    regex.compile(r'^\s*(?:Ref\.?\s*)?No\.(?=\s*[A-Z0-9]+/)', I),
]


def _nit_value(rest: str) -> str | None:
    # Labels like "Bid Number/बोली क्रमांक :" carry a translation before the colon.
    value = regex.sub(r'^[^:\-–A-Z0-9]*?[:\-–]\s*', '', rest, count=1, flags=I)
    value = regex.sub(r'^\s*[:\-–.]\s*', '', value, count=1)
    value = regex.split(
        r'\s*(?:,|\s)\s*(?:dated|dt\.?|date)\b|\s{3,}|\s+F\.\s?No\b|\s+(?:for|regarding|under|is|has|are|was)\s|\s+\(',
        value,
        maxsplit=1,
        flags=I,
    )[0]
    value = regex.sub(r'[.,;:)\s]+$', '', value.strip())
    if not regex.search(r'\d', value) or len(value) < 3 or len(value) > 80:
        return None
    if regex.fullmatch(r'\d{1,4}', value) or regex.match(r'(?:of|for|and|the|in)\b', value, I):
        return None
    if not regex.search(r'[/\-_]|[A-Z].*\d|\d.*[A-Z]', value, I):
        return None
    return value


def find_nit_reference(text: str) -> str | None:
    """The NIT / tender reference, e.g. "NIT No. 14/EE/PCD-II/2026-27" or "Bid Number: GEM/2024/B/4869384"."""
    for label in NIT_LABELS:
        found = labelled_value(text, label, _nit_value, lookahead=1)
        if found:
            return found
    return None


PERIOD_LABEL = regex.compile(
    r'(?:period of completion|completion period|time allowed(?: for (?:carrying out|completion)[a-z ]*)?|time of completion|duration of (?:the )?(?:contract|work)|contract (?:period|duration)|period of (?:the )?contract|stipulated (?:period|time)(?: of completion)?|to be completed (?:with)?in)',
    I,
)
PERIOD_VALUE = regex.compile(r'^[^0-9\n]{0,40}?\(?\s*(\d{1,3})\s*\)?\s*(?:\([A-Za-z -]+\)\s*)?(calendar\s+)?(months?|days|years?)\b', I)


def _period_months(rest: str) -> str | None:
    m = PERIOD_VALUE.search(rest)
    if not m:
        return None
    n = int(m[1])
    unit = m[3].lower()
    months = math.ceil(n / 30) if unit.startswith('day') else n * 12 if unit.startswith('year') else n
    return str(months) if 1 <= months <= 120 else None


def find_completion_months(text: str) -> int | None:
    """The completion period in months, e.g. "Period of completion: 18 (Eighteen) months" or "90 (Ninety) Days"."""
    found = labelled_value(text, PERIOD_LABEL, _period_months, lookahead=1)
    return int(found) if found else None


AMOUNT = regex.compile(
    r'(?:Rs\.?|₹|INR|रु\.?|\(in INR\))\s*[:\-]?\s*(\d+(?:\s?,\s?\d+)*(?:\.\d+)?)\s*(?:/-)?\s*(lakhs?|lacs?|crores?|cr\b)?', I
)


def _parse_amount(s: str) -> dict | None:
    """"Rs. 38, 32,203/-" -> {printed: "Rs. 38,32,203", rupees: 3832203}"""
    m = AMOUNT.search(s)
    if not m:
        return None
    # "EMD will be Rs. 50 lakh for tenders valuing above Rs. 50 Cr." is a rule, not this tender's figure.
    if regex.match(r'\s*(?:for|if|where|in case|above|up\s?to|upto|exceeding|and\s*(?:above|$)|or more)\b', s[m.end() :], I):
        return None
    digits = regex.sub(r'\s', '', m[1])
    unit = m[2].lower() if m[2] else None
    base = float(digits.replace(',', ''))
    rupees = base * 1e7 if unit and unit.startswith('cr') else base * 1e5 if unit else base
    printed = f"Rs. {digits}{(' crore' if unit.startswith('cr') else ' lakh') if unit else ''}"
    return {'printed': printed, 'rupees': rupees}


def _is_fraction(before: str) -> bool:
    """
    "40% of the estimated cost (i.e. ₹ 7.86 lakhs)" and "works having estimated value of Rs. 10 lakhs" are thresholds,
    not this tender's figure.
    """
    return (
        regex.search(
            r'\d\s*%\s*(?:of\s*)?(?:the\s*)?$|\b(?:having|valuing|exceeding|above|below|more than|less than|up\s?to)\s*(?:an?\s*|the\s*)?$',
            before,
            I,
        )
        is not None
    )


def _find_amount(text: str, label, min_rupees: float) -> str | None:
    def value(rest: str) -> str | None:
        # A data row prints the amount right after the label; prose ("EMD ... subject to a maximum of Rs 20 lakh") doesn't.
        amount = _parse_amount(rest[:45])
        return amount['printed'] if amount and amount['rupees'] >= min_rupees else None

    return labelled_value(text, label, value, lookahead=2, skip=lambda before, _: _is_fraction(before))


def _table_row_amounts(text: str) -> dict | None:
    """
    A press-notice table: a header row naming "Estimated Cost" then "Earnest Money", and a work's row below it carrying
    the two amounts in that order ("Rs.6 ,65 , 42, 704 /· Rs.13, 30 ,854/·" in a scan).
    """
    lines = text.split('\n')
    header = next((i for i, line in enumerate(lines) if regex.search(r'estimated\s+cost.*earnest\s+money', line, I)), -1)
    if header == -1:
        return None
    for line in lines[header + 1 : header + 15]:
        amounts = [_parse_amount(m[0]) for m in AMOUNT.finditer(line)]
        if len(amounts) >= 2 and amounts[0] and amounts[1] and amounts[0]['rupees'] > amounts[1]['rupees']:
            return {'cost': amounts[0]['printed'], 'emd': amounts[1]['printed']}
    return None


# GeM bids with item-wise evaluation print one EMD per schedule; no single figure applies then.
SCHEDULE_WISE_EMD = 'as per the schedule-wise EMD in the bid document'


def find_emd_amount(text: str) -> str | None:
    if len(regex.findall(r'Schedule\s*\d+\s*EMD Amount', text, I)) > 1:
        return SCHEDULE_WISE_EMD
    found = _find_amount(
        text, regex.compile(r'\b(?:earnest money(?: deposit)?|E\.?M\.?D\.?(?: amount)?|bid security(?: deposit)?)\b', I), 500
    )
    if found:
        return found
    row = _table_row_amounts(text)
    return row['emd'] if row else None


def find_estimated_cost(text: str) -> str | None:
    found = _find_amount(
        text,
        regex.compile(r'\b(?:estimated (?:cost|value)(?: (?:put to tender|of (?:the )?work))?|tender value|e\.?\s?c\.?\s?p\.?\s?t\.?)\b', I),
        10000,
    )
    if found:
        return found
    row = _table_row_amounts(text)
    return row['cost'] if row else None


def join_wrapped(text: str) -> str:
    """PDF lines wrap mid-sentence; join lines within a page so a sentence reads as one."""
    blocks = regex.split(r'\n\s*\n', text)
    return '\n'.join(regex.sub(r'\s*\n\s*', ' ', regex.sub(r'-\n(?=[a-z])', '', block)) for block in blocks)


def flat_text(text: str) -> str:
    return join_wrapped(text).replace('\n', ' ')


def find_delay_compensation(text: str) -> str | None:
    """The delay compensation rate and cap, e.g. "1.5% per month, maximum 10%"."""
    match = regex.search(
        r'(\d+(?:\.\d+)?\s*%)\s*(?:per month|per mensem|p\.\s?m\.)[^.]{0,120}?(?:maximum|ceiling|limited to|max\.?)\s*(?:of\s*)?(\d+(?:\.\d+)?\s*%)',
        join_wrapped(text),
        I,
    )
    if not match:
        return None
    rate, cap = (regex.sub(r'\s', '', m) for m in (match[1], match[2]))
    return f'{rate} per month, maximum {cap}'


def _is_contents_row(s: str) -> bool:
    """A table of contents row: several page ranges ("3-5 … 12-13 … 26-27"), never a clause."""
    return (
        len(regex.findall(r'\b\d{1,3}-\d{1,3}\b', s)) >= 2
        or len(regex.findall(r'\b\d{1,2}\.\d{1,2}\s+[A-Z][a-z]+(?:\s+[A-Za-z&]+){0,4}\s+\d{1,3}\b', s)) >= 2
    )


def _is_clause(s: str) -> bool:
    """
    A clause reads as a sentence with an obligation in it. Index lines ("Notice Inviting E-tenders 6-7 2."), signature
    blocks and pro-forma table headers ("Sr. No Name of work / project …", "Description of item Qty Unit") do not.
    """
    return (
        len(regex.split(r'\s+', s)) >= 8
        and regex.search(r'\b(?:shall|will|must|should|is|are|be|may)\b', s, I) is not None
        and not regex.search(
            r'\bsignature of\b|\bSr\.?\s*No\b|\bdescription of item\b|\bname of work\s*/\s*project\b|\btelephone number\b', s, I
        )
    )


# Where the next numbered heading starts ("4.0 Special Conditions"), so a quote doesn't run into it.
NEXT_HEADING = regex.compile(r'\s\d{1,2}\.\d{1,2}\s+(?=[A-Z][a-z])')

_BREAK = regex.compile(_ABBREVIATIONS + r'\.\s+|;\s+|\s(?=\(?[ivx]+\)\s|\(?[a-h]\)\s|\d+(?:\.\d+)+\s)')
_STOP = regex.compile(_ABBREVIATIONS + r'\.(?=\s|$)')


def sentence_start(flat: str, at: int) -> int:
    """Where the sentence holding `at` starts: the last full stop, colon-led row break or clause number before it."""
    before = flat[max(0, at - 160) : at]
    breaks = list(_BREAK.finditer(before))
    if breaks:
        last = breaks[-1]
        return at - len(before) + last.end()
    # No break close by: start at the term itself rather than mid-word, or at the text's start when that is near.
    return 0 if at < 160 else at


def sentence_end(flat: str, start: int, cap: int = 320) -> int:
    """Up to the end of the sentence that starts at `start`, capped so a table row doesn't run on."""
    rest = flat[start : start + cap]
    stop = _STOP.search(rest)
    return start + (len(rest) if not stop else stop.start() + 1)


def tidy_quote(s: str) -> str:
    quote = regex.sub(r'\s+', ' ', s).strip()
    return quote if regex.search(r'[.!?]$', quote) else f'{quote} …'


def _quote_terms(text: str, terms: list[dict]) -> list[dict]:
    flat = flat_text(text)
    out = []
    for term in terms:
        for match in regex.finditer(term['term'], flat, I):
            start = sentence_start(flat, match.start())
            end = sentence_end(flat, match.start())
            sentence = flat[start:end]
            if not regex.search(term['prefer'], sentence, I) or _is_contents_row(sentence) or not _is_clause(sentence):
                continue
            heading = NEXT_HEADING.search(sentence)
            if heading and heading.start() > 40:
                end = start + heading.start()
            also = term.get('also')
            if also and not regex.search(also, sentence, I):
                following = sentence_end(flat, end)
                if regex.search(also, flat[end:following], I):
                    end = following
            out.append({'label': term['label'], 'text': tidy_quote(flat[start:end])[:400]})
            break
    return out


KEY_TERMS = [
    {'label': 'Compensation for delay (Clause 2)', 'term': r'compensation for delay', 'prefer': r'\d\s*%'},
    {'label': 'Mobilisation advance', 'term': r'mobili[sz]ation advance', 'prefer': r'\d\s*%'},
    {'label': 'Security deposit', 'term': r'security deposit|retention money', 'prefer': r'\d\s*%', 'also': r'refund|releas|return'},
    {'label': 'Performance guarantee', 'term': r'performance (?:guarantee|security)', 'prefer': r'\d\s*%'},
]


def _find_price_variation(flat: str) -> str | None:
    """
    Clause 10CC as the tender states it. CPWD Schedule F prints it as a table row ("Clause 10 CC ... NOT APPLICABLE"),
    and the row text itself can contain "applicable" ("to be applicable in contracts with ..."), so the decisive word
    is the last applicability phrase before the next clause heading.
    """
    for match in regex.finditer(r'Clause\s*10\s*CC\b', flat, I):
        after = flat[match.start() : match.start() + 300]
        next_clause = regex.search(r'\bClause\s*\d', after[10:], I)
        window = after if not next_clause else after[: next_clause.start() + 10]
        phrases = list(
            regex.finditer(
                r'\b(?:(?:shall|will|is)\s+not\s+be\s+applicable|not\s+applicable|(?:shall|will)\s+be\s+applicable|is\s+applicable|applicable)\b',
                window,
                I,
            )
        )
        if not phrases:
            continue
        last = phrases[-1]
        # In a Schedule F table the "sentence" runs back through earlier rows; start at this clause's own row then.
        sentence = sentence_start(flat, match.start())
        start = match.start() if regex.search(r'\bClause\s*\d', flat[sentence : match.start()], I) else sentence
        end = match.start() + last.end()
        stop = regex.search(r'\.(?:\s|$)', flat[end : end + 80])
        if stop and not regex.search(r'\bClause\s*\d', flat[end : end + stop.start()], I):
            end += stop.start() + 1
        return regex.sub(r' …$', '.', tidy_quote(flat[start:end]))
    return None


def find_key_terms(text: str) -> list[dict]:
    """
    Key contract terms quoted word for word from the tender, so the bid never depends on a model's paraphrase.
    For each term: the first place it is followed closely by a figure, quoted from the start of its sentence to the
    end (capped), plus the next sentence when that says how the amount is refunded. Unfilled template blanks
    ("___%") are skipped.
    """
    flat = flat_text(text)
    terms = []
    for term in KEY_TERMS:
        for match in regex.finditer(term['term'], flat, I):
            end = sentence_end(flat, match.start())
            # The figure must be in the term's own sentence, not the next one.
            window = flat[match.start() : min(end, match.start() + 200)]
            if not regex.search(term['prefer'], window) or regex.search(r'_{3,}', window):
                continue
            start = sentence_start(flat, match.start())
            also = term.get('also')
            if also and not regex.search(also, flat[start:end], I):
                following = sentence_end(flat, end)
                if regex.search(also, flat[end:following], I):
                    end = following
            terms.append({'label': term['label'], 'text': tidy_quote(flat[start:end])[:420]})
            break
    price_variation = _find_price_variation(flat)
    if price_variation:
        terms.insert(1, {'label': 'Price variation (Clause 10CC)', 'text': price_variation})
    return terms


_PG = regex.compile(
    r'performance (?:guarantee|security)\b(?:(?!additional)[^.]){0,120}?(\d+(?:\.\d+)?)\s*%\s*(?:\([^)]*\)\s*)?of\s+(?:the\s+)?([^.]{0,90})', I
)


def find_guarantee_conflict(text: str) -> list[str] | None:
    """
    Two places in one tender that set the performance guarantee on different bases ("5% of tendered value" in the NIT,
    "5% of ECPT or contract amount, whichever is higher" in Schedule F). Returns both quotes, for a pre-bid query.
    "Contract amount" is the tendered value; the additional guarantee for low bids is a separate rule and is skipped.
    """
    flat = flat_text(text)
    seen: dict[str, str] = {}
    for m in _PG.finditer(flat):
        base = m[2].lower()
        bases = [
            regex.search(r'tender(?:ed)?\s+(?:value|amount)|contract\s+(?:amount|value)', base) and 'tendered',
            regex.search(r'ecpt|estimated cost', base) and 'ecpt',
        ]
        key = f"{m[1]}%:{'+'.join(b for b in bases if b)}"
        if key in seen:
            continue
        seen[key] = tidy_quote(flat[sentence_start(flat, m.start()) : sentence_end(flat, m.start())])[:300]
        if len(seen) == 2:
            return list(seen.values())
    return None


ANY = r'.'

# Clauses the compliance rules look for in the tender itself rather than in the model's summary.
RISK_CLAUSES = [
    {'label': 'Unconditional guarantee', 'term': r'unconditional', 'prefer': r'guarantee|\bBG\b|bond'},
    {'label': 'Forfeiture', 'term': r'forfeit', 'prefer': ANY},
    {'label': 'Final and binding', 'term': r'final,? (?:and )?(?:binding|conclusive)', 'prefer': ANY},
    {'label': 'Indemnity', 'term': r'indemnif|indemnity', 'prefer': ANY},
    {
        'label': 'Dispute resolution',
        'term': r'arbitrat|dispute|jurisdiction',
        'prefer': r'\b(?:refer\w*|settle\w*|resolv\w*|seat|venue|courts?\s+(?:at|of|in))\b',
    },
    {
        'label': 'Extension of time',
        'term': r'extension of time|time extension|extension (?:in|of) (?:the )?(?:completion|contract) period',
        'prefer': ANY,
    },
    {'label': 'Force majeure', 'term': r'force majeure', 'prefer': ANY},
    {
        'label': 'Short notice',
        'term': r'within (?:24|48|72) hours|within (?:one|two|three|[123]) days?\b',
        'prefer': r'notice|submit|furnish|repl(?:y|ies)|respond|deposit|sign',
    },
    {'label': 'Online submission', 'term': r'e-?tender|online|portal|digital signature', 'prefer': r'\b(?:submi\w*|upload\w*)\b'},
]


def find_risk_clauses(text: str) -> list[dict]:
    """Risk-bearing clauses quoted from the tender text, one sentence each, absent when the tender has no such clause."""
    return _quote_terms(text, RISK_CLAUSES)


ELIGIBILITY = [
    ('Enlistment', r'\b(?:enlisted|enlistment|registered)\b[^.]{0,80}\b(?:appropriate\s+)?(?:class|category)\b'),
    ('Similar works', r'\bs\s?imilar\s+(?:completed\s+)?works?\b[^.]{0,80}?\d{2}\s*%'),
    ('Government work', r'\bone\s+completed\s+work\b[^.]{0,240}\b(?:Central|State)\s+Govern?ment'),
    (
        'Turnover',
        r'\baverage\s+annual\s+(?:financial\s+)?turn\s?over\b[^.]{0,200}?(?:\d{1,3}\s*%|Rs\.?\s*\d)|\bturn\s?over\b[^.]{0,40}\b(?:should|must|shall|not less than|at least)\b[^.]{0,120}?(?:\d{1,3}\s*%|Rs\.?\s*\d)',
    ),
    ('Solvency', r'\bsolvency\b[^.]{0,40}\b(?:should|must|shall|not less than|at least|minimum)\b[^.]{0,120}?(?:\d{1,3}\s*%|Rs\.?\s*\d)'),
    ('Profitability', r'\bnot\s+(?:have\s+)?incurred\s+(?:any\s+)?loss'),
]

# "OR Two similar works …", "ii) …", "And One completed work …": the next option of the same rule.
CONTINUES = regex.compile(r'^\s*(?:or|\(?i{1,3}\)|\(?[a-c]\))\s', I)


def find_eligibility(text: str) -> list[dict]:
    """
    Eligibility rules quoted from the tender (enlistment class, similar-work thresholds, turnover, solvency), so a long
    CPWD tender's eligibility never depends on which chunks reach the model. First match per rule, quoted from its
    sentence start, with the "OR …" options that follow it.
    """
    flat = flat_text(text)
    out = []
    for label, term in ELIGIBILITY:
        for match in regex.finditer(term, flat, I):
            start = sentence_start(flat, match.start())
            if _is_contents_row(flat[start : start + 200]):
                continue
            end = sentence_end(flat, match.start(), 360)
            while CONTINUES.search(flat[end : end + 12]) and end - start < 700:
                end = sentence_end(flat, end + 1, 260)
            out.append({'label': label, 'text': tidy_quote(flat[start:end])})
            break
    return out


WORK_NAME_LABEL = regex.compile(
    r'\b(?:name of (?:the )?work(?: & location)?|subject|sub\.?|description of (?:the )?work|name of (?:the )?(?:project|assignment|services?))\b(?:\s*[:\-–])*\s*',
    I,
)
# A following line that starts a new row ("2 Estimated cost : ...") ends the name.
NEXT_ROW = regex.compile(
    r'^(?:\d+[.)]?\s|[A-Z][A-Za-z ()/.]{2,40}\s*[:\-–]\s)|^(?:estimated|earnest|time allowed|period|tender|nit|e-?tender|bid|issued|dear)\b', I
)


def find_name_of_work(text: str) -> str | None:
    """
    The name of work as the NIT prints it, e.g. "Name of work : Carrying out minor maintenance civil works of ...".
    It often wraps over two or three lines; the next labelled row ends it.
    """
    lines = text.split('\n')[:600]
    for i, line in enumerate(lines):
        match = WORK_NAME_LABEL.search(line)
        if not match or match.start() > 12:
            continue
        value = line[match.end() :].strip()
        j = i + 1
        while j < min(i + 4, len(lines)) and len(value) < 250:
            following = lines[j].strip()
            if not following or NEXT_ROW.search(following) or regex.search(r'[.”"]$', value):
                break
            value += f' {following}'
            j += 1
        value = regex.sub(r'^[“"\'‘]+|[”"\'’]+$', '', value)
        value = regex.sub(r'\s+', ' ', value)
        value = regex.sub(r'[.”"’]+$', '', value).strip()
        words = value.split(' ')
        # Numbers ("Hall No. 2, 5, 12") and short words ("of", "at") are neither readable nor garbled.
        counted = [w for w in words if not regex.fullmatch(r'[\d,&./()-]+', w) and len(w) > 2]
        readable = len([w for w in counted if regex.fullmatch(r"[\p{L}][\p{L}&,().'-]{2,}", w)]) / max(1, len(counted))
        if len(words) < 4 or readable < 0.6 or regex.search(r'[|°]', value) or regex.match(r'(?:as per|of the|submission of)\b', value, I):
            continue
        return value[:250]
    return None


def is_maintenance_work(name_of_work: str | None, text: str) -> bool:
    """Maintenance, repair and rate contracts: work comes as orders over the period, not one build sequence."""
    name = name_of_work if name_of_work is not None else text[:3000]
    return (
        regex.search(
            r'\b(?:maintenance|repairs?|renovation|upkeep|white ?washing|painting|replacement|annual (?:rate|maintenance)|rate contract|work orders?)\b',
            name,
            I,
        )
        is not None
    )


def find_works_type(name_of_work: str | None, text: str) -> str:
    """
    Building (a structure with floors, finishes and services), infrastructure (roads, bridges, drains, pipelines,
    boundary walls) or maintenance (repair and rate contracts). Decides which methodology items and which kind of
    programme the bid gets.
    """
    if is_maintenance_work(name_of_work, text):
        return 'maintenance'
    building = r'\b(?:building|quarters|hostel|block|complex|storey|G\s?\+\s?\d|hospital|school|office|residential|auditorium|housing)\b'
    infrastructure = r'\b(?:road|highway|bridge|ROB|RUB|flyover|culvert|drain|pipeline|sewer|canal|embankment|bounda?ry wall|track|yard)\b'
    name = name_of_work if name_of_work is not None else text[:3000]
    # "Raising of boundary wall ... at Residential Complex": what is built comes before "at <place>".
    built = regex.split(r'\s+at\s+', name, flags=I)[0]
    for part in (built, name):
        if regex.search(building, part, I):
            return 'building'
        if regex.search(infrastructure, part, I):
            return 'infrastructure'
    return 'building'


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def find_standards(text: str) -> dict:
    """IS codes ("IS 1786" -> "1786") and concrete / steel grades ("M25", "Fe500") the whole tender mentions."""
    is_codes = [m[1] for m in regex.finditer(r'\bIS[:\s]*(\d{3,5})', text)]
    grades = [f'M{m[1]}' if m[1] else f'Fe{m[2]}' for m in regex.finditer(r'\b(?:M\s?[-‐–]?\s?(\d{2})|Fe\s?[-‐–]?\s?(\d{3})\s?(?:D)?)\b', text)]
    return {'isCodes': _unique(is_codes), 'grades': _unique(grades)}


KIND_SIGNALS = {
    'works': r"\bname of (?:the )?work\b|\bconstruction\b|\bcivil works?\b|\bpercentage rate\b|\bitem rate\b|\bCPWD\b|\bschedule\s*['‘’]?F\b|\brepairs?\b|\brenovation\b|\bearthwork\b|\bRCC\b|\bROB\b|\bbridge\b|\bbuilding\b|\bmaintenance (?:civil|works)\b",
    'supply': r'\bGeM\b|\bBid Number\b|\bsupply(?:ing)? of\b|\bsupply,?\s+(?:installation|delivery)\b|\bSITC\b|\bcommissioning\b|\bequipment\b|\brate contract\b|\bpurchase\b|\bconsignee\b|\bmake and model\b|\bitem category\b|\bwarranty\b|\bdelivery period\b|\bOEM\b|\bpre-dispatch inspection\b|\bpurchase order\b',
    'services': r'\bhiring of\b|\bservices? provider\b|\bmanpower\b|\bsecurity (?:personnel|guards?|services)\b|\bhousekeeping\b|\boutsourc\w*|\bconsultan(?:cy|t)\b|\boperation and maintenance\b|\bannual maintenance contract\b|\bdeployment of\b|\bman-?months?\b|\bscope of services\b',
}


def find_tender_kind(text: str) -> str:
    """
    Works (build or repair), supply (goods, GeM) or services (manpower, security, consultancy, O&M): decides which
    bid sections and proformas apply. The NIT head says what is being bought, so it counts three times.
    """
    head = text[:6000]

    def score(kind: str) -> int:
        pattern = KIND_SIGNALS[kind]
        return len(regex.findall(pattern, head, I)) * 3 + min(len(regex.findall(pattern, text, I)), 60)

    ranked = sorted((('works', score('works')), ('supply', score('supply')), ('services', score('services'))), key=lambda r: -r[1])
    return ranked[0][0] if ranked[0][1] > 0 else 'works'


OFFICE = regex.compile(
    r'\b(?:Office of the\s+)?((?:Chief|Superintending|Executive|Divisional|Resident)\s+Engineer[^\n]{0,150}|(?:Chief\s+)?General Manager[^\n]{0,150}|(?:Municipal|Deputy|Additional)\s+Commissioner[^\n]{0,150})',
    I,
)


def find_inviting_office(text: str) -> str | None:
    """The tender inviting office, e.g. "Office of the Executive Engineer, Pune Central Division-II, ..."."""
    lines = text.split('\n')[:400]
    line = next((l for l in lines if regex.search(r'\bOffice of the\b', l, I) and OFFICE.search(l)), None)
    if line is None:
        line = next((l for l in lines if regex.search(r'\binvites?\b', l, I) and OFFICE.search(l)), None)
    match = OFFICE.search(line) if line is not None else None
    if not match:
        return None
    office = regex.split(r'\s+(?:invites?|on behalf|for and on behalf)\b', match[1], flags=I)[0].strip()
    return regex.sub(r'[.,;:]$', '', office)
