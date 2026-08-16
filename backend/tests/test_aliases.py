"""Seeing, correcting and forgetting what the system has been taught.

A confirmation is permanent and silent: it decides the same printed name
forever, at stage zero, with no further review and no way to look at it. Three
wrong ones were found sitting in a real account —

    FERRTN SER  ->  Serine                        (ferritin)
    HCT         ->  Reticulocyte production index (haematocrit)
    VIT B-12    ->  Thiamine                      (cobalamin)

— every one of them a plausible pick from a correctly-widened candidate list,
and none of them reachable afterwards. The cascade was not the weak part. The
confirmation step had no undo.
"""

from pathlib import Path

import pytest

from app.models.alias import Alias
from app.models.observation import Observation
from app.worker import process_document

pytestmark = pytest.mark.asyncio

PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()

FERRITIN = "2276-4"
SERINE = "20656-5"


async def with_data(client, h, pid):
    doc = (await client.post(f"/api/documents/{pid}", headers=h,
           files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc)
    return doc


async def confirm_something_wrong(client, h, pid, code=SERINE):
    """Confirm the first pending row to a code, writing the alias behind it."""
    item = (await client.get(f"/api/review/{pid}", headers=h)).json()[0]
    r = await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                          headers=h, json={"loinc_code": code, "remember": True})
    assert r.status_code == 200, r.text
    return item


# --- seeing them -------------------------------------------------------------

async def test_confirmed_mappings_can_finally_be_listed(client, account):
    """There was no screen on which a confirmation could be seen at all."""
    h, pid = account
    await with_data(client, h, pid)
    await confirm_something_wrong(client, h, pid)

    rows = (await client.get("/api/review/aliases", headers=h)).json()
    assert len(rows) == 1
    assert rows[0]["loinc_code"] == SERINE
    assert rows[0]["loinc_display"]           # resolved, not just a bare code
    assert rows[0]["uses"] >= 1               # and it is deciding real rows


async def test_the_list_is_scoped_to_the_account(client, account):
    """Aliases are per-user: one person's confirmations are not another's."""
    h, pid = account
    await with_data(client, h, pid)
    await confirm_something_wrong(client, h, pid)

    other = await client.post("/api/auth/register", json={
        "email": "other@example.com", "name": "Other",
        "password": "correct-horse-battery"})
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert (await client.get("/api/review/aliases", headers=oh)).json() == []


# --- correcting them ---------------------------------------------------------

async def test_correcting_an_alias_repairs_what_it_wrote(client, account):
    """The point. Fixing the rule without fixing the history leaves the mistake
    exactly where it does harm — on the chart — while making the settings
    screen look fixed."""
    h, pid = account
    await with_data(client, h, pid)
    item = await confirm_something_wrong(client, h, pid)

    alias = (await client.get("/api/review/aliases", headers=h)).json()[0]
    r = await client.patch(f"/api/review/aliases/{alias['id']}", headers=h,
                           json={"loinc_code": FERRITIN})
    assert r.status_code == 200, r.text
    assert len(r.json()) >= 1

    obs = await Observation.get(item["observation_id"])
    assert obs.loinc_code == FERRITIN
    assert obs.review_status == "confirmed"

    after = (await client.get("/api/review/aliases", headers=h)).json()[0]
    assert after["loinc_code"] == FERRITIN


async def test_correcting_recomputes_units_and_ranges(client, account):
    """Both are keyed by LOINC, so a corrected code invalidates the pair. A row
    left converted under the old code is wrong in a way that still charts."""
    h, pid = account
    await with_data(client, h, pid)
    item = await confirm_something_wrong(client, h, pid)
    alias = (await client.get("/api/review/aliases", headers=h)).json()[0]

    await client.patch(f"/api/review/aliases/{alias['id']}", headers=h,
                       json={"loinc_code": FERRITIN})

    obs = await Observation.get(item["observation_id"])
    # Ferritin has an audited conversion; the canonical unit must belong to the
    # new code rather than being left over from the old one.
    assert obs.canonical_unit in ("ng/mL", None)
    if obs.canonical_value is not None:
        assert obs.canonical_unit == "ng/mL"


async def test_correcting_to_an_unknown_code_is_refused(client, account):
    h, pid = account
    await with_data(client, h, pid)
    await confirm_something_wrong(client, h, pid)
    alias = (await client.get("/api/review/aliases", headers=h)).json()[0]

    r = await client.patch(f"/api/review/aliases/{alias['id']}", headers=h,
                           json={"loinc_code": "0000-0"})
    assert r.status_code == 422


async def test_another_accounts_alias_is_404(client, account):
    """Owning an alias is not a right to rewrite somebody else's history."""
    h, pid = account
    await with_data(client, h, pid)
    await confirm_something_wrong(client, h, pid)
    alias = (await client.get("/api/review/aliases", headers=h)).json()[0]

    other = await client.post("/api/auth/register", json={
        "email": "other@example.com", "name": "Other",
        "password": "correct-horse-battery"})
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}

    assert (await client.patch(f"/api/review/aliases/{alias['id']}", headers=oh,
                               json={"loinc_code": FERRITIN})).status_code == 404
    assert (await client.delete(f"/api/review/aliases/{alias['id']}",
                                headers=oh)).status_code == 404


# --- forgetting them ---------------------------------------------------------

async def test_forgetting_sends_the_rows_back_for_review(client, account):
    """Deliberately not the same as correcting. "This is wrong and I know the
    right answer" and "this is wrong and I do not" are different admissions,
    and answering the second by guessing puts the system back where it started."""
    h, pid = account
    await with_data(client, h, pid)
    item = await confirm_something_wrong(client, h, pid)
    alias = (await client.get("/api/review/aliases", headers=h)).json()[0]

    assert (await client.delete(f"/api/review/aliases/{alias['id']}",
                                headers=h)).status_code == 204
    assert (await client.get("/api/review/aliases", headers=h)).json() == []

    obs = await Observation.get(item["observation_id"])
    assert obs.review_status == "pending"
    assert obs.loinc_code is None
    # And the wrongly-converted value is gone rather than left to chart.
    assert obs.canonical_value is None

    queued = (await client.get(f"/api/review/{pid}", headers=h)).json()
    assert item["observation_id"] in [q["observation_id"] for q in queued]


async def test_forgetting_really_forgets(client, account):
    """The next report with that printed name must not resolve at stage zero
    to the code that was just disowned."""
    h, pid = account
    await with_data(client, h, pid)
    await confirm_something_wrong(client, h, pid)
    alias = (await client.get("/api/review/aliases", headers=h)).json()[0]
    await client.delete(f"/api/review/aliases/{alias['id']}", headers=h)

    assert await Alias.find_one(Alias.loinc_code == SERINE) is None
