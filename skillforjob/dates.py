"""Parsing and applying the free form date ranges.

The range is written with the ``old`` and ``new`` keywords and filters on the
``posted`` date of the job.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any


def parse_date_bound(text: str) -> tuple[date, date] | None:
    """Parse a date token into the first and the last day it covers.

    ``2026`` covers the whole year, ``2026-03`` the whole month and
    ``2026-03-15`` a single day.
    """
    raw = text.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        if fmt == "%Y":
            last = date(parsed.year, 12, 31)
        elif fmt == "%Y-%m":
            last = parsed + timedelta(
                days=calendar.monthrange(parsed.year, parsed.month)[1] - 1
            )
        else:
            last = parsed
        return parsed, last
    return None


def parse_date_range(text: str) -> tuple[date | None, date | None]:
    """Parse a free form date range.

    The range is written with the ``old`` and ``new`` keywords:

    ``old 2026``        from the earliest date of the dataset until 2026
    ``2026-01-01 new``  from the 1st of January 2026 until the newest date
    ``old 2026 2024-01-01 new``  from the 1st of January 2024 until 2026

    Two plain dates (``2024-01-01 2026-12-31``) are read as a range and a
    single plain date as that whole period (day, month or year).

    Returns the ``(oldest, newest)`` bounds; ``None`` means "open".
    """
    tokens = text.split()
    if not tokens:
        raise ValueError("the date range is empty")

    lower: date | None = None
    upper: date | None = None
    consumed: set[int] = set()

    # "old <date>" -> the upper bound.
    for index, token in enumerate(tokens):
        if token.lower() != "old":
            continue
        if index + 1 >= len(tokens):
            raise ValueError('"old" needs a date, e.g. "old 2026"')
        bounds = parse_date_bound(tokens[index + 1])
        if bounds is None:
            raise ValueError(f"{tokens[index + 1]!r} is not a date I understand")
        upper = bounds[1]
        consumed.update({index, index + 1})

    # "<date> new" -> the lower bound.
    for index, token in enumerate(tokens):
        if token.lower() != "new":
            continue
        if index == 0:
            raise ValueError('"new" needs a date, e.g. "2026-01-01 new"')
        bounds = parse_date_bound(tokens[index - 1])
        if bounds is None:
            raise ValueError(f"{tokens[index - 1]!r} is not a date I understand")
        lower = bounds[0]
        consumed.update({index - 1, index})

    # Whatever is left has to be a plain date.
    plain: list[tuple[date, date]] = []
    for index, token in enumerate(tokens):
        if index in consumed:
            continue
        if token.lower() in ("old", "new"):
            continue
        bounds = parse_date_bound(token)
        if bounds is None:
            raise ValueError(f"{token!r} is not a date I understand")
        plain.append(bounds)

    if len(plain) >= 2:
        if lower is None:
            lower = plain[0][0]
        if upper is None:
            upper = plain[1][1]
    elif len(plain) == 1:
        if lower is None and upper is None:
            lower, upper = plain[0]
        elif lower is None:
            lower = plain[0][0]
        elif upper is None:
            upper = plain[0][1]

    if lower and upper and lower > upper:
        raise ValueError(
            f"the range starts on {lower} but ends on {upper}, that cannot work"
        )
    return lower, upper


def filter_date_range(frame: Any, lower: date | None, upper: date | None) -> Any:
    """Keep only the jobs that were posted inside the given range.

    The ``posted`` column is used.  Jobs without a posting date are dropped
    when a bound is given, because they cannot be placed inside the range.
    """
    if lower is None and upper is None:
        return frame

    posted = frame["posted"]
    mask = posted.ne("")
    if lower is not None:
        mask &= posted.ge(lower.isoformat())
    if upper is not None:
        mask &= posted.le(upper.isoformat())
    return frame[mask]


def describe_date_range(frame: Any, lower: date | None, upper: date | None) -> str:
    """Describe the range the jobs were taken from."""
    posted = frame["posted"]
    posted = posted[posted.ne("")]
    if posted.empty:
        return "no dated jobs"

    oldest = posted.min()
    newest = posted.max()
    start = lower.isoformat() if lower else oldest
    end = upper.isoformat() if upper else newest
    return f"{start} -> {end} (posted)"
