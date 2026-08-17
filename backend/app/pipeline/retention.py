"""Disposing of stored PDFs once they have outlived their use.

**Only the blob.** A document's bytes are very nearly all in its PDF; the
extracted results are small and are the clinical value. Deleting the file and
keeping the numbers reclaims essentially all the storage while the chart, the
printed names behind each result, and the audit trail all survive.

Be plain about what this is: **disposal of source documents, not a retention
policy**. A retention policy says how long clinical records live and who may
end them. This says how long the scanned page lives. It closes the storage
question and part of the compliance one; it does not close the clinical one,
and `ARCHITECTURE.md` should keep saying so.

**Off by default.** `DOCUMENT_RETENTION_DAYS = 0` disposes of nothing.
Deleting somebody's data because a setting was left at its default is the wrong
way round, so a deployment has to ask for this.

What is lost is real and worth naming: the original page can no longer be
opened, so a number can be traced to its printed name, value and page *number*
but not back to the image it was read from. That is why the file endpoint
answers **410 Gone** afterwards rather than 404 — "held and disposed of" is a
different statement from "no such document", and a reader deserves the true one.
"""

import logging
from datetime import timedelta

from app.config import settings
from app.models.document import LabDocument
from app.models.user import utcnow

logger = logging.getLogger("labledger.retention")


async def purge_expired_blobs(days: int | None = None) -> int:
    """Drop the stored PDF of every document past its retention age.

    Returns how many were disposed of. Idempotent: a document whose blob is
    already gone is not selected again.
    """
    days = settings.document_retention_days if days is None else days
    if not days:
        return 0

    cutoff = utcnow() - timedelta(days=days)
    # `blob_enc != None` rather than checking `blob_purged_at`: the blob being
    # absent is the fact that matters, and a document that never had one should
    # not be counted as disposed of.
    stale = LabDocument.find(
        LabDocument.created_at < cutoff,
        {"blob_enc": {"$ne": None}},
    )
    purged = 0
    async for doc in stale:
        doc.blob_enc = None
        doc.blob_purged_at = utcnow()
        await doc.save()
        purged += 1

    if purged:
        # Counts only. Which documents, for whom, is exactly the sort of detail
        # the application logs already refuse to carry.
        logger.info("purged %d stored PDFs older than %d days", purged, days)
    return purged
