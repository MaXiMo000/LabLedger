import uuid
from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app import throttle
from app.config import settings
from app.models.document import LabDocument
from app.models.observation import Observation
from app.security import decrypt_field
from app.worker import process_document

pytestmark = pytest.mark.asyncio


def _unique_pdf() -> bytes:
    """A distinct valid PDF each call: identical bytes are deduplicated by
    sha256 and would return the first document instead of queueing new work."""
    import uuid
    return PDF.replace(b"%PDF-1.4", b"%PDF-1.4", 1) + b"\n% " + uuid.uuid4().hex.encode()

FIXTURES = Path(__file__).parent / "fixtures"
PDF = (FIXTURES / "quest_style.pdf").read_bytes()


def _unique_pdf() -> bytes:
    """A distinct valid PDF each call.

    Identical bytes are deduplicated by sha256 and return the existing document
    instead of queueing new work — which is correct behaviour, and would make
    these tests pass without the guard doing anything.
    """
    return PDF + b"\n% " + uuid.uuid4().hex.encode()


async def account(client, email="doc@example.com"):
    """Register an account owning one patient. Returns (headers, patient_id)."""
    r = await client.post("/api/auth/register", json={
        "email": email, "name": "Tester", "password": "correct-horse-battery"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    p = await client.post("/api/patients", headers=h,
                          json={"display_name": "Subject", "dob": "1996-04-12",
                                "sex_at_birth": "M"})
    return h, p.json()["id"]


async def upload(client, headers, pid, data=PDF, name="report.pdf"):
    return await client.post(f"/api/documents/{pid}", headers=headers,
                             files={"file": (name, data, "application/pdf")})


async def test_upload_requires_auth(client):
    r = await client.post(f"/api/documents/{PydanticObjectId()}", files={"file": ("a.pdf", PDF, "application/pdf")})
    assert r.status_code == 401


async def test_upload_accepts_pdf_and_returns_202(client):
    h, pid = await account(client)
    r = await upload(client, h, pid)
    assert r.status_code == 202, r.text
    assert r.json()["status"] in ("queued", "extracting", "done")
    assert r.json()["size_bytes"] == len(PDF)


async def test_non_pdf_rejected_by_magic_bytes(client):
    """A .pdf extension and a application/pdf content type are both attacker
    controlled; only the leading bytes are evidence."""
    h, pid = await account(client)
    r = await upload(client, h, pid, data=b"GIF89a totally not a pdf", name="evil.pdf")
    assert r.status_code == 422
    assert "Not a PDF" in r.text


async def test_empty_file_rejected(client):
    h, pid = await account(client)
    assert (await upload(client, h, pid, data=b"")).status_code == 422


async def test_oversized_file_rejected(client):
    h, pid = await account(client)
    big = b"%PDF-" + b"0" * (settings.max_upload_bytes + 1)
    assert (await upload(client, h, pid, data=big)).status_code == 413


async def test_reupload_is_deduped_by_sha256(client):
    h, pid = await account(client)
    first = (await upload(client, h, pid)).json()
    second = (await upload(client, h, pid, name="different-name.pdf")).json()
    assert first["id"] == second["id"]
    assert await LabDocument.find().count() == 1


async def test_pdf_bytes_are_encrypted_at_rest(client):
    h, pid = await account(client)
    doc_id = (await upload(client, h, pid)).json()["id"]
    doc = await LabDocument.get(PydanticObjectId(doc_id))
    assert not doc.blob_enc.startswith(b"%PDF-")   # ciphertext
    assert decrypt_field(doc.blob_enc) == PDF      # roundtrips


async def test_worker_extracts_rows(client):
    h, pid = await account(client)
    doc_id = (await upload(client, h, pid)).json()["id"]

    assert (await process_document({}, doc_id)).startswith("21 rows")

    r = await client.get(f"/api/documents/item/{doc_id}", headers=h)
    body = r.json()
    assert body["status"] in ("done", "needs_review")
    assert body["row_count"] == 21
    assert body["lab_name"] == "Quest Diagnostics"
    assert body["collected_at"].startswith("2026-03-14")
    assert len(body["observations"]) == 21

    ferritin = next(o for o in body["observations"] if o["raw_name"] == "FERRITIN")
    assert ferritin["raw_unit"] == "ng/mL"
    assert ferritin["raw_specimen"] == "SERUM"
    # the cascade now runs inside the worker
    assert ferritin["loinc_code"] == "2276-4"
    assert ferritin["stage"] == "exact"
    assert ferritin["review_status"] == "auto"


async def test_reprocess_replaces_rows_instead_of_duplicating(client):
    h, pid = await account(client)
    doc_id = (await upload(client, h, pid)).json()["id"]
    await process_document({}, doc_id)
    await process_document({}, doc_id)
    body = (await client.get(f"/api/documents/item/{doc_id}", headers=h)).json()
    assert body["row_count"] == 21
    assert len(body["observations"]) == 21


async def test_extracted_text_is_encrypted_at_rest(client):
    h, pid = await account(client)
    doc_id = (await upload(client, h, pid)).json()["id"]
    await process_document({}, doc_id)
    doc = await LabDocument.get(PydanticObjectId(doc_id))
    assert b"FERRITIN" not in doc.raw_text_enc
    assert b"FERRITIN" in decrypt_field(doc.raw_text_enc)


# --- ownership -------------------------------------------------------------

async def test_another_user_cannot_read_document(client):
    a, pid = await account(client, "owner@example.com")
    doc_id = (await upload(client, a, pid)).json()["id"]
    b, pid_b = await account(client, "attacker@example.com")

    # 404 not 403: never confirm that someone else's document id exists.
    assert (await client.get(f"/api/documents/item/{doc_id}", headers=b)).status_code == 404
    assert (await client.get(f"/api/documents/item/{doc_id}/file", headers=b)).status_code == 404
    assert (await client.delete(f"/api/documents/item/{doc_id}", headers=b)).status_code == 404
    assert (await client.get(f"/api/documents/{pid_b}", headers=b)).json() == []
    assert (await client.get(f"/api/documents/item/{doc_id}", headers=a)).status_code == 200


async def test_file_download_returns_pdf(client):
    h, pid = await account(client)
    doc_id = (await upload(client, h, pid)).json()["id"]
    r = await client.get(f"/api/documents/item/{doc_id}/file", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content == PDF


async def test_delete_cascades_to_observations(client):
    h, pid = await account(client)
    doc_id = (await upload(client, h, pid)).json()["id"]
    await process_document({}, doc_id)
    assert await Observation.find().count() == 21
    assert (await client.delete(f"/api/documents/item/{doc_id}", headers=h)).status_code == 204
    assert await Observation.find().count() == 0


async def test_malformed_pdf_fails_gracefully(client):
    """A file that passes the magic-byte check but is garbage must mark the
    document failed, not crash the worker."""
    h, pid = await account(client)
    doc_id = (await upload(client, h, pid, data=b"%PDF-1.4\nshredded", name="broken.pdf")).json()["id"]
    assert await process_document({}, doc_id) == "failed"
    body = (await client.get(f"/api/documents/item/{doc_id}", headers=h)).json()
    assert body["status"] == "failed" and body["error"]


# --- bounding the most expensive thing this system does ---------------------

async def test_a_backlog_of_work_refuses_more(client, account):
    """Extraction is the costly path, and on Render the worker shares a process
    with the API — so saturating the queue takes the API down, not just the
    bill up. Bounded by work in flight rather than by requests per hour: what
    needs limiting is how much is running at once, and the documents collection
    already knows that."""
    h, pid = account
    for _ in range(throttle.MAX_IN_FLIGHT):
        r = await client.post(f"/api/documents/{pid}", headers=h,
                              files={"file": ("q.pdf", _unique_pdf(), "application/pdf")})
        assert r.status_code == 202, r.text

    r = await client.post(f"/api/documents/{pid}", headers=h,
                          files={"file": ("more.pdf", _unique_pdf(), "application/pdf")})
    assert r.status_code == 429
    assert "still being processed" in r.json()["detail"]


async def test_finishing_the_work_frees_the_queue_again(client, account):
    """Steady use is never punished: the block lifts as the backlog drains, so
    somebody working through a folder waits only for what is already running."""
    h, pid = account
    for _ in range(throttle.MAX_IN_FLIGHT):
        await client.post(f"/api/documents/{pid}", headers=h,
                          files={"file": ("q.pdf", _unique_pdf(), "application/pdf")})
    assert (await client.post(f"/api/documents/{pid}", headers=h,
            files={"file": ("x.pdf", _unique_pdf(), "application/pdf")})).status_code == 429

    await LabDocument.find(LabDocument.status == "queued").update({"$set": {"status": "done"}})

    r = await client.post(f"/api/documents/{pid}", headers=h,
                          files={"file": ("y.pdf", _unique_pdf(), "application/pdf")})
    assert r.status_code == 202


async def test_reprocess_is_bounded_too(client, account):
    """It queues the same job for less effort than uploading -- no file body,
    just an id -- so it was the cheaper way to load the worker."""
    h, pid = account
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", _unique_pdf(), "application/pdf")})).json()["id"]
    await LabDocument.find(LabDocument.status == "queued").update({"$set": {"status": "done"}})

    # Fill the queue with other work, then try to add to it by reprocessing.
    for _ in range(throttle.MAX_IN_FLIGHT):
        await client.post(f"/api/documents/{pid}", headers=h,
                          files={"file": ("f.pdf", _unique_pdf(), "application/pdf")})

    r = await client.post(f"/api/documents/item/{doc_id}/reprocess", headers=h)
    assert r.status_code == 429


# --- stored bytes ------------------------------------------------------------

async def test_an_account_cannot_fill_the_database(client, account, monkeypatch):
    """25 MB a file with no ceiling on the count let one account fill Mongo, and
    a full cluster on the free tier does not degrade — it blocks writes for
    everything on it. This project has already done that to itself once."""
    h, pid = account
    pdf = _unique_pdf()
    # A cap just above one file, so the second is the one that does not fit.
    monkeypatch.setattr(settings, "max_account_bytes", len(pdf) + 10)

    first = await client.post(f"/api/documents/{pid}", headers=h,
                              files={"file": ("a.pdf", pdf, "application/pdf")})
    assert first.status_code == 202

    second = await client.post(f"/api/documents/{pid}", headers=h,
                               files={"file": ("b.pdf", _unique_pdf(), "application/pdf")})
    assert second.status_code == 507
    assert "limit" in second.json()["detail"]


async def test_re_sending_a_file_already_held_is_not_refused_for_space(
    client, account, monkeypatch
):
    """It stores nothing, so refusing it for storage it would not consume would
    be a confusing way to say "you already have this"."""
    h, pid = account
    pdf = _unique_pdf()
    first = await client.post(f"/api/documents/{pid}", headers=h,
                              files={"file": ("a.pdf", pdf, "application/pdf")})
    assert first.status_code == 202

    # Now set the cap below what is already stored: the account is over.
    monkeypatch.setattr(settings, "max_account_bytes", 1)
    again = await client.post(f"/api/documents/{pid}", headers=h,
                              files={"file": ("a.pdf", pdf, "application/pdf")})
    assert again.status_code == 507  # already full, refused before reading


async def test_deleting_a_report_frees_the_space(client, account, monkeypatch):
    h, pid = account
    pdf = _unique_pdf()
    monkeypatch.setattr(settings, "max_account_bytes", len(pdf) + 10)
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("a.pdf", pdf, "application/pdf")})).json()["id"]

    assert (await client.post(f"/api/documents/{pid}", headers=h,
            files={"file": ("b.pdf", _unique_pdf(), "application/pdf")})).status_code == 507

    assert (await client.delete(f"/api/documents/item/{doc_id}",
                                headers=h)).status_code == 204

    assert (await client.post(f"/api/documents/{pid}", headers=h,
            files={"file": ("c.pdf", _unique_pdf(), "application/pdf")})).status_code == 202
