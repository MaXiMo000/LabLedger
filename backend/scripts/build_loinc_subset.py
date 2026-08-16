"""Distill the 925 MB LOINC release into the ~5 MB subset LabLedger actually uses.

Run once against an extracted LOINC release, then the release can be deleted:

    python scripts/build_loinc_subset.py ~/Downloads/Loinc_2.82

Row filter
    STATUS   = ACTIVE               drop deprecated/discouraged codes
    CLASSTYPE= 1                    laboratory only (2=clinical, 3=claims, 4=survey)
    ORDER_OBS in Observation|Both   things that can appear as a result on a report

Column filter
    Keeps the six LOINC axes (the narrowing keys for mapping stage 2), every
    name variant used for matching, UCUM units, and COMMON_TEST_RANK. Drops
    definitions, HL7 attachment metadata, survey text, and version history.

COMMON_TEST_RANK is the release's own frequency ranking of real-world lab
results, so it replaces the separately published "Top 2000" file. Rank 0 means
the code was never observed in the source survey: those rows are kept but are
matched only via explicit review-queue search, never auto-matched (see
pipeline/mapping.py). Without them a rare-but-real test would be unresolvable
even by a human, which would defeat the point of the review queue.
"""

import csv
import gzip
import hashlib
import sys
from pathlib import Path

csv.field_size_limit(10**9)

KEEP = [
    "LOINC_NUM",
    "COMPONENT", "PROPERTY", "TIME_ASPCT", "SYSTEM", "SCALE_TYP", "METHOD_TYP",
    "CLASS",
    "LONG_COMMON_NAME", "SHORTNAME", "DisplayName", "CONSUMER_NAME", "RELATEDNAMES2",
    "EXAMPLE_UCUM_UNITS",
    "COMMON_TEST_RANK",
]

OUT = Path(__file__).resolve().parent.parent / "data" / "loinc_lab.csv.gz"


def build(release_dir: Path) -> None:
    src = release_dir / "LoincTable" / "Loinc.csv"
    if not src.exists():
        sys.exit(f"Not found: {src}\nPoint at an extracted LOINC release directory.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    kept = ranked = restricted = 0

    with src.open(encoding="utf-8-sig", newline="") as fin, \
            gzip.open(OUT, "wt", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=KEEP, extrasaction="ignore")
        writer.writeheader()
        for row in csv.DictReader(fin):
            if row["STATUS"] != "ACTIVE" or row["CLASSTYPE"] != "1":
                continue
            if row["ORDER_OBS"] not in ("Observation", "Both"):
                continue
            # 6 codes in 2.82 carry a third-party copyright; excluded rather than
            # tracking per-code redistribution terms for a rounding error.
            if row["EXTERNAL_COPYRIGHT_NOTICE"].strip():
                restricted += 1
                continue
            rank = (row["COMMON_TEST_RANK"] or "0").strip()
            row["COMMON_TEST_RANK"] = rank if rank.isdigit() else "0"
            ranked += row["COMMON_TEST_RANK"] != "0"
            writer.writerow(row)
            kept += 1

    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    size_mb = OUT.stat().st_size / 1e6
    print(f"wrote {OUT.relative_to(OUT.parent.parent)}  {size_mb:.1f} MB")
    print(f"  codes kept          {kept:,}")
    print(f"  auto-matchable      {ranked:,}  (COMMON_TEST_RANK > 0)")
    print(f"  review-search only  {kept - ranked:,}")
    print(f"  copyright-excluded  {restricted}")
    print(f"  sha256              {digest}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(Path(sys.argv[1]).expanduser())
