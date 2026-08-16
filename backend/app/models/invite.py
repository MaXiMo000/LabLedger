"""An offer of access to somebody who does not have an account yet.

`Access` cannot express this: a grant points at a `user_id`, and there is no
user to point at. So the offer is its own record, and becomes an `Access` the
moment it is accepted.

**Why a token and not just the email.** Registration does not verify email
addresses — nothing in this system can send mail. If an invitation were claimed
by matching the address alone, then learning that `nurse@ward.example` had been
invited would be enough: register that address first, and the record is yours.
The token closes that, and accepting still requires the invited address, so an
attacker needs both the link and the mailbox.

Stored as a hash, for the reason refresh tokens are: a leaked database must not
be a set of working keys.
"""

from datetime import datetime, timedelta
from typing import Annotated, ClassVar

import pymongo
from beanie import Document, Indexed, PydanticObjectId
from pydantic import EmailStr, Field

from app.models.patient import Role
from app.models.user import utcnow

# Long enough to survive a weekend, short enough that a forgotten invitation
# stops being a way in. An owner can always send another.
INVITE_TTL_DAYS = 7


def default_expiry() -> datetime:
    """Return when an invitation created now should stop working."""
    return utcnow() + timedelta(days=INVITE_TTL_DAYS)


class Invite(Document):
    """A pending offer of access to one patient, for one email address."""

    # Lowercased on the way in: addresses are case-insensitive in practice, and
    # an invitation that cannot be accepted because of a capital letter is
    # indistinguishable from a broken one.
    email: Annotated[EmailStr, Indexed()]
    patient_id: Annotated[PydanticObjectId, Indexed()]
    role: Role = "viewer"

    token_hash: Annotated[str, Indexed(unique=True)]

    invited_by: PydanticObjectId
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(default_factory=default_expiry)

    # Kept rather than deleted once used, for the same reason a revoked grant
    # is kept: who was let in, by whom, and when is what an investigation asks.
    claimed_at: datetime | None = None
    claimed_by: PydanticObjectId | None = None

    @property
    def pending(self) -> bool:
        """True while the invitation can still be accepted."""
        return self.claimed_at is None and self.expires_at > utcnow()

    class Settings:
        name = "invites"
        indexes: ClassVar = [
            [("patient_id", pymongo.ASCENDING), ("claimed_at", pymongo.ASCENDING)],
        ]
