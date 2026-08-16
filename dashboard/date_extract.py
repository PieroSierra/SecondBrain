"""Shared, deterministic content-date detection for dashboard imports."""

import re
from datetime import date


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\.?"
)
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"

_ISO_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")
_MONTH_DAY_YEAR_RE = re.compile(
    rf"\b(?P<month>{_MONTH})\s+(?P<day>{_DAY})(?:,)?\s+(?P<year>\d{{2}}|\d{{4}})\b",
    re.IGNORECASE,
)
_DAY_MONTH_YEAR_RE = re.compile(
    rf"\b(?P<day>{_DAY})\s+(?P<month>{_MONTH})(?:,)?\s+(?P<year>\d{{2}}|\d{{4}})\b",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(
    r"\b(?P<first>\d{1,2})/(?P<second>\d{1,2})/(?P<year>\d{2}|\d{4})\b"
)
_MONTH_YEAR_RE = re.compile(
    rf"\b(?P<month>{_MONTH})\s+(?P<year>\d{{4}})\b",
    re.IGNORECASE,
)


def _year(value):
    year = int(value)
    if len(value) == 2:
        return 2000 + year if year <= 68 else 1900 + year
    return year


def _day(value):
    return int(re.match(r"\d+", value).group(0))


def _month(value):
    return _MONTHS.get(value.lower()[:3])


def _validated(year, month, day):
    try:
        return date(year, month, day).isoformat()
    except (TypeError, ValueError):
        return None


def detect_first_date(text):
    """Return the earliest unambiguous supported date in text, or None.

    Numeric dates are accepted only when one of the first two components is
    greater than 12, making month/day order inferable without a locale guess.
    """
    candidates = []

    for match in _ISO_RE.finditer(text or ""):
        value = _validated(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
        if value:
            candidates.append((match.start(), value))

    for pattern in (_MONTH_DAY_YEAR_RE, _DAY_MONTH_YEAR_RE):
        for match in pattern.finditer(text or ""):
            value = _validated(
                _year(match.group("year")),
                _month(match.group("month")),
                _day(match.group("day")),
            )
            if value:
                candidates.append((match.start(), value))

    for match in _NUMERIC_RE.finditer(text or ""):
        first = int(match.group("first"))
        second = int(match.group("second"))
        if first > 12 >= second:
            day, month = first, second
        elif second > 12 >= first:
            month, day = first, second
        else:
            continue
        value = _validated(_year(match.group("year")), month, day)
        if value:
            candidates.append((match.start(), value))

    for match in _MONTH_YEAR_RE.finditer(text or ""):
        value = _validated(
            int(match.group("year")),
            _month(match.group("month")),
            1,
        )
        if value:
            candidates.append((match.start(), value))

    return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def select_content_date(*, context=None, title=None, content=None, metadata=None):
    """Apply the dashboard's content-date source precedence."""
    return (
        detect_first_date(context)
        or detect_first_date(title)
        or detect_first_date(content)
        or detect_first_date(metadata)
    )
