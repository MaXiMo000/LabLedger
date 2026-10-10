"""Fill an empty database with a demo account and six synthetic reports.

    python scripts/seed_demo.py
    python scripts/seed_demo.py --make-keys   # print fresh JWT and field keys

Used by `docker compose up`. The reports come from make_sample_reports.py and
go through the same `process_document` an upload does, so what the demo shows
is what the pipeline produced -- nothing is inserted pre-mapped.

Safe to run again: it does nothing once the demo account exists. The password
is a default for a database that only ever holds generated data; set
DEMO_PASSWORD to use another.
"""

import asyncio
import base64
import hashlib
import os
import secrets
import sys
import tempfile
from pathlib import Path

if "--make-keys" in sys.argv:
    # Before any `app` import: Settings refuses to load without these.
    print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
    print(f"FIELD_ENCRYPTION_KEY={base64.b64encode(os.urandom(32)).decode()}")
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_sample_reports
import seed_loinc

from app import access
from app.db import close_db, init_db
from app.models.document import LabDocument
from app.models.loinc import LoincEntry
from app.models.patient import Patient
from app.models.user import User
from app.security import encrypt_field, hash_password
from app.worker import process_document

EMAIL = "demo@example.com"
PASSWORD = os.environ.get("DEMO_PASSWORD", "labledger-demo-only")


async def main() -> None:
    await init_db()
    if not await LoincEntry.count():
        # seed_loinc opens and closes its own connection.
        await close_db()
        await seed_loinc.main(drop=False)
        await init_db()
    try:
        if await User.find_one(User.email == EMAIL):
            print("demo account already present")
        else:
            user = User(email=EMAIL, name="Demo", password_hash=hash_password(PASSWORD))
            await user.insert()
            patient = Patient(
                display_name="Sample Patient",
                dob_enc=encrypt_field(make_sample_reports.DOB.isoformat()),
                sex_at_birth_enc=encrypt_field("M"),
                created_by=user.id,
            )
            await patient.insert()
            await access.grant(user.id, patient.id, "owner", granted_by=user.id)

            with tempfile.TemporaryDirectory() as tmp:
                make_sample_reports.main(Path(tmp))
                for pdf in sorted(Path(tmp).glob("*.pdf")):
                    data = pdf.read_bytes()
                    doc = LabDocument(
                        patient_id=patient.id, uploaded_by=user.id, filename=pdf.name,
                        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                        blob_enc=encrypt_field(data),
                    )
                    await doc.insert()
                    print(f"  {pdf.name}: {await process_document({}, str(doc.id))}")
    finally:
        await close_db()
    print(f"\nSign in at http://localhost:8080 as {EMAIL} / {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
