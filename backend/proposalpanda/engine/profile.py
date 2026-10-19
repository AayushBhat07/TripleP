"""
Arithmetic over the company file: financial years, ₹ lakh formatting and the CPWD bid capacity formula, for the bid
proformas. The company page and dashboard use the same rules from lib/company/profile.ts.
"""

from datetime import date

import regex

from ..fmt import format_in, js_number, round_half_up


def fy_start(d: date) -> int:
    """Start year of the Indian financial year (April to March) that `d` falls in."""
    return d.year if d.month >= 4 else d.year - 1


def last_five_years(d: date | None = None) -> list[str]:
    """The five completed financial years before the current one, most recent first: "2025-26", "2024-25", …"""
    current = fy_start(d or date.today())
    return [f'{start}-{(start + 1) % 100:02d}' for start in (current - 1 - i for i in range(5))]


def format_lakh(lakh: float) -> str:
    """"₹ 1,564.87 lakh (₹ 15.65 crore)" for large values, "₹ 82.50 lakh" otherwise."""
    in_lakh = f'₹ {format_in(lakh, 2, 2)} lakh'
    return f'{in_lakh} (₹ {format_in(lakh / 100, 2, 2)} crore)' if lakh >= 100 else in_lakh


def bid_capacity(profile: dict, months: int | None, today: date | None = None) -> dict | None:
    """
    Available bid capacity = 2·A·N − B (CPWD Works Manual). A is the highest annual value of works executed in the last
    five years, each year enhanced at a simple 7% for every year since. None when a figure is missing.
    """
    current = fy_start(today or date.today())
    years = [t for t in profile.get('turnover') or [] if t.get('lakh') is not None and t['lakh'] > 0]
    if not years or not months or profile.get('worksInHandLakh') is None:
        return None
    best_a, best_year = 0.0, ''
    for t in years:
        try:
            start = int(t['year'][:4])
        except (ValueError, TypeError):
            continue
        enhanced = t['lakh'] * (1 + 0.07 * max(0, current - start))
        if enhanced > best_a:
            best_a, best_year = enhanced, t['year']
    if not best_year:
        return None
    n = round_half_up(months / 12, 2)
    b = profile['worksInHandLakh']
    return {
        'a': round_half_up(best_a, 2),
        'aYear': best_year,
        'n': n,
        'b': b,
        'capacity': round_half_up(2 * best_a * n - b, 2),
    }


def experience_line(profile: dict) -> str:
    """One line for the model's context: what the bidder brings, without identifiers."""
    parts = []
    recent = next((t for t in profile.get('turnover') or [] if t.get('lakh') is not None), None)
    if recent:
        parts.append(f"turnover FY {recent['year']} {format_lakh(recent['lakh'])}")
    works = [w for w in profile.get('similarWorks') or [] if w.get('name')]
    if works:
        parts.append(f"similar works completed: {'; '.join(w['name'] for w in works)}")
    staff = [s for s in profile.get('keyStaff') or [] if s.get('name')]
    if staff:
        described = ', '.join((s.get('qualification') or 'staff') + (f" ({js_number(s['years'])} yrs)" if s.get('years') else '') for s in staff)
        parts.append(f'key staff: {described}')
    machinery = (profile.get('machinery') or '').strip()
    if machinery:
        parts.append('plant and machinery: ' + regex.sub(r'\s*\n\s*', '; ', machinery))
    return '. '.join(parts)
