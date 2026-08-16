"""Values the laboratory never measured.

Two things to earn. That the equations are *the* published equations — checked
against worked examples, because an implementation that only agrees with itself
is a plausible-looking number on somebody's kidney function. And that a missing
input produces nothing at all, rather than a default quietly standing in for a
measurement.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.pipeline.derived import derive, egfr
from app.pipeline.units import CANONICAL

pytestmark = pytest.mark.asyncio

AT = datetime(2026, 3, 1, tzinfo=UTC)
DOB_40 = datetime(1986, 3, 1, tzinfo=UTC).date()


class Row:
    """The few Observation fields `derive` reads."""

    def __init__(self, code, value, unit, at=AT, status="auto"):
        self.loinc_code = code
        self.canonical_value = value
        self.canonical_unit = unit
        self.collected_at = at
        self.review_status = status


def by_name(results, name):
    return next((d for d in results if d.display.startswith(name)), None)


# --- the inputs must be things the pipeline can actually produce --------------

async def test_every_input_has_an_audited_unit_conversion():
    """A formula written in mg/dL is only ever satisfied if the conversion
    table can produce mg/dL. An input the pipeline never emits in the expected
    unit is a derivation that silently never fires."""
    from app.pipeline import derived as d
    specs = [d.CREATININE, d.SODIUM, d.CHLORIDE, d.BICARBONATE, d.CALCIUM,
             d.ALBUMIN, d.CHOLESTEROL, d.HDL, d.IRON, d.TIBC]
    wrong = {
        code: (CANONICAL[code][0], unit)
        for code, unit in specs
        if code in CANONICAL and CANONICAL[code][0] != unit
    }
    assert not wrong, f"formula unit does not match the canonical unit: {wrong}"
    assert not [c for c, _ in specs if c not in CANONICAL]


# --- eGFR --------------------------------------------------------------------

def ckd_epi_2021(scr: float, female: bool, age: float) -> float:
    """The published equation, transcribed longhand.

    Deliberately a second implementation rather than a table of remembered
    numbers. The first version of this test carried expected values written
    from memory — 93, 88, 39, 122 — three of which were wrong, and following
    them would have meant "fixing" correct arithmetic about somebody's kidney
    function to match a guess. A transcription can be checked against the paper
    line by line; a remembered number cannot.
    """
    k = 0.7 if female else 0.9
    a = -0.241 if female else -0.302
    r = scr / k
    value = 142.0 * math.pow(min(r, 1.0), a) * math.pow(max(r, 1.0), -1.200)
    value *= math.pow(0.9938, age)
    return value * 1.012 if female else value


@pytest.mark.parametrize(("scr", "sex", "age"), [
    (0.9, "F", 40),
    (1.1, "M", 40),
    (2.0, "M", 60),
    (0.6, "F", 25),
    (1.5, "F", 72),
    (0.7, "M", 18),
])
async def test_egfr_matches_the_published_equation(scr, sex, age):
    """An eGFR that only agrees with itself is a plausible-looking number
    about somebody's kidneys."""
    dob = datetime(AT.year - age, AT.month, AT.day, tzinfo=UTC).date()
    got = egfr({"2160-0": (scr, "mg/dL")}, dob, sex, AT)
    assert got is not None
    assert abs(got.value - ckd_epi_2021(scr, sex == "F", age)) < 0.05


async def test_egfr_is_absent_without_demographics():
    """Not a default. An eGFR computed against an assumed age is a number about
    a person who does not exist."""
    draw = {"2160-0": (1.0, "mg/dL")}
    assert egfr(draw, None, "M", AT) is None
    assert egfr(draw, DOB_40, None, AT) is None
    assert egfr(draw, DOB_40, "X", AT) is None
    assert egfr(draw, DOB_40, "M", AT) is not None


async def test_egfr_refuses_a_creatinine_in_the_wrong_unit():
    """The safety argument for the whole module. µmol/L into an equation
    expecting mg/dL is wrong by a factor of eighty-eight and charts fine."""
    assert egfr({"2160-0": (88.4, "umol/L")}, DOB_40, "M", AT) is None


async def test_egfr_falls_as_creatinine_rises():
    a = egfr({"2160-0": (0.8, "mg/dL")}, DOB_40, "M", AT)
    b = egfr({"2160-0": (2.4, "mg/dL")}, DOB_40, "M", AT)
    assert a.value > b.value
    assert b.flag == "low"      # below the 60 screening boundary
    assert a.flag == "normal"


# --- the rest ----------------------------------------------------------------

async def test_anion_gap():
    rows = [Row("2951-2", 140, "mmol/L"), Row("2075-0", 104, "mmol/L"),
            Row("2028-9", 24, "mmol/L")]
    got = by_name(derive(rows, None, None), "Anion gap")
    assert got.value == 12.0
    assert got.flag == "normal"
    assert len(got.inputs) == 3


async def test_corrected_calcium_raises_a_low_albumin_result():
    """The point of the correction: the measured total understates calcium when
    albumin is low, because half of it is albumin-bound."""
    rows = [Row("17861-6", 8.4, "mg/dL"), Row("1751-7", 2.5, "g/dL")]
    got = by_name(derive(rows, None, None), "Calcium corrected")
    assert got.value == 9.6            # 8.4 + 0.8 * (4.0 - 2.5)
    assert got.flag == "normal"        # the raw 8.4 would have read low


async def test_non_hdl():
    rows = [Row("2093-3", 198, "mg/dL"), Row("2085-9", 54, "mg/dL")]
    got = by_name(derive(rows, None, None), "Non-HDL")
    assert got.value == 144.0
    assert got.flag == "high"


async def test_transferrin_saturation():
    rows = [Row("2498-4", 60, "ug/dL"), Row("2500-7", 300, "ug/dL")]
    got = by_name(derive(rows, None, None), "Transferrin")
    assert got.value == 20.0


async def test_a_zero_tibc_does_not_divide_by_zero():
    rows = [Row("2498-4", 60, "ug/dL"), Row("2500-7", 0, "ug/dL")]
    assert by_name(derive(rows, None, None), "Transferrin") is None


# --- absent rather than assumed ----------------------------------------------

async def test_a_missing_input_produces_nothing():
    """A missing albumin does not mean "assume 4.0". It means there is no
    corrected calcium, and the interface has to say so."""
    rows = [Row("17861-6", 9.0, "mg/dL")]
    assert by_name(derive(rows, None, None), "Calcium corrected") is None


async def test_inputs_from_different_draws_are_never_combined():
    """An anion gap from a January sodium and a June chloride is arithmetic,
    not a measurement."""
    rows = [
        Row("2951-2", 140, "mmol/L", AT),
        Row("2075-0", 104, "mmol/L", AT - timedelta(days=150)),
        Row("2028-9", 24, "mmol/L", AT),
    ]
    assert by_name(derive(rows, None, None), "Anion gap") is None


async def test_rows_awaiting_review_are_not_used():
    """A mapping nobody has confirmed must not silently become an input to a
    number presented as clinical."""
    rows = [Row("2951-2", 140, "mmol/L"), Row("2075-0", 104, "mmol/L"),
            Row("2028-9", 24, "mmol/L", status="pending")]
    assert by_name(derive(rows, None, None), "Anion gap") is None


async def test_a_row_without_a_date_cannot_participate():
    rows = [Row("2093-3", 198, "mg/dL"), Row("2085-9", 54, "mg/dL", at=None)]
    assert by_name(derive(rows, None, None), "Non-HDL") is None


async def test_each_result_carries_its_formula_and_inputs():
    """A number the reader cannot check is one they have to take on trust, and
    this module is doing arithmetic on their clinical data."""
    rows = [Row("2093-3", 198, "mg/dL"), Row("2085-9", 54, "mg/dL")]
    got = by_name(derive(rows, None, None), "Non-HDL")
    assert got.formula == "total cholesterol - HDL"
    assert [i.display for i in got.inputs] == ["Total cholesterol", "HDL"]
    assert [i.value for i in got.inputs] == [198, 54]


# --- through the API ---------------------------------------------------------

async def test_the_endpoint_says_why_an_egfr_is_missing(client, account):
    """Silence would make a fixable gap — no date of birth on the record —
    look like a feature the system does not have."""
    h, pid = account
    await client.patch(f"/api/patients/{pid}", headers=h,
                       json={"display_name": "Test Patient", "dob": None,
                             "sex_at_birth": None})
    out = (await client.get(f"/api/observations/{pid}/derived", headers=h)).json()
    assert any("eGFR" in u for u in out["unavailable"])
    assert "date of birth" in " ".join(out["unavailable"])


async def test_derived_values_need_access_like_everything_else(client, account):
    h, pid = account
    other = await client.post("/api/auth/register", json={
        "email": "other@example.com", "name": "O", "password": "correct-horse-battery"})
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert h
    assert (await client.get(f"/api/observations/{pid}/derived", headers=oh)).status_code == 404
