"""Resolving what a user may do with a patient's record.

One function decides it, and every route asks that function. The alternative —
each handler checking roles itself — produces a permission matrix that is
implicit, untestable, and wrong in exactly one place.

The failure mode is always **404, never 403**. Telling a caller "you lack
permission for patient 6a80…" confirms that patient exists, which is itself a
disclosure: in a small clinic, knowing a particular person has a record is
sometimes the whole secret. An unreachable patient is indistinguishable from a
non-existent one.
"""

from datetime import datetime, timedelta

from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, status

from app.config import settings
from app.models.patient import RANK, Access, Patient, Role
from app.models.user import User, utcnow

# What each action needs. Named here rather than inline at call sites, so the
# permission model can be read in one place.
CAN_READ: Role = "viewer"
CAN_UPLOAD: Role = "nurse"
# Confirming a mapping decides what a number *is*, which is a clinical
# judgement — the same reasoning that routes critical analytes to a human
# rather than trusting a confident score.
CAN_CONFIRM: Role = "clinician"
CAN_MANAGE: Role = "owner"


def _oid(value: str) -> PydanticObjectId:
    try:
        return PydanticObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found") from None


def live() -> dict:
    """Return the filter for a grant that is in force right now.

    One definition, used by every query that asks "can they reach it" — the
    alternative is five call sites that each remember `revoked_at` and four
    that remember expiry, and the one that forgets is a grant that outlives
    the shift it was issued for.
    """
    return {
        "revoked_at": None,
        "$or": [{"expires_at": None}, {"expires_at": {"$gt": utcnow()}}],
    }


async def live_grants(user: User) -> list[Access]:
    """Every grant this user currently holds."""
    return await Access.find(Access.user_id == user.id, live()).to_list()


async def role_for(user: User, patient_id: PydanticObjectId) -> Role | None:
    """Return this user's active role on this patient, or None."""
    grant = await Access.find_one(
        Access.user_id == user.id,
        Access.patient_id == patient_id,
        live(),
    )
    return grant.role if grant else None


async def require(user: User, patient_id, minimum: Role = CAN_READ) -> Patient:
    """Resolve a patient the user may reach at `minimum` role, or 404.

    Returns the patient so callers do not fetch it twice.
    """
    pid = _oid(str(patient_id))
    role = await role_for(user, pid)
    if role is None or RANK[role] < RANK[minimum]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    patient = await Patient.get(pid)
    if patient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    await _require_second_factor(user, patient)
    return patient


async def _require_second_factor(user: User, patient: Patient) -> None:
    """Enforce MFA for accounts that reach somebody else's record.

    Deliberately **403, not 404**, and the one place that rule is inverted.
    Everywhere else a refusal must not confirm the record exists — but this
    caller already has a live grant and has been reading the record for days.
    Hiding it now would read as data loss, and the person would go looking for
    a bug instead of opening the screen the message names.

    The clock starts at the first such access rather than at the grant, so
    switching the policy on does not lock out every existing grant at once. The
    write happens once per account, ever.
    """
    grace = settings.mfa_grace_days
    if not grace or user.mfa_enabled or patient.created_by == user.id:
        return

    if user.mfa_required_since is None:
        user.mfa_required_since = utcnow()
        await user.save()
        return

    if utcnow() - user.mfa_required_since > timedelta(days=grace):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Two-factor authentication is required to open a record shared with "
            "you. Turn it on under Security.",
        )


async def reaches_others(user: User) -> bool:
    """Report whether this account can reach a record it did not create.

    The single definition of "needs a second factor". `_require_second_factor`
    asks the same question one patient at a time; if the prompt used a
    different rule — say, a count of grants — an account could sit silently
    below the prompt while the deadline it is never warned about arrives.
    """
    ids = [g.patient_id for g in await live_grants(user)]
    if not ids:
        return False
    return await Patient.find(
        {"_id": {"$in": ids}, "created_by": {"$ne": user.id}}
    ).count() > 0


async def reachable_patient_ids(user: User) -> list[PydanticObjectId]:
    """Every patient this user can currently see."""
    return [g.patient_id for g in await live_grants(user)]


async def last_owner(patient_id: PydanticObjectId, user_id: PydanticObjectId) -> bool:
    """Report whether this user is the only live owner of this record."""
    owners = await Access.find(
        Access.patient_id == patient_id, Access.role == "owner", live()
    ).to_list()
    return len(owners) == 1 and owners[0].user_id == user_id


async def grant(
    user_id: PydanticObjectId,
    patient_id: PydanticObjectId,
    role: Role,
    granted_by: PydanticObjectId | None = None,
    expires_at: datetime | None = None,
) -> Access:
    """Give a user access, or update the role if a live grant already exists.

    A record must always have a live owner. That was enforced on revocation and
    nowhere else, so the same record could be orphaned through the other door —
    by *demoting* its last owner instead of removing them. Both paths reduce
    the owner count, so the invariant belongs here, where the count changes,
    rather than at one of the two call sites that can change it.
    """
    existing = await Access.find_one(
        Access.user_id == user_id,
        Access.patient_id == patient_id,
        live(),
    )
    if existing:
        if (
            existing.role == "owner"
            and role != "owner"
            and await last_owner(patient_id, user_id)
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This is the only owner. Make someone else an owner first.",
            )
        existing.role = role
        existing.expires_at = expires_at
        await existing.save()
        return existing
    access = Access(
        user_id=user_id, patient_id=patient_id, role=role,
        granted_by=granted_by, expires_at=expires_at,
    )
    await access.insert()
    return access
