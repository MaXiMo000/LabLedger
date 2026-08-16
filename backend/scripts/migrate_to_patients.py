"""Move existing single-user data onto the patient model.

    python scripts/migrate_to_patients.py [--apply]

Dry run by default. Nothing is written until `--apply` is passed, because this
rewrites the key on every clinical row and there is no undo.

For each user holding data, it creates one `Patient` from that user's own
demographics, grants them `owner` on it, and stamps `patient_id` onto their
documents and observations. A personal account therefore ends up exactly where
it started — one person, one record — with multi-patient available as the same
mechanism applied more than once.

Idempotent: rows that already carry `patient_id` are skipped, so a partial run
can be finished by running it again.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.models.user import utcnow


async def migrate(apply: bool) -> None:
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db_name]

    users = await db.users.find({}).to_list(None)
    print(f"{len(users)} accounts\n")

    total_docs = total_obs = created = 0

    for u in users:
        uid = u["_id"]
        docs = await db.documents.count_documents({"user_id": uid, "patient_id": {"$exists": False}})
        obs = await db.observations.count_documents({"user_id": uid, "patient_id": {"$exists": False}})

        existing = await db.access.find_one({"user_id": uid, "role": "owner"})
        if existing:
            print(f"  {u['email']:<34} already migrated, skipping")
            continue
        if docs == 0 and obs == 0:
            # Still give them a record: an account with no data that signs in
            # tomorrow needs somewhere to put it.
            print(f"  {u['email']:<34} no data — creating an empty record")
        else:
            print(f"  {u['email']:<34} {docs} documents, {obs} observations")

        if not apply:
            created += 1
            total_docs += docs
            total_obs += obs
            continue

        # Demographics move from the account to the record: they describe a
        # body, not a login.
        patient = {
            "display_name": u.get("name") or u["email"].split("@")[0],
            "dob_enc": u.get("dob_enc"),
            "sex_at_birth_enc": u.get("sex_at_birth_enc"),
            "mrn_enc": None,
            "created_by": uid,
            "created_at": utcnow(),
        }
        pid = (await db.patients.insert_one(patient)).inserted_id

        await db.access.insert_one({
            "user_id": uid, "patient_id": pid, "role": "owner",
            "granted_by": uid, "granted_at": utcnow(), "revoked_at": None,
        })

        r1 = await db.documents.update_many(
            {"user_id": uid, "patient_id": {"$exists": False}},
            {"$set": {"patient_id": pid, "uploaded_by": uid}},
        )
        r2 = await db.observations.update_many(
            {"user_id": uid, "patient_id": {"$exists": False}},
            {"$set": {"patient_id": pid}},
        )
        created += 1
        total_docs += r1.modified_count
        total_obs += r2.modified_count

    verb = "migrated" if apply else "would migrate"
    print(f"\n{verb}: {created} records, {total_docs} documents, {total_obs} observations")

    if apply:
        # The old key is left in place rather than unset. It costs a few bytes
        # and it is the only way back if something downstream turns out to have
        # depended on it; a later release can drop it once this has held.
        print("\n`user_id` retained on migrated rows as a fallback.")
        print("Aliases are unchanged — they stay scoped to the user by design.")
    else:
        print("\nDry run. Re-run with --apply to write.")

    client.close()


if __name__ == "__main__":
    asyncio.run(migrate("--apply" in sys.argv))
