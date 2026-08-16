"""Patients, roles, and the boundaries between them.

The permission matrix is the security surface of a multi-patient system, so it
is asserted explicitly — including every negative case. A role test that only
checks the allowed paths proves nothing.
"""

from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.models.patient import Access
from app.models.user import User
from app.worker import process_document

pytestmark = pytest.mark.asyncio

PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()


async def make_user(client, email):
    r = await client.post("/api/auth/register", json={
        "email": email, "name": email.split("@")[0], "password": "correct-horse-battery"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_patient(client, h, name="Subject"):
    return (await client.post("/api/patients", headers=h,
            json={"display_name": name, "dob": "1996-04-12",
                  "sex_at_birth": "M"})).json()["id"]


async def with_data(client, h, pid):
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc_id)
    return doc_id


# --- patients ---------------------------------------------------------------

async def test_creator_becomes_owner(client):
    h = await make_user(client, "a@example.com")
    pid = await make_patient(client, h)
    got = (await client.get(f"/api/patients/{pid}", headers=h)).json()
    assert got["role"] == "owner"


async def test_demographics_are_encrypted_at_rest(client):
    from app.models.patient import Patient
    h = await make_user(client, "a@example.com")
    pid = await make_patient(client, h)
    p = await Patient.get(pid)
    assert isinstance(p.dob_enc, bytes)
    assert b"1996" not in p.dob_enc
    # The display name is deliberately not encrypted: it is rendered on every
    # screen and identifies nobody to an outsider on its own.
    assert p.display_name == "Subject"


async def test_one_account_can_hold_several_patients(client):
    h = await make_user(client, "nurse@example.com")
    for name in ("Patient One", "Patient Two", "Patient Three"):
        await make_patient(client, h, name)
    assert len((await client.get("/api/patients", headers=h)).json()) == 3


async def test_patients_are_isolated_from_each_other(client):
    """The most dangerous failure in this system is data from one patient
    appearing under another."""
    h = await make_user(client, "a@example.com")
    p1, p2 = await make_patient(client, h, "One"), await make_patient(client, h, "Two")
    await with_data(client, h, p1)

    assert len((await client.get(f"/api/observations/{p1}/panels", headers=h)).json()) > 0
    assert (await client.get(f"/api/observations/{p2}/panels", headers=h)).json() == []
    assert (await client.get(f"/api/documents/{p2}", headers=h)).json() == []


# --- editing ----------------------------------------------------------------

async def test_owner_can_edit_demographics(client):
    h = await make_user(client, "a@example.com")
    pid = await make_patient(client, h)
    r = await client.patch(f"/api/patients/{pid}", headers=h, json={
        "display_name": "Renamed", "dob": "1984-02-29",
        "sex_at_birth": "F", "mrn": "MRN-7781"})
    assert r.status_code == 200, r.text
    assert r.json() == {**r.json(), "display_name": "Renamed", "dob": "1984-02-29",
                        "sex_at_birth": "F", "mrn": "MRN-7781"}


async def test_a_field_can_be_cleared_once_set(client):
    """A date of birth typed wrong must not be permanent: it selects the
    reference range for every result on the record."""
    h = await make_user(client, "a@example.com")
    pid = await make_patient(client, h)  # created with dob 1996-04-12

    r = await client.patch(f"/api/patients/{pid}", headers=h,
                           json={"display_name": "Subject", "dob": None,
                                 "sex_at_birth": None, "mrn": None})
    assert r.status_code == 200
    assert r.json()["dob"] is None
    assert r.json()["sex_at_birth"] is None


async def test_a_field_left_out_is_not_touched(client):
    """Omitting a key and sending it as null are different requests. Treating
    them the same is what made clearing impossible in the first place."""
    h = await make_user(client, "a@example.com")
    pid = await make_patient(client, h)
    r = await client.patch(f"/api/patients/{pid}", headers=h,
                           json={"display_name": "Subject"})
    assert r.json()["dob"] == "1996-04-12"
    assert r.json()["sex_at_birth"] == "M"


async def test_editing_demographics_needs_ownership(client):
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    clin = await make_user(client, "clin@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "clin@example.com", "role": "clinician"})

    r = await client.patch(f"/api/patients/{pid}", headers=clin,
                           json={"display_name": "Hijacked"})
    assert r.status_code == 404


async def test_a_future_date_of_birth_is_still_rejected_on_edit(client):
    h = await make_user(client, "a@example.com")
    pid = await make_patient(client, h)
    r = await client.patch(f"/api/patients/{pid}", headers=h,
                           json={"display_name": "Subject", "dob": "2999-01-01"})
    assert r.status_code == 422


# --- reachability -----------------------------------------------------------

async def test_a_patient_you_cannot_reach_is_404_not_403(client):
    """403 confirms the record exists. In a small clinic, knowing that a
    particular person has a record is sometimes the whole secret."""
    a = await make_user(client, "a@example.com")
    pid = await make_patient(client, a)
    b = await make_user(client, "b@example.com")

    for url in (f"/api/patients/{pid}",
                f"/api/documents/{pid}",
                f"/api/observations/{pid}/panels",
                f"/api/review/{pid}"):
        assert (await client.get(url, headers=b)).status_code == 404, url


async def test_documents_of_an_unreachable_patient_are_404(client):
    a = await make_user(client, "a@example.com")
    pid = await make_patient(client, a)
    doc_id = await with_data(client, a, pid)
    b = await make_user(client, "b@example.com")

    assert (await client.get(f"/api/documents/item/{doc_id}", headers=b)).status_code == 404
    assert (await client.get(f"/api/documents/item/{doc_id}/file", headers=b)).status_code == 404


# --- the role matrix --------------------------------------------------------

@pytest.mark.parametrize(
    ("role", "can_read", "can_upload", "can_confirm", "can_manage"),
    [
        ("viewer", True, False, False, False),
        ("nurse", True, True, False, False),
        ("clinician", True, True, True, False),
        ("owner", True, True, True, True),
    ],
)
async def test_role_matrix(client, role, can_read, can_upload, can_confirm, can_manage):
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    doc_id = await with_data(client, owner, pid)
    item = (await client.get(f"/api/review/{pid}", headers=owner)).json()[0]

    other = await make_user(client, "other@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "other@example.com", "role": role})

    # read
    r = await client.get(f"/api/observations/{pid}/panels", headers=other)
    assert (r.status_code == 200) is can_read

    # upload
    r = await client.post(f"/api/documents/{pid}", headers=other,
                          files={"file": ("x.pdf", PDF, "application/pdf")})
    assert (r.status_code == 202) is can_upload

    # confirm — a clinical judgement, so a nurse cannot make it
    r = await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                          headers=other, json={"loinc_code": "2276-4"})
    assert (r.status_code == 200) is can_confirm

    # manage access
    r = await client.get(f"/api/patients/{pid}/access", headers=other)
    assert (r.status_code == 200) is can_manage

    assert doc_id  # the fixture document exists throughout


async def test_a_nurse_cannot_confirm_a_mapping(client):
    """Called out on its own because it is the least obvious rule: confirming
    decides what a number *is*, which is a clinical judgement."""
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    await with_data(client, owner, pid)
    item = (await client.get(f"/api/review/{pid}", headers=owner)).json()[0]

    nurse = await make_user(client, "nurse@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "nurse@example.com", "role": "nurse"})

    assert (await client.get(f"/api/review/{pid}", headers=nurse)).status_code == 200
    r = await client.post(f"/api/review/item/{item['observation_id']}/confirm",
                          headers=nurse, json={"loinc_code": "2276-4"})
    assert r.status_code == 404


# --- granting and revoking --------------------------------------------------

async def test_granting_then_revoking_removes_reach(client):
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    other = await make_user(client, "other@example.com")

    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "other@example.com", "role": "clinician"})
    assert (await client.get(f"/api/patients/{pid}", headers=other)).status_code == 200

    grant = await Access.find_one(Access.patient_id == PydanticObjectId(pid), Access.role == "clinician")
    r = await client.delete(f"/api/patients/{pid}/access/{grant.user_id}", headers=owner)
    assert r.status_code == 204
    assert (await client.get(f"/api/patients/{pid}", headers=other)).status_code == 404


async def test_revoking_keeps_the_record_of_the_grant(client):
    """Deleting the row would destroy the answer to 'who had access, and when' —
    which is exactly what an investigation asks."""
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    await make_user(client, "other@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "other@example.com", "role": "viewer"})

    grant = await Access.find_one(Access.patient_id == PydanticObjectId(pid), Access.role == "viewer")
    await client.delete(f"/api/patients/{pid}/access/{grant.user_id}", headers=owner)

    rows = (await client.get(f"/api/patients/{pid}/access", headers=owner)).json()
    revoked = [r for r in rows if r["revoked_at"]]
    assert len(revoked) == 1
    assert revoked[0]["email"] == "other@example.com"


async def test_the_last_owner_cannot_be_revoked(client):
    """A record with no owner is unreachable and unrecoverable."""
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    grant = await Access.find_one(Access.patient_id == PydanticObjectId(pid), Access.role == "owner")
    r = await client.delete(f"/api/patients/{pid}/access/{grant.user_id}", headers=owner)
    assert r.status_code == 409


async def test_the_last_owner_cannot_be_demoted_either(client):
    """The same invariant, through the other door. Revocation was guarded and
    role changes were not, so a record could be orphaned by demoting its only
    owner instead of removing them — which is exactly what happened."""
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    second = await make_user(client, "second@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "second@example.com", "role": "owner"})

    # Two owners: demoting one is legitimate.
    r = await client.post(f"/api/patients/{pid}/access", headers=owner,
                          json={"email": "second@example.com", "role": "viewer"})
    assert r.status_code == 201
    assert (await client.get(f"/api/patients/{pid}/access", headers=second)).status_code == 404

    # One owner left: demoting them is refused, and the record keeps an owner.
    grant = await Access.find_one(Access.patient_id == PydanticObjectId(pid),
                                  Access.role == "owner", Access.revoked_at == None)  # noqa: E711
    owner_user = await User.get(grant.user_id)
    r = await client.post(f"/api/patients/{pid}/access", headers=owner,
                          json={"email": owner_user.email, "role": "viewer"})
    assert r.status_code == 409
    assert (await client.get(f"/api/patients/{pid}", headers=owner)).json()["role"] == "owner"


async def test_you_cannot_grant_access_to_yourself(client):
    """The footgun that orphaned a record: typing your own address into a form
    that says "give someone access" reads as adding a person, but lands on your
    own grant with whatever role the picker was showing."""
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)

    r = await client.post(f"/api/patients/{pid}/access", headers=owner,
                          json={"email": "owner@example.com", "role": "viewer"})
    assert r.status_code == 409
    assert "your own role" in r.json()["detail"]
    assert (await client.get(f"/api/patients/{pid}", headers=owner)).json()["role"] == "owner"


async def test_every_record_keeps_a_live_owner(client):
    """The invariant itself, stated once. Whatever an owner does through the
    access panel, the record must still be manageable afterwards."""
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    await make_user(client, "other@example.com")

    for attempt in (
        {"email": "owner@example.com", "role": "viewer"},
        {"email": "owner@example.com", "role": "nurse"},
        {"email": "other@example.com", "role": "viewer"},
    ):
        await client.post(f"/api/patients/{pid}/access", headers=owner, json=attempt)

    owners = await Access.find(Access.patient_id == PydanticObjectId(pid),
                               Access.role == "owner",
                               Access.revoked_at == None).to_list()  # noqa: E711
    assert len(owners) >= 1


async def test_granting_to_an_unknown_email_does_not_confirm_the_account(client):
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    r = await client.post(f"/api/patients/{pid}/access", headers=owner,
                          json={"email": "nobody@example.com", "role": "viewer"})
    assert r.status_code == 404


async def test_a_non_owner_cannot_grant_access(client):
    owner = await make_user(client, "owner@example.com")
    pid = await make_patient(client, owner)
    clin = await make_user(client, "clin@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=owner,
                      json={"email": "clin@example.com", "role": "clinician"})
    await make_user(client, "third@example.com")

    r = await client.post(f"/api/patients/{pid}/access", headers=clin,
                          json={"email": "third@example.com", "role": "owner"})
    assert r.status_code == 404


# --- shared care ------------------------------------------------------------

async def test_two_clinicians_share_one_patient(client):
    """The case the whole phase exists for."""
    a = await make_user(client, "a@example.com")
    pid = await make_patient(client, a, "Shared Patient")
    await with_data(client, a, pid)

    b = await make_user(client, "b@example.com")
    await client.post(f"/api/patients/{pid}/access", headers=a,
                      json={"email": "b@example.com", "role": "clinician"})

    seen_by_a = (await client.get(f"/api/observations/{pid}/panels", headers=a)).json()
    seen_by_b = (await client.get(f"/api/observations/{pid}/panels", headers=b)).json()
    assert seen_by_a == seen_by_b
    assert len(seen_by_b) > 0
