import logging
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from app import access, mailer
from app.audit import record
from app.config import settings
from app.deps import current_user
from app.models.alias import Alias
from app.models.document import LabDocument
from app.models.invite import Invite
from app.models.observation import Observation
from app.models.patient import RANK, Access, Patient
from app.models.session import ROTATION_GRACE, Session, device_label, idle_cutoff
from app.models.user import User, utcnow
from app.security import (
    create_access_token,
    decrypt_str,
    encrypt_field,
    hash_password,
    hash_refresh_token,
    new_recovery_codes,
    new_refresh_token,
    new_totp_secret,
    take_recovery_code,
    totp_qr_svg,
    totp_uri,
    verify_password,
    verify_totp,
)
from app.throttle import clear_code_failures, guard_code_attempts, note_code_failure

logger = logging.getLogger("labledger.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Credential endpoints are the brute-force surface. Per-IP and deliberately
# tight: a human logs in a handful of times, a script does not.
limiter = Limiter(key_func=get_remote_address, enabled=not settings.is_test)

REFRESH_COOKIE = "ll_refresh"
# Scoped to /api/auth so the cookie is never sent to data endpoints.
COOKIE_PATH = "/api/auth"

oauth = OAuth()
if settings.google_client_id and settings.google_client_secret:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


# ---------- schemas ----------

class RegisterIn(BaseModel):
    """New account with an email and password."""

    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    # 12 chars minimum; bcrypt caps at 72 bytes and silently ignores the rest.
    password: str = Field(min_length=12, max_length=72)


class LoginIn(BaseModel):
    """Email and password credentials, plus a TOTP code once MFA is on."""

    email: EmailStr
    password: str = Field(max_length=72)
    code: str | None = Field(default=None, max_length=10)


class TokenOut(BaseModel):
    """Access token for the client to hold in memory only."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    """Public view of an account, with demographics decrypted."""

    id: str
    email: EmailStr
    name: str
    role: str
    dob: date | None = None
    sex_at_birth: str | None = None
    has_password: bool
    mfa_enabled: bool = False
    # True once the account reaches a record it did not create. Now enforced
    # after `MFA_GRACE_DAYS` — see `access._require_second_factor`. Sign-in
    # itself is never blocked, so the account can always still reach the screen
    # where MFA is turned on.
    mfa_recommended: bool = False
    # When the prompt becomes a wall. None while there is no deadline.
    mfa_deadline: datetime | None = None
    recovery_codes_left: int = 0
    # So the client can clear the screen at the same moment the server ends the
    # session, rather than leaving a ward monitor showing results until somebody
    # clicks and gets a 401.
    idle_timeout_min: int = 30


class SessionOut(BaseModel):
    """One signed-in device."""

    id: str
    device: str | None
    ip: str | None
    created_at: datetime
    last_seen: datetime
    current: bool


class MfaSetupOut(BaseModel):
    """The enrolment secret, shown once. Not active until a code is verified."""

    secret: str
    uri: str
    # Inline SVG of `uri`. Scanning beats transcribing 32 characters, but the
    # key stays available because a desktop authenticator has no camera.
    qr_svg: str


class RecoveryOut(BaseModel):
    """Recovery codes, shown once and never again."""

    codes: list[str]


class MfaCodeIn(BaseModel):
    """A six-digit TOTP code."""

    code: str = Field(max_length=10)


class ProfileIn(BaseModel):
    """Profile fields a user may change. Demographics drive reference ranges."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    dob: date | None = None
    sex_at_birth: Literal["M", "F", "X"] | None = None


async def to_user_out(user: User) -> UserOut:
    """Project a User to its public shape, decrypting demographics."""
    dob_raw = decrypt_str(user.dob_enc)
    # The same predicate the enforcement uses, so the prompt cannot stay quiet
    # while the deadline approaches.
    reaches_others = await access.reaches_others(user)
    return UserOut(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        dob=date.fromisoformat(dob_raw) if dob_raw else None,
        sex_at_birth=decrypt_str(user.sex_at_birth_enc),
        has_password=user.password_hash is not None,
        mfa_enabled=user.mfa_enabled,
        mfa_recommended=reaches_others and not user.mfa_enabled,
        mfa_deadline=(
            user.mfa_required_since + timedelta(days=settings.mfa_grace_days)
            if user.mfa_required_since and settings.mfa_grace_days and not user.mfa_enabled
            else None
        ),
        recovery_codes_left=len(user.recovery_hashes),
        idle_timeout_min=settings.session_idle_timeout_min,
    )


# ---------- session helpers ----------

async def _issue_session(
    user: User, response: Response, request: Request | None = None,
    session: Session | None = None,
) -> TokenOut:
    """Open or rotate a device session, returning its access token.

    Passing `session` rotates that row's refresh hash in place — same device,
    new secret. Passing none opens a new one. The distinction is the whole
    point of the collection: rotation is a defence against a stolen cookie,
    signing in on a second device is not an attack, and the old single-hash
    design could not tell them apart.
    """
    token, token_hash = new_refresh_token()
    now = utcnow()
    ip = request.client.host if request and request.client else None
    ua = request.headers.get("user-agent") if request else None

    if session is None:
        session = Session(
            user_id=user.id, refresh_hash=token_hash,
            device=device_label(ua), ip=ip,
        )
        await session.insert()
        user.last_login = now
        await user.save()
    else:
        session.previous_hash = session.refresh_hash
        session.rotated_at = now
        session.refresh_hash = token_hash
        session.last_seen = now
        if ip:
            session.ip = ip
        await session.save()

    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.jwt_refresh_ttl_days * 86400,
        httponly=True,
        secure=settings.is_prod,  # localhost is http in dev
        samesite="lax",  # blocks cross-site POST -> CSRF protection for /refresh
        path=COOKIE_PATH,
    )
    return TokenOut(
        access_token=create_access_token(str(user.id), user.role, str(session.id)),
        expires_in=settings.jwt_access_ttl_min * 60,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


async def _detect_replay(ll_refresh: str | None) -> bool:
    """End the session if a rotated-away token is presented, and say so.

    Rotation already makes a stolen cookie useless once the real client
    refreshes — but "useless" was answered with the same 401 as an ordinary
    expiry, so a theft looked exactly like a lapsed tab and nothing acted on
    it. A token that *was* valid and has since been rotated is being presented
    by whoever lost the race to rotate it, which is one party too many.

    The safe move is to end the session rather than guess which party is the
    thief: the legitimate user signs in again, the attacker's copy dies with
    it, and the event is in the audit log for somebody to read.
    """
    if not ll_refresh:
        return False
    # Anything inside the grace window was already accepted upstream, so
    # reaching here with a previous hash means the copy is genuinely old.
    session = await Session.find_one(
        Session.previous_hash == hash_refresh_token(ll_refresh),
        Session.revoked_at == None,  # noqa: E711
    )
    if session is None:
        return False

    session.revoked_at = utcnow()
    await session.save()
    user = await User.get(session.user_id)
    logger.warning("REFRESH TOKEN REPLAY session=%s user=%s device=%s",
                   session.id, session.user_id, session.device)
    if user:
        await record("sign_out", "session", str(session.id), actor=user)
    return True


async def _session_from_cookie(ll_refresh: str | None) -> Session | None:
    """Resolve the refresh cookie to a live session.

    Also accepts the *just*-rotated token, inside `ROTATION_GRACE`. Two tabs
    both refreshing on load is ordinary, and the one that loses the race holds
    a value that went stale while its request was in flight — refusing it
    signs somebody out of their own browser for having two tabs open.
    """
    if not ll_refresh:
        return None
    presented = hash_refresh_token(ll_refresh)
    return await Session.find_one(
        {
            "revoked_at": None,
            "$or": [
                {"refresh_hash": presented},
                {"previous_hash": presented,
                 "rotated_at": {"$gt": utcnow() - ROTATION_GRACE}},
            ],
        }
    )


# ---------- email + password ----------

@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
async def register(request: Request, body: RegisterIn, response: Response):
    """Create an account and open a session."""
    if await User.find_one(User.email == body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
    )
    await user.insert()
    return await _issue_session(user, response, request)


# Distinct message so the client knows to ask for a code rather than to treat
# the attempt as a bad password. It is only ever reached after the password
# has already verified, so it discloses nothing to a guesser.
MFA_REQUIRED = "Verification code required"


async def _same_device(ll_refresh: str | None, user: User) -> Session | None:
    """Return the session this browser already holds for this account, if any.

    Signing in again on a device that is already signed in is not a new device.
    Without this, every sign-in appends a row, and the device list fills with
    identical entries — which defeats its only purpose, since the question it
    answers is "is one of these not me".

    Only ever adopts a session belonging to the account that just
    authenticated, so a second account signing in on a shared browser opens its
    own session rather than taking over the first.
    """
    session = await _session_from_cookie(ll_refresh)
    return session if session and session.user_id == user.id else None


@router.post("/login", response_model=TokenOut)
@limiter.limit("10/minute")
async def login(
    request: Request, body: LoginIn, response: Response,
    ll_refresh: str | None = Cookie(default=None),
):
    """Exchange credentials for an access token and a refresh cookie."""
    user = await User.find_one(User.email == body.email)
    # Same message and roughly the same work for both failures: don't leak
    # which emails are registered.
    if user is None or user.password_hash is None:
        hash_password("dummy_password_x")  # equalize timing
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not verify_password(body.password, user.password_hash):
        # Failed attempts are recorded too: a run of them against one account
        # is the signal an audit trail exists to surface.
        await record("sign_in_failed", "session", actor=user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    if user.mfa_enabled:
        # Checked only once the password has verified, so the challenge never
        # tells an attacker that an account exists or has MFA on it.
        if not body.code:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, MFA_REQUIRED)
        await guard_code_attempts(user)
        if not verify_totp(decrypt_str(user.mfa_secret_enc) or "", body.code):
            # One field takes both: a person reaching for a recovery code has
            # already lost their phone, and making them find a second box to
            # type it into is a worse moment to introduce a puzzle.
            remaining = take_recovery_code(body.code, user.recovery_hashes)
            if remaining is None:
                await record("sign_in_failed", "session", actor=user)
                await note_code_failure(user)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                    "Invalid verification code")
            user.recovery_hashes = remaining
            await user.save()
        await clear_code_failures(user)

    out = await _issue_session(user, response, request,
                               session=await _same_device(ll_refresh, user))
    await record("sign_in", "session", actor=user)
    return out


@router.post("/refresh", response_model=TokenOut)
async def refresh(
    request: Request, response: Response, ll_refresh: str | None = Cookie(default=None)
):
    """Rotate this device's refresh cookie and mint a new access token."""
    session = await _session_from_cookie(ll_refresh)
    if session is None:
        if await _detect_replay(ll_refresh):
            _clear_session_cookie(response)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session ended")
        # Either revoked, already expired, or forged. Clear it either way.
        _clear_session_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    idle = timedelta(minutes=settings.session_idle_timeout_min)
    if utcnow() - session.last_seen > idle:
        # The idle timeout is not something a refresh can walk around, or it
        # would only ever apply to clients that stopped asking.
        session.revoked_at = utcnow()
        await session.save()
        _clear_session_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session timed out")

    user = await User.get(session.user_id)
    if user is None:
        _clear_session_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    return await _issue_session(user, response, request, session=session)  # rotates


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, ll_refresh: str | None = Cookie(default=None)):
    """End this device's session and clear the cookie."""
    session = await _session_from_cookie(ll_refresh)
    if session:
        session.revoked_at = utcnow()
        await session.save()
        user = await User.get(session.user_id)
        if user:
            await record("sign_out", "session", actor=user)
    _clear_session_cookie(response)


# ---------- devices ----------

@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    user: User = Depends(current_user), ll_refresh: str | None = Cookie(default=None)
):
    """Every device currently signed in to this account, newest first.

    Closes anything that has sat past the idle timeout before listing. The
    timeout is enforced when a session is *used*, so an abandoned one is never
    reaped on its own and would sit in this list indefinitely — which is how a
    single signed-in browser came to show two devices, one of them nine hours
    dead. A list nobody can trust is worse than no list, because the whole
    point of it is answering "is one of these not me".
    """
    await Session.find(
        Session.user_id == user.id,
        Session.revoked_at == None,  # noqa: E711
        {"last_seen": {"$lte": idle_cutoff()}},
    ).update({"$set": {"revoked_at": utcnow()}})

    current = await _session_from_cookie(ll_refresh)
    sessions = await Session.find(
        Session.user_id == user.id,
        Session.revoked_at == None,  # noqa: E711
    ).sort("-last_seen").to_list()
    return [
        SessionOut(
            id=str(s.id), device=s.device, ip=s.ip, created_at=s.created_at,
            last_seen=s.last_seen, current=current is not None and s.id == current.id,
        )
        for s in sessions
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(session_id: str, user: User = Depends(current_user)):
    """End one device's session.

    Takes effect on that device's next request, not at its token expiry:
    `deps.current_user` resolves this row every time.
    """
    session = await Session.get(session_id)
    # 404 rather than 403 for someone else's session, for the same reason a
    # patient they cannot reach is 404: the response must not confirm it exists.
    if session is None or session.user_id != user.id or session.revoked_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such session")
    session.revoked_at = utcnow()
    await session.save()
    await record("delete", "session", str(session.id))


@router.post("/sessions/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all_sessions(user: User = Depends(current_user)):
    """Sign out of everything, this device included.

    The answer to "I think somebody has my laptop": one action that needs no
    judgement about which row in the list is the intruder.
    """
    await Session.find(
        Session.user_id == user.id,
        Session.revoked_at == None,  # noqa: E711
    ).update({"$set": {"revoked_at": utcnow()}})
    await record("delete", "session")


# ---------- password ----------

class PasswordIn(BaseModel):
    """Change, or first set, this account's password."""

    # Absent when the account has none yet — a Google sign-in adding one.
    current_password: str | None = Field(default=None, max_length=72)
    new_password: str = Field(min_length=12, max_length=72)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def change_password(
    request: Request,  # noqa: ARG001 - slowapi requires `request` in the signature
    body: PasswordIn,
    response: Response,
    user: User = Depends(current_user),
    ll_refresh: str | None = Cookie(default=None),
):
    """Set a new password, and sign every other device out.

    The current password is required even though the caller already holds a
    session, because a borrowed session is exactly the thing this defends
    against: without it, fifteen minutes of an unlocked screen is enough to
    take the account permanently.

    An account created through Google has no password to prove, so the first
    one it sets needs none. After that the rule applies as normal.

    Other sessions end here. Changing a password is what somebody does when
    they think it is known, and leaving the other copies signed in would answer
    the fear without addressing it. This device stays, so the act of securing
    the account does not also sign you out of it.
    """
    if user.password_hash is not None:
        if not body.current_password:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password required")
        if not verify_password(body.current_password, user.password_hash):
            await record("sign_in_failed", "session", actor=user)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is wrong")

    user.password_hash = hash_password(body.new_password)
    await user.save()

    keep = await _session_from_cookie(ll_refresh)
    await Session.find(
        Session.user_id == user.id,
        Session.revoked_at == None,  # noqa: E711
        {"_id": {"$ne": keep.id}} if keep else {},
    ).update({"$set": {"revoked_at": utcnow()}})

    await record("update", "account", str(user.id))
    if keep is None:
        # No cookie to keep, so nothing was spared — do not leave the browser
        # holding one that no longer resolves.
        _clear_session_cookie(response)


class ResetRequestIn(BaseModel):
    """Ask for a reset link."""

    email: EmailStr


class ResetConfirmIn(BaseModel):
    """Use one."""

    token: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=72)


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/hour")
async def request_password_reset(
    request: Request,  # noqa: ARG001 - slowapi requires `request` in the signature
    body: ResetRequestIn,
):
    """Send a reset link, if that address has an account with a password.

    **Always 204**, whatever is true. Answering differently for a registered
    address turns this endpoint into a way to enumerate who has an account
    here — which, on a system holding clinical records, leaks that a named
    person is a patient somewhere. The rate limit is the compensating control,
    since silence means a guesser learns nothing per attempt.

    A Google-only account gets nothing: there is no password to reset, and
    sending a link that sets one would let anybody with mailbox access add a
    second way in past the identity provider.
    """
    user = await User.find_one(User.email == body.email.lower())
    if user is None or user.password_hash is None:
        return

    token, token_hash = new_refresh_token()
    user.reset_token_hash = token_hash
    user.reset_expires_at = utcnow() + timedelta(minutes=settings.password_reset_ttl_min)
    await user.save()

    await mailer.send_password_reset(
        user.email, f"{settings.frontend_url}/reset/{token}"
    )
    await record("update", "account", str(user.id), actor=user)


@router.post("/password/reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/hour")
async def confirm_password_reset(
    request: Request,  # noqa: ARG001 - slowapi requires `request` in the signature
    body: ResetConfirmIn,
    response: Response,
):
    """Set a new password from a reset link, and end every session.

    All of them, including whoever is holding the account right now. A reset is
    what somebody does when they have lost control of an account, so leaving
    the existing sessions alive would hand the new password to a stranger and
    change nothing for the person who took it.
    """
    user = await User.find_one(
        User.reset_token_hash == hash_refresh_token(body.token)
    )
    if user is None or not user.reset_expires_at or user.reset_expires_at < utcnow():
        # Expired, spent and forged answer alike: a distinguishable "expired"
        # confirms the address had a live link, which is half an enumeration.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This link is no longer valid. Request another.")

    user.password_hash = hash_password(body.new_password)
    user.reset_token_hash = None
    user.reset_expires_at = None
    # The second factor stays on. A reset proves control of the mailbox, which
    # is exactly the thing MFA exists to not be sufficient on its own.
    await user.save()

    await Session.find(
        Session.user_id == user.id,
        Session.revoked_at == None,  # noqa: E711
    ).update({"$set": {"revoked_at": utcnow()}})

    await record("update", "account", str(user.id), actor=user)
    _clear_session_cookie(response)


# ---------- the account itself ----------

@router.get("/export")
async def export_account(user: User = Depends(current_user)):
    """Everything this account is, as JSON.

    Deliberately *not* everything it can see. A viewer on somebody else's
    record could otherwise turn one grant into a permanent private copy of
    their results, which is the opposite of what an access model is for.
    Clinical data is exported per record by its owner, from
    `GET /api/patients/{id}/export`.
    """
    sessions = await Session.find(Session.user_id == user.id).to_list()
    aliases = await Alias.find(Alias.user_id == user.id).to_list()
    grants = await Access.find(Access.user_id == user.id).to_list()
    await record("download", "account", str(user.id))
    return {
        "exported_at": utcnow(),
        "account": {
            "email": user.email, "name": user.name,
            "dob": decrypt_str(user.dob_enc),
            "sex_at_birth": decrypt_str(user.sex_at_birth_enc),
            "created_at": user.created_at, "last_login": user.last_login,
            "mfa_enabled": user.mfa_enabled,
        },
        "sessions": [
            {"device": s.device, "ip": s.ip, "created_at": s.created_at,
             "last_seen": s.last_seen, "revoked_at": s.revoked_at}
            for s in sessions
        ],
        # What this account has taught the system about how labs print names.
        "aliases": [
            {"printed_name": a.raw_name, "loinc_code": a.loinc_code,
             "created_at": a.created_at}
            for a in aliases
        ],
        "record_access": [
            {"patient_id": str(g.patient_id), "role": g.role,
             "granted_at": g.granted_at, "revoked_at": g.revoked_at}
            for g in grants
        ],
    }


class DeleteMeIn(BaseModel):
    """Proof that the person deleting the account is its owner."""

    password: str | None = Field(default=None, max_length=72)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(body: DeleteMeIn, user: User = Depends(current_user)):
    """Delete this account, and any record only it can reach.

    Re-authenticates first. This destroys clinical data irreversibly, and a
    live session is a weaker claim than a password — an unlocked screen should
    not be enough to erase somebody's results. An account with no password of
    its own (Google-only) has nothing further to prove, and its session is the
    strongest evidence available.

    Refused while the account is the sole owner of a record somebody else can
    still reach: deleting it would take their access with it, and nobody should
    be able to destroy a colleague's data by closing their own account. Transfer
    ownership first — the error says so.

    Audit entries survive. They are append-only and carry a denormalised
    `actor_email` precisely so the trail stays readable after the actor is gone;
    a log that erases itself when somebody leaves is not a log.
    """
    if user.password_hash is not None and not verify_password(
        body.password or "", user.password_hash
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password is wrong")

    owned = await Access.find(Access.user_id == user.id, Access.role == "owner",
                              access.live()).to_list()
    for grant in owned:
        others = await Access.find(
            Access.patient_id == grant.patient_id, access.live(),
            {"user_id": {"$ne": user.id}},
        ).count()
        if others and await access.last_owner(grant.patient_id, user.id):
            patient = await Patient.get(grant.patient_id)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"You are the only owner of “{patient.display_name if patient else 'a record'}”, "
                "and other people can still reach it. Transfer ownership first.",
            )

    for grant in owned:
        if await access.last_owner(grant.patient_id, user.id):
            await _erase_patient(grant.patient_id)

    await Access.find(Access.user_id == user.id).delete()
    await Alias.find(Alias.user_id == user.id).delete()
    await Session.find(Session.user_id == user.id).delete()
    await Invite.find(Invite.email == user.email.lower()).delete()
    await record("delete", "account", str(user.id), actor=user)
    await user.delete()


async def _erase_patient(patient_id) -> None:
    """Destroy a record and everything measured from it."""
    await Observation.find(Observation.patient_id == patient_id).delete()
    await LabDocument.find(LabDocument.patient_id == patient_id).delete()
    await Invite.find(Invite.patient_id == patient_id).delete()
    patient = await Patient.get(patient_id)
    if patient:
        await patient.delete()


# ---------- invitations ----------

class ClaimIn(BaseModel):
    """The token from an invitation link."""

    token: str = Field(min_length=1, max_length=200)


class ClaimOut(BaseModel):
    """The record the invitation let you into."""

    patient_id: str
    display_name: str
    role: str


@router.post("/invites/claim", response_model=ClaimOut)
@limiter.limit("20/minute")
async def claim_invite(
    request: Request, body: ClaimIn, user: User = Depends(current_user),  # noqa: ARG001 - slowapi requires `request` in the signature
):
    """Accept an invitation and receive the access it offers.

    Lives on the account rather than under `/api/patients`, because the caller
    has no access to that patient yet — that is the entire point — and every
    route under the patient prefix begins by proving they do.

    Both halves are required. The link alone is not enough, since registration
    does not verify addresses; the address alone is not enough, since the token
    is unguessable. Expired, spent and forged all answer 404 alike, so a wrong
    token cannot be told apart from a used one.
    """
    row = await Invite.find_one(Invite.token_hash == hash_refresh_token(body.token))
    if row is None or not row.pending:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invitation is no longer valid")
    if row.email != user.email.lower():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This invitation was sent to {row.email}. "
            "Sign in as that account to accept it.",
        )

    patient = await Patient.get(row.patient_id)
    if patient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invitation is no longer valid")

    # Accepting must never cost you access you already had. An invitation
    # issued before somebody was made an owner would otherwise hand them
    # "viewer" and quietly take the record away.
    held = await access.role_for(user, patient.id)
    if held is None or RANK[row.role] > RANK[held]:
        await access.grant(user.id, patient.id, row.role, granted_by=row.invited_by)
    row.claimed_at = utcnow()
    row.claimed_by = user.id
    await row.save()
    await record("create", "account", patient.id, patient_id=patient.id)
    return ClaimOut(patient_id=str(patient.id), display_name=patient.display_name,
                    role=row.role)


# ---------- second factor ----------

@router.post("/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(user: User = Depends(current_user)):
    """Begin enrolment: mint a secret and hand back its otpauth URI.

    Stored immediately but inert — `mfa_enabled` only flips in /mfa/enable,
    once a code proves the authenticator actually holds the same secret. A
    scanned-but-unverified secret that already gated sign-in would lock the
    account out on a mistyped QR.
    """
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA is already enabled")
    secret = new_totp_secret()
    user.mfa_secret_enc = encrypt_field(secret)
    await user.save()
    uri = totp_uri(secret, user.email)
    return MfaSetupOut(secret=secret, uri=uri, qr_svg=totp_qr_svg(uri))


@router.post("/mfa/enable", response_model=RecoveryOut)
@limiter.limit("10/minute")
async def mfa_enable(
    request: Request, body: MfaCodeIn, user: User = Depends(current_user),  # noqa: ARG001 - slowapi requires `request` in the signature
):
    """Verify the first code, turn MFA on, and issue recovery codes.

    Returns the codes rather than the account, because this is the only moment
    they exist in the clear. A caller that ignores the response has locked
    themselves out of their own recovery path, so it is the whole body.
    """
    secret = decrypt_str(user.mfa_secret_enc)
    if not secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "Start with /mfa/setup")
    await guard_code_attempts(user)
    if not verify_totp(secret, body.code):
        await note_code_failure(user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid verification code")
    await clear_code_failures(user)

    codes, hashes = new_recovery_codes()
    user.mfa_enabled = True
    user.recovery_hashes = hashes
    # The requirement is met; the clock stops and is not carried forward.
    user.mfa_required_since = None
    await user.save()
    await record("update", "account", str(user.id))
    return RecoveryOut(codes=codes)


@router.post("/mfa/recovery", response_model=RecoveryOut)
@limiter.limit("10/minute")
async def mfa_new_recovery_codes(
    request: Request, body: MfaCodeIn, user: User = Depends(current_user),  # noqa: ARG001 - slowapi requires `request` in the signature
):
    """Replace the recovery codes. The old ones stop working immediately.

    Needs a current code: reissuing is how somebody with a borrowed session
    would mint themselves a permanent way back in.
    """
    if not user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA is not enabled")
    await guard_code_attempts(user)
    if not verify_totp(decrypt_str(user.mfa_secret_enc) or "", body.code):
        await note_code_failure(user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid verification code")
    await clear_code_failures(user)
    codes, user.recovery_hashes = new_recovery_codes()
    await user.save()
    await record("update", "account", str(user.id))
    return RecoveryOut(codes=codes)


@router.post("/mfa/disable", response_model=UserOut)
@limiter.limit("10/minute")
async def mfa_disable(
    request: Request, body: MfaCodeIn, user: User = Depends(current_user),  # noqa: ARG001 - slowapi requires `request` in the signature
):
    """Turn MFA off. Needs a current code — a borrowed session must not."""
    if not user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA is not enabled")
    await guard_code_attempts(user)
    if not verify_totp(decrypt_str(user.mfa_secret_enc) or "", body.code):
        await note_code_failure(user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid verification code")
    await clear_code_failures(user)
    user.mfa_enabled = False
    user.mfa_secret_enc = None
    user.recovery_hashes = []
    await user.save()
    await record("update", "account", str(user.id))
    return await to_user_out(user)


# ---------- Google OAuth (same client as Quiz-App, second redirect URI) ----------

@router.get("/google")
async def google_login(request: Request):
    """Redirect to Google's consent screen."""
    if "google" not in oauth._clients:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Google OAuth not configured")
    return await oauth.google.authorize_redirect(request, settings.google_callback_url)


@router.get("/google/callback")
async def google_callback(request: Request):
    """Complete the OAuth exchange and open a session.

    Redirects with no token in the URL: query strings end up in server logs.
    """
    if "google" not in oauth._clients:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Google OAuth not configured")
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse(f"{settings.frontend_url}/login?error=oauth")

    info = token.get("userinfo") or {}
    email, sub = info.get("email"), info.get("sub")
    if not email or not sub:
        return RedirectResponse(f"{settings.frontend_url}/login?error=oauth")

    user = await User.find_one(User.email == email)
    if user is None:
        user = User(email=email, name=info.get("name") or email.split("@")[0], google_sub=sub)
        await user.insert()
    elif user.google_sub is None:
        user.google_sub = sub

    # No token in the URL: set the refresh cookie and let the SPA call
    # /refresh on mount. Access tokens in query strings end up in server logs.
    # No MFA challenge here: Google performed the authentication, including
    # whatever second factor the account carries there.
    response = RedirectResponse(f"{settings.frontend_url}/auth/callback")
    await _issue_session(user, response, request,
                         session=await _same_device(request.cookies.get(REFRESH_COOKIE), user))
    return response


# ---------- profile ----------

@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    """Return the signed-in account."""
    return await to_user_out(user)


@router.patch("/me", response_model=UserOut)
async def update_me(body: ProfileIn, user: User = Depends(current_user)):
    """Update profile fields, encrypting demographics at rest."""
    if body.name is not None:
        user.name = body.name
    if body.dob is not None:
        if body.dob > datetime.now(UTC).date():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "DOB is in the future")
        user.dob_enc = encrypt_field(body.dob.isoformat())
    if body.sex_at_birth is not None:
        user.sex_at_birth_enc = encrypt_field(body.sex_at_birth)
    await user.save()
    return await to_user_out(user)
