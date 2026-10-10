"""The FHIR export: the record leaves whole, and says only what is known."""

from pathlib import Path

import pytest

from app.models.observation import Observation
from app.worker import process_document

pytestmark = pytest.mark.asyncio

PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()


async def exported(client, account):
    h, pid = account
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc_id)
    r = await client.get(f"/api/observations/{pid}/fhir", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+json")
    return r.json(), pid


async def test_needs_a_session_and_hides_other_records(client, account):
    _, pid = account
    assert (await client.get(f"/api/observations/{pid}/fhir")).status_code == 401
    r = await client.post("/api/auth/register", json={
        "email": "stranger@example.com", "name": "Stranger",
        "password": "correct-horse-battery"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get(f"/api/observations/{pid}/fhir", headers=other)).status_code == 404


async def test_every_row_leaves_and_points_at_the_patient(client, account):
    bundle, _ = await exported(client, account)
    assert (bundle["resourceType"], bundle["type"]) == ("Bundle", "collection")
    patient, *obs = bundle["entry"]
    assert patient["resource"]["resourceType"] == "Patient"
    assert patient["resource"]["birthDate"] == "1996-04-12"
    assert len(obs) == await Observation.find_all().count() > 0
    urls = [e["fullUrl"] for e in bundle["entry"]]
    assert len(set(urls)) == len(urls)
    for e in obs:
        r = e["resource"]
        assert r["subject"]["reference"] == patient["fullUrl"]
        assert r["code"]["text"]
        assert ("valueQuantity" in r) != ("valueString" in r)


async def test_an_unconfirmed_code_is_not_asserted(client, account):
    h, pid = account
    await exported(client, account)
    o = await Observation.find_one(Observation.loinc_code != None)  # noqa: E711
    o.review_status = "pending"
    await o.save()
    bundle = (await client.get(f"/api/observations/{pid}/fhir", headers=h)).json()
    by_id = {e["resource"]["id"]: e["resource"] for e in bundle["entry"]}
    pending = by_id[str(o.id)]
    assert pending["status"] == "preliminary"
    assert "coding" not in pending["code"]
    final = [r for r in by_id.values()
             if r["resourceType"] == "Observation" and r["status"] == "final"]
    assert any(r["code"].get("coding", [{}])[0].get("system") == "http://loinc.org" for r in final)


async def test_a_value_shares_a_unit_with_its_range(client, account):
    bundle, _ = await exported(client, account)
    checked = 0
    for e in bundle["entry"][1:]:
        r = e["resource"]
        for rng in r.get("referenceRange", []):
            for bound in ("low", "high"):
                if bound in rng and "valueQuantity" in r:
                    assert rng[bound].get("unit") == r["valueQuantity"].get("unit")
                    checked += 1
    assert checked > 0


async def test_the_download_is_in_the_audit_trail(client, account):
    h, pid = account
    await exported(client, account)
    rows = (await client.get(f"/api/audit/patient/{pid}", headers=h)).json()
    assert any(r["action"] == "download" and r["resource"] == "observation" for r in rows)
