"""One signed-in device.

Replaces the single `refresh_token_hash` that used to live on `User`. That
field made sessions mutually exclusive: signing in on the ward tablet silently
ended the desktop session, which on a ward is not a nuisance but a reason to
share a login.

A session is the unit of revocation. The access token carries this row's id as
`sid`, and `deps.current_user` resolves it on every request — so revoking a
session ends a live one within the same request rather than at the fifteen
minute token expiry.

The refresh hash still rotates on every use; the *session* survives the
rotation. Rotation detects a stolen cookie, the session identifies the device,
and conflating the two is what made the old design wrong.
"""

from datetime import datetime, timedelta
from typing import Annotated, ClassVar

import pymongo
from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.config import settings
from app.models.user import utcnow


class Session(Document):
    """A live sign-in on one device."""

    user_id: Annotated[PydanticObjectId, Indexed()]
    # sha256 of the opaque refresh token. Rotated on every refresh; the row is
    # not. Unique so a rotated-away hash can never resolve to two sessions.
    refresh_hash: Annotated[str, Indexed(unique=True)]

    # The hash this one replaced. Rotation already made a replayed cookie
    # useless; remembering one step back is what makes it *detectable* — a
    # token that was valid and has since been rotated away is being presented
    # by whoever did not do the rotating. One step is enough: the legitimate
    # client and the thief cannot both hold the current token, so the loser of
    # that race presents exactly this value on its next refresh.
    previous_hash: Annotated[str | None, Indexed()] = None
    # When the rotation happened, so a token that is *barely* stale can be told
    # from one presented hours later. See ROTATION_GRACE.
    rotated_at: datetime | None = None

    # Enough to recognise a device in a list, and no more. Derived from the
    # User-Agent, which is a claim, not evidence — it labels, it never authorises.
    device: str | None = None
    ip: str | None = None

    created_at: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    revoked_at: datetime | None = None

    class Settings:
        name = "sessions"
        indexes: ClassVar = [
            [("user_id", pymongo.ASCENDING), ("revoked_at", pymongo.ASCENDING)],
        ]


# Two tabs of the same app both call /refresh on load. One wins, rotates the
# token, and the other's request — already in flight with the value that was
# current when it left — arrives a moment later holding what is now the
# previous hash. That is a race, not a theft, and treating it as one signs a
# legitimate user out of their own browser for having two tabs open. Inside
# this window the old token is still accepted; outside it, presenting a
# rotated-away token means somebody kept a copy.
#
# Ten seconds, not thirty. The race it covers is only the gap between one tab
# sending its request and the other's Set-Cookie landing — milliseconds in
# practice — and every second of the window is a second in which a stolen
# cookie still works. Wide enough for a slow network, no wider.
ROTATION_GRACE = timedelta(seconds=10)


def idle_cutoff() -> datetime:
    """Return the `last_seen` before which a session has timed out."""
    return utcnow() - timedelta(minutes=settings.session_idle_timeout_min)


def live() -> dict:
    """Return the filter for a session that is in force right now.

    Revoked *and* not idle, in one place. The idle timeout only fires when a
    session is used, so an abandoned one is never reaped — and a list that
    asked only `revoked_at is None` therefore showed sessions that would be
    refused on their very next request. Somebody reading that list to answer
    "is one of these not me" was being shown ghosts.
    """
    return {"revoked_at": None, "last_seen": {"$gt": idle_cutoff()}}


def device_label(user_agent: str | None) -> str:
    """Derive a short human label from a User-Agent string.

    Substring matching in a fixed order, not a parser: the list is read by a
    person deciding whether to end a session, and "Chrome on macOS" answers
    that. A full UA parser is a dependency and a maintenance burden for a
    label nobody authorises anything on.
    """
    ua = user_agent or ""
    browser = next(
        (n for k, n in (
            ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox", "Firefox"),
            ("Chrome", "Chrome"), ("Safari", "Safari"),
        ) if k in ua),
        None,
    )
    platform = next(
        (n for k, n in (
            ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
            ("Mac OS X", "macOS"), ("Windows", "Windows"), ("Linux", "Linux"),
        ) if k in ua),
        None,
    )
    if browser and platform:
        return f"{browser} on {platform}"
    return browser or platform or "Unknown device"
