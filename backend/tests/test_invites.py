"""Inviting somebody who does not have an account yet.

The feature exists because granting access requires an account to grant it to,
which made the real ward sequence impossible: a nurse had to sign themselves up
before an owner could give them anything.

The security claim it has to earn is narrower than it looks. Registration does
not verify email addresses, so "the invited address signed up, give them the
access" is an account takeover waiting to happen: anyone who learns that an
address was invited can register it first. Accepting therefore needs the link
*and* the address, and the tests below assert that neither half is sufficient.
"""

import pytest

from app.models.invite import Invite
from app.models.patient import Access
from app.models.user import User, utcnow

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery"


async def register(client, email):
    client.cookies.clear()  # each account is its own browser
    r = await client.post("/api/auth/register", json={
        "email": email, "name": email.split("@")[0], "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_patient(client, h, name="Subject"):
    return (await client.post("/api/patients", headers=h,
            json={"display_name": name})).json()["id"]


async def invite(client, h, pid, email, role="nurse"):
    return await client.post(f"/api/patients/{pid}/invites", headers=h,
                             json={"email": email, "role": role})


def token_of(link):
    return link.rsplit("/", 1)[-1]


# --- the flow ----------------------------------------------------------------

async def test_owner_can_invite_an_address_with_no_account(client):
    """The gap this closes: previously this was a 404, and the only way in was
    for the nurse to sign herself up first."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)

    r = await invite(client, owner, pid, "nurse@example.com")
    assert r.status_code == 201, r.text
    assert r.json()["link"].endswith(token_of(r.json()["link"]))
    assert r.json()["role"] == "nurse"


async def test_accepting_an_invitation_grants_the_access(client):
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner, "Shared Patient")
    link = (await invite(client, owner, pid, "nurse@example.com")).json()["link"]

    nurse = await register(client, "nurse@example.com")
    assert (await client.get("/api/patients", headers=nurse)).json() == []

    r = await client.post("/api/auth/invites/claim", headers=nurse,
                          json={"token": token_of(link)})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Shared Patient"
    assert r.json()["role"] == "nurse"

    # And the record is now actually reachable, at nurse level.
    assert (await client.get(f"/api/patients/{pid}", headers=nurse)).status_code == 200
    assert len((await client.get("/api/patients", headers=nurse)).json()) == 1


async def test_the_granted_role_is_the_invited_role(client):
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    link = (await invite(client, owner, pid, "v@example.com", role="viewer")).json()["link"]

    viewer = await register(client, "v@example.com")
    await client.post("/api/auth/invites/claim", headers=viewer,
                      json={"token": token_of(link)})

    # A viewer reads and does nothing else — the invitation cannot smuggle in
    # more than the role it named.
    assert (await client.get(f"/api/patients/{pid}", headers=viewer)).status_code == 200
    assert (await client.get(f"/api/patients/{pid}/access", headers=viewer)).status_code == 404


# --- the attack it exists to stop --------------------------------------------

async def test_registering_the_invited_address_is_not_enough(client):
    """Registration does not verify email. Without the token, knowing that an
    address was invited would be enough to take the access."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    await invite(client, owner, pid, "nurse@example.com")

    impostor = await register(client, "nurse@example.com")
    assert (await client.get(f"/api/patients/{pid}", headers=impostor)).status_code == 404
    assert (await client.get("/api/patients", headers=impostor)).json() == []


async def test_the_link_alone_is_not_enough(client):
    """Holding a forwarded link does not admit an account it was not sent to."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    link = (await invite(client, owner, pid, "nurse@example.com")).json()["link"]

    someone_else = await register(client, "stranger@example.com")
    r = await client.post("/api/auth/invites/claim", headers=someone_else,
                          json={"token": token_of(link)})
    assert r.status_code == 403
    assert "nurse@example.com" in r.json()["detail"]
    assert (await client.get(f"/api/patients/{pid}", headers=someone_else)).status_code == 404


async def test_a_forged_token_is_404(client):
    await register(client, "owner@example.com")
    nurse = await register(client, "nurse@example.com")
    r = await client.post("/api/auth/invites/claim", headers=nurse,
                          json={"token": "not-a-real-invitation-token"})
    assert r.status_code == 404


async def test_an_invitation_cannot_be_used_twice(client):
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    link = (await invite(client, owner, pid, "nurse@example.com")).json()["link"]

    nurse = await register(client, "nurse@example.com")
    assert (await client.post("/api/auth/invites/claim", headers=nurse,
                              json={"token": token_of(link)})).status_code == 200
    # Spent, and indistinguishable from forged.
    assert (await client.post("/api/auth/invites/claim", headers=nurse,
                              json={"token": token_of(link)})).status_code == 404


async def test_an_expired_invitation_is_refused(client):
    """A forgotten invitation must stop being a way in."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    link = (await invite(client, owner, pid, "nurse@example.com")).json()["link"]

    row = await Invite.find_one(Invite.email == "nurse@example.com")
    row.expires_at = utcnow()
    await row.save()

    nurse = await register(client, "nurse@example.com")
    assert (await client.post("/api/auth/invites/claim", headers=nurse,
                              json={"token": token_of(link)})).status_code == 404


async def test_the_token_is_not_stored_in_the_clear(client):
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    link = (await invite(client, owner, pid, "nurse@example.com")).json()["link"]

    row = await Invite.find_one(Invite.email == "nurse@example.com")
    assert row.token_hash != token_of(link)
    assert token_of(link) not in row.token_hash


async def test_the_link_is_never_returned_again(client):
    """Re-readable links would be standing keys sitting beside the door."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    await invite(client, owner, pid, "nurse@example.com")

    rows = (await client.get(f"/api/patients/{pid}/invites", headers=owner)).json()
    assert len(rows) == 1
    assert rows[0]["link"] is None


# --- managing them -----------------------------------------------------------

async def test_only_an_owner_can_invite(client):
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    clin = await register(client, "clin@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "clin@example.com", "role": "clinician"})

    assert (await invite(client, clin, pid, "x@example.com")).status_code == 404
    assert (await client.get(f"/api/patients/{pid}/invites", headers=clin)).status_code == 404


async def test_inviting_an_existing_account_says_to_grant_instead(client):
    """Two ways to do one thing is how one of them ends up wrong."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    await register(client, "has@example.com")

    r = await invite(client, owner, pid, "has@example.com")
    assert r.status_code == 409


async def test_re_inviting_replaces_the_outstanding_link(client):
    """Two live links to one record is one more than anybody can track."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    first = (await invite(client, owner, pid, "nurse@example.com")).json()["link"]
    second = (await invite(client, owner, pid, "nurse@example.com", role="clinician")).json()["link"]

    assert len((await client.get(f"/api/patients/{pid}/invites", headers=owner)).json()) == 1

    nurse = await register(client, "nurse@example.com")
    assert (await client.post("/api/auth/invites/claim", headers=nurse,
                              json={"token": token_of(first)})).status_code == 404
    r = await client.post("/api/auth/invites/claim", headers=nurse,
                          json={"token": token_of(second)})
    assert r.status_code == 200
    assert r.json()["role"] == "clinician"


async def test_a_cancelled_invitation_cannot_be_accepted(client):
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    link = (await invite(client, owner, pid, "nurse@example.com")).json()["link"]
    row_id = (await client.get(f"/api/patients/{pid}/invites", headers=owner)).json()[0]["id"]

    assert (await client.delete(f"/api/patients/{pid}/invites/{row_id}",
                                headers=owner)).status_code == 204

    nurse = await register(client, "nurse@example.com")
    assert (await client.post("/api/auth/invites/claim", headers=nurse,
                              json={"token": token_of(link)})).status_code == 404


async def test_an_invitation_on_another_record_is_404(client):
    """Cancelling must not confirm that somebody else's invitation exists."""
    a = await register(client, "a@example.com")
    pid_a = await make_patient(client, a)
    await invite(client, a, pid_a, "nurse@example.com")
    row_id = (await client.get(f"/api/patients/{pid_a}/invites", headers=a)).json()[0]["id"]

    b = await register(client, "b@example.com")
    pid_b = await make_patient(client, b)
    assert (await client.delete(f"/api/patients/{pid_b}/invites/{row_id}",
                                headers=b)).status_code == 404


async def test_accepting_is_recorded_against_the_patient(client):
    """Somebody gaining access to a record is exactly what the log is for."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    link = (await invite(client, owner, pid, "nurse@example.com")).json()["link"]

    nurse = await register(client, "nurse@example.com")
    await client.post("/api/auth/invites/claim", headers=nurse,
                      json={"token": token_of(link)})

    trail = (await client.get(f"/api/audit/patient/{pid}", headers=owner)).json()
    assert any(e["actor_email"] == "nurse@example.com" for e in trail)

    # And the grant records who issued it, not who accepted it.
    grant = await Access.find_one(Access.role == "nurse")
    owner_user = await User.find_one(User.email == "owner@example.com")
    assert grant.granted_by == owner_user.id
