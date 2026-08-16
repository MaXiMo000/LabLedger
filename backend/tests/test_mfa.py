"""Enrolment, recovery codes, and the point where the prompt becomes a wall.

Three claims to earn. Scanning a QR must enrol the same secret the key would
have; a lost phone must not mean a lost account; and requiring a second factor
must never lock somebody out of the screen where they would turn it on.
"""

import time
from datetime import timedelta

import pytest

from app.config import settings
from app.models.patient import Patient
from app.models.user import User, utcnow
from app.security import (
    hash_recovery_code,
    new_recovery_codes,
    take_recovery_code,
    totp_at,
)

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery"


async def register(client, email="a@example.com"):
    client.cookies.clear()
    r = await client.post("/api/auth/register", json={
        "email": email, "name": email.split("@")[0], "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def code_for(secret):
    return totp_at(secret, int(time.time()) // 30)


async def enrol(client, h):
    """Run the whole enrolment and return (secret, recovery_codes)."""
    setup = (await client.post("/api/auth/mfa/setup", headers=h)).json()
    r = await client.post("/api/auth/mfa/enable", headers=h,
                          json={"code": code_for(setup["secret"])})
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()["codes"]


# --- the QR ------------------------------------------------------------------

async def test_setup_offers_a_qr_and_the_key_for_the_same_secret(client):
    """The QR is a convenience, not a second enrolment path: a desktop
    authenticator has no camera, and both must land on one secret."""
    h = await register(client)
    s = (await client.post("/api/auth/mfa/setup", headers=h)).json()

    assert s["qr_svg"].startswith("<svg")
    assert s["secret"] in s["uri"]
    # The QR encodes the URI, which carries the same secret the key shows.
    assert s["uri"].startswith("otpauth://totp/")
    # Drawn in currentColor so it takes the page's ink rather than pasting a
    # black rectangle onto warm paper.
    assert "currentColor" in s["qr_svg"]

    r = await client.post("/api/auth/mfa/enable", headers=h,
                          json={"code": code_for(s["secret"])})
    assert r.status_code == 200


# --- recovery codes ----------------------------------------------------------

async def test_enabling_issues_recovery_codes_once(client):
    h = await register(client)
    _, codes = await enrol(client, h)
    assert len(codes) == 10
    assert len(set(codes)) == 10

    # Never returned again — /me reports only how many are left.
    me = (await client.get("/api/auth/me", headers=h)).json()
    assert me["recovery_codes_left"] == 10
    assert "codes" not in me


async def test_a_recovery_code_signs_you_in_and_is_then_spent(client):
    """The lost-phone path. Without it, MFA turns a mislaid device into an
    account only a database edit can reach."""
    h = await register(client)
    _, codes = await enrol(client, h)

    async def login(code):
        client.cookies.clear()
        return await client.post("/api/auth/login", json={
            "email": "a@example.com", "password": PASSWORD, "code": code})

    assert (await login(codes[0])).status_code == 200
    # Good exactly once.
    assert (await login(codes[0])).status_code == 401
    assert (await login(codes[1])).status_code == 200

    me = (await client.get("/api/auth/me", headers=h)).json()
    assert me["recovery_codes_left"] == 8


async def test_recovery_codes_are_hashed_at_rest(client):
    h = await register(client)
    _, codes = await enrol(client, h)
    user = await User.find_one(User.email == "a@example.com")
    assert codes[0] not in user.recovery_hashes
    assert hash_recovery_code(codes[0]) in user.recovery_hashes


async def test_recovery_codes_are_accepted_however_they_are_typed(client):
    """They get read off a printout, so case and spacing are not the user's
    problem to get right."""
    h = await register(client)
    _, codes = await enrol(client, h)
    client.cookies.clear()
    r = await client.post("/api/auth/login", json={
        "email": "a@example.com", "password": PASSWORD,
        "code": codes[0].lower()})
    assert r.status_code == 200


async def test_reissuing_replaces_the_old_codes(client):
    h = await register(client)
    secret, old = await enrol(client, h)

    r = await client.post("/api/auth/mfa/recovery", headers=h,
                          json={"code": code_for(secret)})
    assert r.status_code == 200
    new = r.json()["codes"]
    assert set(new).isdisjoint(old)

    client.cookies.clear()
    assert (await client.post("/api/auth/login", json={
        "email": "a@example.com", "password": PASSWORD, "code": old[0]})).status_code == 401
    assert (await client.post("/api/auth/login", json={
        "email": "a@example.com", "password": PASSWORD, "code": new[0]})).status_code == 200


async def test_reissuing_needs_a_current_code(client):
    """Otherwise a borrowed session mints itself a permanent way back in."""
    h = await register(client)
    await enrol(client, h)
    r = await client.post("/api/auth/mfa/recovery", headers=h, json={"code": "000000"})
    assert r.status_code == 401


async def test_turning_mfa_off_destroys_the_recovery_codes(client):
    h = await register(client)
    secret, codes = await enrol(client, h)
    await client.post("/api/auth/mfa/disable", headers=h, json={"code": code_for(secret)})

    user = await User.find_one(User.email == "a@example.com")
    assert user.recovery_hashes == []
    # And they are not a back door into an account that no longer has MFA.
    client.cookies.clear()
    r = await client.post("/api/auth/login", json={
        "email": "a@example.com", "password": PASSWORD, "code": codes[0]})
    assert r.status_code == 200  # password alone is enough again
    assert (await client.get("/api/auth/me", headers=h)).json()["recovery_codes_left"] == 0


async def test_take_recovery_code_consumes_exactly_one():
    codes, hashes = new_recovery_codes(4)
    left = take_recovery_code(codes[2], hashes)
    assert left is not None
    assert len(left) == 3
    assert hash_recovery_code(codes[2]) not in left
    assert take_recovery_code("XXXX-XXXX", hashes) is None


# --- enforcement -------------------------------------------------------------

async def share(client, owner_h, pid, email):
    await client.post(f"/api/patients/{pid}/access", headers=owner_h,
                      json={"email": email, "role": "clinician"})


async def setup_shared(client):
    owner = await register(client, "owner@example.com")
    pid = (await client.post("/api/patients", headers=owner,
                             json={"display_name": "Shared"})).json()["id"]
    other = await register(client, "clin@example.com")
    await share(client, owner, pid, "clin@example.com")
    return owner, other, pid


async def test_the_clock_starts_at_first_access_not_at_the_grant(client):
    """Switching the policy on must not lock out every existing grant at once."""
    _, other, pid = await setup_shared(client)
    user = await User.find_one(User.email == "clin@example.com")
    assert user.mfa_required_since is None  # granted, but never opened

    assert (await client.get(f"/api/patients/{pid}", headers=other)).status_code == 200
    user = await User.find_one(User.email == "clin@example.com")
    assert user.mfa_required_since is not None


async def test_inside_the_grace_period_access_continues(client):
    _, other, pid = await setup_shared(client)
    for _ in range(3):
        assert (await client.get(f"/api/patients/{pid}", headers=other)).status_code == 200


async def test_past_the_grace_period_a_shared_record_is_refused(client):
    _, other, pid = await setup_shared(client)
    await client.get(f"/api/patients/{pid}", headers=other)  # starts the clock

    user = await User.find_one(User.email == "clin@example.com")
    user.mfa_required_since = utcnow() - timedelta(days=settings.mfa_grace_days + 1)
    await user.save()

    r = await client.get(f"/api/patients/{pid}", headers=other)
    # 403, not 404: they have a live grant and have been reading this record.
    # Hiding it now would read as data loss and send them hunting for a bug.
    assert r.status_code == 403
    assert "Security" in r.json()["detail"]


async def test_being_locked_out_never_blocks_the_screen_that_fixes_it(client):
    """The trap this whole design exists to avoid."""
    _, other, pid = await setup_shared(client)
    await client.get(f"/api/patients/{pid}", headers=other)
    user = await User.find_one(User.email == "clin@example.com")
    user.mfa_required_since = utcnow() - timedelta(days=settings.mfa_grace_days + 1)
    await user.save()

    # Sign-in, the account, the session list and enrolment all still work.
    client.cookies.clear()
    assert (await client.post("/api/auth/login", json={
        "email": "clin@example.com", "password": PASSWORD})).status_code == 200
    assert (await client.get("/api/auth/me", headers=other)).status_code == 200
    assert (await client.get("/api/auth/sessions", headers=other)).status_code == 200
    assert (await client.post("/api/auth/mfa/setup", headers=other)).status_code == 200


async def test_enrolling_restores_access_immediately(client):
    _, other, pid = await setup_shared(client)
    await client.get(f"/api/patients/{pid}", headers=other)
    user = await User.find_one(User.email == "clin@example.com")
    user.mfa_required_since = utcnow() - timedelta(days=settings.mfa_grace_days + 1)
    await user.save()
    assert (await client.get(f"/api/patients/{pid}", headers=other)).status_code == 403

    await enrol(client, other)
    assert (await client.get(f"/api/patients/{pid}", headers=other)).status_code == 200


async def test_your_own_record_is_never_gated(client):
    """The requirement is about other people's bodies. Being shut out of your
    own results by a policy about somebody else's would be indefensible."""
    h = await register(client)
    pid = (await client.post("/api/patients", headers=h,
                             json={"display_name": "Mine"})).json()["id"]
    user = await User.find_one(User.email == "a@example.com")
    user.mfa_required_since = utcnow() - timedelta(days=settings.mfa_grace_days + 10)
    await user.save()

    assert (await client.get(f"/api/patients/{pid}", headers=h)).status_code == 200
    assert (await Patient.get(pid)).created_by == user.id


async def test_the_deadline_is_reported_before_it_bites(client):
    """A wall nobody saw coming is an outage."""
    _, other, pid = await setup_shared(client)
    await client.get(f"/api/patients/{pid}", headers=other)

    me = (await client.get("/api/auth/me", headers=other)).json()
    assert me["mfa_recommended"] is True
    assert me["mfa_deadline"] is not None

    await enrol(client, other)
    me = (await client.get("/api/auth/me", headers=other)).json()
    assert me["mfa_deadline"] is None
    assert me["mfa_recommended"] is False


# --- a lost authenticator is not a lost account ------------------------------

async def test_a_recovery_code_turns_mfa_off(client):
    """The reason to hold one.

    Sign-in always took a recovery code, but `disable` took only TOTP — so
    somebody who had lost their phone could get *in* and never get *out*. They
    spent one code per sign-in until none were left, and then the account was
    unreachable: the exact lockout recovery codes exist to prevent, arrived at
    by using them as intended.
    """
    h = await register(client)
    _, codes = await enrol(client, h)

    r = await client.post("/api/auth/mfa/disable", headers=h, json={"code": codes[0]})

    assert r.status_code == 200, r.text
    assert r.json()["mfa_enabled"] is False
    user = await User.find_one(User.email == "a@example.com")
    assert user.recovery_hashes == []
    assert user.mfa_secret_enc is None


async def test_a_recovery_code_mints_a_fresh_set(client):
    """Same gap on the reissue path: without this, running low on codes with no
    authenticator to hand had no remedy."""
    h = await register(client)
    _, codes = await enrol(client, h)

    r = await client.post("/api/auth/mfa/recovery", headers=h, json={"code": codes[0]})

    assert r.status_code == 200, r.text
    fresh = r.json()["codes"]
    assert fresh and set(fresh).isdisjoint(codes)
    # The old set stops working the moment a new one exists.
    user = await User.find_one(User.email == "a@example.com")
    assert all(hash_recovery_code(c) not in user.recovery_hashes for c in codes)


async def test_a_spent_recovery_code_does_not_work_twice(client):
    h = await register(client)
    _, codes = await enrol(client, h)

    # Spend it at sign-in, then try the same string against disable.
    await client.post("/api/auth/login", json={
        "email": "a@example.com", "password": PASSWORD, "code": codes[0]})
    r = await client.post("/api/auth/mfa/disable", headers=h, json={"code": codes[0]})

    assert r.status_code == 401


async def test_a_wrong_code_still_fails_both_ways(client):
    h = await register(client)
    await enrol(client, h)

    r = await client.post("/api/auth/mfa/disable", headers=h, json={"code": "000000"})

    assert r.status_code == 401
    assert (await User.find_one(User.email == "a@example.com")).mfa_enabled is True


# --- the admin last resort ---------------------------------------------------

async def make_admin(email="admin@example.com"):
    user = await User.find_one(User.email == email)
    user.role = "admin"
    await user.save()
    return user


async def test_an_admin_clears_an_enrolment_and_ends_their_sessions(client):
    """For somebody who has lost the authenticator *and* every code."""
    victim = await register(client, "victim@example.com")
    await enrol(client, victim)
    target = await User.find_one(User.email == "victim@example.com")

    admin_h = await register(client, "admin@example.com")
    await make_admin()

    r = await client.post(f"/api/auth/admin/users/{target.id}/mfa/reset", headers=admin_h)

    assert r.status_code == 204, r.text
    target = await User.find_one(User.email == "victim@example.com")
    assert target.mfa_enabled is False
    assert target.recovery_hashes == []
    # Their sessions end: an enrolment is cleared because control of a factor
    # is uncertain, the same reason a password reset ends sessions.
    assert (await client.get("/api/auth/me", headers=victim)).status_code == 401


async def test_a_plain_account_cannot_reset_anyone(client):
    victim = await register(client, "victim@example.com")
    await enrol(client, victim)
    target = await User.find_one(User.email == "victim@example.com")

    other = await register(client, "nosy@example.com")
    r = await client.post(f"/api/auth/admin/users/{target.id}/mfa/reset", headers=other)

    assert r.status_code == 403
    assert (await User.find_one(User.email == "victim@example.com")).mfa_enabled is True


async def test_an_admin_cannot_reset_their_own(client):
    """Otherwise a borrowed admin session strips its own second factor without
    the code /mfa/disable demands — turning the route meant to help others into
    a way to weaken the account holding the privilege."""
    h = await register(client, "admin@example.com")
    await enrol(client, h)
    admin = await make_admin()

    r = await client.post(f"/api/auth/admin/users/{admin.id}/mfa/reset", headers=h)

    assert r.status_code == 409
    assert (await User.find_one(User.email == "admin@example.com")).mfa_enabled is True


async def test_the_grace_clock_survives_an_admin_reset(client):
    """Clearing an enrolment must not hand out a fresh week of reaching shared
    records without a second factor. Enrolment always works, so nothing is
    blocked that they cannot themselves unblock."""
    victim = await register(client, "victim@example.com")
    await enrol(client, victim)
    target = await User.find_one(User.email == "victim@example.com")
    target.mfa_required_since = utcnow() - timedelta(days=99)
    await target.save()

    admin_h = await register(client, "admin@example.com")
    await make_admin()
    await client.post(f"/api/auth/admin/users/{target.id}/mfa/reset", headers=admin_h)

    target = await User.find_one(User.email == "victim@example.com")
    assert target.mfa_required_since is not None
