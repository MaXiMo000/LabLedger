"""Getting back into an account you have locked yourself out of.

The one flow that has to work for somebody who cannot sign in, which means
every guard here is about a stranger holding a link rather than a user holding
a session. Mail is stubbed throughout: the tests assert what was *asked* to be
sent, never that an email arrived.
"""

from datetime import timedelta

import pytest

from app.models.session import Session
from app.models.user import User, utcnow
from app.security import hash_refresh_token, verify_password

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery"
NEW = "an-entirely-new-secret"


@pytest.fixture
def sent(monkeypatch):
    """Capture reset emails instead of sending them."""
    box = []

    async def _stub(to_email, reset_url):
        box.append((to_email, reset_url))
        return True

    monkeypatch.setattr("app.mailer.send_password_reset", _stub)
    return box


async def register(client, email="a@example.com"):
    client.cookies.clear()
    r = await client.post("/api/auth/register", json={
        "email": email, "name": "A", "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def token_from(url):
    return url.rsplit("/", 1)[-1]


# --- asking ------------------------------------------------------------------

async def test_a_reset_link_is_sent(client, sent):
    await register(client)
    r = await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    assert r.status_code == 204
    assert len(sent) == 1
    assert sent[0][0] == "a@example.com"
    assert "/reset/" in sent[0][1]


async def test_an_unknown_address_answers_identically(client, sent):
    """Answering differently would turn this into a way to enumerate who holds
    a record here — which on a clinical system leaks that a named person is a
    patient somewhere."""
    await register(client)
    known = await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    unknown = await client.post("/api/auth/password/reset", json={"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 204
    assert known.text == unknown.text
    assert len(sent) == 1  # only the real one actually sent


async def test_a_google_only_account_gets_no_link(client, sent):
    """There is no password to reset, and mailing a link that sets one would
    let anybody with mailbox access add a second way in past Google."""
    await register(client)
    user = await User.find_one(User.email == "a@example.com")
    user.password_hash = None
    await user.save()

    r = await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    assert r.status_code == 204
    assert sent == []


async def test_the_token_is_hashed_at_rest(client, sent):
    await register(client)
    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    token = token_from(sent[0][1])

    user = await User.find_one(User.email == "a@example.com")
    assert user.reset_token_hash == hash_refresh_token(token)
    assert token not in (user.reset_token_hash or "")


async def test_asking_again_replaces_the_previous_link(client, sent):
    """A link left in a mailbox stops working the moment a newer one exists."""
    await register(client)
    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    first, second = token_from(sent[0][1]), token_from(sent[1][1])

    assert (await client.post("/api/auth/password/reset/confirm", json={
        "token": first, "new_password": NEW})).status_code == 400
    assert (await client.post("/api/auth/password/reset/confirm", json={
        "token": second, "new_password": NEW})).status_code == 204


# --- using -------------------------------------------------------------------

async def test_the_link_sets_the_password(client, sent):
    await register(client)
    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})

    r = await client.post("/api/auth/password/reset/confirm", json={
        "token": token_from(sent[0][1]), "new_password": NEW})
    assert r.status_code == 204

    user = await User.find_one(User.email == "a@example.com")
    assert verify_password(NEW, user.password_hash)
    assert user.reset_token_hash is None

    client.cookies.clear()
    assert (await client.post("/api/auth/login", json={
        "email": "a@example.com", "password": NEW})).status_code == 200
    assert (await client.post("/api/auth/login", json={
        "email": "a@example.com", "password": PASSWORD})).status_code == 401


async def test_using_it_ends_every_session(client, sent):
    """Including whoever holds the account right now. A reset is what somebody
    does when they have lost control of it — leaving the sessions alive hands
    the new password to a stranger and changes nothing for the real owner."""
    h = await register(client)
    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    await client.post("/api/auth/password/reset/confirm", json={
        "token": token_from(sent[0][1]), "new_password": NEW})

    assert (await client.get("/api/auth/me", headers=h)).status_code == 401
    user = await User.find_one(User.email == "a@example.com")
    live = await Session.find(Session.user_id == user.id,
                              Session.revoked_at == None).count()  # noqa: E711
    assert live == 0


async def test_a_link_is_good_once(client, sent):
    await register(client)
    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    token = token_from(sent[0][1])

    assert (await client.post("/api/auth/password/reset/confirm", json={
        "token": token, "new_password": NEW})).status_code == 204
    assert (await client.post("/api/auth/password/reset/confirm", json={
        "token": token, "new_password": "yet-another-secret-1"})).status_code == 400


async def test_an_expired_link_is_refused(client, sent):
    await register(client)
    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})

    user = await User.find_one(User.email == "a@example.com")
    user.reset_expires_at = utcnow() - timedelta(minutes=1)
    await user.save()

    r = await client.post("/api/auth/password/reset/confirm", json={
        "token": token_from(sent[0][1]), "new_password": NEW})
    assert r.status_code == 400


async def test_expired_spent_and_forged_answer_alike(client, sent):
    """A distinguishable "expired" confirms the address had a live link, which
    is half an enumeration."""
    await register(client)
    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    token = token_from(sent[0][1])
    await client.post("/api/auth/password/reset/confirm", json={
        "token": token, "new_password": NEW})

    spent = await client.post("/api/auth/password/reset/confirm", json={
        "token": token, "new_password": "another-secret-here"})
    forged = await client.post("/api/auth/password/reset/confirm", json={
        "token": "not-a-real-token", "new_password": "another-secret-here"})
    assert spent.status_code == forged.status_code == 400
    assert spent.json()["detail"] == forged.json()["detail"]


async def test_a_short_password_is_refused(client, sent):
    await register(client)
    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    r = await client.post("/api/auth/password/reset/confirm", json={
        "token": token_from(sent[0][1]), "new_password": "short"})
    assert r.status_code == 422


async def test_the_second_factor_survives_a_reset(client, sent):
    """A reset proves control of the mailbox, which is precisely the thing MFA
    exists to not be sufficient on its own."""
    await register(client)
    user = await User.find_one(User.email == "a@example.com")
    user.mfa_enabled = True
    await user.save()

    await client.post("/api/auth/password/reset", json={"email": "a@example.com"})
    await client.post("/api/auth/password/reset/confirm", json={
        "token": token_from(sent[0][1]), "new_password": NEW})

    user = await User.find_one(User.email == "a@example.com")
    assert user.mfa_enabled is True
    client.cookies.clear()
    r = await client.post("/api/auth/login", json={
        "email": "a@example.com", "password": NEW})
    assert r.status_code == 401  # still asks for the code
