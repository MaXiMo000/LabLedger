"""The panel map is data, so what is worth testing is the data being wrong.

No database and no fixtures here: this table is a pure lookup, and the suite is
slow enough already that a check which can run in milliseconds should.
"""

from app.data.panels import OTHER_KEY, PANELS, panel_for
from app.pipeline.units import CANONICAL


def test_every_mapped_code_is_one_the_pipeline_actually_produces():
    """A mistyped LOINC is invisible: the analyte just lands in "Other" forever.

    Nothing else catches it — the map is never wrong at import, the endpoint
    never errors, and the row still renders. It renders under the wrong
    heading, quietly, for as long as nobody looks. Pinning membership against
    `units.CANONICAL` makes the typo fail here instead.
    """
    known = set(CANONICAL)
    unknown = {
        code: key for key, _label, codes in PANELS for code in codes if code not in known
    }
    assert not unknown, (
        f"panel map references LOINC codes the unit table does not know: {unknown}. "
        "Either the code is a typo, or units.CANONICAL needs the analyte first."
    )


def test_membership_is_one_to_one():
    """A code in two panels renders the result twice, under two headings."""
    seen = [code for _key, _label, codes in PANELS for code in codes]
    assert len(seen) == len(set(seen))


def test_unmapped_and_missing_codes_fall_to_the_catch_all():
    """Never a KeyError, never a dropped row — the leftovers are a visible group."""
    assert panel_for("9999-9")[0] == OTHER_KEY
    assert panel_for(None)[0] == OTHER_KEY
    assert panel_for("")[0] == OTHER_KEY


def test_the_headings_a_reader_would_check_first():
    """Spot checks, so a wholesale reshuffle has to be deliberate."""
    assert panel_for("718-7")[0] == "cbc"          # Haemoglobin
    assert panel_for("2823-3")[0] == "metabolic"   # Potassium
    assert panel_for("13457-7")[0] == "lipids"     # LDL, calculated
    assert panel_for("3016-3")[0] == "thyroid"     # TSH
    assert panel_for("2276-4")[0] == "iron"        # Ferritin


def test_every_panel_carries_a_label():
    for key, label, codes in PANELS:
        assert key and label and codes
        assert panel_for(codes[0]) == (key, label)
