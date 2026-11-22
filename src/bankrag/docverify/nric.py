"""Malaysian NRIC (MyKad number) checks: YYMMDD-PB-###G.

YYMMDD is the date of birth, PB the place-of-birth code (states 01-16, 21-59; foreign/unknown 60-68,
71-72, 74-79, 82-93, 98-99). The last digit is odd for men and even for women; it carries no checksum,
so a structurally valid number can still be fabricated - this catches impossible numbers, not stolen ones.
"""
from __future__ import annotations

import re
from datetime import date

_FORMAT = re.compile(r"^(\d{2})(\d{2})(\d{2})-?(\d{2})-?(\d{4})$")
VALID_PLACE_CODES = frozenset(
    [*range(1, 17), *range(21, 60), *range(60, 69), 71, 72, *range(74, 80), *range(82, 94), 98, 99])


def normalise(nric: str | None) -> str | None:
    if not nric:
        return None
    digits = re.sub(r"\D", "", nric)
    return f"{digits[:6]}-{digits[6:8]}-{digits[8:]}" if len(digits) == 12 else nric.strip()


def birth_date(nric: str, ref: date) -> date | None:
    """Date of birth, or None if the date is impossible. Century: the latest one not after `ref`."""
    m = _FORMAT.match(nric.strip())
    if not m:
        return None
    yy, mm, dd = (int(g) for g in m.groups()[:3])
    year = 2000 + yy if 2000 + yy <= ref.year else 1900 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def problems(nric: str | None, ref: date, min_age: int = 18, max_age: int = 100) -> list[str]:
    """Empty list if the NRIC is structurally valid for an applicant on date `ref`."""
    if not nric:
        return ["missing"]
    m = _FORMAT.match(nric.strip())
    if not m:
        return ["bad format"]
    out = []
    dob = birth_date(nric, ref)
    if dob is None:
        out.append(f"impossible birth date {m.group(1)}{m.group(2)}{m.group(3)}")
    else:
        age = ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))
        if not min_age <= age <= max_age:
            out.append(f"age {age} outside {min_age}-{max_age}")
    if int(m.group(4)) not in VALID_PLACE_CODES:
        out.append(f"invalid place-of-birth code {m.group(4)}")
    return out


def mask(nric: str | None) -> str | None:
    """Keep the last 4 digits only, for responses shown to reviewers."""
    n = normalise(nric)
    return None if n is None else "XXXXXX-XX-" + re.sub(r"\D", "", n)[-4:]
