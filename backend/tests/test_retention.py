"""Disposing of stored PDFs while keeping what was read out of them.

The claim: the blob is very nearly all of a document's bytes and the results
are the clinical value, so dropping one and keeping the other reclaims the
storage without losing the chart.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from app.models.document import LabDocument
from app.models.observation import Observation
from app.models.user import User, utcnow
from app.pipeline.retention import purge_expired_blobs
from app.throttle import storage_used
from app.worker import process_document

pytestmark = pytest.mark.asyncio

PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()


async def uploaded(client, account, age_days: int = 0):
    h, pid = account
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    if age_days:
        doc = await LabDocument.get(doc_id)
        doc.created_at = utcnow() - timedelta(days=age_days)
        await doc.save()
    return h, pid, doc_id


async def test_disposal_is_off_unless_asked_for(client, account):
    """Deleting somebody's data because a setting was left at its default is
    the wrong way round."""
    await uploaded(client, account, age_days=9999)
    assert await purge_expired_blobs(days=0) == 0
    assert (await LabDocument.find_one()).blob_enc is not None


async def test_the_results_survive_the_document_they_came_from(client, account):
    """The whole basis for disposing of blobs rather than documents."""
    _, _, doc_id = await uploaded(client, account, age_days=400)
    await process_document({}, doc_id)
    before = await Observation.find(Observation.patient_id == (await LabDocument.get(doc_id)).patient_id).count()
    assert before > 0

    assert await purge_expired_blobs(days=365) == 1

    doc = await LabDocument.get(doc_id)
    assert doc.blob_enc is None
    assert doc.blob_purged_at is not None
    # Everything read out of it is untouched, including the printed names that
    # make a number traceable.
    rows = await Observation.find(Observation.document_id == doc.id).to_list()
    assert len(rows) == before
    assert all(o.raw_name and o.raw_value for o in rows)


async def test_a_document_inside_its_retention_is_left_alone(client, account):
    await uploaded(client, account, age_days=10)
    assert await purge_expired_blobs(days=365) == 0
    assert (await LabDocument.find_one()).blob_enc is not None


async def test_purging_twice_disposes_of_nothing_the_second_time(client, account):
    await uploaded(client, account, age_days=400)
    assert await purge_expired_blobs(days=365) == 1
    assert await purge_expired_blobs(days=365) == 0


async def test_downloading_a_disposed_file_is_410_not_404(client, account):
    """"Held and disposed of" is a different statement from "no such document",
    and answering the wrong one sends somebody hunting for a bug."""
    h, _, doc_id = await uploaded(client, account, age_days=400)
    await purge_expired_blobs(days=365)

    r = await client.get(f"/api/documents/item/{doc_id}/file", headers=h)
    assert r.status_code == 410
    assert "retention" in r.json()["detail"]

    # The document itself still lists and still opens.
    detail = await client.get(f"/api/documents/item/{doc_id}", headers=h)
    assert detail.status_code == 200
    assert detail.json()["blob_purged_at"] is not None


async def test_reprocessing_a_disposed_document_is_refused(client, account):
    """Re-queueing would mark it failed a minute later, which looks like a bug
    rather than a policy."""
    h, _, doc_id = await uploaded(client, account, age_days=400)
    await purge_expired_blobs(days=365)

    # Park it in a terminal state first, so "reprocess left it alone" is
    # distinguishable from "it was queued anyway".
    doc = await LabDocument.get(doc_id)
    doc.status = "done"
    await doc.save()

    r = await client.post(f"/api/documents/item/{doc_id}/reprocess", headers=h)
    assert r.status_code == 410
    assert (await LabDocument.get(doc_id)).status == "done"


async def test_disposal_gives_the_account_its_quota_back(client, account):
    """The two features have to agree, and at first they did not.

    `size_bytes` survives disposal as a record of what the document was, and
    the storage cap summed it — so an account whose files had been reclaimed
    was still blocked from uploading by bytes nobody was storing. The cap
    counts what is held now.
    """
    await uploaded(client, account, age_days=400)
    user = await User.find_one(User.email == "owner@example.com")
    assert await storage_used(user) > 0

    await purge_expired_blobs(days=365)
    assert await storage_used(user) == 0
