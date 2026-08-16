"""Reading the audit trail.

Deliberately read-only: no route updates or deletes an entry, because a log
that can be edited is evidence of nothing. Callers see only their own trail
today; Step 3 scopes it by patient once patients exist.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app import access
from app.deps import current_user
from app.models.audit import AuditEntry
from app.models.user import User

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditOut(BaseModel):
    """One recorded access."""

    at: datetime
    actor_email: str
    action: str
    resource: str
    resource_id: str | None
    ip: str | None


@router.get("/patient/{patient_id}", response_model=list[AuditOut])
async def patient_trail(
    patient_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(current_user),
):
    """Who has touched this record, newest first.

    Owner-only. Seeing every access to a record — including accesses by other
    clinicians — is itself sensitive, and is the reason the answer belongs to
    whoever is accountable for the record rather than to anyone who can read it.
    """
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    rows = await (
        AuditEntry.find(AuditEntry.patient_id == patient.id)
        .sort("-at").limit(limit).to_list()
    )
    return [
        AuditOut(
            at=r.at, actor_email=r.actor_email, action=r.action,
            resource=r.resource, resource_id=r.resource_id, ip=r.ip,
        )
        for r in rows
    ]


@router.get("", response_model=list[AuditOut])
async def trail(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(current_user),
):
    """Return this account's access history, newest first."""
    rows = await (
        AuditEntry.find(AuditEntry.actor_id == user.id)
        .sort("-at").limit(limit).to_list()
    )
    return [
        AuditOut(
            at=r.at, actor_email=r.actor_email, action=r.action,
            resource=r.resource, resource_id=r.resource_id, ip=r.ip,
        )
        for r in rows
    ]
