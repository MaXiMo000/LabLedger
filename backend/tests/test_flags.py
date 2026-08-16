"""Critical values, and changes a reference interval cannot express.

Two claims. That a value past a published critical limit is reported as a
different kind of finding from "high" — and that when no limit is published,
the result is reported as *not assessed* rather than as fine, because those are
not the same statement and only one of them is true.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.pipeline import flags
from app.pipeline.units import CANONICAL

pytestmark = pytest.mark.asyncio

PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()
NOW = datetime(2026, 3, 1, tzinfo=UTC)

POTASSIUM = "2823-3"
CREATININE = "2160-0"
CHOLESTEROL = "2093-3"  # in the units table, deliberately not in the critical one


# --- the tables must agree ---------------------------------------------------

async def test_every_critical_limit_uses_the_canonical_unit():
    """The one way this module fails silently. A threshold written in a unit
    the conversion table never produces is never compared against anything, so
    the analyte is quietly never assessed — and "no alert" is indistinguishable
    from "no problem". Two entries were wrong exactly this way when written."""
    wrong = {
        code: (CANONICAL[code][0], unit)
        for code, (unit, _, _) in flags.CRITICAL.items()
        if code in CANONICAL and CANONICAL[code][0] != unit
    }
    assert not wrong, f"critical limits in a unit the pipeline never emits: {wrong}"

    missing = [c for c in flags.CRITICAL if c not in CANONICAL]
    assert not missing, f"critical limits for analytes with no unit conversion: {missing}"


async def test_every_delta_limit_has_a_unit_conversion():
    """A delta is a percentage, so it needs both values in one unit — which
    only happens for analytes the conversion table knows."""
    assert not [c for c in flags.DELTA if c not in CANONICAL]


# --- critical ----------------------------------------------------------------

@pytest.mark.parametrize(("value", "side"), [
    (7.0, "high"),
    (6.5, "high"),   # at the limit counts: published limits read "at or beyond"
    (2.5, "low"),
    (1.9, "low"),
])
async def test_a_value_past_the_limit_is_critical(value, side):
    got = flags.critical_for(POTASSIUM, value, "mmol/L")
    assert got is not None and got.side == side


@pytest.mark.parametrize("value", [3.5, 4.0, 5.2, 6.4])
async def test_a_value_inside_the_limits_is_not(value):
    assert flags.critical_for(POTASSIUM, value, "mmol/L") is None


async def test_the_threshold_and_its_basis_travel_with_the_flag():
    """A reader who can check the basis is being given background. A reader
    handed only a verdict is being given a recommendation, and that is a
    different kind of software with a different regulatory answer."""
    got = flags.critical_for(POTASSIUM, 7.0, "mmol/L")
    assert got.threshold == 6.5
    assert got.unit == "mmol/L"
    assert "institution" in got.basis


async def test_a_mismatched_unit_never_produces_a_verdict():
    """A limit in mmol/L tested against mg/dL is the ten-fold error the
    conversion table exists to prevent — and here it would produce either a
    false alarm or, far worse, a false silence."""
    assert flags.critical_for(POTASSIUM, 7.0, "mg/dL") is None
    assert flags.critical_for(POTASSIUM, 7.0, None) is None
    assert flags.critical_for(POTASSIUM, None, "mmol/L") is None


async def test_an_analyte_with_no_limit_is_not_assessed_rather_than_fine():
    """The distinction the whole module turns on. Silence about cholesterol
    means nobody published a panic value for it, not that the number is safe."""
    assert flags.critical_for(CHOLESTEROL, 400.0, "mg/dL") is None
    assert flags.is_assessed(CHOLESTEROL, "mg/dL") is False
    assert flags.is_assessed(POTASSIUM, "mmol/L") is True
    # Assessed is also false when the unit is one we cannot compare against.
    assert flags.is_assessed(POTASSIUM, "mg/dL") is False


async def test_one_sided_limits_only_fire_on_their_side():
    """Creatinine has no published low limit. A very low one is not an alert,
    and must not be reported as one."""
    assert flags.critical_for(CREATININE, 0.2, "mg/dL") is None
    assert flags.critical_for(CREATININE, 12.0, "mg/dL").side == "high"


# --- delta -------------------------------------------------------------------

async def test_a_doubling_inside_the_reference_range_is_still_a_finding():
    """The case a reference interval cannot express: both values are ordinary,
    the change between them is not."""
    got = flags.delta_for(CREATININE, 1.6, NOW, 0.8, NOW - timedelta(days=3))
    assert got is not None
    assert got.percent == 100.0
    assert got.from_value == 0.8
    assert got.days == 3


async def test_a_fall_is_reported_as_a_fall():
    got = flags.delta_for("718-7", 9.0, NOW, 14.0, NOW - timedelta(days=2))
    assert got.percent < 0


async def test_a_change_under_the_limit_is_not_reported():
    assert flags.delta_for(CREATININE, 1.0, NOW, 0.9, NOW - timedelta(days=3)) is None


async def test_the_same_change_years_apart_is_not_a_delta():
    """A delta check is a statement about speed. Without the window it would
    fire on every patient who aged."""
    assert flags.delta_for(CREATININE, 1.6, NOW, 0.8, NOW - timedelta(days=800)) is None


async def test_an_analyte_with_no_delta_limit_is_silent():
    assert flags.delta_for(CHOLESTEROL, 400.0, NOW, 100.0, NOW - timedelta(days=1)) is None


async def test_a_zero_previous_value_does_not_divide_by_zero():
    assert flags.delta_for(CREATININE, 1.0, NOW, 0.0, NOW - timedelta(days=1)) is None


async def test_points_out_of_order_are_refused_rather_than_guessed():
    assert flags.delta_for(CREATININE, 1.6, NOW - timedelta(days=5), 0.8, NOW) is None


# --- through the API ---------------------------------------------------------

async def test_panels_report_whether_the_analyte_was_assessed_at_all(client, account):
    """Rendered wrong, this is the module's whole risk: an unassessed analyte
    that looks checked."""
    from app.worker import process_document
    h, pid = account
    doc = (await client.post(f"/api/documents/{pid}", headers=h,
           files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc)

    rows = (await client.get(f"/api/observations/{pid}/panels", headers=h)).json()
    assert rows, "fixture should produce panels"
    assert all("critical_assessed" in r and "latest_critical" in r for r in rows)
    # Nothing in the fixture is a panic value, so every flag is absent — and
    # every row still says whether that silence was a judgement or a gap.
    assert all(r["latest_critical"] is None for r in rows)
    assert any(r["critical_assessed"] for r in rows), "some fixture analyte should be assessable"


async def test_a_series_carries_the_critical_check_per_point(client, account):
    from app.worker import process_document
    h, pid = account
    doc = (await client.post(f"/api/documents/{pid}", headers=h,
           files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc)

    panels = (await client.get(f"/api/observations/{pid}/panels", headers=h)).json()
    code = next(p["loinc_code"] for p in panels if p["count"] > 0)
    s = (await client.get(f"/api/observations/{pid}/series?loinc={code}", headers=h)).json()
    for point in s["points"]:
        assert "critical" in point
        assert "delta" in point
