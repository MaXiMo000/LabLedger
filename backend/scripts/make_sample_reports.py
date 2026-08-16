"""Generate a realistic set of lab reports to exercise the whole flow.

    python scripts/make_sample_reports.py [outdir]

Produces six reports for one fictional person across three years and three
labs. They are built to exercise the parts of the pipeline that matter:

  * the same analyte printed differently by each lab
    ("FERRITIN, SERUM" / "Ferritin (S)" / "FERRTN SER")
  * a unit change between labs (ng/mL vs µg/L) so conversion is tested
  * different column orders per lab, so the extractor cannot rely on position
  * values that drift over time, so a trend has a shape
  * a few out-of-range values, so flagging shows
  * qualitative results ("NEGATIVE") that must not become chart points
  * abbreviations that only resolve at later cascade stages

Nothing here is a real person or a real result.
"""

import sys
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# (printed name, unit, ref range, [value per visit])  — six visits
PANEL = [
    # ferritin falls over time: the story the trend should tell
    ("FERRITIN, SERUM",   "ng/mL",    "24-336",    [96, 74, 55, 38, 24, 18]),
    ("HEMOGLOBIN",        "g/dL",     "13.0-17.7", [15.2, 15.0, 14.6, 14.1, 13.6, 13.2]),
    ("HEMATOCRIT",        "%",        "37.5-51.0", [45.1, 44.6, 43.1, 42.0, 40.8, 39.9]),
    ("MCHC",              "g/dL",     "31.5-35.7", [34.1, 33.9, 33.9, 33.4, 33.0, 32.8]),
    ("WBC",               "x10E3/uL", "3.4-10.8",  [6.1, 6.4, 6.2, 5.9, 6.6, 6.3]),
    ("PLATELETS",         "x10E3/uL", "150-450",   [251, 244, 262, 248, 239, 255]),
    ("GLUCOSE",           "mg/dL",    "65-99",     [88, 91, 95, 97, 101, 99]),
    ("CREATININE",        "mg/dL",    "0.76-1.27", [0.95, 0.98, 1.02, 1.00, 1.06, 1.03]),
    ("SODIUM",            "mmol/L",   "134-144",   [140, 139, 139, 141, 138, 140]),
    ("POTASSIUM",         "mmol/L",   "3.5-5.2",   [4.2, 4.4, 4.1, 4.6, 5.4, 4.8]),
    ("TSH",               "uIU/mL",   "0.45-4.50", [1.88, 2.01, 2.14, 2.44, 2.90, 3.10]),
    ("VITAMIN B12",       "pg/mL",    "232-1245",  [610, 540, 412, 366, 301, 268]),
    ("HEMOGLOBIN A1C",    "%",        "4.8-5.6",   [5.1, 5.2, 5.4, 5.5, 5.7, 5.6]),
    ("CHOLESTEROL, TOTAL", "mg/dL",   "100-199",   [176, 181, 184, 192, 205, 198]),
    ("HDL CHOLESTEROL",   "mg/dL",    ">39",       [62, 60, 58, 55, 51, 54]),
    ("LDL-CHOLESTEROL",   "mg/dL",    "0-99",      [92, 97, 104, 112, 128, 118]),
    ("TRIGLYCERIDES",     "mg/dL",    "0-149",     [101, 108, 110, 126, 141, 132]),
]

URINE = [("PH", "", "5.0-8.0"), ("PROTEIN", "", "NEGATIVE"), ("GLUCOSE", "", "NEGATIVE")]

# Each lab prints its own names, units and column order.
LABS = [
    {
        "name": "Quest Diagnostics",
        "order": "quest",              # NAME VALUE FLAG UNIT REF
        "rename": {},
        "unit_swap": {},
    },
    {
        "name": "LabCorp",
        "order": "labcorp",            # NAME VALUE FLAG REF UNIT
        "rename": {
            "FERRITIN, SERUM": "Ferritin (S)",
            "HEMOGLOBIN": "HGB",
            "HEMATOCRIT": "HCT",
            "PLATELETS": "PLT",
            "VITAMIN B12": "Vit B-12",
            "CHOLESTEROL, TOTAL": "Cholesterol, Total",
        },
        "unit_swap": {},
    },
    {
        "name": "Sutter Health Laboratory",
        "order": "quest",
        "rename": {
            "FERRITIN, SERUM": "FERRTN SER",
            "HEMOGLOBIN A1C": "A1C",
            "CREATININE": "CREAT",
            "SODIUM": "NA",
            "POTASSIUM": "K",
        },
        # Same analyte, different unit: the conversion table gets exercised.
        "unit_swap": {"FERRITIN, SERUM": ("µg/L", 1.0)},
    },
]

VISITS = [
    (date(2023, 2, 14), 0), (date(2023, 9, 6), 1),
    (date(2024, 3, 14), 2), (date(2024, 10, 22), 3),
    (date(2025, 4, 8), 4),  (date(2026, 1, 19), 5),
]


def flag_for(value, ref):
    """The H/L marker a lab would print, so the extractor sees a real one."""
    try:
        if ref.startswith(">"):
            return "L" if value < float(ref[1:]) else ""
        lo, hi = (float(x) for x in ref.replace("–", "-").split("-"))
        if value < lo:
            return "L"
        if value > hi:
            return "H"
    except (ValueError, AttributeError):
        pass
    return ""


def build(path: Path, lab: dict, when: date, idx: int) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740

    c.setFont("Helvetica-Bold", 13)
    c.drawString(60, y, lab["name"])
    y -= 18
    c.setFont("Helvetica", 8)
    for line in [
        "Patient: SAINI, RITISH              DOB: 04/12/1996        Sex: M",
        f"MRN: SAMPLE-00417                   Accession: {lab['name'][:2].upper()}-{when:%Y%m%d}",
        f"Collected: {when:%m/%d/%Y}                Reported: {when:%m/%d/%Y}",
        "Ordering Physician: PATEL, ANJALI MD",
    ]:
        c.drawString(60, y, line)
        y -= 11
    y -= 10

    c.setFont("Helvetica-Bold", 8)
    if lab["order"] == "quest":
        cols = [("TEST NAME", 60), ("RESULT", 230), ("UNITS", 310), ("REFERENCE RANGE", 400)]
    else:
        cols = [("TEST NAME", 60), ("RESULT", 230), ("REFERENCE INTERVAL", 310), ("UNITS", 430)]
    for label, x in cols:
        c.drawString(x, y, label)
    y -= 16

    def section(title, rows):
        nonlocal y
        c.setFont("Helvetica-Bold", 9)
        c.drawString(60, y, title)
        y -= 14
        c.setFont("Helvetica", 8)
        for name, unit, ref, value in rows:
            printed = lab["rename"].get(name, name)
            if name in lab["unit_swap"]:
                unit, factor = lab["unit_swap"][name]
                value = round(value * factor, 2) if isinstance(value, (int, float)) else value
            flag = flag_for(value, ref) if isinstance(value, (int, float)) else ""

            c.drawString(60, y, printed)
            c.drawString(230, y, str(value))
            c.drawString(280, y, flag)
            if lab["order"] == "quest":
                c.drawString(310, y, unit)
                c.drawString(400, y, ref)
            else:
                c.drawString(310, y, ref)
                c.drawString(430, y, unit)
            y -= 12
            if y < 90:
                c.showPage()
                y = 740
                c.setFont("Helvetica", 8)
        y -= 8

    chem = [(n, u, r, v[idx]) for n, u, r, v in PANEL if n not in
            ("HEMOGLOBIN", "HEMATOCRIT", "MCHC", "WBC", "PLATELETS")]
    heme = [(n, u, r, v[idx]) for n, u, r, v in PANEL if n in
            ("HEMOGLOBIN", "HEMATOCRIT", "MCHC", "WBC", "PLATELETS")]

    section("CHEMISTRY (SERUM)", chem)
    section("HEMATOLOGY (WHOLE BLOOD)", heme)
    section("URINALYSIS (URINE)", [(n, u, r, "NEGATIVE" if r == "NEGATIVE" else 6.0)
                                   for n, u, r in URINE])

    c.setFont("Helvetica-Oblique", 7)
    c.drawString(60, y, "Sample data for testing. Not a real medical record.")
    c.save()


def main(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    made = []
    for i, (when, idx) in enumerate(VISITS):
        lab = LABS[i % len(LABS)]
        slug = lab["name"].split()[0].lower()
        path = outdir / f"{when:%Y-%m-%d}_{slug}.pdf"
        build(path, lab, when, idx)
        made.append((path, lab["name"]))

    print(f"{len(made)} sample reports in {outdir}\n")
    for p, lab in made:
        print(f"  {p.name:<28} {lab}")
    print("\nUpload all six. Ferritin falls 96 -> 18 ng/mL across them, and the")
    print("third lab prints it as 'FERRTN SER' in µg/L, so you can watch the")
    print("names and units reconcile into one line.")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Desktop" / "LabLedger-samples")
