"""The only sanctioned way to reach clinical data.

Two things happen here and nowhere else: access is resolved, and the access is
recorded. Both live at this choke point for the same reason — thirty handlers
means twenty-nine correct ones and one that forgot, and the one that forgot is
either an IDOR or a gap in the audit trail.

Every function takes the calling `User` and resolves which patient the data
belongs to before returning anything. A record the caller cannot reach answers
404, identical to one that does not exist.
"""

from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, status

from app import access
from app.audit import record
from app.models.document import LabDocument
from app.models.observation import Observation
from app.models.patient import Patient, Role
from app.models.user import User


def _oid(value: str, what: str) -> PydanticObjectId:
    try:
        return PydanticObjectId(value)
    except (InvalidId, TypeError):
        # A malformed id is indistinguishable from a non-existent one, by design.
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found") from None


# --- documents --------------------------------------------------------------

async def get_document(
    user: User, document_id: str, minimum: Role = access.CAN_READ
) -> LabDocument:
    """Fetch one document the user may reach, recording the access."""
    doc = await LabDocument.get(_oid(document_id, "Document"))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    # Resolve the document first, then the right to see it: the document knows
    # its own patient, so the caller never has to name one.
    await access.require(user, doc.patient_id, minimum)
    await record("read", "document", doc.id, patient_id=doc.patient_id)
    return doc


async def list_documents(user: User, patient_id, limit: int = 50) -> list[LabDocument]:
    """Fetch a patient's documents, newest first.

    Listing is access, so it is recorded like any other read.
    """
    patient = await access.require(user, patient_id)
    docs = await (
        LabDocument.find(LabDocument.patient_id == patient.id)
        .sort("-created_at").limit(limit).to_list()
    )
    await record("list", "document", patient_id=patient.id)
    return docs


# --- observations -----------------------------------------------------------

async def get_observation(
    user: User, observation_id: str, minimum: Role = access.CAN_READ
) -> Observation:
    """Fetch one observation the user may reach, recording the access."""
    obs = await Observation.get(_oid(observation_id, "Observation"))
    if obs is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Observation not found")
    await access.require(user, obs.patient_id, minimum)
    await record("read", "observation", obs.id, patient_id=obs.patient_id)
    return obs


async def patient_observations(user: User, patient_id) -> tuple[Patient, object]:
    """Establish access, then return (patient, an unexecuted query builder).

    Handed back unexecuted so callers can add their own filters without this
    module growing a parameter per screen — but they can only ever receive one
    already scoped to a patient they may reach.
    """
    patient = await access.require(user, patient_id)
    return patient, Observation.find(Observation.patient_id == patient.id)
