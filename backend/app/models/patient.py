from datetime import datetime
from typing import Annotated, ClassVar, Literal

import pymongo
from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.models.user import utcnow

# Deliberately few. Every role added is a permission matrix row somebody has to
# reason about at 3am, and four covers the real distinctions.
Role = Literal["owner", "clinician", "nurse", "viewer"]

# Ordered, so a check is "at least this" rather than a set membership test that
# has to be updated everywhere a role is introduced.
RANK: dict[str, int] = {"viewer": 0, "nurse": 1, "clinician": 2, "owner": 3}


class Patient(Document):
    """The subject of the results — a body, not an account.

    Splitting this from `User` is the whole point of the phase. Previously one
    account meant one person's body, which makes a nurse with twelve patients
    unrepresentable, and a parent tracking a child equally so.

    `display_name` is deliberately *not* encrypted: it is rendered in the
    patient switcher on every screen, and decrypting on every render to show a
    label the user typed themselves buys nothing. Everything that identifies
    the person to an outsider — date of birth, sex, medical record number — is
    encrypted, because those are the fields that turn a leaked row into an
    identified individual.
    """

    display_name: str
    dob_enc: bytes | None = None
    sex_at_birth_enc: bytes | None = None
    mrn_enc: bytes | None = None

    created_by: Annotated[PydanticObjectId, Indexed()]
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "patients"


class Access(Document):
    """One user's reach into one patient's record.

    Access is a record rather than a field on either side, because it carries
    its own facts: who granted it, when, and whether it has been revoked.
    Revocation sets `revoked_at` instead of deleting the row — the fact that
    someone *used to* have access is exactly what an investigation needs, and
    deleting it destroys the answer.
    """

    user_id: Annotated[PydanticObjectId, Indexed()]
    patient_id: Annotated[PydanticObjectId, Indexed()]
    role: Role = "viewer"

    granted_by: PydanticObjectId | None = None
    granted_at: datetime = Field(default_factory=utcnow)
    revoked_at: datetime | None = None

    # A locum covers a shift, not a career. None means open-ended, which is
    # still the default: an expiry nobody set is an expiry that ends a
    # clinician's access mid-round.
    expires_at: datetime | None = None

    @property
    def active(self) -> bool:
        """True while the grant is neither revoked nor expired."""
        return self.revoked_at is None and (
            self.expires_at is None or self.expires_at > utcnow()
        )

    class Settings:
        name = "access"
        indexes: ClassVar = [
            # The hot query: can this user reach this patient, right now.
            [("user_id", pymongo.ASCENDING),
             ("patient_id", pymongo.ASCENDING),
             ("revoked_at", pymongo.ASCENDING)],
        ]
