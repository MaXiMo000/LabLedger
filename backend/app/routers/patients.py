"""Patients, and who may reach them."""

from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app import access, mailer
from app.audit import record
from app.config import settings
from app.deps import current_user
from app.models.document import LabDocument
from app.models.invite import Invite
from app.models.observation import Observation
from app.models.patient import Access, Patient, Role
from app.models.user import User, utcnow
from app.security import decrypt_str, encrypt_field, new_refresh_token

router = APIRouter(prefix="/api/patients", tags=["patients"])


class PatientIn(BaseModel):
    """A new record, or an edit to one."""

    display_name: str = Field(min_length=1, max_length=120)
    dob: date | None = None
    sex_at_birth: Literal["M", "F", "X"] | None = None
    mrn: str | None = Field(default=None, max_length=64)


class PatientOut(BaseModel):
    """A patient, with the caller's own role on them."""

    id: str
    display_name: str
    dob: date | None
    sex_at_birth: str | None
    mrn: str | None
    role: Role
    created_at: datetime


class AccessIn(BaseModel):
    """Give another account access to this record."""

    email: EmailStr
    role: Role = "viewer"
    # For a locum covering a shift. None is open-ended.
    expires_at: datetime | None = None


class AccessOut(BaseModel):
    """One person's reach into this record."""

    user_id: str
    email: str
    role: Role
    granted_at: datetime
    revoked_at: datetime | None
    expires_at: datetime | None = None


def _out(p: Patient, role: Role) -> PatientOut:
    dob = decrypt_str(p.dob_enc)
    return PatientOut(
        id=str(p.id), display_name=p.display_name,
        dob=date.fromisoformat(dob) if dob else None,
        sex_at_birth=decrypt_str(p.sex_at_birth_enc),
        mrn=decrypt_str(p.mrn_enc),
        role=role, created_at=p.created_at,
    )


@router.get("", response_model=list[PatientOut])
async def list_patients(user: User = Depends(current_user)):
    """Every record this account can currently reach."""
    by_id = {g.patient_id: g.role for g in await access.live_grants(user)}
    if not by_id:
        return []
    patients = await Patient.find({"_id": {"$in": list(by_id)}}).to_list()
    patients.sort(key=lambda p: p.display_name.lower())
    return [_out(p, by_id[p.id]) for p in patients]


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
async def create_patient(body: PatientIn, user: User = Depends(current_user)):
    """Create a record. The creator becomes its owner."""
    if body.dob and body.dob > datetime.now(UTC).date():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Date of birth is in the future")

    patient = Patient(
        display_name=body.display_name,
        dob_enc=encrypt_field(body.dob.isoformat()) if body.dob else None,
        sex_at_birth_enc=encrypt_field(body.sex_at_birth) if body.sex_at_birth else None,
        mrn_enc=encrypt_field(body.mrn) if body.mrn else None,
        created_by=user.id,
    )
    await patient.insert()
    await access.grant(user.id, patient.id, "owner", granted_by=user.id)
    await record("create", "account", patient.id, patient_id=patient.id)
    return _out(patient, "owner")


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(patient_id: str, user: User = Depends(current_user)):
    """One record, if the caller may reach it."""
    patient = await access.require(user, patient_id)
    role = await access.role_for(user, patient.id)
    await record("read", "account", patient.id, patient_id=patient.id)
    return _out(patient, role)


@router.patch("/{patient_id}", response_model=PatientOut)
async def update_patient(
    patient_id: str, body: PatientIn, user: User = Depends(current_user)
):
    """Edit demographics. Owner only — these drive reference ranges.

    Keyed on `model_fields_set` rather than on the value being non-None, so
    "not sent" and "sent as null" stay different things. Testing for None
    conflates them, which makes a field impossible to *clear* once set: a DOB
    typed wrong is then permanent, and a wrong DOB silently selects the wrong
    reference range for every future result.
    """
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    sent = body.model_fields_set
    patient.display_name = body.display_name
    if "dob" in sent:
        if body.dob and body.dob > datetime.now(UTC).date():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "Date of birth is in the future")
        patient.dob_enc = encrypt_field(body.dob.isoformat()) if body.dob else None
    if "sex_at_birth" in sent:
        patient.sex_at_birth_enc = encrypt_field(body.sex_at_birth)
    if "mrn" in sent:
        # An MRN typed as spaces is not an MRN.
        patient.mrn_enc = encrypt_field(body.mrn.strip() or None) if body.mrn else None
    await patient.save()
    await record("update", "account", patient.id, patient_id=patient.id)
    return _out(patient, "owner")


# --- access ------------------------------------------------------------------

@router.get("/{patient_id}/access", response_model=list[AccessOut])
async def list_access(patient_id: str, user: User = Depends(current_user)):
    """Who can reach this record, including grants that have been revoked.

    Revoked rows are shown rather than hidden: that somebody *used to* have
    access is exactly what an investigation needs to know.
    """
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    grants = await Access.find(Access.patient_id == patient.id).to_list()
    users = {u.id: u for u in await User.find(
        {"_id": {"$in": [g.user_id for g in grants]}}
    ).to_list()}
    return [
        AccessOut(
            user_id=str(g.user_id),
            email=users[g.user_id].email if g.user_id in users else "(deleted account)",
            role=g.role, granted_at=g.granted_at, revoked_at=g.revoked_at,
            expires_at=g.expires_at,
        )
        for g in grants
    ]


@router.post("/{patient_id}/access", response_model=AccessOut,
             status_code=status.HTTP_201_CREATED)
async def add_access(patient_id: str, body: AccessIn, user: User = Depends(current_user)):
    """Grant another account access to this record."""
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    target = await User.find_one(User.email == body.email)
    if target is None:
        # Do not reveal whether an email has an account here.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No account with that email")

    # Typing your own address into a form that says "give someone access" reads
    # as adding a person, not as changing your own role — but it lands on the
    # same grant, and the role it lands with is whatever the picker happened to
    # be showing. That is how an owner demotes themselves to viewer by accident.
    if target.id == user.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You already have access to this record. To change your own role, "
            "ask another owner.",
        )

    if body.expires_at is not None and body.expires_at <= utcnow():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Expiry is in the past")

    granted = await access.grant(target.id, patient.id, body.role,
                                 granted_by=user.id, expires_at=body.expires_at)
    await record("update", "account", patient.id, patient_id=patient.id)
    return AccessOut(
        user_id=str(target.id), email=target.email, role=granted.role,
        granted_at=granted.granted_at, revoked_at=None,
        expires_at=granted.expires_at,
    )


class TransferIn(BaseModel):
    """Hand this record to another account."""

    email: EmailStr


@router.post("/{patient_id}/transfer", response_model=PatientOut)
async def transfer_ownership(
    patient_id: str, body: TransferIn, user: User = Depends(current_user)
):
    """Make somebody else the owner, and step down to clinician.

    Without this, handing a record over means granting owner and asking the
    other person to demote you — which only works while you are both available
    and willing. A record whose owner has left was recoverable only by editing
    the database, which had to be done once already.

    The order matters: the new owner is promoted *before* the old one steps
    down, so the record is never momentarily ownerless even if the second write
    fails. Stepping down to clinician rather than out entirely keeps the
    clinical access somebody handing over a record almost always still needs;
    leaving completely is then one revoke away, and possible because there is
    now another owner to permit it.
    """
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    target = await User.find_one(User.email == body.email.lower())
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No account with that email")
    if target.id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already own this record")

    await access.grant(target.id, patient.id, "owner", granted_by=user.id)
    await access.grant(user.id, patient.id, "clinician", granted_by=user.id)
    await record("update", "account", patient.id, patient_id=patient.id)
    return _out(patient, "clinician")


@router.get("/{patient_id}/export")
async def export_patient(patient_id: str, user: User = Depends(current_user)):
    """Everything held about this record, as JSON. Owner only.

    Owner rather than reader, because an export is a permanent private copy
    that outlives any revocation — the one action where "can read it now" and
    "may keep it forever" have to be different questions.

    Source PDFs are named but not embedded: they are already downloadable one
    at a time, and inlining megabytes of base64 would make the common case —
    reading what the system holds — unusable.
    """
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    docs = await LabDocument.find(LabDocument.patient_id == patient.id).to_list()
    obs = await (
        Observation.find(Observation.patient_id == patient.id)
        .sort("+collected_at").to_list()
    )
    await record("download", "account", patient.id, patient_id=patient.id)
    return {
        "exported_at": utcnow(),
        "patient": _out(patient, "owner").model_dump(mode="json"),
        "reports": [
            {"id": str(d.id), "filename": d.filename, "lab": d.lab_name,
             "collected_at": d.collected_at, "status": d.status,
             "uploaded_at": d.created_at, "pages": d.page_count,
             "sha256": d.sha256}
            for d in docs
        ],
        "results": [
            {
                "collected_at": o.collected_at,
                "printed_name": o.raw_name, "printed_value": o.raw_value,
                "printed_unit": o.raw_unit,
                "loinc_code": o.loinc_code, "loinc_display": o.loinc_display,
                "value": o.canonical_value, "unit": o.canonical_unit,
                "value_operator": o.value_operator,
                "reference_low": o.ref_low, "reference_high": o.ref_high,
                "reference_source": o.ref_source, "flag": o.flag,
                # How the mapping was reached, not just what it decided — an
                # export that drops the provenance is not the same record.
                "mapping_stage": o.mapping.stage,
                "mapping_confidence": o.mapping.confidence,
                "review_status": o.review_status,
                "report_id": str(o.document_id), "page": o.page,
            }
            for o in obs
        ],
    }


# --- invitations -------------------------------------------------------------

class InviteIn(BaseModel):
    """Offer access to an address that has no account yet."""

    email: EmailStr
    role: Role = "viewer"


class InviteOut(BaseModel):
    """A pending offer. `link` is returned once, at creation."""

    id: str
    email: str
    role: Role
    invited_at: datetime
    expires_at: datetime
    link: str | None = None


@router.post("/{patient_id}/invites", response_model=InviteOut,
             status_code=status.HTTP_201_CREATED)
async def invite(patient_id: str, body: InviteIn, user: User = Depends(current_user)):
    """Create an invitation link for an address with no account.

    The link is returned once and never stored in the clear, so it has to be
    passed to the person by whatever channel the ward already trusts. This
    system sends no mail, and a link that could be re-read from the invitation
    list would be a standing key sitting next to the door it opens.
    """
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    email = body.email.lower()

    # Already has an account: grant directly rather than making them accept an
    # invitation to something they could simply be given.
    if await User.find_one(User.email == email):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That address already has an account. Grant access to it directly.",
        )

    existing = await Invite.find_one(
        Invite.email == email, Invite.patient_id == patient.id,
        Invite.claimed_at == None,  # noqa: E711
    )
    if existing:
        # Re-inviting replaces the outstanding offer rather than adding a
        # second: two live links to the same record is one more than anybody
        # can keep track of.
        await existing.delete()

    token, token_hash = new_refresh_token()
    row = Invite(email=email, patient_id=patient.id, role=body.role,
                 token_hash=token_hash, invited_by=user.id)
    await row.insert()
    # Sent *and* returned. The link is the only copy, so if the send fails the
    # owner still has something to pass on by hand rather than an invitation
    # that exists but can never be delivered.
    await mailer.send_invitation(
        to_email=email, inviter_email=user.email, role=row.role,
        invite_url=f"{settings.frontend_url}/invite/{token}",
        expires_on=row.expires_at.strftime("%d %B %Y"),
    )
    await record("create", "account", patient.id, patient_id=patient.id)

    return InviteOut(
        id=str(row.id), email=row.email, role=row.role,
        invited_at=row.created_at, expires_at=row.expires_at,
        link=f"{settings.frontend_url}/invite/{token}",
    )


@router.get("/{patient_id}/invites", response_model=list[InviteOut])
async def list_invites(patient_id: str, user: User = Depends(current_user)):
    """Offers on this record that nobody has accepted yet."""
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    rows = await Invite.find(
        Invite.patient_id == patient.id,
        Invite.claimed_at == None,  # noqa: E711
        {"expires_at": {"$gt": utcnow()}},
    ).to_list()
    return [
        InviteOut(id=str(i.id), email=i.email, role=i.role,
                  invited_at=i.created_at, expires_at=i.expires_at)
        for i in rows
    ]


@router.delete("/{patient_id}/invites/{invite_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def cancel_invite(
    patient_id: str, invite_id: str, user: User = Depends(current_user)
):
    """Withdraw an invitation before it is accepted."""
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    row = await Invite.get(invite_id)
    # 404 for an invitation on another record, by the same rule as everything
    # else: the response must not confirm that it exists.
    if row is None or row.patient_id != patient.id or row.claimed_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invitation")
    # Deleted, not marked: nothing was granted, so there is no access history
    # to preserve — only an offer that was withdrawn.
    await row.delete()
    await record("delete", "account", patient.id, patient_id=patient.id)


@router.delete("/{patient_id}/access/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_access(
    patient_id: str, user_id: str, user: User = Depends(current_user)
):
    """Revoke a grant.

    Sets `revoked_at` rather than deleting the row — deleting it destroys the
    record of who had reach and when, which is the answer an audit needs.
    """
    patient = await access.require(user, patient_id, access.CAN_MANAGE)
    grants = await Access.find(Access.patient_id == patient.id, access.live()).to_list()

    target = next((g for g in grants if str(g.user_id) == user_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such grant")

    # A record with no owner is unreachable and unrecoverable.
    if target.role == "owner" and sum(1 for g in grants if g.role == "owner") == 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This is the only owner. Give someone else ownership first.",
        )

    target.revoked_at = utcnow()
    await target.save()
    await record("delete", "account", patient.id, patient_id=patient.id)
