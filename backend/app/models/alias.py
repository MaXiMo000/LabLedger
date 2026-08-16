from datetime import datetime
from typing import Annotated, ClassVar, Literal

import pymongo
from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.models.user import utcnow


class Alias(Document):
    """A learned name -> LOINC mapping.

    The review queue writes these; mapping stage 0 reads them.

    This is the collection that makes the system get *more* deterministic with
    use: every confirmation converts one probabilistic decision into a lookup
    that never needs the LLM again.
    """

    # None = shipped/global. Otherwise private to one user.
    #
    # Scoped to the user rather than the patient, deliberately: confirming that
    # "FERRTN SER" means ferritin is knowledge about how a lab prints things,
    # belonging to the person who made that judgement — not to the body it was
    # measured from. A clinician's confirmations should carry across their whole
    # caseload rather than being relearned per patient.
    user_id: PydanticObjectId | None = None
    normalized_name: Annotated[str, Indexed()]
    specimen: str | None = None
    loinc_code: str

    source: Literal["seed", "user_confirmed"] = "user_confirmed"
    confirmed_count: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None

    class Settings:
        name = "aliases"
        indexes: ClassVar = [
            [("user_id", pymongo.ASCENDING),
             ("normalized_name", pymongo.ASCENDING),
             ("specimen", pymongo.ASCENDING)],
        ]
