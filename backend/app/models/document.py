from datetime import datetime
from typing import Annotated, ClassVar, Literal

import pymongo
from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.models.user import utcnow

DocStatus = Literal["queued", "extracting", "mapping", "needs_review", "done", "failed"]


class LabDocument(Document):
    """An uploaded lab report PDF.

    The PDF bytes and the extracted text are both encrypted at rest; everything
    else on this document is metadata that carries no clinical content.
    """

    # Whose body this describes. The clinical key.
    patient_id: Annotated[PydanticObjectId, Indexed()]
    # Who put it here. Kept separately from the subject because "who uploaded"
    # and "who it is about" are different questions, and an audit needs both.
    uploaded_by: PydanticObjectId

    filename: str
    sha256: str  # per-user dedupe: re-uploading the same PDF is a no-op
    content_type: str = "application/pdf"
    size_bytes: int
    page_count: int = 0

    lab_name: str | None = None
    collected_at: datetime | None = None
    # Whether collected_at came from a specimen collection date or was inferred
    # from the report date. Plotting a trend on report dates is subtly wrong.
    date_source: Literal["collected", "reported", "none"] = "none"

    status: DocStatus = "queued"
    error: str | None = None
    extraction_method: str | None = None  # "tables" | "text" | "ocr"
    row_count: int = 0

    # The PDF itself. Optional because it can be disposed of while everything
    # extracted from it stays: the blob is very nearly all of a document's
    # bytes, and the numbers are the clinical value. `blob_purged_at` records
    # that this happened, so a missing blob is never confused with one that was
    # never stored — the file endpoint answers 410 rather than 404, because
    # "held and disposed of" is a different statement from "no such thing".
    blob_enc: bytes | None = None
    blob_purged_at: datetime | None = None
    raw_text_enc: bytes | None = None

    created_at: datetime = Field(default_factory=utcnow)
    processed_at: datetime | None = None

    class Settings:
        name = "documents"
        indexes: ClassVar = [
            [("patient_id", pymongo.ASCENDING), ("sha256", pymongo.ASCENDING)],
            [("patient_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)],
        ]
