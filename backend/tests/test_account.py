"""Changing a password, handing a record over, spotting a stolen cookie, and leaving.

Four gaps in what already shipped, and each one is the kind that only shows up
when somebody tries to do an ordinary thing: rotate a password after a scare,
hand a patient to a colleague, or close an account.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from app.models.patient import Access, Patient
from app.models.session import Session
from app.models.user import User
from app.worker import process_document

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "a-completely-different-one"
PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()


async def register(client, email="a@example.com"):
    client.cookies.clear()
    r = await client.post("/api/auth/register", json={
        "email": email, "name": email.split("@")[0], "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def login(client, email="a@example.com", password=PASSWORD):
    client.cookies.clear()
    return await client.post("/api/auth/login",
                             json={"email": email, "password": password})


async def make_patient(client, h, name="Subject"):
    return (await client.post("/api/patients", headers=h,
            json={"display_name": name})).json()["id"]


# --- password ----------------------------------------------------------------

async def test_a_password_can_finally_be_changed(client):
    """There was no endpoint at all, which made rotating after a scare
    impossible and left "sign out everywhere" as only half an answer."""
    h = await register(client)
    r = await client.post("/api/auth/password", headers=h, json={
        "current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert r.status_code == 204, r.text

    assert (await login(client, password=PASSWORD)).status_code == 401
    assert (await login(client, password=NEW_PASSWORD)).status_code == 200


async def test_changing_it_needs_the_old_one(client):
    """A borrowed session is exactly what this defends against: without the
    check, an unlocked screen is enough to take the account permanently."""
    h = await register(client)
    r = await client.post("/api/auth/password", headers=h, json={
        "current_password": "not-the-password", "new_password": NEW_PASSWORD})
    assert r.status_code == 401
    assert (await login(client, password=PASSWORD)).status_code == 200


async def test_a_google_account_can_set_its_first_password(client):
    h = await register(client)
    user = await User.find_one(User.email == "a@example.com")
    user.password_hash = None  # as a Google-only account arrives
    await user.save()

    r = await client.post("/api/auth/password", headers=h,
                          json={"new_password": NEW_PASSWORD})
    assert r.status_code == 204
    assert (await login(client, password=NEW_PASSWORD)).status_code == 200


async def test_changing_it_signs_other_devices_out_but_not_this_one(client):
    """Somebody changes a password because they think it is known. Leaving the
    other copies signed in answers the fear without addressing it."""
    first = await register(client)                      # device A, holds the cookie
    second_r = await login(client)                      # device B — takes the cookie
    second = {"Authorization": f"Bearer {second_r.json()['access_token']}"}

    r = await client.post("/api/auth/password", headers=second, json={
        "current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert r.status_code == 204

    assert (await client.get("/api/auth/me", headers=first)).status_code == 401
    assert (await client.get("/api/auth/me", headers=second)).status_code == 200


async def test_a_short_password_is_refused(client):
    h = await register(client)
    r = await client.post("/api/auth/password", headers=h, json={
        "current_password": PASSWORD, "new_password": "short"})
    assert r.status_code == 422


# --- a replayed refresh cookie -----------------------------------------------

async def test_a_replayed_refresh_token_ends_the_session(client):
    """Rotation already made the stolen copy useless — but that was answered
    with the same 401 as a lapsed tab, so a theft looked like nothing."""
    h = await register(client)
    stolen = client.cookies.get("ll_refresh")

    # The legitimate client refreshes, rotating the token away.
    assert (await client.post("/api/auth/refresh")).status_code == 200
    assert (await client.get("/api/auth/me", headers=h)).status_code == 200

    # Age the rotation past ROTATION_GRACE. Inside that window a stale token is
    # two tabs racing on load, not a thief — see
    # test_sessions.py::test_two_tabs_refreshing_at_once_is_not_treated_as_theft.
    user = await User.find_one(User.email == "a@example.com")
    session = await Session.find_one(Session.user_id == user.id)
    session.rotated_at = session.rotated_at - timedelta(minutes=5)
    await session.save()

    # The thief presents the copy they took before that. Sent as a raw header
    # rather than through the jar, which is the client's own store and would
    # just hand back the freshly rotated value.
    client.cookies.clear()
    r = await client.post("/api/auth/refresh",
                          headers={"Cookie": f"ll_refresh={stolen}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Session ended", "replay should be recognised as such"

    # The session is gone for *both* of them: the safe move is to end it rather
    # than guess which party is the thief.
    assert (await client.get("/api/auth/me", headers=h)).status_code == 401
    session = await Session.find_one(Session.user_id ==
                                     (await User.find_one(User.email == "a@example.com")).id)
    assert session.revoked_at is not None


async def test_an_ordinary_expired_cookie_is_not_treated_as_theft(client):
    """A revoked session presenting its own last token is somebody signing out
    and back in, not an attack — it must not be reported as one."""
    h = await register(client)
    await client.post("/api/auth/logout")
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid refresh token"
    assert h  # the original headers are irrelevant here, only the wording is


# --- transfer ----------------------------------------------------------------

async def test_ownership_can_be_handed_over(client):
    """A record whose owner left was recoverable only by editing the database."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    other = await register(client, "other@example.com")

    r = await client.post(f"/api/patients/{pid}/transfer", headers=owner,
                          json={"email": "other@example.com"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "clinician"

    assert (await client.get(f"/api/patients/{pid}/access", headers=other)).status_code == 200
    assert (await client.get(f"/api/patients/{pid}/access", headers=owner)).status_code == 404
    assert (await client.get(f"/api/patients/{pid}", headers=owner)).json()["role"] == "clinician"


async def test_the_record_is_never_ownerless_during_a_transfer(client):
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    await register(client, "other@example.com")
    await client.post(f"/api/patients/{pid}/transfer", headers=owner,
                      json={"email": "other@example.com"})

    live = await Access.find(Access.role == "owner",
                             Access.revoked_at == None).to_list()  # noqa: E711
    assert len(live) == 1


async def test_transferring_to_a_stranger_is_404(client):
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    r = await client.post(f"/api/patients/{pid}/transfer", headers=owner,
                          json={"email": "nobody@example.com"})
    assert r.status_code == 404


async def test_only_an_owner_can_transfer(client):
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    clin = await register(client, "clin@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "clin@example.com", "role": "clinician"})
    await register(client, "third@example.com")

    r = await client.post(f"/api/patients/{pid}/transfer", headers=clin,
                          json={"email": "third@example.com"})
    assert r.status_code == 404


# --- export ------------------------------------------------------------------

async def test_a_record_exports_with_its_provenance(client):
    h = await register(client)
    pid = await make_patient(client, h)
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc_id)

    out = (await client.get(f"/api/patients/{pid}/export", headers=h)).json()
    assert out["patient"]["display_name"] == "Subject"
    assert len(out["reports"]) == 1
    assert len(out["results"]) > 0
    # An export that drops how the mapping was reached is not the same record.
    first = out["results"][0]
    assert "mapping_stage" in first
    assert "printed_name" in first and "loinc_code" in first


async def test_only_an_owner_may_export_a_record(client):
    """An export outlives any revocation, so "can read it now" and "may keep it
    forever" have to be different questions."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner)
    viewer = await register(client, "viewer@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "viewer@example.com", "role": "viewer"})

    assert (await client.get(f"/api/patients/{pid}", headers=viewer)).status_code == 200
    assert (await client.get(f"/api/patients/{pid}/export", headers=viewer)).status_code == 404


async def test_the_account_export_excludes_other_peoples_records(client):
    """Otherwise one grant becomes a permanent private copy of somebody's
    results, which is the opposite of what an access model is for."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner, "Not Yours")
    viewer = await register(client, "viewer@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "viewer@example.com", "role": "viewer"})

    out = (await client.get("/api/auth/export", headers=viewer)).json()
    assert out["account"]["email"] == "viewer@example.com"
    assert "Not Yours" not in str(out)
    # The grant is listed — that it exists is the account's own fact.
    assert len(out["record_access"]) == 1


# --- deletion ----------------------------------------------------------------

async def test_deleting_an_account_takes_its_records_with_it(client):
    h = await register(client)
    pid = await make_patient(client, h)
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc_id)

    r = await client.request("DELETE", "/api/auth/me", headers=h,
                             json={"password": PASSWORD})
    assert r.status_code == 204, r.text

    assert await User.find_one(User.email == "a@example.com") is None
    assert await Patient.get(pid) is None
    assert (await login(client)).status_code == 401


async def test_deleting_needs_the_password(client):
    """It destroys clinical data irreversibly; a live session is a weaker claim
    than a password, and an unlocked screen should not be enough."""
    h = await register(client)
    r = await client.request("DELETE", "/api/auth/me", headers=h,
                             json={"password": "not-it"})
    assert r.status_code == 401
    assert await User.find_one(User.email == "a@example.com") is not None


async def test_you_cannot_delete_a_record_out_from_under_a_colleague(client):
    """Nobody should be able to destroy somebody else's data by closing their
    own account."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner, "Shared")
    await register(client, "clin@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "clin@example.com", "role": "clinician"})

    r = await client.request("DELETE", "/api/auth/me", headers=owner,
                             json={"password": PASSWORD})
    assert r.status_code == 409
    assert "Transfer ownership first" in r.json()["detail"]
    assert await Patient.get(pid) is not None


async def test_after_transferring_the_account_can_be_deleted(client):
    """The refusal has to be escapable, or it is just a trap."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner, "Shared")
    await register(client, "clin@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "clin@example.com", "role": "clinician"})
    await client.post(f"/api/patients/{pid}/transfer", headers=owner,
                      json={"email": "clin@example.com"})

    r = await client.request("DELETE", "/api/auth/me", headers=owner,
                             json={"password": PASSWORD})
    assert r.status_code == 204
    # The colleague keeps the record.
    assert await Patient.get(pid) is not None


async def test_the_audit_trail_survives_the_account(client):
    """A log that erases itself when somebody leaves is not a log — which is
    why `actor_email` is denormalised onto every entry."""
    owner = await register(client, "owner@example.com")
    pid = await make_patient(client, owner, "Shared")
    clin = await register(client, "clin@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "clin@example.com", "role": "clinician"})
    await client.get(f"/api/patients/{pid}", headers=clin)
    await client.post(f"/api/patients/{pid}/transfer", headers=owner,
                      json={"email": "clin@example.com"})
    await client.request("DELETE", "/api/auth/me", headers=owner,
                         json={"password": PASSWORD})

    trail = (await client.get(f"/api/audit/patient/{pid}", headers=clin)).json()
    assert any(e["actor_email"] == "owner@example.com" for e in trail)
