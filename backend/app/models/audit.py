from datetime import datetime
from typing import Annotated, ClassVar, Literal

import pymongo
from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.models.user import utcnow

# Read actions are recorded as well as writes. HIPAA §164.312(b) requires
# recording access to ePHI, and access means looking — most real breaches are
# somebody reading a record they had no business opening.
AuditAction = Literal[
    "read", "list", "download",
    "create", "update", "delete",
    "confirm", "reject", "reprocess",
    "sign_in", "sign_out", "sign_in_failed",
]

ResourceType = Literal[
    "document", "observation", "alias", "series", "review", "account", "session",
    # The attention view reads every observation a patient has, so it is worth
    # distinguishing in the trail from a single-analyte series read.
    "attention",
]


class AuditEntry(Document):
    """One recorded touch of clinical data.

    Append-only by construction: nothing in the application updates or deletes
    an entry, and no route exposes either operation. A log that can be edited
    is evidence of nothing.

    Contains identifiers and actions, never content. No test name, no value, no
    filename — the same rule the application logs follow, for the same reason:
    an audit trail that leaks the data it protects is a second copy of the
    problem.
    """

    actor_id: Annotated[PydanticObjectId, Indexed()]
    actor_email: str  # denormalised: the entry must stay readable if the account goes

    # Nullable for now; Step 3 makes every clinical entry carry one.
    patient_id: PydanticObjectId | None = None

    action: AuditAction
    resource: ResourceType
    resource_id: str | None = None

    # Enough to recognise an unusual access pattern, and no more.
    ip: str | None = None
    user_agent: str | None = None

    at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "audit"
        indexes: ClassVar = [
            [("patient_id", pymongo.ASCENDING), ("at", pymongo.DESCENDING)],
            [("actor_id", pymongo.ASCENDING), ("at", pymongo.DESCENDING)],
            [("resource", pymongo.ASCENDING), ("resource_id", pymongo.ASCENDING)],
        ]
