"""Resolve the reference range for a result, and flag it against that range.

Priority order, and the first one is the one most implementations get wrong:

1. The range printed on that PDF. Reference intervals are instrument- and
   assay-specific -- LabCorp's ferritin range legitimately differs from
   Quest's, and both differ from a hospital's. The lab's own printed range
   beats any table we could build, because it is the range that lab's
   pathologist actually signed off on.
2. A built-in demographic table, keyed by LOINC + sex + age, used only when
   the PDF omits a range entirely.
3. Nothing. Flag `unknown` rather than inventing an interval.

`ref_source` records which of the three applied, so the UI can show whether a
band came from the user's actual lab or from our fallback.
"""

import re
from datetime import date

# Printed ranges come in several shapes.
_RANGE_BOUNDED = re.compile(r"^\s*([\d.]+)\s*[-–—]\s*([\d.]+)\s*$")
_RANGE_UPPER = re.compile(r"^\s*[<≤]=?\s*([\d.]+)\s*$")
_RANGE_LOWER = re.compile(r"^\s*[>≥]=?\s*([\d.]+)\s*$")

# loinc -> (sex or None, min_age, max_age, low, high) in the canonical unit.
# Deliberately small: a fallback for the handful of analytes where a missing
# printed range is common, not an attempt to replace the lab's own intervals.
BUILTIN: dict[str, list[tuple[str | None, int, int, float | None, float | None]]] = {
    "2276-4":  [("F", 18, 120, 11.0, 307.0), ("M", 18, 120, 24.0, 336.0)],   # Ferritin ng/mL
    "718-7":   [("F", 18, 120, 11.7, 15.5), ("M", 18, 120, 13.2, 17.1)],     # Hemoglobin g/dL
    "4544-3":  [("F", 18, 120, 34.9, 44.5), ("M", 18, 120, 38.5, 50.0)],     # Hematocrit %
    "2160-0":  [("F", 18, 120, 0.57, 1.00), ("M", 18, 120, 0.74, 1.35)],     # Creatinine mg/dL
    "3016-3":  [(None, 18, 120, 0.45, 4.50)],                                # TSH uIU/mL
    "2345-7":  [(None, 18, 120, 70.0, 99.0)],                                # Glucose mg/dL
    "2823-3":  [(None, 18, 120, 3.5, 5.2)],                                  # Potassium mmol/L
    "2951-2":  [(None, 18, 120, 134.0, 144.0)],                              # Sodium mmol/L
    "41995-2": [(None, 18, 120, None, 5.7)],                                 # HbA1c %
    "2132-9":  [(None, 18, 120, 232.0, 1245.0)],                             # B12 pg/mL
}


def parse_printed_range(raw: str | None) -> tuple[float | None, float | None]:
    """Parse a reference range as printed on the report."""
    if not raw:
        return None, None
    s = raw.strip()
    if m := _RANGE_BOUNDED.match(s):
        return float(m.group(1)), float(m.group(2))
    if m := _RANGE_UPPER.match(s):
        return None, float(m.group(1))
    if m := _RANGE_LOWER.match(s):
        return float(m.group(1)), None
    return None, None


def age_on(dob: date | None, when: date | None) -> int | None:
    """Age in whole years at `when`, or None if either date is unknown."""
    if dob is None or when is None:
        return None
    return when.year - dob.year - ((when.month, when.day) < (dob.month, dob.day))


def builtin_range(
    loinc_code: str | None, sex: str | None, age: int | None
) -> tuple[float | None, float | None]:
    """Look up a demographic fallback range. Returns (None, None) if unsure."""
    rows = BUILTIN.get(loinc_code or "")
    if not rows or age is None:
        return None, None
    # A sex-specific row is preferred; a sex-agnostic row is a valid fallback.
    for want_sex in (sex, None):
        for row_sex, lo_age, hi_age, low, high in rows:
            if row_sex == want_sex and lo_age <= age <= hi_age:
                return low, high
    return None, None


def resolve_range(
    printed: str | None,
    loinc_code: str | None,
    sex: str | None = None,
    age: int | None = None,
) -> tuple[float | None, float | None, str]:
    """Return (low, high, source) using the priority order in the module docstring."""
    low, high = parse_printed_range(printed)
    if low is not None or high is not None:
        return low, high, "pdf"
    low, high = builtin_range(loinc_code, sex, age)
    if low is not None or high is not None:
        return low, high, "builtin"
    return None, None, "none"


def flag_value(
    value: float | None,
    low: float | None,
    high: float | None,
    printed_flag: str | None = None,
) -> str:
    """Classify a value against its range.

    The lab's own printed H/L marker wins when present: it reflects that
    instrument's delta checks and any pathologist override, which a numeric
    comparison against a range cannot see.
    """
    if printed_flag:
        f = printed_flag.strip().upper()
        if f in ("H", "HH", "HIGH", "CRITICAL"):
            return "high"
        if f in ("L", "LL", "LOW"):
            return "low"
        if f in ("A", "AB", "ABNORMAL"):
            return "abnormal"
        if f == "N":
            return "normal"
    if value is None or (low is None and high is None):
        return "unknown"
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    return "normal"
