"""Load data/loinc_lab.csv.gz into the `loinc` collection.

    python scripts/seed_loinc.py [--drop]

Idempotent: re-running replaces the collection contents. Safe to run after
`build_loinc_subset.py` produces a new subset from a newer LOINC release.
"""

import asyncio
import csv
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beanie import init_beanie

from app.config import settings
from app.db import DOCUMENT_MODELS, close_db, init_db
from app.models.loinc import LoincEntry

SUBSET = Path(__file__).resolve().parent.parent / "data" / "loinc_lab.csv.gz"
BATCH = 5000


def build_search_blob(row: dict) -> str:
    """Every string a lab might print for this test, lowercased into one field.

    RELATEDNAMES2 is the highest-value part: it carries the abbreviations labs
    actually use ("HGB", "T4 FREE") that never appear in the long common name.
    """
    parts = [
        row["LONG_COMMON_NAME"], row["SHORTNAME"], row["DisplayName"],
        row["CONSUMER_NAME"], row["COMPONENT"], row["RELATEDNAMES2"],
    ]
    return " ".join(p.strip() for p in parts if p and p.strip()).lower()


async def main(drop: bool) -> None:
    if not SUBSET.exists():
        sys.exit(f"Missing {SUBSET}\nRun: python scripts/build_loinc_subset.py <release dir>")

    await init_db()
    if drop or await LoincEntry.count():
        await LoincEntry.get_motor_collection().drop()
        print("dropped existing loinc collection")

    batch: list[LoincEntry] = []
    total = ranked = 0
    with gzip.open(SUBSET, "rt", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rank = int(row["COMMON_TEST_RANK"] or 0)
            batch.append(LoincEntry(
                loinc_num=row["LOINC_NUM"],
                component=row["COMPONENT"],
                property=row["PROPERTY"],
                time_aspct=row["TIME_ASPCT"],
                system=row["SYSTEM"],
                scale_typ=row["SCALE_TYP"],
                method_typ=row["METHOD_TYP"],
                loinc_class=row["CLASS"],
                long_common_name=row["LONG_COMMON_NAME"],
                shortname=row["SHORTNAME"],
                display_name=row["DisplayName"],
                consumer_name=row["CONSUMER_NAME"],
                related_names=row["RELATEDNAMES2"],
                example_ucum_units=row["EXAMPLE_UCUM_UNITS"],
                common_rank=rank,
                auto_matchable=rank > 0,
                search_blob=build_search_blob(row),
            ))
            total += 1
            ranked += rank > 0
            if len(batch) >= BATCH:
                await LoincEntry.insert_many(batch)
                batch.clear()
                print(f"  {total:,}...", end="\r", flush=True)
    if batch:
        await LoincEntry.insert_many(batch)

    # Beanie creates indexes on init; the collection was dropped after that.
    await LoincEntry.get_motor_collection().database.client.admin.command("ping")
    from app.db import _client
    await init_beanie(database=_client[settings.mongo_db_name],
                      document_models=DOCUMENT_MODELS)

    print(f"seeded {total:,} LOINC codes ({ranked:,} auto-matchable, "
          f"{total - ranked:,} review-search only)")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main("--drop" in sys.argv))
