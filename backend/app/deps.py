from datetime import timedelta

import jwt
from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.audit import set_actor
from app.config import settings
from app.models.session import Session
from app.models.user import User, utcnow
from app.security import decode_access_token

# auto_error=False so a missing header yields our 401 rather than a bare 403.
_bearer = HTTPBearer(auto_error=False)

# Don't write `last_seen` on every request — a busy screen fires four. The idle
# timeout is measured in minutes, so a minute of slack costs nothing and turns
# a write-per-request into a write-per-minute.
_TOUCH_AFTER = timedelta(seconds=60)


async def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """Resolve the bearer token to a user, or raise 401/403.

    The only sanctioned way a handler obtains a User: FastAPI will not call a
    route whose dependency raised, so authentication cannot be forgotten.

    The token is checked against its `Session` row on every request, which is
    what makes a session revocable. A signed JWT is otherwise valid until it
    expires, so "sign out this device" would mean "in up to fifteen minutes" —
    on a ward, fifteen minutes is the whole incident.
    """
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_access_token(cred.credentials)
    except jwt.ExpiredSignatureError:
        # 401 tells the client to refresh. Quiz-App returns 403 here, which is
        # why its interceptor logs you out on expiry instead of refreshing.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from None
    except jwt.PyJWTError:
        # `from None`: never leak why a token failed to verify.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid token") from None

    session = await _live_session(payload.get("sid"))
    user = await User.get(PydanticObjectId(payload["sub"]))
    if user is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid token")
    set_actor(user)
    return user


async def _live_session(sid: str | None) -> Session:
    """Load the token's session, enforcing revocation and the idle timeout.

    401 rather than 403 throughout: the client's correct response is to try the
    refresh cookie, which fails for the same reason, and only then to sign out.
    A 403 would make the interceptor drop the session without that check.
    """
    if not sid:
        # A token minted before sessions existed. It carries no revocable
        # identity, so it is not honoured.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session ended")
    try:
        session = await Session.get(PydanticObjectId(sid))
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session ended") from None
    if session is None or session.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session ended")

    now = utcnow()
    idle = timedelta(minutes=settings.session_idle_timeout_min)
    if now - session.last_seen > idle:
        # Close it rather than merely refusing: an idle session that stays open
        # is still listed as live, and the list is what somebody acts on.
        session.revoked_at = now
        await session.save()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session timed out")

    if now - session.last_seen > _TOUCH_AFTER:
        session.last_seen = now
        await session.save()
    return session


async def admin_user(user: User = Depends(current_user)) -> User:
    """Require the admin role on top of a valid token."""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user
