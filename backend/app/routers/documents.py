import hashlib
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from app import access, repo
from app.audit import record
from app.config import settings
from app.deps import current_user
from app.models.document import DocStatus, LabDocument
from app.models.observation import Observation
from app.models.user import User
from app.security import decrypt_field, encrypt_field
from app.throttle import guard_queue_depth

router = APIRouter(prefix="/api/documents", tags=["documents"])

limiter = Limiter(key_func=get_remote_address, enabled=not settings.is_test)

PDF_MAGIC = b"%PDF-"


class DocumentOut(BaseModel):
    """Document metadata. Carries no clinical content."""

    id: str
    filename: str
    status: DocStatus
    lab_name: str | None
    collected_at: datetime | None
    date_source: str
    page_count: int
    row_count: int
    size_bytes: int
    extraction_method: str | None
    error: str | None
    created_at: datetime

    @classmethod
    def of(cls, d: LabDocument) -> "DocumentOut":
        """Project a stored document to its API shape."""
        return cls(id=str(d.id), filename=d.filename, status=d.status,
                   lab_name=d.lab_name, collected_at=d.collected_at,
                   date_source=d.date_source, page_count=d.page_count,
                   row_count=d.row_count, size_bytes=d.size_bytes,
                   extraction_method=d.extraction_method, error=d.error,
                   created_at=d.created_at)


class ObservationOut(BaseModel):
    """One result row with its raw form and its resolved mapping."""

    id: str
    raw_name: str
    raw_value: str
    raw_unit: str | None
    raw_ref_range: str | None
    raw_specimen: str | None
    raw_flag: str | None
    page: int
    loinc_code: str | None
    loinc_display: str | None
    stage: str
    confidence: float
    review_status: str

    @classmethod
    def of(cls, o: Observation) -> "ObservationOut":
        """Project a stored observation to its API shape."""
        return cls(id=str(o.id), raw_name=o.raw_name, raw_value=o.raw_value,
                   raw_unit=o.raw_unit, raw_ref_range=o.raw_ref_range,
                   raw_specimen=o.raw_specimen, raw_flag=o.raw_flag, page=o.page,
                   loinc_code=o.loinc_code, loinc_display=o.loinc_display,
                   stage=o.mapping.stage, confidence=o.mapping.confidence,
                   review_status=o.review_status)


class DocumentDetail(DocumentOut):
    """A document plus every row extracted from it."""

    observations: list[ObservationOut] = []


@router.post("/{patient_id}", response_model=DocumentOut,
             status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("30/hour")
async def upload(
    request: Request, patient_id: str, file: UploadFile,
    user: User = Depends(current_user),
):
    """Accept a PDF for one patient, store it encrypted, and queue it."""
    patient = await access.require(user, patient_id, access.CAN_UPLOAD)
    # Checked before the body is read: refusing a 25 MB upload after receiving
    # it spends the bandwidth anyway.
    await guard_queue_depth(user)
    data = await file.read()

    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Empty file")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {settings.max_upload_bytes // (1024*1024)} MB",
        )
    # Magic bytes, not the extension or the client-supplied content type: both
    # are attacker-controlled.
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a PDF")

    digest = hashlib.sha256(data).hexdigest()
    if existing := await LabDocument.find_one(
        LabDocument.patient_id == patient.id, LabDocument.sha256 == digest
    ):
        return DocumentOut.of(existing)  # idempotent re-upload

    doc = LabDocument(
        patient_id=patient.id,
        uploaded_by=user.id,
        filename=(file.filename or "upload.pdf")[:200],
        sha256=digest,
        size_bytes=len(data),
        blob_enc=encrypt_field(data),
    )
    await doc.insert()
    await record("create", "document", doc.id, patient_id=patient.id)

    pool = request.app.state.arq
    if pool is None:
        # Worker unreachable: the upload is still safely stored and can be
        # replayed. Never lose the file because a queue is down.
        doc.status = "queued"
        doc.error = "Worker unavailable; retry processing later"
        await doc.save()
    else:
        await pool.enqueue_job("process_document", str(doc.id))
    return DocumentOut.of(doc)


@router.get("/{patient_id}", response_model=list[DocumentOut])
async def list_documents(
    patient_id: str, user: User = Depends(current_user), limit: int = 50
):
    """List one patient's reports, newest first."""
    docs = await repo.list_documents(user, patient_id, min(limit, 200))
    return [DocumentOut.of(d) for d in docs]


@router.get("/item/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, user: User = Depends(current_user)):
    """Return one document with all of its extracted rows."""
    doc = await repo.get_document(user, document_id)
    obs = await Observation.find(Observation.document_id == doc.id).sort("+page", "+line_no").to_list()
    return DocumentDetail(**DocumentOut.of(doc).model_dump(),
                          observations=[ObservationOut.of(o) for o in obs])


@router.get("/item/{document_id}/file")
async def get_file(document_id: str, user: User = Depends(current_user)):
    """Stream the decrypted PDF back to its owner."""
    doc = await repo.get_document(user, document_id)
    # The stored PDF carries far more than the extracted rows; taking a copy of
    # it is the single most sensitive action in the API.
    await record("download", "document", doc.id, patient_id=doc.patient_id)
    return Response(
        content=decrypt_field(doc.blob_enc),
        media_type="application/pdf",
        headers={
            # inline+nosniff: the browser renders it, never treats it as script.
            "Content-Disposition": f'inline; filename="{doc.id}.pdf"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/item/{document_id}/reprocess", response_model=DocumentOut)
@limiter.limit("30/hour")
async def reprocess(request: Request, document_id: str, user: User = Depends(current_user)):
    """Re-queue a document, replacing its rows rather than duplicating them.

    Limited exactly as `upload` is, and it was not. This queues the same
    extraction job for less effort than uploading does — no file body, just an
    id — so it was the cheaper way to load the worker, and on Render that worker
    shares a process with the API.
    """
    doc = await repo.get_document(user, document_id, access.CAN_UPLOAD)
    await guard_queue_depth(user)
    doc.status, doc.error = "queued", None
    await doc.save()
    await record("reprocess", "document", doc.id, patient_id=doc.patient_id)
    if pool := request.app.state.arq:
        await pool.enqueue_job("process_document", str(doc.id))
    return DocumentOut.of(doc)


@router.delete("/item/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, user: User = Depends(current_user)):
    """Delete a document and cascade to its observations."""
    doc = await repo.get_document(user, document_id, access.CAN_UPLOAD)
    await Observation.find(Observation.document_id == doc.id).delete()
    await doc.delete()
    await record("delete", "document", doc.id, patient_id=doc.patient_id)
