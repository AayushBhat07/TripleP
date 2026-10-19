"""Formatting the way the browser's en-IN locale does it, so server text matches what the pages print."""

import math
from datetime import date, datetime, timezone

MONTHS_EN_IN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec']


def round_half_up(x: float, digits: int = 0) -> float:
    """Math.round semantics (half away from zero for positives), not Python's banker's rounding."""
    scale = 10**digits
    return math.floor(x * scale + 0.5) / scale


def group_in(integer: int) -> str:
    """12345678 -> "1,23,45,678" (Indian digit grouping)."""
    s = str(abs(integer))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        s = ','.join(pairs + [tail])
    return f"-{s}" if integer < 0 else s


def format_in(value: float, min_fraction: int = 0, max_fraction: int = 3) -> str:
    """Number.toLocaleString('en-IN', { minimumFractionDigits, maximumFractionDigits })."""
    rounded = round_half_up(value, max_fraction)
    text = f"{abs(rounded):.{max_fraction}f}"
    whole, _, frac = text.partition('.')
    frac = frac.rstrip('0')
    if len(frac) < min_fraction:
        frac = frac.ljust(min_fraction, '0')
    sign = '-' if rounded < 0 else ''
    return f"{sign}{group_in(int(whole))}{'.' + frac if frac else ''}"


def date_en_in(d: date) -> str:
    """toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) -> "19 Sept 2026"."""
    return f"{d.day:02d} {MONTHS_EN_IN[d.month - 1]} {d.year}"


def iso_now() -> str:
    """new Date().toISOString(): UTC with milliseconds and a Z."""
    now = datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + f"{now.microsecond // 1000:03d}Z"


def num(value: float) -> float | int:
    """A whole float as int, so JSON prints 3832203 as JavaScript would, not 3832203.0."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def js_number(x: float) -> str:
    """How JavaScript prints a number in a template string: 1250 not 1250.0, 0.1 not 0.10000000000000001."""
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def compact(value):
    """Drops None from dicts, recursively: what JSON.stringify does with undefined fields."""
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [compact(v) for v in value]
    return value
