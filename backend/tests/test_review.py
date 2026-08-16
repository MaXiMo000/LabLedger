"""Review queue: the loop that turns a proposal into a permanent fact."""

from pathlib import Path

import pytest

from app.models.alias import Alias
from app.models.observation import Observation
from app.worker import process_document

pytestmark = pytest.mark.asyncio

PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()


async def setup_doc(client, email="rev@example.com"):
    """Upload and process one document. Returns (headers, patient_id, document_id)."""
    r = await client.post("/api/auth/register", json={
        "email": email, "name": "Reviewer", "password": "correct-horse-battery"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    pid = (await client.post("/api/patients", headers=h,
           json={"display_name": "Subject", "dob": "1996-04-12",
                 "sex_at_birth": "M"})).json()["id"]
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc_id)
    return h, pid, doc_id


async def test_queue_lists_pending_rows_with_candidates(client):
    h, pid, _ = await setup_doc(client)
    items = (await client.get(f"/api/review/{pid}", headers=h)).json()
    assert items, "the fixture must produce rows needing review"
    for item in items:
        assert item["reason"]
        assert item["raw_name"] and item["raw_value"]


async def test_queue_is_scoped_to_patients_you_can_reach(client):
    """Under the patient model an unreachable queue answers 404, not an empty
    list: an empty list would confirm the patient exists."""
    h, pid, _ = await setup_doc(client, "owner@example.com")
    other = await client.post("/api/auth/register", json={
        "email": "other@example.com", "name": "Other", "password": "correct-horse-battery"})
    h2 = {"Authorization": f"Bearer {other.json()['access_token']}"}

    assert (await client.get(f"/api/review/{pid}", headers=h)).json() != []
    assert (await client.get(f"/api/review/{pid}", headers=h2)).status_code == 404


async def test_current_proposal_is_always_offered_as_a_candidate(client):
    h, pid, _ = await setup_doc(client)
    items = (await client.get(f"/api/review/{pid}", headers=h)).json()
    proposed = [i for i in items if i["proposed_loinc"]]
    assert proposed, "related_corroborated rows carry a proposal"
    for item in proposed:
        codes = {c["loinc_code"] for c in item["candidates"]}
        assert item["proposed_loinc"] in codes


# --- the learning loop -----------------------------------------------------

async def test_confirming_writes_an_alias_and_next_time_is_stage_zero(client):
    """The whole point of the queue: one confirmation converts a probabilistic
    decision into a deterministic lookup, forever."""
    h, pid, doc_id = await setup_doc(client)
    item = next(i for i in (await client.get(f"/api/review/{pid}", headers=h)).json()
                if i["proposed_loinc"])
    obs_id, code, raw_name = item["observation_id"], item["proposed_loinc"], item["raw_name"]

    r = await client.post(f"/api/review/item/{obs_id}/confirm",
                          json={"loinc_code": code}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_status"] == "confirmed"
    assert body["alias_written"] is True
    assert await Alias.find(Alias.source == "user_confirmed").count() >= 1

    # Re-processing the same document now resolves that row at stage 0.
    await process_document({}, doc_id)
    obs = await Observation.find_one(Observation.raw_name == raw_name)
    assert obs.mapping.stage == "alias"
    assert obs.mapping.confidence == 1.0
    assert obs.review_status == "auto"


async def test_confirming_recomputes_units_and_ranges(client):
    """Units and ranges are keyed by LOINC code, so a corrected code must not
    leave a value converted under the old one."""
    h, pid, _ = await setup_doc(client)
    item = next(i for i in (await client.get(f"/api/review/{pid}", headers=h)).json()
                if i["raw_name"] == "VITAMIN B12")
    await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                      json={"loinc_code": "2132-9"}, headers=h)
    obs = await Observation.find_one(Observation.raw_name == "VITAMIN B12")
    assert obs.canonical_unit == "pg/mL"
    assert obs.canonical_value == 412.0


async def test_confirming_the_last_row_flips_the_document_to_done(client):
    h, pid, doc_id = await setup_doc(client)
    while items := (await client.get(f"/api/review/{pid}", headers=h)).json():
        item = items[0]
        code = item["proposed_loinc"] or item["candidates"][0]["loinc_code"]
        r = await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                              json={"loinc_code": code}, headers=h)
        assert r.status_code == 200, r.text
    assert r.json()["remaining_pending"] == 0
    assert (await client.get(f"/api/documents/item/{doc_id}", headers=h)).json()["status"] == "done"


async def test_alias_is_scoped_to_the_confirming_user(client):
    """One person's confirmation must never rewrite another's history."""
    h, pid, _ = await setup_doc(client, "a@example.com")
    item = next(i for i in (await client.get(f"/api/review/{pid}", headers=h)).json()
                if i["proposed_loinc"])
    await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                      json={"loinc_code": item["proposed_loinc"]}, headers=h)
    alias = await Alias.find_one(Alias.source == "user_confirmed")
    assert alias.user_id is not None


async def test_remember_false_confirms_without_writing_an_alias(client):
    h, pid, _ = await setup_doc(client)
    item = (await client.get(f"/api/review/{pid}", headers=h)).json()[0]
    code = item["proposed_loinc"] or item["candidates"][0]["loinc_code"]
    r = await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                          json={"loinc_code": code, "remember": False}, headers=h)
    assert r.json()["alias_written"] is False
    assert await Alias.find(Alias.source == "user_confirmed").count() == 0


# --- rejection and validation ---------------------------------------------

async def test_rejecting_clears_the_proposed_code(client):
    """A rejected guess left on the record would still be charted."""
    h, pid, _ = await setup_doc(client)
    item = next(i for i in (await client.get(f"/api/review/{pid}", headers=h)).json()
                if i["proposed_loinc"])
    r = await client.post(f"/api/review/item/{item['observation_id']}/reject", headers=h)
    assert r.json()["review_status"] == "rejected"
    obs = await Observation.get(item["observation_id"])
    assert obs.loinc_code is None
    assert obs.canonical_value is None


async def test_confirming_an_unknown_loinc_code_is_rejected(client):
    h, pid, _ = await setup_doc(client)
    item = (await client.get(f"/api/review/{pid}", headers=h)).json()[0]
    r = await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                          json={"loinc_code": "0000-0"}, headers=h)
    assert r.status_code == 422


async def test_cannot_confirm_another_users_observation(client):
    h, pid, _ = await setup_doc(client, "owner2@example.com")
    item = (await client.get(f"/api/review/{pid}", headers=h)).json()[0]
    other = await client.post("/api/auth/register", json={
        "email": "attacker2@example.com", "name": "X", "password": "correct-horse-battery"})
    h2 = {"Authorization": f"Bearer {other.json()['access_token']}"}
    r = await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                          json={"loinc_code": "2276-4"}, headers=h2)
    assert r.status_code == 404


# --- search: the backstop must be able to reach any code -------------------

async def test_search_finds_codes_by_name(client):
    h, pid, _ = await setup_doc(client)
    hits = (await client.get("/api/review/loinc/search?q=ferritin", headers=h)).json()
    assert any(x["loinc_code"] == "2276-4" for x in hits)


async def test_search_reaches_codes_the_cascade_would_never_auto_match(client):
    """40k codes are excluded from auto-matching. If a human could not reach
    them here, the review queue would be a dead end, not a backstop."""
    h, pid, _ = await setup_doc(client)
    hits = (await client.get("/api/review/loinc/search?q=galactose&limit=50", headers=h)).json()
    assert hits
    assert any(not x["auto_matchable"] for x in hits)


async def test_search_requires_auth(client):
    assert (await client.get("/api/review/loinc/search?q=ferritin")).status_code == 401


async def test_search_matches_partial_words(client):
    """Typing towards a name must get better, not worse.

    `$text` matched whole words only, so every one of these returned either
    nothing relevant or -- for "hema" -- the entries whose synonym blob happens
    to contain that exact token, which is a set of F8 gene mutation panels. On
    the screen where a human corrects the machine, a search that cannot surface
    the obvious answer pushes people towards accepting the cascade's guess.

    Each of these is the top hit, not merely present: being on page one of a
    twenty-row list is not the same as being found.
    """
    h, _, _ = await setup_doc(client)
    expected = {
        "ferr": "2276-4",       # Ferritin
        "thyro": "3016-3",      # Thyrotropin
        "tsh": "3016-3",        # by synonym rather than by display name
        "creat": "2160-0",      # Creatinine
        "ldl": "13457-7",       # Cholesterol in LDL
        "hematocr": "4544-3",   # Hematocrit
    }
    for q, code in expected.items():
        hits = (await client.get(f"/api/review/loinc/search?q={q}", headers=h)).json()
        assert hits, f"{q!r} found nothing"
        assert hits[0]["loinc_code"] == code, (
            f"{q!r} -> {hits[0]['loinc_code']} ({hits[0]['display']}), expected {code}"
        )


async def test_every_word_typed_narrows_the_search(client):
    """Tokens are ANDed. A search that widened as the user said more would get
    less useful the more they told it."""
    h, _, _ = await setup_doc(client)

    both = (await client.get("/api/review/loinc/search?q=glucose+urine", headers=h)).json()
    assert both
    assert both[0]["loinc_code"] == "5792-7"        # Glucose, urine, test strip
    # Serum glucose matches "glucose" but not "urine", so it must be absent.
    assert all(x["loinc_code"] != "2345-7" for x in both)

    one = (await client.get("/api/review/loinc/search?q=glucose", headers=h)).json()
    assert len(one) >= len(both)


async def test_search_ranks_by_how_commonly_a_test_is_ordered(client):
    """Rank 0 means "never observed in the frequency survey", not "most
    common" — so those come last, after everything with a real rank."""
    h, _, _ = await setup_doc(client)
    hits = (await client.get("/api/review/loinc/search?q=ferr&limit=50", headers=h)).json()

    ranks = [x["common_rank"] for x in hits]
    ranked = [r for r in ranks if r > 0]
    assert ranked == sorted(ranked), f"ranked hits out of order: {ranked}"
    # Nothing with a real rank may appear after an unranked one.
    if 0 in ranks:
        assert all(r == 0 for r in ranks[ranks.index(0):])


# --- the queue is grouped the way a report is printed -----------------------

async def test_queue_items_carry_the_panel_they_are_reviewed_under(client):
    """Same map as the results screen, so the two screens agree on what a
    blood count is."""
    h, pid, _ = await setup_doc(client)
    items = (await client.get(f"/api/review/{pid}", headers=h)).json()
    assert items

    for it in items:
        assert it["panel"] and it["panel_label"]
        if it["proposed_loinc"] is None:
            # No proposal is not the same claim as "a result we cannot file".
            assert it["panel"] == "unmatched", it["raw_name"]

    proposed = [i for i in items if i["proposed_loinc"]]
    if proposed:
        from app.data.panels import panel_for
        for it in proposed:
            assert it["panel"] == panel_for(it["proposed_loinc"])[0]


async def test_an_unproposed_row_is_not_filed_as_other(client):
    """`other` means "a result whose panel is unknown"; `unmatched` means "we do
    not know what this test is". Collapsing them buries the rows needing the
    most work under a heading that reads like leftovers."""
    from app.data.panels import OTHER_KEY, UNMATCHED_KEY
    assert OTHER_KEY != UNMATCHED_KEY

    h, pid, _ = await setup_doc(client)
    items = (await client.get(f"/api/review/{pid}", headers=h)).json()
    for it in items:
        if it["proposed_loinc"] is None:
            assert it["panel"] != OTHER_KEY
