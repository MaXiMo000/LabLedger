from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.config import settings
from app.models.document import LabDocument
from app.models.observation import Observation
from app.security import decrypt_field
from app.worker import process_document

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).parent / "fixtures"
PDF = (FIXTURES / "quest_style.pdf").read_bytes()


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
