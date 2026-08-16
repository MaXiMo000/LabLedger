"""Sessions, revocation, idle timeout, grant expiry, and the second factor.

The claim this file has to earn is the one the phase is named for: a session
can be ended, and the ending is felt on the next request rather than at the
fifteen-minute token expiry. Everything else here is the machinery that makes
that claim true without signing a nurse out of the ward tablet every time they
open a desktop.
"""

import time
from datetime import timedelta

import pytest
from beanie import PydanticObjectId

from app.models.patient import Access
from app.models.session import Session, device_label
from app.models.user import User, utcnow
from app.security import create_access_token, totp_at, verify_totp

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery"


async def register(client, email="a@example.com", **headers):
    r = await client.post(
        "/api/auth/register",
        json={"email": email, "name": email.split("@")[0], "password": PASSWORD},
        headers=headers or None,
    )
    return r


def bearer(r):
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def sign_in(client, email="a@example.com", *, new_device=False, **kw):
    """Sign in. `new_device=True` drops the refresh cookie first.

    One httpx client is one cookie jar, which is to say one browser. Signing in
    again through it is the same device signing in again — so a test that means
    "a second device" has to say so by discarding the first one's cookie.
    """
    if new_device:
        client.cookies.clear()
    return await client.post("/api/auth/login",
                             json={"email": email, "password": PASSWORD, **kw})


# --- several devices at once -------------------------------------------------

async def test_two_devices_stay_signed_in(client):
    """The old single `refresh_token_hash` made these mutually exclusive:
    signing in on one device silently ended the other."""
    first = bearer(await register(client))
    second = bearer(await sign_in(client, new_device=True))

    assert (await client.get("/api/auth/me", headers=first)).status_code == 200
    assert (await client.get("/api/auth/me", headers=second)).status_code == 200
    assert len((await client.get("/api/auth/sessions", headers=first)).json()) == 2


async def test_sessions_are_labelled_by_device(client):
    await register(client)
    client.cookies.clear()  # a fresh browser, so a session is created not rotated
    r = await client.post("/api/auth/login",
                          json={"email": "a@example.com", "password": PASSWORD},
                          headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel "
                                                 "Mac OS X 10_15_7) Chrome/131.0"})
    rows = (await client.get("/api/auth/sessions", headers=bearer(r))).json()
    assert "Chrome on macOS" in [s["device"] for s in rows]


@pytest.mark.parametrize(("ua", "label"), [
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/605", "Safari on iPhone"),
    ("Mozilla/5.0 (Windows NT 10.0) Firefox/121.0", "Firefox on Windows"),
    # Edge and Opera both carry "Chrome" in their UA; order decides, so it is
    # asserted rather than assumed.
    ("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537 Edg/120", "Edge on Windows"),
    ("", "Unknown device"),
])
async def test_device_label(ua, label):
    assert device_label(ua) == label


async def test_signing_in_again_on_the_same_device_reuses_its_session(client):
    """Otherwise the list fills with identical rows and stops answering the
    only question it exists for: is one of these not me."""
    await register(client)                       # the cookie jar is this device
    for _ in range(3):
        assert (await sign_in(client)).status_code == 200

    r = await sign_in(client)
    rows = (await client.get("/api/auth/sessions", headers=bearer(r))).json()
    assert len(rows) == 1
    assert rows[0]["current"] is True


async def test_a_second_account_on_one_browser_gets_its_own_session(client):
    """Adopting by cookie must never hand one account another's session."""
    await register(client, "a@example.com")
    b = bearer(await register(client, "b@example.com"))  # cookie now belongs to b
    b_session = (await client.get("/api/auth/sessions", headers=b)).json()[0]["id"]

    r = await sign_in(client, "a@example.com")   # a signs in holding b's cookie
    rows = (await client.get("/api/auth/sessions", headers=bearer(r))).json()

    # b's session was not taken over, and everything a can see belongs to a.
    assert b_session not in [s["id"] for s in rows]
    user_a = await User.find_one(User.email == "a@example.com")
    for s in rows:
        assert (await Session.get(PydanticObjectId(s["id"]))).user_id == user_a.id
    assert (await Session.get(PydanticObjectId(b_session))).revoked_at is None


async def test_refresh_rotates_the_secret_but_not_the_session(client):
    """A device keeps its identity across rotation. Conflating the two is what
    made the old design unable to tell a second device from a stolen cookie."""
    r = await register(client)
    before = (await client.get("/api/auth/sessions", headers=bearer(r))).json()

    rotated = await client.post("/api/auth/refresh")
    assert rotated.status_code == 200
    after = (await client.get("/api/auth/sessions", headers=bearer(rotated))).json()

    assert len(after) == 1
    assert after[0]["id"] == before[0]["id"]


# --- revocation is felt immediately ------------------------------------------

async def test_revoking_a_session_ends_a_live_token(client):
    """The phase goal. The revoked device still holds a signed, unexpired
    access token; it must stop working on the very next request."""
    keeper = bearer(await register(client))
    doomed = bearer(await sign_in(client, new_device=True))

    rows = (await client.get("/api/auth/sessions", headers=doomed)).json()
    target = next(s["id"] for s in rows if not s["current"])

    assert (await client.delete(f"/api/auth/sessions/{target}",
                                headers=doomed)).status_code == 204
    assert (await client.get("/api/auth/me", headers=keeper)).status_code == 401
    assert (await client.get("/api/auth/me", headers=doomed)).status_code == 200


async def test_revoke_all_ends_every_session_including_this_one(client):
    first = bearer(await register(client))
    second = bearer(await sign_in(client, new_device=True))

    assert (await client.post("/api/auth/sessions/revoke-all",
                              headers=second)).status_code == 204
    assert (await client.get("/api/auth/me", headers=first)).status_code == 401
    assert (await client.get("/api/auth/me", headers=second)).status_code == 401


async def test_logout_ends_only_this_device(client):
    """Signing out of the desktop must not sign out the ward tablet."""
    other = bearer(await register(client))
    await sign_in(client, new_device=True)  # the cookie now belongs to the second device

    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.get("/api/auth/me", headers=other)).status_code == 200


async def test_another_accounts_session_is_404_not_403(client):
    """A 403 would confirm the session id exists — the same disclosure rule as
    a patient somebody cannot reach."""
    mine = bearer(await register(client, "a@example.com"))
    theirs = bearer(await register(client, "b@example.com"))
    target = (await client.get("/api/auth/sessions", headers=theirs)).json()[0]["id"]

    assert (await client.delete(f"/api/auth/sessions/{target}",
                                headers=mine)).status_code == 404
    assert (await client.get("/api/auth/me", headers=theirs)).status_code == 200


async def test_a_token_without_a_session_is_refused(client):
    """A token minted before sessions existed carries no revocable identity."""
    r = await register(client)
    user = await User.find_one(User.email == "a@example.com")
    forged = create_access_token(str(user.id), user.role, "")
    assert (await client.get("/api/auth/me",
                             headers={"Authorization": f"Bearer {forged}"})
            ).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer(r))).status_code == 200


# --- idle timeout ------------------------------------------------------------

async def idle_out(session_id):
    """Backdate a session past the idle window rather than sleeping for it."""
    s = await Session.get(PydanticObjectId(session_id))
    s.last_seen = utcnow() - timedelta(days=1)
    await s.save()


async def test_an_idle_session_times_out(client):
    h = bearer(await register(client))
    sid = (await client.get("/api/auth/sessions", headers=h)).json()[0]["id"]
    await idle_out(sid)

    assert (await client.get("/api/auth/me", headers=h)).status_code == 401
    # Closed, not merely refused: a session that stays listed as live is one
    # somebody has to reason about later.
    assert (await Session.get(PydanticObjectId(sid))).revoked_at is not None


async def test_refresh_cannot_walk_around_the_idle_timeout(client):
    """Otherwise the timeout would only ever apply to clients that stopped
    asking, which is exactly the ones that do not need it."""
    h = bearer(await register(client))
    await idle_out((await client.get("/api/auth/sessions", headers=h)).json()[0]["id"])
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_activity_keeps_a_session_alive(client):
    h = bearer(await register(client))
    sid = (await client.get("/api/auth/sessions", headers=h)).json()[0]["id"]
    before = (await Session.get(PydanticObjectId(sid))).last_seen

    # Backdate inside the window: the request should touch it, not end it.
    s = await Session.get(PydanticObjectId(sid))
    s.last_seen = utcnow() - timedelta(minutes=5)
    await s.save()

    assert (await client.get("/api/auth/me", headers=h)).status_code == 200
    assert (await Session.get(PydanticObjectId(sid))).last_seen >= before


# --- grant expiry ------------------------------------------------------------

async def make_patient(client, h):
    return (await client.post("/api/patients", headers=h,
            json={"display_name": "Subject"})).json()["id"]


async def test_an_expired_grant_loses_reach(client):
    """A locum covers a shift, not a career."""
    owner = bearer(await register(client, "owner@example.com"))
    pid = await make_patient(client, owner)
    locum = bearer(await register(client, "locum@example.com"))

    await client.post(f"/api/patients/{pid}/access", headers=owner, json={
        "email": "locum@example.com", "role": "clinician",
        "expires_at": (utcnow() + timedelta(hours=8)).isoformat()})
    assert (await client.get(f"/api/patients/{pid}", headers=locum)).status_code == 200

    grant = await Access.find_one(Access.patient_id == PydanticObjectId(pid),
                                  Access.role == "clinician")
    grant.expires_at = utcnow() - timedelta(minutes=1)
    await grant.save()

    assert (await client.get(f"/api/patients/{pid}", headers=locum)).status_code == 404
    assert (await client.get("/api/patients", headers=locum)).json() == []


async def test_an_expiry_in_the_past_is_rejected(client):
    """A grant that is dead on arrival is a mistake, not an intention."""
    owner = bearer(await register(client, "owner@example.com"))
    pid = await make_patient(client, owner)
    await register(client, "locum@example.com")

    r = await client.post(f"/api/patients/{pid}/access", headers=owner, json={
        "email": "locum@example.com", "role": "viewer",
        "expires_at": (utcnow() - timedelta(hours=1)).isoformat()})
    assert r.status_code == 422


async def test_a_grant_with_no_expiry_is_open_ended(client):
    """The default must not quietly end a clinician's access mid-round."""
    owner = bearer(await register(client, "owner@example.com"))
    pid = await make_patient(client, owner)
    other = bearer(await register(client, "other@example.com"))
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "other@example.com", "role": "viewer"})
    assert (await client.get(f"/api/patients/{pid}", headers=other)).status_code == 200


# --- TOTP --------------------------------------------------------------------

async def test_totp_matches_the_rfc_6238_vector():
    """RFC 6238 appendix B, SHA1, T=59. A hand-rolled implementation that
    passes the published vector is interoperable with every authenticator app;
    one that only agrees with itself is not."""
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # b32("12345678901234567890")
    assert totp_at(secret, 59 // 30) == "287082"
    assert totp_at(secret, 1111111109 // 30) == "081804"


async def test_totp_tolerates_clock_drift_but_not_a_wrong_code():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    step = int(time.time()) // 30
    for drift in (-1, 0, 1):
        assert verify_totp(secret, totp_at(secret, step + drift))
    assert not verify_totp(secret, totp_at(secret, step + 5))
    assert not verify_totp(secret, "000")  # wrong shape
    assert not verify_totp(secret, "abcdef")


async def enrol_mfa(client, h):
    secret = (await client.post("/api/auth/mfa/setup", headers=h)).json()["secret"]
    code = totp_at(secret, int(time.time()) // 30)
    r = await client.post("/api/auth/mfa/enable", headers=h, json={"code": code})
    assert r.status_code == 200, r.text
    return secret


async def test_mfa_gates_the_next_sign_in(client):
    h = bearer(await register(client))
    secret = await enrol_mfa(client, h)

    assert (await sign_in(client)).status_code == 401
    assert (await sign_in(client, code="000000")).status_code == 401
    ok = await sign_in(client, code=totp_at(secret, int(time.time()) // 30))
    assert ok.status_code == 200


async def test_mfa_is_inert_until_a_code_is_verified(client):
    """A scanned-but-unverified secret that already gated sign-in would lock
    the account out on a mistyped QR."""
    h = bearer(await register(client))
    await client.post("/api/auth/mfa/setup", headers=h)

    assert (await client.get("/api/auth/me", headers=h)).json()["mfa_enabled"] is False
    assert (await sign_in(client)).status_code == 200


async def test_a_wrong_password_never_reveals_that_mfa_is_on(client):
    """The challenge is only reachable after the password verifies, so it tells
    a guesser nothing about which accounts exist."""
    h = bearer(await register(client))
    await enrol_mfa(client, h)
    r = await client.post("/api/auth/login",
                          json={"email": "a@example.com", "password": "wrong-one-here"})
    assert r.json()["detail"] == "Invalid email or password"


async def test_disabling_mfa_needs_a_current_code(client):
    """A borrowed session must not be able to take the second factor off."""
    h = bearer(await register(client))
    secret = await enrol_mfa(client, h)

    assert (await client.post("/api/auth/mfa/disable", headers=h,
                              json={"code": "000000"})).status_code == 401
    r = await client.post("/api/auth/mfa/disable", headers=h,
                          json={"code": totp_at(secret, int(time.time()) // 30)})
    assert r.status_code == 200
    assert r.json()["mfa_enabled"] is False
    assert (await sign_in(client)).status_code == 200


async def test_the_totp_secret_is_encrypted_at_rest(client):
    h = bearer(await register(client))
    secret = await enrol_mfa(client, h)
    user = await User.find_one(User.email == "a@example.com")
    assert isinstance(user.mfa_secret_enc, bytes)
    assert secret.encode() not in user.mfa_secret_enc


async def test_mfa_is_recommended_once_an_account_reaches_another_record(client):
    """Advisory, not enforced: blocking sign-in for an account that already
    holds shared grants locks it out of the screen where MFA is enrolled."""
    owner = bearer(await register(client, "owner@example.com"))
    pid = await make_patient(client, owner)
    nurse = bearer(await register(client, "nurse@example.com"))
    await make_patient(client, nurse)  # their own record: one grant, no prompt

    assert (await client.get("/api/auth/me", headers=nurse)).json()["mfa_recommended"] is False
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "nurse@example.com", "role": "nurse"})
    assert (await client.get("/api/auth/me", headers=nurse)).json()["mfa_recommended"] is True


# --- ghosts and races --------------------------------------------------------

async def test_an_abandoned_session_does_not_haunt_the_list(client):
    """One signed-in browser once showed two devices, the second of them nine
    hours dead. The idle timeout only fires when a session is *used*, so an
    abandoned one is never reaped and sat in the list indefinitely — and a list
    nobody can trust is worse than none, since its whole job is answering "is
    one of these not me"."""
    first = bearer(await register(client))
    second = bearer(await sign_in(client, new_device=True))
    assert len((await client.get("/api/auth/sessions", headers=second)).json()) == 2

    # The first device is closed and walks away without saying so.
    sid = next(s["id"] for s in
               (await client.get("/api/auth/sessions", headers=second)).json()
               if not s["current"])
    await idle_out(sid)

    rows = (await client.get("/api/auth/sessions", headers=second)).json()
    assert len(rows) == 1, "an idle-timed-out session must not be listed as live"
    assert rows[0]["current"] is True
    # And it is actually closed, not merely hidden from this one view.
    assert (await Session.get(PydanticObjectId(sid))).revoked_at is not None
    assert (await client.get("/api/auth/me", headers=first)).status_code == 401


async def test_two_tabs_refreshing_at_once_is_not_treated_as_theft(client):
    """Both tabs of an open app call /refresh on load. The one that loses the
    race presents a token that went stale while its request was in flight —
    refusing it signs somebody out of their own browser for having two tabs."""
    h = bearer(await register(client))
    in_flight = client.cookies.get("ll_refresh")

    assert (await client.post("/api/auth/refresh")).status_code == 200  # tab A wins

    # Tab B arrives a moment later still holding the previous value.
    r = await client.post("/api/auth/refresh",
                          headers={"Cookie": f"ll_refresh={in_flight}"})
    assert r.status_code == 200, "a token stale by milliseconds is a race, not a theft"
    assert (await client.get("/api/auth/me", headers=h)).status_code == 200


async def test_the_same_token_much_later_is_still_treated_as_theft(client):
    """The grace window must not become a thirty-second hole. Once it closes,
    a rotated-away token means somebody kept a copy."""
    h = bearer(await register(client))
    stolen = client.cookies.get("ll_refresh")
    assert (await client.post("/api/auth/refresh")).status_code == 200

    user = await User.find_one(User.email == "a@example.com")
    session = await Session.find_one(Session.user_id == user.id)
    session.rotated_at = utcnow() - timedelta(minutes=5)
    await session.save()

    r = await client.post("/api/auth/refresh",
                          headers={"Cookie": f"ll_refresh={stolen}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Session ended"
    assert (await client.get("/api/auth/me", headers=h)).status_code == 401
