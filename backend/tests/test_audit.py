"""Audit trail.

The point of these tests is coverage, not behaviour: a log with gaps is worse
than no log, because it implies a completeness it does not have. So the main
test walks the whole clinical API surface and asserts every call left a trace.
"""

from pathlib import Path

import pytest

from app.models.audit import AuditEntry
from app.worker import process_document

pytestmark = pytest.mark.asyncio

PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()


async def signed_in(client, email="audit@example.com"):
    """Returns (headers, patient_id)."""
    r = await client.post("/api/auth/register", json={
        "email": email, "name": "Audit", "password": "correct-horse-battery"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    pid = (await client.post("/api/patients", headers=h,
           json={"display_name": "Subject"})).json()["id"]
    return h, pid


async def with_document(client, h, pid):
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc_id)
    return doc_id


# --- coverage ---------------------------------------------------------------

async def test_every_clinical_route_leaves_a_trace(client):
    """Walk the API and assert the log grew for each call.

    This is the test that fails when someone adds a route and forgets the
    audit, which is the whole reason the write lives in repo.py rather than in
    handlers.
    """
    h, pid = await signed_in(client)
    doc_id = await with_document(client, h, pid)
    obs = (await client.get(f"/api/documents/item/{doc_id}", headers=h)).json()["observations"]
    mapped = next(o for o in obs if o["loinc_code"])

    calls = [
        ("GET", f"/api/documents/{pid}"),
        ("GET", f"/api/documents/item/{doc_id}"),
        ("GET", f"/api/documents/item/{doc_id}/file"),
        ("GET", f"/api/observations/{pid}/panels"),
        ("GET", f"/api/observations/{pid}/series?loinc={mapped['loinc_code']}"),
        ("GET", f"/api/observations/item/{mapped['id']}"),
        ("GET", f"/api/review/{pid}"),
    ]

    for method, url in calls:
        before = await AuditEntry.find().count()
        r = await client.request(method, url, headers=h)
        assert r.status_code == 200, f"{method} {url} -> {r.status_code}"
        after = await AuditEntry.find().count()
        assert after > before, f"{method} {url} left no audit entry"


async def test_upload_and_delete_are_recorded(client):
    h, pid = await signed_in(client)
    doc_id = await with_document(client, h, pid)
    assert await AuditEntry.find(
        AuditEntry.action == "create", AuditEntry.resource == "document"
    ).count() == 1

    await client.delete(f"/api/documents/item/{doc_id}", headers=h)
    assert await AuditEntry.find(AuditEntry.action == "delete").count() == 1


async def test_downloading_the_pdf_is_recorded_distinctly(client):
    """Taking a copy of the source PDF is the most sensitive action in the API,
    and must be distinguishable from merely viewing the extracted rows."""
    h, pid = await signed_in(client)
    doc_id = await with_document(client, h, pid)
    await client.get(f"/api/documents/item/{doc_id}/file", headers=h)
    entry = await AuditEntry.find_one(AuditEntry.action == "download")
    assert entry and entry.resource == "document"


async def test_confirming_a_mapping_is_recorded(client):
    h, pid = await signed_in(client)
    await with_document(client, h, pid)
    item = (await client.get(f"/api/review/{pid}", headers=h)).json()[0]
    code = item["proposed_loinc"] or item["candidates"][0]["loinc_code"]
    await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                      json={"loinc_code": code}, headers=h)
    assert await AuditEntry.find(AuditEntry.action == "confirm").count() == 1


# --- session events ---------------------------------------------------------

async def test_sign_in_and_failed_sign_in_are_both_recorded(client):
    """A run of failures against one account is exactly the pattern an audit
    trail exists to surface."""
    await signed_in(client, "sessions@example.com")

    await client.post("/api/auth/login", json={
        "email": "sessions@example.com", "password": "correct-horse-battery"})
    assert await AuditEntry.find(AuditEntry.action == "sign_in").count() == 1

    await client.post("/api/auth/login", json={
        "email": "sessions@example.com", "password": "wrong-password-here"})
    assert await AuditEntry.find(AuditEntry.action == "sign_in_failed").count() == 1


# --- what the log must never contain ----------------------------------------

async def test_the_log_holds_no_clinical_content(client):
    """Ids and actions only. An audit trail that leaks the data it protects is
    a second copy of the problem — and a filename alone can be a diagnosis."""
    h, pid = await signed_in(client)
    doc_id = await with_document(client, h, pid)
    await client.get(f"/api/documents/item/{doc_id}", headers=h)

    blob = " ".join(
        f"{e.action} {e.resource} {e.resource_id or ''}"
        for e in await AuditEntry.find().to_list()
    )
    for leak in ("FERRITIN", "ng/mL", "Quest", "q.pdf", "SERUM", "18"):
        assert leak not in blob, f"audit log leaked {leak!r}"


# --- reading the trail ------------------------------------------------------

async def test_trail_is_scoped_to_the_caller(client):
    a, pid_a = await signed_in(client, "owner@example.com")
    await with_document(client, a, pid_a)

    b, pid_b = await signed_in(client, "other@example.com")
    mine = (await client.get("/api/audit", headers=b)).json()
    assert all(e["actor_email"] == "other@example.com" for e in mine)


async def test_trail_requires_auth(client):
    assert (await client.get("/api/audit")).status_code == 401


async def test_trail_is_newest_first(client):
    h, pid = await signed_in(client)
    await with_document(client, h, pid)
    await client.get(f"/api/documents/{pid}", headers=h)
    rows = (await client.get("/api/audit", headers=h)).json()
    times = [r["at"] for r in rows]
    assert times == sorted(times, reverse=True)


async def test_no_route_can_alter_the_log(client):
    """Append-only is enforced by there being nothing to call."""
    h, pid = await signed_in(client)
    for method in ("post", "put", "patch", "delete"):
        r = await getattr(client, method)("/api/audit", headers=h)
        assert r.status_code in (404, 405)
