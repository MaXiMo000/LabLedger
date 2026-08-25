"""The "what needs looking at" view.

/series answers "how has this analyte moved", which you can only ask once you
already know which analyte to look at. This endpoint answers the question that
comes first, and the one a stack of reports makes hard: which results anywhere
in this patient's history crossed a published critical limit or fell outside
their reference interval.

The property worth testing hardest is not the findings — it is the counts
beside them. `critical: []` alone has the shape of an all-clear, and this
system is not entitled to give one: a result with no published limit for its
analyte has not been found safe, it has not been assessed. That is the same
three-states rule flags.critical_for already refuses to collapse, and it has to
survive the trip through the API.
"""

from pathlib import Path

import pytest

from app.models.observation import Observation
from app.worker import process_document

pytestmark = pytest.mark.asyncio

PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()


async def loaded(client, account):
    h, pid = account
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc_id)
    return h, pid


async def test_attention_requires_auth(client, account):
    _, pid = account
    assert (await client.get(f"/api/observations/{pid}/attention")).status_code == 401


async def test_another_account_gets_404_not_403(client, account):
    """A refusal must not confirm the patient exists — the same rule the rest
    of the API follows."""
    _, pid = account
    r = await client.post("/api/auth/register", json={
        "email": "stranger@example.com", "name": "Stranger",
        "password": "correct-horse-battery"})
    other_h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get(f"/api/observations/{pid}/attention",
                             headers=other_h)).status_code == 404


async def test_every_result_is_accounted_for_somewhere(client, account):
    """Nothing may vanish. Each observation is either reported as a finding or
    counted in exactly one of the three tallies; a result that appears in no
    bucket has silently disappeared from the view a clinician is reading."""
    h, pid = await loaded(client, account)
    d = (await client.get(f"/api/observations/{pid}/attention", headers=h)).json()

    total = await Observation.find_all().count()
    assert total > 0, "fixture produced no observations"

    counted = (d["assessed_within_limits"] + d["not_assessable"]
               + d["awaiting_review"])
    # Findings are drawn from the assessed and not-assessable pools, never
    # added on top, so the tallies alone must cover every row.
    assert counted == total


async def test_findings_carry_the_basis_not_just_a_verdict(client, account):
    h, pid = await loaded(client, account)
    d = (await client.get(f"/api/observations/{pid}/attention", headers=h)).json()
    for item in d["critical"] + d["abnormal"]:
        assert item["loinc_code"], "a finding with no code cannot be looked up"
        assert item["ref_source"], "the interval's origin must travel with it"
        assert "value" in item and "unit" in item
        if item["critical"]:
            # The threshold travels with the flag, so a reader can reach the
            # same conclusion rather than being handed one.
            assert item["critical"]["threshold"] is not None
            assert item["critical"]["side"] in ("low", "high")


async def test_criticals_are_not_repeated_as_abnormal(client, account):
    """One result, one bucket. A value counted twice inflates how much is
    wrong, and a reader triaging by list length would be misled."""
    h, pid = await loaded(client, account)
    d = (await client.get(f"/api/observations/{pid}/attention", headers=h)).json()
    crit_ids = {i["observation_id"] for i in d["critical"]}
    abn_ids = {i["observation_id"] for i in d["abnormal"]}
    assert crit_ids.isdisjoint(abn_ids)


async def test_an_unassessable_result_is_not_counted_as_within_limits(client, account):
    """The heart of it. A result whose analyte has no published critical limit
    must land in not_assessable, never in assessed_within_limits — otherwise
    "nothing crossed a limit" quietly starts meaning "everything was checked"."""
    h, pid = await loaded(client, account)
    d = (await client.get(f"/api/observations/{pid}/attention", headers=h)).json()
    assert d["not_assessable"] > 0, (
        "this fixture is expected to contain analytes with no published "
        "critical limit; if that changed, this test needs a new fixture rather "
        "than deleting")


async def test_within_limits_never_counts_a_result_that_has_no_limit(client, account):
    """Cross-checked against the limit table, not against the endpoint's own
    arithmetic.

    The sabotage this exists for: flipping the `is_assessed` branch so an
    analyte with no published limit lands in assessed_within_limits. The
    totals still add up, every result is still accounted for, and the view now
    quietly claims to have checked things it never checked. Counting the rows
    that genuinely have a limit is the only way to notice.
    """
    from app.pipeline import flags

    h, pid = await loaded(client, account)
    d = (await client.get(f"/api/observations/{pid}/attention", headers=h)).json()

    assessable = 0
    for o in await Observation.find_all().to_list():
        if o.review_status == "pending":
            continue
        if o.loinc_code and o.canonical_unit and flags.is_assessed(
                o.loinc_code, o.canonical_unit):
            assessable += 1

    criticals_assessed = len(d["critical"])
    assert d["assessed_within_limits"] + criticals_assessed == assessable, (
        "assessed_within_limits must only ever count results whose analyte has "
        "a published critical limit in the unit the value is in")
    assert d["not_assessable"] == (
        await Observation.find_all().count()
        - d["awaiting_review"] - assessable)


async def test_a_patient_with_no_documents_reports_nothing_assessed(client, account):
    """An empty patient must not look like a clean bill of health: no findings
    AND no assessments, which is a different statement."""
    h, pid = account
    d = (await client.get(f"/api/observations/{pid}/attention", headers=h)).json()
    assert d["critical"] == []
    assert d["abnormal"] == []
    assert d["assessed_within_limits"] == 0
    assert d["not_assessable"] == 0
    assert d["awaiting_review"] == 0


async def test_the_read_is_audited(client, account):
    """Every read of clinical data is logged; this one is no exception."""
    from app.models.audit import AuditEntry
    h, pid = await loaded(client, account)
    before = await AuditEntry.find(AuditEntry.resource == "attention").count()
    await client.get(f"/api/observations/{pid}/attention", headers=h)
    after = await AuditEntry.find(AuditEntry.resource == "attention").count()
    assert after == before + 1
