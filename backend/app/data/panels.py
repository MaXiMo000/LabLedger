"""Which panel a lab prints each analyte under.

Presentation, not interpretation. Grouping a flat alphabetical list into the
panels a report is actually ordered and printed in — a blood count, a metabolic
panel, a lipid profile — is how a reader scans one, and it says nothing about
anyone's results that the flat list did not already say.

Two rules this table follows, for the same reason the rest of `pipeline/`
follows them:

**Membership is one-to-one.** An analyte clinically belongs to several panels —
glucose is on a metabolic panel and central to diabetes monitoring — but a
result that appears under two headings has been *duplicated*, and a reader
counting their own out-of-range values would count it twice. Each code sits in
exactly one group; the clinical overlap is not lost, it is simply not expressed
by this particular list.

**An unmapped code is never hidden.** Anything absent here falls to `OTHER`,
which is a visible group, not a filter. A result the map has not heard of is
still the reader's result, and the failure mode of a taxonomy that silently
drops its leftovers is the one worth spending a catch-all to avoid.

The order below is the conventional order these appear on a report and is used
as a tiebreak only; the interface sorts groups by what needs attention first.
"""

# key, label, member LOINC codes
PANELS: list[tuple[str, str, tuple[str, ...]]] = [
    ("cbc", "Complete blood count", (
        "718-7",     # Haemoglobin
        "4544-3",    # Haematocrit
        "20570-8",   # Haematocrit (alternate)
        "31100-1",   # Haematocrit (alternate)
        "789-8",     # Red cell count
        "6690-2",    # White cell count
        "777-3",     # Platelets
        "787-2",     # MCV
        "786-4",     # MCHC
        "788-0",     # RDW
    )),
    ("metabolic", "Metabolic panel", (
        "2951-2",    # Sodium
        "2823-3",    # Potassium
        "2075-0",    # Chloride
        "2028-9",    # CO2 / bicarbonate
        "3094-0",    # BUN
        "2160-0",    # Creatinine
        "2345-7",    # Glucose
        "17861-6",   # Calcium
        "2777-1",    # Phosphate
        "2601-3",    # Magnesium
        "3084-1",    # Uric acid
    )),
    # Amylase and lipase are pancreatic rather than hepatic. They sit here
    # because the label says so, and because a two-analyte "Pancreas" heading
    # is a worse read than an honest joint one.
    ("liver", "Liver & pancreas", (
        "1742-6",    # ALT
        "1920-8",    # AST
        "6768-6",    # Alkaline phosphatase
        "1975-2",    # Bilirubin, total
        "1751-7",    # Albumin
        "2885-2",    # Total protein
        "2532-0",    # LDH
        "14805-6",   # LDH (alternate)
        "3040-3",    # Lipase
        "1798-8",    # Amylase
    )),
    ("lipids", "Lipids", (
        "2093-3",    # Total cholesterol
        "2085-9",    # HDL
        "2089-1",    # LDL
        "13457-7",   # LDL, calculated
        "43396-1",   # non-HDL
        "2571-8",    # Triglycerides
    )),
    ("thyroid", "Thyroid", (
        "3016-3",    # TSH
        "3024-7",    # Free T4
        "3051-0",    # Free T3
    )),
    ("diabetes", "Diabetes", (
        "41995-2",   # HbA1c
        "4548-4",    # HbA1c / total Hb
    )),
    ("iron", "Iron studies", (
        "2276-4",    # Ferritin
        "2498-4",    # Iron
        "2500-7",    # TIBC
    )),
    ("vitamins", "Vitamins", (
        "2132-9",    # Vitamin B12
        "2284-8",    # Folate
        "1989-3",    # Vitamin D, 25-OH
        "62292-8",   # Vitamin D, 25-OH (alternate)
    )),
    ("cardiac", "Cardiac & inflammatory", (
        "1988-5",    # CRP
        "10839-9",   # Troponin I
        "4537-7",    # ESR
    )),
    ("coagulation", "Coagulation", (
        "6301-6",    # INR
        "5902-2",    # Prothrombin time
    )),
    ("urinalysis", "Urinalysis", (
        "5792-7",    # Glucose, urine, test strip
        "2887-8",    # Protein, urine, presence
        "5803-2",    # pH, urine, test strip
    )),
]

# Codes deliberately absent from `units.CANONICAL`.
#
# A dipstick reports Negative / Trace / 1+, which is not a quantity: there is
# no unit and nothing to convert, so `units.CANONICAL` is the wrong place for
# them and adding entries there to satisfy a test would be inventing a
# conversion that does not exist. They are still results and still need a
# heading, so they are listed above and excused here.
#
# The exemption is kept explicit and short on purpose — the test it relaxes is
# the only thing standing between a mistyped LOINC and an analyte silently
# filed under the wrong panel forever. Every code here was read off real data
# rather than recalled.
UNITLESS: frozenset[str] = frozenset({
    "5792-7",
    "2887-8",
})

OTHER_KEY = "other"
OTHER_LABEL = "Other results"

# Built once at import. A code listed under two panels is a mistake in the
# table above rather than something to resolve at lookup time, so it is caught
# here instead of silently taking whichever entry happened to be last.
_BY_CODE: dict[str, tuple[str, str]] = {}
for _key, _label, _codes in PANELS:
    for _code in _codes:
        if _code in _BY_CODE:
            raise ValueError(
                f"LOINC {_code} is in both {_BY_CODE[_code][0]} and {_key}; "
                "membership must be one-to-one or the result renders twice"
            )
        _BY_CODE[_code] = (_key, _label)


def panel_for(loinc_code: str | None) -> tuple[str, str]:
    """Return the (key, label) this analyte is printed under, or the catch-all."""
    return _BY_CODE.get(loinc_code or "", (OTHER_KEY, OTHER_LABEL))
