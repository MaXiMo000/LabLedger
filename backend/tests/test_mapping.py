"""Mapping cascade. These tests encode safety properties, not just behaviour.

They run against the seeded `loinc` collection in the real database (reference
data, no PHI), and never call the LLM -- stage 4 is exercised with a stub.
"""

import pytest

from app.models.alias import Alias
from app.pipeline import llm
from app.pipeline.mapping import (
    Candidate,
    normalize,
    resolve,
    should_force_review,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def loinc_db(client):
    """The test database, with LOINC copied in by conftest and user data wiped."""
    yield


# --- normalisation ---------------------------------------------------------

def test_normalize_strips_specimen_suffix():
    assert normalize("FERRITIN, SERUM") == "FERRITIN"
    assert normalize("Glucose, Plasma") == "GLUCOSE"
    assert normalize("  TSH  ") == "TSH"


def test_normalize_keeps_digits_that_are_part_of_the_name():
    assert normalize("VITAMIN B12") == "VITAMIN B12"
    assert normalize("HEMOGLOBIN A1C") == "HEMOGLOBIN A1C"


# --- the safety properties -------------------------------------------------

async def test_specimen_separates_identically_named_tests(loinc_db):
    """Serum glucose and urine glucose are different LOINC codes. Merging them
    would produce one nonsense trend line out of two valid ones."""
    ser = await resolve("GLUCOSE", "mg/dL", "SERUM")
    uri = await resolve("GLUCOSE", "mg/dL", "URINE")
    assert ser.loinc_code and uri.loinc_code
    assert ser.loinc_code != uri.loinc_code


async def test_exact_match_does_not_trust_related_names(loinc_db):
    """'HCT' appears in RELATEDNAMES2 of Reticulocyte production index, because
    RPI is calculated from the hematocrit. Accepting that as an identity match
    produced a confident wrong mapping at conf 0.95."""
    m = await resolve("HCT", "%", "WHOLE BLOOD")
    assert m.stage != "exact"
    if m.loinc_code == "31111-8":
        assert should_force_review(m.stage, m.component), \
            "a related-name hit must never auto-accept"


async def test_related_name_hits_always_require_review(loinc_db):
    m = await resolve("TSH", "uIU/mL", "SERUM")
    if m.stage == "related_corroborated":
        assert should_force_review(m.stage, m.component) is True
        assert m.confidence < 0.95


async def test_critical_analyte_never_auto_accepts_from_a_probabilistic_stage():
    """Confidence is statistical; these failures are categorical."""
    assert should_force_review("llm", "Potassium") is True
    assert should_force_review("narrowed_fuzzy", "Troponin I.cardiac") is True
    assert should_force_review("llm", "Ferritin") is False
    # exact and alias are not probabilistic, so they are exempt
    assert should_force_review("exact", "Potassium") is False
    assert should_force_review("alias", "Potassium") is False


async def test_unmapped_rows_still_carry_candidates_for_review(loinc_db):
    m = await resolve("HDL CHOLESTEROL", "mg/dL", "SERUM")
    if m.stage == "unmapped":
        assert m.candidates, "an unmapped row must still give a human something to pick from"


# --- stage 0: the learning loop -------------------------------------------

async def test_confirming_an_alias_makes_the_next_lookup_deterministic(loinc_db):
    before = await resolve("MY WEIRD LAB NAME", "ng/mL", "SERUM")
    assert before.stage == "unmapped"

    await Alias(user_id=None, normalized_name=normalize("MY WEIRD LAB NAME"),
                specimen="SERUM", loinc_code="2276-4",
                source="user_confirmed").insert()

    after = await resolve("MY WEIRD LAB NAME", "ng/mL", "SERUM")
    assert after.stage == "alias"
    assert after.loinc_code == "2276-4"
    assert after.confidence == 1.0
    assert should_force_review(after.stage, after.component) is False


async def test_alias_lookup_is_specimen_aware(loinc_db):
    await Alias(user_id=None, normalized_name="ODDNAME", specimen="URINE",
                loinc_code="5792-7", source="user_confirmed").insert()
    assert (await resolve("ODDNAME", None, "URINE")).loinc_code == "5792-7"
    # a different specimen must not silently reuse it
    assert (await resolve("ODDNAME", None, "SERUM")).stage != "alias"


# --- stage 4: the constrained-choice guarantee -----------------------------

CANDIDATES = [
    Candidate("2085-9", "Cholesterol in HDL", 90.0, 63, "Ser/Plas", "MCnc"),
    Candidate("2093-3", "Cholesterol", 88.0, 12, "Ser/Plas", "MCnc"),
]


async def _stub(monkeypatch, reply: str):
    class R:
        status_code = 200
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": reply}]}}]}

    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return R()

    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **k: C())


async def test_llm_index_is_mapped_to_the_offered_code(monkeypatch):
    await _stub(monkeypatch, "0")
    code, _ = await llm.adjudicate("HDL", "mg/dL", "SERUM", CANDIDATES)
    assert code == "2085-9"


async def test_llm_cannot_return_a_code_it_was_not_offered(monkeypatch):
    """The core safety property. Even if the model emits a real LOINC code, it
    is not an index into the offered list, so it is discarded."""
    await _stub(monkeypatch, "2951-2")   # a genuine code, never offered
    code, _ = await llm.adjudicate("HDL", "mg/dL", "SERUM", CANDIDATES)
    assert code is None


async def test_llm_out_of_range_index_is_rejected(monkeypatch):
    await _stub(monkeypatch, "7")
    code, _ = await llm.adjudicate("HDL", "mg/dL", "SERUM", CANDIDATES)
    assert code is None


async def test_llm_none_is_honoured(monkeypatch):
    await _stub(monkeypatch, "NONE")
    code, _ = await llm.adjudicate("HDL", "mg/dL", "SERUM", CANDIDATES)
    assert code is None


async def test_llm_prose_reply_does_not_smuggle_a_code(monkeypatch):
    await _stub(monkeypatch, "The answer is LOINC 2951-2 for sodium")
    code, _ = await llm.adjudicate("HDL", "mg/dL", "SERUM", CANDIDATES)
    # The first integer found is 2951, which is out of range -> rejected.
    assert code is None


async def test_llm_prompt_never_contains_the_measured_value(monkeypatch):
    """De-identification: name, unit and specimen go to Google. Never the
    patient, the date, or the result value."""
    sent = {}

    class R:
        status_code = 200
        def json(self): return {"candidates": [{"content": {"parts": [{"text": "0"}]}}]}

    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            sent["body"] = k["json"]["contents"][0]["parts"][0]["text"]
            return R()

    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **k: C())
    await llm.adjudicate("FERRITIN", "ng/mL", "SERUM", CANDIDATES)
    body = sent["body"]
    assert "FERRITIN" in body and "ng/mL" in body and "SERUM" in body
    for leak in ("18", "1996", "DOE", "JANE", "MRN"):
        assert leak not in body


async def test_llm_unavailable_when_no_key(monkeypatch):
    monkeypatch.setattr(llm.settings, "gemini_api_key", None)
    with pytest.raises(llm.LLMUnavailableError):
        await llm.adjudicate("X", None, None, CANDIDATES)
