"""Generate synthetic lab-report PDFs for testing the extractor.

    python tests/fixtures/make_fixtures.py

Two reports with deliberately DIFFERENT column orders:

    quest_style.pdf     NAME  VALUE  FLAG  UNIT  REF
    labcorp_style.pdf   NAME  VALUE  FLAG  REF   UNIT

Same analytes and values in both. If the extractor is order-dependent it will
swap unit and reference range on exactly one of them, and test_extract.py fails.

All patient details are invented. Replace these with real de-identified reports
when tuning; the golden assertions live in tests/test_extract.py.
"""

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent

# (name, value, flag, unit, ref_range)
CHEMISTRY = [
    ("GLUCOSE",              "95",   "",  "mg/dL",  "65-99"),
    ("CREATININE",           "1.02", "",  "mg/dL",  "0.76-1.27"),
    ("SODIUM",               "139",  "",  "mmol/L", "134-144"),
    ("POTASSIUM",            "5.6",  "H", "mmol/L", "3.5-5.2"),
    ("FERRITIN",             "18",   "L", "ng/mL",  "24-336"),
    ("VITAMIN B12",          "412",  "",  "pg/mL",  "232-1245"),
    ("TSH",                  "2.14", "",  "uIU/mL", "0.45-4.50"),
    ("HEMOGLOBIN A1C",       "5.4",  "",  "%",      "4.8-5.6"),
    ("CHOLESTEROL, TOTAL",   "184",  "",  "mg/dL",  "100-199"),
    ("HDL CHOLESTEROL",      "58",   "",  "mg/dL",  ">39"),
    ("LDL-CHOLESTEROL",      "104",  "H", "mg/dL",  "0-99"),
    ("TRIGLYCERIDES",        "110",  "",  "mg/dL",  "0-149"),
]

HEMATOLOGY = [
    ("WBC",                  "6.2",  "",  "x10E3/uL", "3.4-10.8"),
    ("RBC",                  "4.91", "",  "x10E6/uL", "4.14-5.80"),
    ("HEMOGLOBIN",           "14.6", "",  "g/dL",     "13.0-17.7"),
    ("HEMATOCRIT",           "43.1", "",  "%",        "37.5-51.0"),
    ("MCHC",                 "33.9", "",  "g/dL",     "31.5-35.7"),
    ("PLATELETS",            "244",  "",  "x10E3/uL", "150-450"),
]

URINE = [
    ("PH",                   "6.0",  "",  "",       "5.0-8.0"),
    ("PROTEIN",              "NEGATIVE", "", "",    "NEGATIVE"),
    ("GLUCOSE",              "NEGATIVE", "", "",    "NEGATIVE"),
]


def _header(c, lab: str, y: int) -> int:
    c.setFont("Helvetica-Bold", 13)
    c.drawString(60, y, lab)
    c.setFont("Helvetica", 8)
    y -= 18
    for line in [
        "Patient: DOE, JANE A                    DOB: 04/12/1996        Sex: F",
        "MRN: 000-11-2233                        Accession: SYNTHETIC-0001",
        "Collected: 03/14/2026                   Reported: 03/16/2026",
        "Ordering Physician: SMITH, ROBERT MD",
    ]:
        c.drawString(60, y, line)
        y -= 11
    return y - 10


def _section(c, title: str, y: int) -> int:
    c.setFont("Helvetica-Bold", 9)
    c.drawString(60, y, title)
    return y - 14


def _rows(c, rows, y, order: str) -> int:
    """order='quest'   -> name  value  flag  unit  ref
       order='labcorp' -> name  value  flag  ref   unit"""
    c.setFont("Helvetica", 8)
    for name, value, flag, unit, ref in rows:
        c.drawString(60, y, name)
        c.drawString(230, y, value)
        c.drawString(280, y, flag)
        if order == "quest":
            c.drawString(310, y, unit)
            c.drawString(400, y, ref)
        else:
            c.drawString(310, y, ref)
            c.drawString(430, y, unit)
        y -= 12
        if y < 80:
            c.showPage()
            y = 740
            c.setFont("Helvetica", 8)
    return y - 8


def build(path: Path, lab: str, order: str) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    y = _header(c, lab, 740)

    c.setFont("Helvetica-Bold", 8)
    if order == "quest":
        c.drawString(60, y, "TEST NAME")
        c.drawString(230, y, "RESULT")
        c.drawString(310, y, "UNITS")
        c.drawString(400, y, "REFERENCE RANGE")
    else:
        c.drawString(60, y, "TEST NAME")
        c.drawString(230, y, "RESULT")
        c.drawString(310, y, "REFERENCE INTERVAL")
        c.drawString(430, y, "UNITS")
    y -= 16

    y = _section(c, "CHEMISTRY (SERUM)", y)
    y = _rows(c, CHEMISTRY, y, order)
    y = _section(c, "HEMATOLOGY (WHOLE BLOOD)", y)
    y = _rows(c, HEMATOLOGY, y, order)
    y = _section(c, "URINALYSIS (URINE)", y)
    y = _rows(c, URINE, y, order)

    c.setFont("Helvetica-Oblique", 7)
    c.drawString(60, y - 10, "This report is synthetic test data. Not a real medical record.")
    c.save()


if __name__ == "__main__":
    build(HERE / "quest_style.pdf", "Quest Diagnostics", "quest")
    build(HERE / "labcorp_style.pdf", "LabCorp", "labcorp")
    for p in sorted(HERE.glob("*.pdf")):
        print(f"  {p.stat().st_size/1024:6.1f} KB  {p.name}")
