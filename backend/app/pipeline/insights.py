"""Observations about a series — never conclusions about a person.

Everything here is arithmetic on the user's own numbers: which direction a
series moved, by how much, when it crossed its reference interval, and whether
consecutive draws differ by more than the analyte plausibly can.

Nothing in this module names a disease, suggests a cause, or recommends an
action. That boundary is deliberate and load-bearing. "Ferritin fell 62% over
three years and crossed below the reference interval" is a description of data
the reader can verify against the chart. "This indicates iron deficiency
anaemia" is a diagnosis, which requires a clinician, a history, and an
examination this software will never have.

Educational context about what a test measures lives in
`app/data/analyte_reference.py`, kept separate and separately labelled so the
two can never be mistaken for one another in the API or the interface.
"""

from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from typing import Literal

# A delta check is a real laboratory control: consecutive results for the same
# patient and analyte that differ by more than a plausible amount usually mean
# a specimen mix-up, a mislabelled tube, or an instrument fault — not a genuine
# physiological change. Values are the relative change beyond which a result is
# worth a second look, drawn from conventional laboratory practice; they are
# intentionally loose, because a false alarm costs a glance and a missed
# swap costs a diagnosis.
DELTA_LIMITS: dict[str, float] = {
    "718-7": 0.20,    # Hemoglobin — a 20% swing between draws is rarely real
    "4544-3": 0.20,   # Hematocrit
    "2823-3": 0.25,   # Potassium
    "2951-2": 0.05,   # Sodium — tightly regulated; small shifts are meaningful
    "2160-0": 0.50,   # Creatinine
    "17861-6": 0.15,  # Calcium
    "777-3": 0.50,    # Platelets
    "6690-2": 0.75,   # Leukocytes
    "41995-2": 0.15,  # HbA1c — reflects ~3 months, so it cannot move fast
    "4548-4": 0.15,
}

Direction = Literal["rising", "falling", "steady", "unknown"]


@dataclass
class Insight:
    """One statement about the series, and the evidence behind it."""

    kind: str
    text: str
    severity: Literal["neutral", "attention"] = "neutral"
    at: datetime | None = None


@dataclass
class SeriesInsights:
    """Everything the numbers themselves support saying."""

    direction: Direction = "unknown"
    change_pct: float | None = None
    span_days: int | None = None
    items: list[Insight] = field(default_factory=list)


def _pct(first: float, last: float) -> float | None:
    if first == 0:
        return None
    return (last - first) / abs(first) * 100.0


def analyse(points: list[dict], loinc_code: str) -> SeriesInsights:
    """Describe a chronologically sorted series.

    `points` are dicts with at least `value`, `collected_at`, `ref_low`,
    `ref_high` and `flag`.
    """
    out = SeriesInsights()
    if len(points) < 2:
        if len(points) == 1:
            out.items.append(Insight(
                "single",
                "One result so far. A second gives this a direction.",
            ))
        return out

    values = [p["value"] for p in points]
    first, last = values[0], values[-1]
    out.change_pct = _pct(first, last)

    dates = [p.get("collected_at") for p in points]
    if dates[0] and dates[-1]:
        d0 = dates[0] if isinstance(dates[0], datetime) else datetime.fromisoformat(str(dates[0]))
        d1 = dates[-1] if isinstance(dates[-1], datetime) else datetime.fromisoformat(str(dates[-1]))
        out.span_days = (d1 - d0).days

    # Direction: a run is only called when the change clears a threshold that
    # ordinary assay imprecision would not.
    if out.change_pct is None:
        out.direction = "unknown"
    elif out.change_pct > 8:
        out.direction = "rising"
    elif out.change_pct < -8:
        out.direction = "falling"
    else:
        out.direction = "steady"

    if out.direction in ("rising", "falling") and out.span_days:
        years = out.span_days / 365.25
        window = (f"{years:.1f} years" if years >= 1
                  else f"{out.span_days} days")
        out.items.append(Insight(
            "trend",
            f"{'Up' if out.direction == 'rising' else 'Down'} "
            f"{abs(out.change_pct):.0f}% over {window}, "
            f"from {first:g} to {last:g}.",
        ))

    # A monotonic run is worth naming: three consecutive moves the same way is
    # a different observation from noise that happens to end lower.
    diffs = [b - a for a, b in pairwise(values)]
    if len(diffs) >= 2:
        run = 1
        for i in range(len(diffs) - 1, 0, -1):
            if (diffs[i] > 0) == (diffs[i - 1] > 0) and diffs[i] != 0:
                run += 1
            else:
                break
        if run >= 3:
            out.items.append(Insight(
                "run",
                f"{run + 1} consecutive results moving the same direction.",
            ))

    # Crossings: the moment a series left or entered its reference interval is
    # the single most useful date on the chart.
    for prev, cur in pairwise(points):
        was, now = prev["flag"], cur["flag"]
        if was == "normal" and now in ("high", "low"):
            out.items.append(Insight(
                "crossing",
                f"Moved outside the reference interval at this draw "
                f"({cur['value']:g}, {'above' if now == 'high' else 'below'} range).",
                severity="attention",
                at=cur.get("collected_at"),
            ))
        elif was in ("high", "low") and now == "normal":
            out.items.append(Insight(
                "crossing",
                f"Returned inside the reference interval at this draw ({cur['value']:g}).",
                at=cur.get("collected_at"),
            ))

    # Delta checks: implausible jumps point at the specimen, not the patient.
    limit = DELTA_LIMITS.get(loinc_code)
    if limit:
        for prev, cur in pairwise(points):
            if prev["value"] == 0:
                continue
            change = abs(cur["value"] - prev["value"]) / abs(prev["value"])
            if change > limit:
                out.items.append(Insight(
                    "delta",
                    f"Changed {change * 100:.0f}% between consecutive draws "
                    f"({prev['value']:g} → {cur['value']:g}), more than this test "
                    f"usually moves. Worth checking the two reports are the same person "
                    f"and the same assay before reading anything into it.",
                    severity="attention",
                    at=cur.get("collected_at"),
                ))

    return out
