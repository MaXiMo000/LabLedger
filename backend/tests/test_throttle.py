"""Slowing down guesses at a six-digit secret.

The hole this closes was measured, not theorised: 25 consecutive attempts at
`/auth/mfa/disable` were accepted without a single refusal, while `/login`
correctly cut off at 10. Anyone holding a borrowed session could therefore
brute-force the code guarding *turning the second factor off* — a rotating
window offers three valid codes out of a million every thirty seconds, so a
modest request rate gets there in hours.

The per-IP limiter is disabled under test (`ENV=test`, or every test would
share one 127.0.0.1 bucket), so what these exercise is the per-account
throttle — which is the half that matters anyway, since the per-IP bucket is
distributed around trivially.
"""

import time
from datetime import timedelta

import pytest

from app.models.user import User, utcnow
from app.security import totp_at
from app.throttle import CODE_ATTEMPT_LIMIT

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
    setup = (await client.post("/api/auth/mfa/setup", headers=h)).json()
    r = await client.post("/api/auth/mfa/enable", headers=h,
                          json={"code": code_for(setup["secret"])})
    assert r.status_code == 200, r.text
    return setup["secret"]


async def wrong(client, h, path="/api/auth/mfa/disable"):
    return await client.post(path, headers=h, json={"code": "000000"})


# --- the hole ----------------------------------------------------------------

async def test_guessing_the_code_that_turns_mfa_off_is_cut_off(client):
    """The measured hole: unlimited guesses at the code protecting `disable`."""
    h = await register(client)
    await enrol(client, h)

    for i in range(CODE_ATTEMPT_LIMIT):
        assert (await wrong(client, h)).status_code == 401, f"attempt {i} should be a plain refusal"

    r = await wrong(client, h)
    assert r.status_code == 429
    assert "Try again in" in r.json()["detail"]


async def test_the_lockout_holds_even_for_the_correct_code(client):
    """A throttle you can step over with the right answer is not a throttle —
    and the attacker's whole plan is to eventually supply the right answer."""
    h = await register(client)
    secret = await enrol(client, h)

    for _ in range(CODE_ATTEMPT_LIMIT):
        await wrong(client, h)

    r = await client.post("/api/auth/mfa/disable", headers=h,
                          json={"code": code_for(secret)})
    assert r.status_code == 429

    # And MFA is still on: the lockout did not half-apply.
    assert (await client.get("/api/auth/me", headers=h)).json()["mfa_enabled"] is True


async def test_the_lockout_expires(client):
    h = await register(client)
    secret = await enrol(client, h)
    for _ in range(CODE_ATTEMPT_LIMIT):
        await wrong(client, h)
    assert (await wrong(client, h)).status_code == 429

    user = await User.find_one(User.email == "a@example.com")
    user.code_locked_until = utcnow() - timedelta(seconds=1)
    await user.save()

    r = await client.post("/api/auth/mfa/disable", headers=h,
                          json={"code": code_for(secret)})
    assert r.status_code == 200


async def test_a_correct_code_clears_the_count(client):
    """Four fumbled codes followed by a right one must not leave the account one
    mistake away from a lockout tomorrow."""
    h = await register(client)
    secret = await enrol(client, h)

    for _ in range(CODE_ATTEMPT_LIMIT - 1):
        await wrong(client, h)

    r = await client.post("/api/auth/mfa/recovery", headers=h,
                          json={"code": code_for(secret)})
    assert r.status_code == 200

    user = await User.find_one(User.email == "a@example.com")
    assert user.code_failures == 0
    assert user.code_locked_until is None


# --- it covers every door, not just the one that was measured ----------------

@pytest.mark.parametrize("path", [
    "/api/auth/mfa/disable",
    "/api/auth/mfa/recovery",
])
async def test_every_code_checking_route_is_throttled(client, path):
    """The bug was that limits were on the two obvious routes and nowhere else.
    Fixing only the route that was measured would repeat exactly that."""
    h = await register(client)
    await enrol(client, h)

    for _ in range(CODE_ATTEMPT_LIMIT):
        await wrong(client, h, path)
    assert (await wrong(client, h, path)).status_code == 429


async def test_enrolment_is_throttled_too(client):
    h = await register(client)
    await client.post("/api/auth/mfa/setup", headers=h)

    for _ in range(CODE_ATTEMPT_LIMIT):
        assert (await client.post("/api/auth/mfa/enable", headers=h,
                                  json={"code": "000000"})).status_code == 401
    assert (await client.post("/api/auth/mfa/enable", headers=h,
                              json={"code": "000000"})).status_code == 429


async def test_signing_in_shares_the_same_budget(client):
    """Sign-in is where the code is guessed without a session at all, so it must
    draw on the same per-account count rather than its own."""
    h = await register(client)
    await enrol(client, h)

    for _ in range(CODE_ATTEMPT_LIMIT):
        client.cookies.clear()
        r = await client.post("/api/auth/login", json={
            "email": "a@example.com", "password": PASSWORD, "code": "000000"})
        assert r.status_code == 401

    client.cookies.clear()
    r = await client.post("/api/auth/login", json={
        "email": "a@example.com", "password": PASSWORD, "code": "000000"})
    assert r.status_code == 429


# --- and it must not become a way to lock somebody out -----------------------

async def test_a_wrong_password_never_locks_the_account(client):
    """Deliberate. Locking on failed passwords hands anyone who knows a
    clinician's address a way to shut them out of a ward terminal, which is a
    patient safety problem wearing a security control's clothes. Code attempts
    are different: every route that checks one already needs a live session."""
    h = await register(client)
    await enrol(client, h)

    for _ in range(CODE_ATTEMPT_LIMIT * 3):
        client.cookies.clear()
        await client.post("/api/auth/login", json={
            "email": "a@example.com", "password": "wrong-password-xx"})

    user = await User.find_one(User.email == "a@example.com")
    assert user.code_locked_until is None
    assert (await client.get("/api/auth/me", headers=h)).status_code == 200
