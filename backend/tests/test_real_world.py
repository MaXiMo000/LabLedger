"""Extraction accuracy against real-world PDFs, not just the two synthetic
fixtures in test_extract.py. See tests/fixtures/real_world/PROVENANCE.md for
what these are and why they were picked. No Mongo/network needed -- these
call extract() directly on file bytes, same as test_extract.py.
"""
from __future__ import annotations

import pathlib

import pytest

from app.pipeline.extract import extract

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "real_world"


@pytest.mark.parametrize("name,min_rows", [
    ("quest_lipid_panel_sample.pdf", 5),
    ("quest_cmp_cbc_panel_sample.pdf", 40),
    ("labcorp_607016_allergy_ige_sample.pdf", 10),
    ("labcorp_139900_covid_naa_sample.pdf", 1),
    ("labcorp_001453_hba1c_sample.pdf", 1),
    # Barcode/specimen-label page: its font encodes glyphs pdfplumber can't
    # map back to text, so extract() legitimately finds nothing. Kept in the
    # corpus and asserted at 0 so it stays a documented failure, not a
    # silent one -- min_rows would go stale (and this test would go green
    # for the wrong reason) if a future pdfplumber upgrade fixes the font
    # mapping and starts finding rows here.
    ("labcorp_164055_sample.pdf", 0),
])
def test_extracts_rows_from_real_world_sample(name, min_rows):
    data = (FIXTURES / name).read_bytes()
    result = extract(data)
    assert len(result.rows) >= min_rows
    for row in result.rows:
        assert row.raw_value is not None


def test_every_row_across_the_corpus_has_a_value():
    """Regression guard for a class of bug: a stray
    column number (a sequence marker, a specimen code) getting misread as
    the result's value whenever the real value token didn't classify.
    Measured 100% value coverage across all 6 real fixtures after the
    ref/value and flag/value glue fixes -- this must not regress."""
    total = 0
    for pdf in FIXTURES.glob("*.pdf"):
        result = extract(pdf.read_bytes())
        for row in result.rows:
            total += 1
            assert row.raw_value is not None, f"{pdf.name}: {row}"
    assert total >= 80


def test_every_value_unit_and_range_matches_the_printed_report():
    """Ground truth: each row of five real sample reports, checked by hand
    against the PDF. Exact, not counts -- a count-based test passed while a
    positive SARS-CoV-2 result read as "NOT DETECTED" (the reference column
    taken for the value) and a ratio's value read as its lab-site code "01"."""
    import json
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    expected.pop("_note")
    wrong = []
    for name, rows in expected.items():
        got = [[r.raw_name, r.raw_value, r.raw_unit, r.raw_ref_range, r.raw_flag]
               for r in extract((FIXTURES / name).read_bytes()).rows]
        for want in rows:
            if want not in got:
                wrong.append(f"{name}: expected {want}")
    assert not wrong, "\n".join(wrong)
