"""Curated LOINC codes for the tests real reports print most.

Run against real Quest and LabCorp sample reports, the fuzzy cascade alone
left most of a basic metabolic panel, a CBC and a lipid panel unmapped --
and got some wrong in ways worse than unmapped: creatinine to the *urine*
code, "Neutrophils 63.4 %" to the absolute-count code, "WEIGHT" to the
weight of a kidney stone. Real lab systems solve this with a curated
compendium; this is a small one.

Every code here was looked up in the seeded LOINC table, not recalled.
Where one printed name means two different tests, the unit decides: a
differential printed in % is a fraction of leukocytes, in cells/uL a count.
With no unit to decide by, the row falls through to the cascade rather
than being guessed.
"""
from __future__ import annotations

import re

# name -> code, or name -> {"percent": code, "count": code}
TABLE: dict[str, str | dict[str, str]] = {
    # basic / comprehensive metabolic panel
    "GLUCOSE": "2345-7",
    "UREA NITROGEN": "3094-0", "UREA NITROGEN BUN": "3094-0", "BUN": "3094-0",
    "CREATININE": "2160-0",
    "EGFR": "98979-8",
    "BUN/Creatinine Ratio": "3097-3",
    "SODIUM": "2951-2", "POTASSIUM": "2823-3", "CHLORIDE": "2075-0",
    "CARBON DIOXIDE": "2028-9", "CO2": "2028-9",
    "CALCIUM": "17861-6",
    "PROTEIN TOTAL": "2885-2", "TOTAL PROTEIN": "2885-2",
    "ALBUMIN": "1751-7", "GLOBULIN": "10834-0",
    "Albumin/Globulin Ratio": "1759-0", "A/G Ratio": "1759-0",
    "BILIRUBIN TOTAL": "1975-2", "TOTAL BILIRUBIN": "1975-2",
    "ALKALINE PHOSPHATASE": "6768-6", "AST": "1920-8", "ALT": "1742-6",
    # lipids
    "TOTAL CHOLESTEROL": "2093-3", "CHOLESTEROL TOTAL": "2093-3", "CHOLESTEROL": "2093-3",
    "HDL CHOLESTEROL": "2085-9", "HDL": "2085-9",
    "LDL CHOLESTEROL CALCULATED": "13457-7", "LDL CHOLESTEROL": "13457-7",
    "LDL-CHOLESTEROL": "13457-7",
    "Non-HDL Cholesterol": "43396-1",
    "Cholesterol/HDL Ratio": "9830-1", "Cholesterol/HDL Ratio (Calculated)": "9830-1",
    "TRIGLYCERIDES": "2571-8",
    # glycemic
    "HEMOGLOBIN A1C": {"percent": "4548-4"}, "A1C": {"percent": "4548-4"},
    "HBA1C": {"percent": "4548-4"},
    # hormones
    "TESTOSTERONE TOTAL MS": "2986-8", "TESTOSTERONE TOTAL": "2986-8",
    "ESTRADIOL": "2243-4", "FSH": "15067-2", "LH": "10501-5",
    # CBC
    "WHITE BLOOD CELL COUNT": "6690-2", "WBC": "6690-2",
    "RED BLOOD CELL COUNT": "789-8", "RBC": "789-8",
    "HEMOGLOBIN": "718-7", "HGB": "718-7", "HEMATOCRIT": "4544-3", "HCT": "4544-3",
    "MCV": "787-2", "MCH": "785-6", "MCHC": "786-4", "RDW": "788-0",
    "PLATELET COUNT": "777-3", "PLATELETS": "777-3", "PLT": "777-3", "MPV": "32623-1",
    # differential: % of leukocytes, or an absolute count
    "NEUTROPHILS": {"percent": "770-8", "count": "751-8"},
    "LYMPHOCYTES": {"percent": "736-9", "count": "731-0"},
    "MONOCYTES": {"percent": "5905-5", "count": "742-7"},
    "EOSINOPHILS": {"percent": "713-8", "count": "711-2"},
    "BASOPHILS": {"percent": "706-2", "count": "704-7"},
    "BAND NEUTROPHILS": {"percent": "764-1", "count": "26507-4"},
    "METAMYELOCYTES": {"percent": "740-1", "count": "30433-7"},
    "MYELOCYTES": {"percent": "749-2", "count": "30446-9"},
    "PROMYELOCYTES": {"percent": "26524-9", "count": "26523-1"},
    "BLASTS": {"percent": "709-6", "count": "30376-8"},
    "ABSOLUTE NEUTROPHILS": {"count": "751-8"},
    "ABSOLUTE LYMPHOCYTES": {"count": "731-0"},
    "ABSOLUTE MONOCYTES": {"count": "742-7"},
    "ABSOLUTE EOSINOPHILS": {"count": "711-2"},
    "ABSOLUTE BASOPHILS": {"count": "704-7"},
    "ABSOLUTE BAND NEUTROPHILS": {"count": "26507-4"},
    "ABSOLUTE METAMYELOCYTES": {"count": "30433-7"},
    "ABSOLUTE MYELOCYTES": {"count": "30446-9"},
    "ABSOLUTE PROMYELOCYTES": {"count": "26523-1"},
    "ABSOLUTE BLASTS": {"count": "30376-8"},
    "ABSOLUTE NUCLEATED RBC": {"count": "30392-5"},
    "NUCLEATED RBC": "58413-6",
    # infectious disease
    "SARS-CoV-2, NAA": "94309-2", "SARS-CoV-2 RNA": "94309-2",
    # allergy (LabCorp prints the ImmunoCAP code first: "E001-IgE Cat Dander")
    "E001-IgE Cat Dander": "6833-8", "CAT DANDER IGE": "6833-8",
    "E005-IgE Dog Dander": "6098-8", "DOG DANDER IGE": "6098-8",
    "E003-IgE Horse Dander": "6143-2", "HORSE DANDER IGE": "6143-2",
    "E094-IgE Fel d 1": "64975-6", "E220-IgE Fel d 2": "19732-7", "E228-IgE Fel d 4": "64976-4",
    "E101-IgE Can f 1": "58773-3", "E102-IgE Can f 2": "58772-5", "E221-IgE Can f 3": "19738-4",
    "E227-IgE Equ c 1": "64974-9",
    # Vital signs: not in the lab LOINC table this app seeds, so there is no
    # right code to give -- and the fuzzy stage gave "WEIGHT" the weight of a
    # kidney stone. "" means: never auto-map, send to review.
    "WEIGHT": "", "HEIGHT FEET": "", "HEIGHT INCHES": "", "HEIGHT": "",
    "SYSTOLIC BLOOD PRESSURE": "", "DIASTOLIC BLOOD PRESSURE": "",
    "CALCULATED BMI": "", "BMI": "",
}

_COUNT_UNIT = re.compile(r"/\s*u?L\b|/\s*mm3|cells|thousand|million|10\*|x\s*10|#", re.IGNORECASE)


def unit_kind(unit: str | None) -> str | None:
    if not unit:
        return None
    if "%" in unit:
        return "percent"
    return "count" if _COUNT_UNIT.search(unit) else None
