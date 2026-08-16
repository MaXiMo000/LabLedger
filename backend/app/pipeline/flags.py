"""Two things a reference interval does not say.

**A critical value is not a very high result.** "High" and "call somebody now"
are different statements, and rendering a potassium of 7.0 in the same tone as
a mildly raised cholesterol is a clinical claim this system has not earned.
Laboratories keep a separate list for exactly this — critical, panic, or alert
values — with its own thresholds and its own obligation to notify.

**A normal result can be the abnormal finding.** A creatinine that doubled in
three days is the important number on the page even while it sits inside its
interval, and a reference range cannot express that because it compares a
person to a population rather than to themselves. That comparison is a *delta
check*, and in a laboratory it catches two different things at once: a genuine
acute change, and a mislabelled specimen.

---

**What this module is careful not to be.** It reports that a value has crossed
a published threshold, and it hands back the threshold it used and where that
threshold came from. It does not interpret, recommend, diagnose, or rank
urgency. The distinction is not cosmetic: software whose basis a clinician can
inspect and independently reach the same conclusion from is background
information, and software that supplies the conclusion is something else, with
a different regulatory answer. Every function here returns its own reasoning
so the interface can show it.

**The thresholds are illustrative and must not ship as-is.** Critical limits
are set per institution — they vary between hospitals in the same city, and
they vary by age in ways this adult-only table does not express. A deployment
replaces `CRITICAL` with the values its own laboratory director has signed,
and until that happens the numbers below are a demonstration of the mechanism
rather than clinical guidance.

**Deterministic or absent**, the same rule the unit table follows. An analyte
that is not in the table is *not assessed*, which is not the same as normal and
must never be rendered as reassurance. A threshold is only ever compared
against a canonical value in the unit the threshold is written in, because a
limit in mmol/L tested against a mg/dL number is precisely the ten-fold error
the whole conversion table exists to prevent.
"""

from dataclasses import dataclass
from datetime import datetime

# loinc -> (unit, critical_low, critical_high)
#
# Adult. Widely-cited values, and every one of them is arguable — that is the
# nature of the list, not a defect in this one. `None` means the laboratories
# consulted do not set a limit on that side, not that any value is acceptable.
CRITICAL: dict[str, tuple[str, float | None, float | None]] = {
    # --- electrolytes: the classic panic panel ---
    "2823-3":  ("mmol/L", 2.5, 6.5),      # Potassium
    "2951-2":  ("mmol/L", 120.0, 160.0),  # Sodium
    "2028-9":  ("mmol/L", 10.0, 40.0),    # CO2 / bicarbonate
    "17861-6": ("mg/dL", 6.0, 13.0),      # Calcium
    "2601-3":  ("mg/dL", 1.0, 4.7),       # Magnesium
    "2777-1":  ("mg/dL", 1.0, None),      # Phosphate

    # --- chemistry ---
    "2345-7":  ("mg/dL", 40.0, 500.0),    # Glucose
    "2160-0":  ("mg/dL", None, 10.0),     # Creatinine

    # --- haematology ---
    "718-7":   ("g/dL", 7.0, 20.0),       # Haemoglobin
    "777-3":   ("x10E3/uL", 50.0, 1000.0),  # Platelets
    "6690-2":  ("x10E3/uL", 1.0, 30.0),    # White cell count
}

# Where the numbers came from, shown alongside any flag so the basis is
# inspectable rather than asserted.
CRITICAL_BASIS = "illustrative adult critical limits — replace per institution"

# loinc -> (percent change, within days)
#
# A delta check is a statement about *speed*, so it needs a window: the same
# proportional change over three days and over three years mean entirely
# different things, and only one of them is a finding. Percentages rather than
# absolutes so one line covers the whole reportable span of an analyte.
DELTA: dict[str, tuple[float, int]] = {
    "2160-0":  (50.0, 30),   # Creatinine — acute kidney injury, or a swapped tube
    "2823-3":  (20.0, 30),   # Potassium
    "2951-2":  (5.0, 30),    # Sodium — a small percentage is a large clinical move
    "17861-6": (10.0, 30),   # Calcium
    "718-7":   (20.0, 30),   # Haemoglobin — bleeding, or dilution
    "777-3":   (50.0, 30),   # Platelets
    "6690-2":  (50.0, 30),   # White cell count
}


@dataclass(frozen=True)
class Critical:
    """A value at or beyond a published critical limit, with the limit shown."""

    side: str          # "low" | "high"
    threshold: float
    unit: str
    basis: str = CRITICAL_BASIS


@dataclass(frozen=True)
class Delta:
    """A change from this patient's own previous result for the same analyte."""

    percent: float     # signed: negative is a fall
    from_value: float
    from_at: datetime
    days: int
    limit_percent: float


def critical_for(loinc: str, value: float | None, unit: str | None) -> Critical | None:
    """Report whether a canonical value sits at or beyond a critical limit.

    Returns None both for "within limits" and for "no limit is published for
    this analyte". Those are genuinely different, and the caller distinguishes
    them with `is_assessed` — collapsing them here would let an unassessed
    analyte render as though it had been checked and passed.
    """
    entry = CRITICAL.get(loinc)
    if entry is None or value is None or unit is None:
        return None
    limit_unit, low, high = entry
    # The unit must match exactly. A threshold in mmol/L compared against a
    # mg/dL number is the ten-fold error the conversion table exists to stop,
    # and here it would produce a false alarm or, far worse, a false silence.
    if unit != limit_unit:
        return None
    # At the limit counts as crossing it: published limits are written as
    # "notify at or beyond", and a strict inequality would let the exact
    # threshold value pass unremarked.
    if low is not None and value <= low:
        return Critical("low", low, limit_unit)
    if high is not None and value >= high:
        return Critical("high", high, limit_unit)
    return None


def is_assessed(loinc: str, unit: str | None) -> bool:
    """Report whether a critical limit exists for this analyte in this unit."""
    entry = CRITICAL.get(loinc)
    return entry is not None and unit is not None and entry[0] == unit


def delta_for(
    loinc: str,
    value: float, at: datetime,
    previous_value: float, previous_at: datetime,
) -> Delta | None:
    """Report a change from the previous result that exceeds the delta limit.

    Both values must already be in the same canonical unit; the caller owns
    that, because it is the one holding the series and already refuses to mix
    units within it.
    """
    limit = DELTA.get(loinc)
    if limit is None or previous_value == 0:
        return None
    limit_percent, window_days = limit

    days = (at - previous_at).days
    # A negative gap means the series is not in order, which is the caller's
    # bug rather than a finding; outside the window there is no claim to make.
    if days < 0 or days > window_days:
        return None

    change = (value - previous_value) / abs(previous_value) * 100.0
    if abs(change) < limit_percent:
        return None
    return Delta(
        percent=round(change, 1), from_value=previous_value,
        from_at=previous_at, days=days, limit_percent=limit_percent,
    )
