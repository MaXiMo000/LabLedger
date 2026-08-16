"""Convert printed lab values to a canonical unit per LOINC code.

This is a hand-audited table, not a computation, and that is deliberate.

`pint` handles dimensional conversion (mg/dL -> g/L) but not mass<->molar
(mg/dL -> umol/L), which is the conversion that actually matters in a lab and
which depends on the analyte's molar mass. Bolting a molar-mass table onto a
units library is more code and more failure surface than writing the factors
out, and an explicit table is auditable line by line.

THE RULE: conversion is deterministic or absent. An unrecognised unit stores
the raw value, leaves the canonical value null, and flags for review. It never
falls back to a factor of 1.0 and it never asks the LLM. A silently wrong unit
conversion is the worst bug this system could ship -- off by 10x and it still
looks completely plausible on a chart.
"""

import re

# loinc_code -> (canonical_unit, {normalized_printed_unit: factor})
# Factors convert INTO the canonical unit. 1.0 means "same unit, different
# spelling". Every non-1.0 factor below is a published conversion.
CANONICAL: dict[str, tuple[str, dict[str, float]]] = {
    # --- chemistry ---
    "2345-7":  ("mg/dL", {"mg/dl": 1.0, "mmol/l": 18.0}),               # Glucose, serum
    "2160-0":  ("mg/dL", {"mg/dl": 1.0, "umol/l": 0.0113}),             # Creatinine
    "3094-0":  ("mg/dL", {"mg/dl": 1.0, "mmol/l": 2.8}),                # BUN
    "2951-2":  ("mmol/L", {"mmol/l": 1.0, "meq/l": 1.0}),               # Sodium
    "2823-3":  ("mmol/L", {"mmol/l": 1.0, "meq/l": 1.0}),               # Potassium
    "2075-0":  ("mmol/L", {"mmol/l": 1.0, "meq/l": 1.0}),               # Chloride
    "2028-9":  ("mmol/L", {"mmol/l": 1.0, "meq/l": 1.0}),               # CO2
    "17861-6": ("mg/dL", {"mg/dl": 1.0, "mmol/l": 4.0}),                # Calcium
    "2777-1":  ("mg/dL", {"mg/dl": 1.0, "mmol/l": 3.1}),                # Phosphate
    "2601-3":  ("mg/dL", {"mg/dl": 1.0, "mmol/l": 2.43, "meq/l": 1.22}),  # Magnesium
    "3084-1":  ("mg/dL", {"mg/dl": 1.0, "umol/l": 0.0168}),             # Uric acid
    "1751-7":  ("g/dL", {"g/dl": 1.0, "g/l": 0.1}),                     # Albumin
    "2885-2":  ("g/dL", {"g/dl": 1.0, "g/l": 0.1}),                     # Total protein
    "1975-2":  ("mg/dL", {"mg/dl": 1.0, "umol/l": 0.0585}),             # Bilirubin, total

    # --- enzymes (catalytic activity: U/L and IU/L are the same thing) ---
    "1742-6":  ("U/L", {"u/l": 1.0, "iu/l": 1.0}),                      # ALT
    "1920-8":  ("U/L", {"u/l": 1.0, "iu/l": 1.0}),                      # AST
    "6768-6":  ("U/L", {"u/l": 1.0, "iu/l": 1.0}),                      # Alk phos
    "2532-0":  ("U/L", {"u/l": 1.0, "iu/l": 1.0}),                      # LDH
    "14805-6": ("U/L", {"u/l": 1.0, "iu/l": 1.0}),                      # LDH (2.82)
    "2157-6":  ("U/L", {"u/l": 1.0, "iu/l": 1.0}),                      # CK
    "3040-3":  ("U/L", {"u/l": 1.0, "iu/l": 1.0}),                      # Lipase
    "1798-8":  ("U/L", {"u/l": 1.0, "iu/l": 1.0}),                      # Amylase

    # --- lipids ---
    "2093-3":  ("mg/dL", {"mg/dl": 1.0, "mmol/l": 38.67}),              # Cholesterol
    "2085-9":  ("mg/dL", {"mg/dl": 1.0, "mmol/l": 38.67}),              # HDL
    "2089-1":  ("mg/dL", {"mg/dl": 1.0, "mmol/l": 38.67}),              # LDL
    "13457-7": ("mg/dL", {"mg/dl": 1.0, "mmol/l": 38.67}),              # LDL calc
    "43396-1": ("mg/dL", {"mg/dl": 1.0, "mmol/l": 38.67}),              # non-HDL
    "2571-8":  ("mg/dL", {"mg/dl": 1.0, "mmol/l": 88.57}),              # Triglycerides

    # --- haematology ---
    "718-7":   ("g/dL", {"g/dl": 1.0, "g/l": 0.1, "mmol/l": 1.61}),     # Hemoglobin
    "4544-3":  ("%", {"%": 1.0, "l/l": 100.0, "fraction": 100.0}),      # Hematocrit
    "20570-8": ("%", {"%": 1.0, "l/l": 100.0}),
    "31100-1": ("%", {"%": 1.0, "l/l": 100.0}),
    "786-4":   ("g/dL", {"g/dl": 1.0, "g/l": 0.1}),                     # MCHC
    "787-2":   ("fL", {"fl": 1.0, "um3": 1.0}),                         # MCV
    "788-0":   ("%", {"%": 1.0}),                                       # RDW
    "6690-2":  ("x10E3/uL", {"x10e3/ul": 1.0, "k/ul": 1.0, "10*3/ul": 1.0,
                             "10^3/ul": 1.0, "/ul": 0.001, "x10e9/l": 1.0}),  # WBC
    "789-8":   ("x10E6/uL", {"x10e6/ul": 1.0, "m/ul": 1.0, "10*6/ul": 1.0,
                             "10^6/ul": 1.0, "x10e12/l": 1.0}),          # RBC
    "777-3":   ("x10E3/uL", {"x10e3/ul": 1.0, "k/ul": 1.0, "10*3/ul": 1.0,
                             "10^3/ul": 1.0, "x10e9/l": 1.0}),           # Platelets

    # --- endocrine / vitamins ---
    "3016-3":  ("uIU/mL", {"uiu/ml": 1.0, "miu/l": 1.0, "uu/ml": 1.0}),  # TSH
    "3024-7":  ("ng/dL", {"ng/dl": 1.0, "pmol/l": 0.0777}),             # Free T4
    "3051-0":  ("pg/mL", {"pg/ml": 1.0, "pmol/l": 0.651}),              # Free T3
    "2276-4":  ("ng/mL", {"ng/ml": 1.0, "ug/l": 1.0, "pmol/l": 0.445}),  # Ferritin
    "2132-9":  ("pg/mL", {"pg/ml": 1.0, "ng/l": 1.0, "pmol/l": 1.355}),  # Vitamin B12
    "2284-8":  ("ng/mL", {"ng/ml": 1.0, "ug/l": 1.0, "nmol/l": 0.441}),  # Folate
    "1989-3":  ("ng/mL", {"ng/ml": 1.0, "ug/l": 1.0, "nmol/l": 0.4}),   # Vitamin D 25-OH
    "62292-8": ("ng/mL", {"ng/ml": 1.0, "nmol/l": 0.4}),
    "2498-4":  ("ug/dL", {"ug/dl": 1.0, "mcg/dl": 1.0, "umol/l": 5.587}),  # Iron
    "2500-7":  ("ug/dL", {"ug/dl": 1.0, "mcg/dl": 1.0, "umol/l": 5.587}),  # TIBC
    "41995-2": ("%", {"%": 1.0}),                                       # HbA1c
    "4548-4":  ("%", {"%": 1.0, "mmol/mol": 0.0915}),                   # HbA1c/Hb total

    # --- cardiac / inflammatory / other ---
    "1988-5":  ("mg/L", {"mg/l": 1.0, "mg/dl": 10.0, "nmol/l": 0.105}),  # CRP
    "10839-9": ("ng/mL", {"ng/ml": 1.0, "ug/l": 1.0, "ng/l": 0.001}),   # Troponin I
    "2857-1":  ("ng/mL", {"ng/ml": 1.0, "ug/l": 1.0}),                  # PSA
    "6301-6":  ("{INR}", {"": 1.0, "ratio": 1.0, "{inr}": 1.0}),        # INR
    "5902-2":  ("s", {"s": 1.0, "sec": 1.0, "seconds": 1.0}),           # Prothrombin time
    "4537-7":  ("mm/h", {"mm/h": 1.0, "mm/hr": 1.0}),                   # ESR
    "5803-2":  ("[pH]", {"": 1.0, "ph": 1.0, "[ph]": 1.0}),             # Urine pH
}

_UNIT_CLEAN = re.compile(r"[\s ]+")


def normalize_unit(unit: str | None) -> str:
    """Fold a printed unit to its lookup key.

    Micro is printed as u, µ (micro sign) and μ (Greek mu) interchangeably;
    they must all collapse to the same key or a real conversion gets missed.
    """
    if unit is None:
        return ""
    u = _UNIT_CLEAN.sub("", unit).strip().lower()
    u = u.replace("µ", "u").replace("μ", "u").replace("mc", "u") if u.startswith(("µ", "μ", "mc")) else u
    return u.replace("µ", "u").replace("μ", "u")


def to_canonical(
    loinc_code: str | None, value: float | None, unit: str | None
) -> tuple[float | None, str | None, float | None]:
    """Convert a value to its canonical unit.

    Returns (canonical_value, canonical_unit, factor), or (None, None, None)
    when the analyte or the unit is not in the table. Never guesses.
    """
    if loinc_code is None or value is None:
        return None, None, None
    entry = CANONICAL.get(loinc_code)
    if entry is None:
        return None, None, None
    canonical_unit, factors = entry
    factor = factors.get(normalize_unit(unit))
    if factor is None:
        # Unknown unit for a known analyte. Do NOT assume 1.0: labs that print
        # an unexpected unit are usually reporting something genuinely
        # different, and assuming parity is how a 10x error ships.
        return None, None, None
    return value * factor, canonical_unit, factor


# --------------------------------------------------------------------------
# value parsing
# --------------------------------------------------------------------------

_CENSORED = re.compile(r"^\s*([<>]=?|[≤≥])\s*([\d.,]+)\s*$")
_NUMERIC = re.compile(r"^\s*-?[\d,]+\.?\d*\s*$")


def parse_value(raw: str) -> tuple[float | None, str | None, str | None]:
    """Split a printed result into (number, operator, text).

    Labs censor values at an assay's limits ("<0.5", ">1000"). The operator is
    kept separately so a censored point can be charted as a bound rather than
    silently treated as an exact measurement.
    """
    if raw is None:
        return None, None, None
    s = raw.strip()
    if m := _CENSORED.match(s):
        op = m.group(1).replace("≤", "<=").replace("≥", ">=")
        try:
            return float(m.group(2).replace(",", "")), op, None
        except ValueError:
            return None, None, s
    if _NUMERIC.match(s):
        try:
            return float(s.replace(",", "")), None, None
        except ValueError:
            return None, None, s
    return None, None, s.upper() or None
