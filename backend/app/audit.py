"""Recording who touched what.

Two rules shape this module.

**It is called from `repo.py`, not from handlers.** `repo.py` is already the
single choke point every piece of clinical data passes through to enforce
ownership. Auditing in the same place means the thirty-first handler someone
adds is audited whether or not they remembered to, and a log with gaps is worse
than no log because it implies a completeness it does not have.

**Request context arrives by ContextVar, not by argument.** The alternative is
threading `Request` down through the repository into every call site, which
makes the audit boundary something callers can forget or bypass. Middleware
sets it once per request; anything running inside that request can record.
"""

import logging
from contextvars import ContextVar

from beanie import PydanticObjectId

from app.models.audit import AuditEntry

logger = logging.getLogger("labledger.audit")


class _Ctx:
    __slots__ = ("actor_email", "actor_id", "ip", "user_agent")

    def __init__(self, actor_id=None, actor_email="", ip=None, user_agent=None):
        self.actor_id = actor_id
        self.actor_email = actor_email
        self.ip = ip
        self.user_agent = user_agent


_ctx: ContextVar[_Ctx | None] = ContextVar("audit_ctx", default=None)


def _current() -> _Ctx:
    """Never share one mutable default across contexts: build per context."""
    return _ctx.get() or _Ctx()


def set_request_context(ip: str | None, user_agent: str | None) -> None:
    """Open the audit context. Called once per request, before the route runs."""
    cur = _current()
    _ctx.set(_Ctx(cur.actor_id, cur.actor_email, ip, user_agent))


def set_actor(user) -> None:
    """Attach the caller. Called by the auth dependency once identity is known."""
    cur = _current()
    _ctx.set(_Ctx(user.id, user.email, cur.ip, cur.user_agent))


async def record(
    action: str,
    resource: str,
    resource_id: str | None = None,
    patient_id: PydanticObjectId | None = None,
    actor=None,
) -> None:
    """Write one entry.

    Awaited rather than fired and forgotten: an unlogged access is exactly the
    thing the log exists to make impossible, so it is worth one insert on the
    request path. A failure is logged loudly and never raised — losing the
    audit entry is bad, but failing a clinician's read because the audit
    collection hiccuped is worse, and the warning is what surfaces it.
    """
    ctx = _current()
    actor_id = actor.id if actor is not None else ctx.actor_id
    actor_email = actor.email if actor is not None else ctx.actor_email

    if actor_id is None:
        return  # nothing to attribute; anonymous routes touch no clinical data

    try:
        await AuditEntry(
            actor_id=actor_id,
            actor_email=actor_email,
            patient_id=patient_id,
            action=action,
            resource=resource,
            resource_id=str(resource_id) if resource_id else None,
            ip=ctx.ip,
            user_agent=(ctx.user_agent or "")[:300] or None,
        ).insert()
    except Exception as exc:  # noqa: BLE001 - never fail a request over the log
        logger.error("AUDIT WRITE FAILED action=%s resource=%s id=%s actor=%s: %s",
                     action, resource, resource_id, actor_id, exc)
