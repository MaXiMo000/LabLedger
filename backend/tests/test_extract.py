from pathlib import Path

import pytest

from app.pipeline.extract import (
    UnreadablePDFError,
    classify_tokens,
    detect_specimen,
    extract,
    parse_line,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def quest():
    return extract((FIXTURES / "quest_style.pdf").read_bytes())


@pytest.fixture(scope="module")
def labcorp():
    return extract((FIXTURES / "labcorp_style.pdf").read_bytes())


def by_name(result, name):
    return next(r for r in result.rows if r.raw_name == name)


# --- the golden assertion -------------------------------------------------

def test_column_order_does_not_change_the_parse(quest, labcorp):
    """quest_style prints NAME VALUE FLAG UNIT REF; labcorp_style prints
    NAME VALUE FLAG REF UNIT. Same analytes, opposite column order.

    A positional parser silently swaps unit and reference range on one of them,
    which yields a plausible and completely wrong chart. Classifying tokens by
    shape must make the two parses identical."""
    q = {(r.raw_name, r.raw_value, r.raw_unit, r.raw_ref_range) for r in quest.rows}
    lc = {(r.raw_name, r.raw_value, r.raw_unit, r.raw_ref_range) for r in labcorp.rows}
    assert q == lc


def test_row_counts(quest, labcorp):
    assert len(quest.rows) == 21
    assert len(labcorp.rows) == 21


def test_units_and_ranges_land_in_the_right_fields(quest):
    ferritin = by_name(quest, "FERRITIN")
    assert (ferritin.raw_value, ferritin.raw_unit) == ("18", "ng/mL")
    assert ferritin.raw_ref_range == "24-336"
    assert ferritin.raw_flag == "L"


def test_open_ended_reference_range(quest):
    assert by_name(quest, "HDL CHOLESTEROL").raw_ref_range == ">39"


def test_name_containing_digits_is_not_split(quest):
    """'VITAMIN B12 412' must parse as name='VITAMIN B12', value='412'."""
    b12 = by_name(quest, "VITAMIN B12")
    assert b12.raw_value == "412"
    assert by_name(quest, "HEMOGLOBIN A1C").raw_value == "5.4"


def test_specimen_sections_scope_the_rows_beneath_them(quest):
    assert by_name(quest, "FERRITIN").raw_specimen == "SERUM"
    assert by_name(quest, "HEMOGLOBIN").raw_specimen == "WHOLE BLOOD"
    urine = [r for r in quest.rows if r.raw_specimen == "URINE"]
    assert {r.raw_name for r in urine} == {"PH", "PROTEIN", "GLUCOSE"}


def test_same_name_in_two_specimens_stays_distinct(quest):
    """GLUCOSE appears in both serum and urine. They are different LOINC codes;
    losing the specimen would merge them into one nonsense trend line."""
    glucose = [r for r in quest.rows if r.raw_name == "GLUCOSE"]
    assert {r.raw_specimen for r in glucose} == {"SERUM", "URINE"}


def test_qualitative_values(quest):
    protein = next(r for r in quest.rows
                   if r.raw_name == "PROTEIN" and r.raw_specimen == "URINE")
    assert protein.raw_value == "NEGATIVE"


def test_collection_date_preferred_over_report_date(quest):
    """The fixture has Collected 03/14 and Reported 03/16. Plotting a trend on
    report dates is subtly wrong, so collection must win."""
    assert quest.collected_at.date().isoformat() == "2026-03-14"
    assert quest.date_source == "collected"


def test_lab_name_detected(quest, labcorp):
    assert quest.lab_name == "Quest Diagnostics"
    assert labcorp.lab_name == "LabCorp"


def test_patient_identifiers_are_not_parsed_as_results(quest):
    names = {r.raw_name.upper() for r in quest.rows}
    assert not any(k in n for n in names for k in ("PATIENT", "MRN", "DOB", "ACCESSION"))


def test_page_and_method(quest):
    assert quest.page_count >= 1
    assert quest.method == "text"


# --- unit-level ------------------------------------------------------------

def test_classify_tokens_is_order_independent():
    a = classify_tokens(["5.6", "H", "mmol/L", "3.5-5.2"])
    b = classify_tokens(["5.6", "H", "3.5-5.2", "mmol/L"])
    assert a == b == ("5.6", "mmol/L", "3.5-5.2", "H")


def test_detect_specimen():
    assert detect_specimen("CHEMISTRY (SERUM)") == "SERUM"
    assert detect_specimen("HEMATOLOGY (WHOLE BLOOD)") == "WHOLE BLOOD"
    assert detect_specimen("URINALYSIS (URINE)") == "URINE"
    # a result row that happens to mention a specimen word is not a header
    assert detect_specimen("SERUM IRON        88      ug/dL     50-180") is None


def test_parse_line_rejects_noise():
    assert parse_line("Patient: DOE, JANE    DOB: 04/12/1996", 1, 0, None) is None
    assert parse_line("Page 1 of 3", 1, 0, None) is None
    assert parse_line("", 1, 0, None) is None


def test_extract_raises_typed_error_on_unreadable_bytes():
    """Callers need to distinguish "bad PDF" from a bug in the extractor."""
    with pytest.raises(UnreadablePDFError):
        extract(b"this is not a pdf")
