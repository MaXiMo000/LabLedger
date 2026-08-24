import os

# Must precede any `app.*` import: pydantic-settings reads the environment at
# import time, and ENV=test is what disables the per-IP rate limits. Without
# it every test would share one 127.0.0.1 bucket and trip the login limiter.
os.environ["ENV"] = "test"

import pytest
import pytest_asyncio
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.db import DOCUMENT_MODELS
from app.main import app

TEST_DB = "labledger_test"

# NOT parallelised, and the reason is a hard constraint rather than a
# preference. The fixture wipes user collections between tests, so xdist
# workers must each have their own database — and each of those needs its own
# copy of the 58k-row LOINC table, which on a 512 MB Atlas free tier fills the
# quota at four workers and blocks writes on the whole cluster. Tried it; it
# did exactly that. Parallelism here needs a local mongod or a paid tier, not
# a flag.

# Reference data, not user data: shared across tests and never mutated.
REFERENCE_COLLECTIONS = {"loinc"}
# Everything user-scoped is wiped between tests.
USER_COLLECTIONS = ["users", "documents", "observations", "aliases", "audit",
                    "patients", "access", "sessions", "invites"]


_loinc_copied = False


async def _ensure_loinc(mongo: AsyncIOMotorClient) -> None:
    """Copy the seeded LOINC table into the test database, once per run.

    Server-side $out, so 58k documents never travel through Python. Without
    this the mapping cascade has nothing to match against and every row
    resolves to `unmapped` -- which turns mapping assertions green for
    entirely the wrong reason.

    A module-level flag rather than a session-scoped fixture: pytest-asyncio
    runs each test on its own event loop, so a session-scoped async fixture
    raises ScopeMismatch.
    """
    global _loinc_copied
    if _loinc_copied:
        return
    src, dst = mongo[settings.mongo_db_name], mongo[TEST_DB]
    if await dst.loinc.estimated_document_count() != await src.loinc.estimated_document_count():
        # Batched cursor copy rather than $out: cross-database $out is not
        # available on Atlas shared tiers.
        await dst.loinc.drop()
        batch = []
        async for doc in src.loinc.find({}):
            batch.append(doc)
            if len(batch) >= 5000:
                await dst.loinc.insert_many(batch)
                batch.clear()
        if batch:
            await dst.loinc.insert_many(batch)

    # The count comparison above cannot tell "already copied" from "both are
    # empty" -- 0 != 0 is false, so an unseeded database skips the copy and
    # reports itself done. Against a fresh mongo the mapping tests then fail
    # eight different ways and none of them mentions LOINC. Say it once, here.
    if await dst.loinc.estimated_document_count() == 0:
        raise RuntimeError(
            f"the LOINC table is empty in both {settings.mongo_db_name!r} and "
            f"{TEST_DB!r}. Run `python scripts/seed_loinc.py` first -- without it "
            "every row resolves to `unmapped` and the mapping assertions are "
            "meaningless."
        )
    _loinc_copied = True


@pytest_asyncio.fixture
async def client():
    # tz_aware must match app.db.init_db, or the idle timeout raises here and
    # only here.
    mongo = AsyncIOMotorClient(settings.mongo_uri, tz_aware=True)
    await _ensure_loinc(mongo)
    db = mongo[TEST_DB]
    for name in USER_COLLECTIONS:
        await db[name].delete_many({})
    # init_beanie also (re)creates indexes, including the loinc text index that
    # $out does not carry over.
    await init_beanie(database=db, document_models=DOCUMENT_MODELS)
    # Tests bypass lifespan, so wire the queue explicitly. None means "no worker":
    # uploads still store the file, and tests drive process_document() directly
    # rather than depending on a live Redis.
    app.state.arq = None
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    for name in USER_COLLECTIONS:
        await db[name].delete_many({})
    mongo.close()


@pytest_asyncio.fixture
async def account(client):
    """A signed-in account owning one empty patient record.

    Returns (headers, patient_id). Almost every clinical test needs both now:
    data belongs to a patient, and reaching it needs a grant.
    """
    r = await client.post("/api/auth/register", json={
        "email": "owner@example.com", "name": "Owner",
        "password": "correct-horse-battery"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    p = await client.post("/api/patients", headers=h,
                          json={"display_name": "Test Patient",
                                "dob": "1996-04-12", "sex_at_birth": "M"})
    return h, p.json()["id"]


@pytest.fixture
def creds():
    return {
        "email": "ferritin@example.com",
        "name": "Test User",
        "password": "correct-horse-battery",
    }


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch, request):
    """Stub stage 4 so the suite never calls a paid external API.

    Returns the top candidate: deterministic, and it exercises the same worker
    path a real answer would. Tests that assert on adjudicator behaviour stub
    httpx themselves and opt out with @pytest.mark.live_llm.
    """
    if "live_llm" in request.keywords:
        return

    async def _stub(name, unit, specimen, candidates, model=None, timeout=30.0):  # noqa: ASYNC109 - must mirror adjudicate()'s signature
        return (candidates[0].loinc_code if candidates else None), "stub"

    monkeypatch.setattr("app.worker.adjudicate", _stub)
