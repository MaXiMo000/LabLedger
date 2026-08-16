from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import settings
from app.models.session import ROTATION_GRACE, Session
from app.models.user import User
from app.routers.auth import REFRESH_COOKIE
from app.security import ALGORITHM, AUDIENCE, decrypt_str, hash_refresh_token

pytestmark = pytest.mark.asyncio


async def test_register_returns_access_token_and_refresh_cookie(client, creds):
    r = await client.post("/api/auth/register", json=creds)
    assert r.status_code == 201, r.text
    assert r.json()["access_token"]
    cookie = r.cookies.get(REFRESH_COOKIE)
    assert cookie, "refresh cookie must be set"
    # The refresh token is stored hashed on the device's session, never in
    # plaintext. Asserted as an equality against the expected hash rather than
    # `!= cookie`, which would also pass if the field held something unrelated.
    user = await User.find_one(User.email == creds["email"])
    session = await Session.find_one(Session.user_id == user.id)
    assert session.refresh_hash == hash_refresh_token(cookie)


async def test_duplicate_email_rejected(client, creds):
    await client.post("/api/auth/register", json=creds)
    r = await client.post("/api/auth/register", json=creds)
    assert r.status_code == 409


async def test_short_password_rejected(client, creds):
    r = await client.post("/api/auth/register", json={**creds, "password": "short"})
    assert r.status_code == 422


async def test_login_wrong_password_is_401(client, creds):
    await client.post("/api/auth/register", json=creds)
    r = await client.post(
        "/api/auth/login", json={"email": creds["email"], "password": "wrong-password-x"}
    )
    assert r.status_code == 401


async def test_login_unknown_email_gives_same_error_as_wrong_password(client, creds):
    r = await client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "whatever-1234"}
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"  # no user enumeration


async def test_me_requires_token(client):
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_me_with_token(client, creds):
    token = (await client.post("/api/auth/register", json=creds)).json()["access_token"]
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == creds["email"]
    assert r.json()["has_password"] is True


async def test_expired_token_is_401_not_403(client, creds):
    """The whole point of short access tokens: expiry must tell the client to
    refresh (401), not to log out (403). Quiz-App conflates these."""
    reg = await client.post("/api/auth/register", json=creds)
    user = await User.find_one(User.email == creds["email"])
    past = datetime.now(UTC) - timedelta(hours=1)
    expired = jwt.encode(
        {"sub": str(user.id), "role": "user", "aud": AUDIENCE, "iat": past, "exp": past},
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert reg.status_code == 201


async def test_token_signed_with_another_secret_is_403(client, creds):
    await client.post("/api/auth/register", json=creds)
    forged = jwt.encode(
        {"sub": "0" * 24, "role": "admin", "aud": AUDIENCE,
         "exp": datetime.now(UTC) + timedelta(hours=1)},
        "some-other-apps-secret",
        algorithm=ALGORITHM,
    )
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 403


async def test_token_without_labledger_audience_is_rejected(client, creds):
    """A token minted by another app on the same secret must not work here."""
    await client.post("/api/auth/register", json=creds)
    user = await User.find_one(User.email == creds["email"])
    cross_app = jwt.encode(
        {"sub": str(user.id), "role": "user", "aud": "quiz-app",
         "exp": datetime.now(UTC) + timedelta(hours=1)},
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {cross_app}"})
    assert r.status_code == 403


async def test_refresh_rotates_and_old_token_stops_working(client, creds):
    await client.post("/api/auth/register", json=creds)
    first = client.cookies.get(REFRESH_COOKIE)

    r = await client.post("/api/auth/refresh")
    assert r.status_code == 200
    second = r.cookies.get(REFRESH_COOKIE)
    assert second and second != first, "refresh token must rotate"

    # Replaying the old cookie must fail — this is how stolen-token reuse
    # surfaces. Aged past ROTATION_GRACE first: inside that window a stale
    # token is two tabs racing on load rather than a theft, and refusing it
    # signed people out of their own browser for having two tabs open.
    user = await User.find_one(User.email == creds["email"])
    session = await Session.find_one(Session.user_id == user.id)
    session.rotated_at = session.rotated_at - ROTATION_GRACE - timedelta(seconds=1)
    await session.save()

    client.cookies.clear()
    assert (await client.post("/api/auth/refresh",
                              headers={"Cookie": f"{REFRESH_COOKIE}={first}"})
            ).status_code == 401


async def test_logout_invalidates_refresh(client, creds):
    await client.post("/api/auth/register", json=creds)
    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_dob_is_encrypted_at_rest(client, creds):
    token = (await client.post("/api/auth/register", json=creds)).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    r = await client.patch(
        "/api/auth/me", json={"dob": "1996-04-12", "sex_at_birth": "M"}, headers=auth
    )
    assert r.status_code == 200
    assert r.json()["dob"] == "1996-04-12"  # decrypts correctly on read

    user = await User.find_one(User.email == creds["email"])
    assert isinstance(user.dob_enc, bytes)
    assert b"1996" not in user.dob_enc  # ciphertext, not plaintext
    assert decrypt_str(user.dob_enc) == "1996-04-12"


async def test_future_dob_rejected(client, creds):
    token = (await client.post("/api/auth/register", json=creds)).json()["access_token"]
    r = await client.patch(
        "/api/auth/me", json={"dob": "2099-01-01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


async def test_health(client):
    assert (await client.get("/api/health")).json()["status"] == "ok"
