"""Units and reference ranges.

The highest-value test file in the repo: a wrong unit conversion is off by 10x
and still looks completely plausible on a chart, so it is the one class of bug
that can ship unnoticed.
"""

from datetime import date

from app.pipeline.ranges import (
    age_on,
    flag_value,
    parse_printed_range,
    resolve_range,
)
from app.pipeline.units import normalize_unit, parse_value, to_canonical

# --- the rule: deterministic or absent -------------------------------------


def test_unknown_unit_never_guesses():
    """Ferritin printed in mg/dL is not ferritin in ng/mL. Returning 45.0 with
    an assumed factor of 1.0 is the exact failure this table exists to stop."""
    assert to_canonical("2276-4", 45.0, "mg/dL") == (None, None, None)


def test_unknown_analyte_never_guesses():
    assert to_canonical("99999-9", 45.0, "ng/mL") == (None, None, None)


def test_missing_value_or_code_is_not_converted():
    assert to_canonical("2276-4", None, "ng/mL") == (None, None, None)
    assert to_canonical(None, 45.0, "ng/mL") == (None, None, None)


# --- real conversions ------------------------------------------------------


def test_same_unit_roundtrips_unchanged():
    value, unit, factor = to_canonical("2276-4", 45.0, "ng/mL")
    assert (value, unit, factor) == (45.0, "ng/mL", 1.0)


def test_creatinine_molar_to_mass():
    """88.4 umol/L is the textbook equivalent of 1.0 mg/dL."""
    value, unit, _ = to_canonical("2160-0", 88.4, "µmol/L")
    assert unit == "mg/dL"
    assert abs(value - 1.0) < 0.01


def test_cholesterol_molar_to_mass():
    value, unit, _ = to_canonical("2093-3", 5.17, "mmol/L")
    assert unit == "mg/dL"
    assert abs(value - 200.0) < 1.0


def test_hemoglobin_g_per_litre_to_g_per_decilitre():
    value, _, factor = to_canonical("718-7", 146.0, "g/L")
    assert factor == 0.1
    assert abs(value - 14.6) < 0.001


def test_enzyme_iu_and_u_are_the_same_unit():
    assert to_canonical("1742-6", 30.0, "IU/L") == to_canonical("1742-6", 30.0, "U/L")


def test_micro_sign_and_greek_mu_normalise_together():
    assert normalize_unit("µmol/L") == normalize_unit("μmol/L") == "umol/l"
    assert normalize_unit("uIU/mL") == "uiu/ml"
    assert normalize_unit(None) == ""


def test_two_labs_two_units_become_one_series():
    """The whole product in one assertion: Quest prints ng/mL, a hospital
    prints ug/L, and both must land on the same number and unit."""
    a = to_canonical("2276-4", 18.0, "ng/mL")
    b = to_canonical("2276-4", 18.0, "ug/L")
    assert a[0] == b[0] and a[1] == b[1]


# --- value parsing ---------------------------------------------------------


def test_plain_numeric():
    assert parse_value("95") == (95.0, None, None)
    assert parse_value("1,250") == (1250.0, None, None)
    assert parse_value("5.4") == (5.4, None, None)


def test_censored_values_keep_their_operator():
    """"<0.5" is a bound, not a measurement of 0.5. Losing the operator turns
    an assay floor into a data point."""
    assert parse_value("<0.5") == (0.5, "<", None)
    assert parse_value(">1000") == (1000.0, ">", None)
    assert parse_value("<=2") == (2.0, "<=", None)


def test_qualitative_values_are_text_not_numbers():
    assert parse_value("NEGATIVE") == (None, None, "NEGATIVE")
    assert parse_value("Not Detected") == (None, None, "NOT DETECTED")


# --- reference ranges ------------------------------------------------------


def test_parse_printed_range_shapes():
    assert parse_printed_range("24-336") == (24.0, 336.0)
    assert parse_printed_range("0.45 - 4.50") == (0.45, 4.5)
    assert parse_printed_range(">39") == (39.0, None)
    assert parse_printed_range("<150") == (None, 150.0)
    assert parse_printed_range("NEGATIVE") == (None, None)
    assert parse_printed_range(None) == (None, None)


def test_printed_range_beats_the_builtin_table():
    """Reference intervals are assay-specific. The lab's own printed range is
    the one its pathologist signed off on, so it wins."""
    low, high, source = resolve_range("11-307", "2276-4", sex="M", age=29)
    assert (low, high, source) == (11.0, 307.0, "pdf")


def test_builtin_is_used_only_when_nothing_is_printed():
    low, high, source = resolve_range(None, "2276-4", sex="M", age=29)
    assert source == "builtin"
    assert (low, high) == (24.0, 336.0)


def test_builtin_is_sex_specific():
    male = resolve_range(None, "718-7", sex="M", age=30)
    female = resolve_range(None, "718-7", sex="F", age=30)
    assert male[:2] != female[:2]


def test_no_range_is_reported_as_none_not_invented():
    assert resolve_range(None, "2276-4", sex=None, age=None) == (None, None, "none")
    assert resolve_range(None, "99999-9", sex="M", age=29) == (None, None, "none")


def test_age_calculation():
    assert age_on(date(1996, 4, 12), date(2026, 3, 14)) == 29   # birthday not yet reached
    assert age_on(date(1996, 4, 12), date(2026, 5, 1)) == 30
    assert age_on(None, date(2026, 5, 1)) is None


# --- flagging --------------------------------------------------------------


def test_flag_from_range():
    assert flag_value(18.0, 24.0, 336.0) == "low"
    assert flag_value(400.0, 24.0, 336.0) == "high"
    assert flag_value(100.0, 24.0, 336.0) == "normal"


def test_open_ended_range_only_flags_the_bound_it_has():
    assert flag_value(30.0, 39.0, None) == "low"
    assert flag_value(50.0, 39.0, None) == "normal"


def test_printed_flag_overrides_numeric_comparison():
    """The lab's own H/L reflects delta checks and pathologist overrides that a
    numeric comparison cannot see."""
    assert flag_value(100.0, 24.0, 336.0, printed_flag="H") == "high"
    assert flag_value(5.6, 3.5, 5.2, printed_flag="H") == "high"


def test_unknown_when_there_is_nothing_to_compare_against():
    assert flag_value(None, 1.0, 2.0) == "unknown"
    assert flag_value(50.0, None, None) == "unknown"
