# Real-world fixture corpus

Six PDFs, downloaded directly from the labs' own public sample-report pages.
All patient data on them is fictional ("TEST, PATIENT" / "DOE, JANE" /
"SAMPLE REPORT") -- these are the labs' own education/demo material, not real
PHI, published specifically so software vendors can test against real layout.

| File | Source | Content |
|---|---|---|
| `quest_lipid_panel_sample.pdf` | questdiagnostics.com, `DRPwS_sample_report.pdf` | lipid panel |
| `quest_cmp_cbc_panel_sample.pdf` | Quest patient-portal sample (pmd-static), report #3878 | full metabolic panel + CBC + hormones, 55 result rows |
| `labcorp_607016_allergy_ige_sample.pdf` | files.labcorp.com sample reports, #607016 | IgE allergy component panel |
| `labcorp_139900_covid_naa_sample.pdf` | files.labcorp.com sample reports, #139900 | SARS-CoV-2 NAA |
| `labcorp_001453_hba1c_sample.pdf` | files.labcorp.com sample reports, #001453 | Hemoglobin A1c, two draws |
| `labcorp_164055_sample.pdf` | files.labcorp.com sample reports, #164055 | specimen/barcode page -- its font encodes glyphs pdfplumber can't map back to text (`(cid:N)` runs), so this one is kept specifically as a documented failure case, not a working example |

## Why these six

They were picked for layout diversity, not for being easy: single-space
column gaps that only separate on `layout=True`'s x-offset padding, a
below-detection-limit result ("<0.7") printed in the same shape a reference
range uses, flag words glued to their value or their range with one space
instead of a column gap, and a "Class N" severity column with no numeric
range at all. Real formatting is messier than the two synthetic fixtures in
`tests/test_extract.py` -- that's the point of having both.

## Measured accuracy (this corpus, current extractor)

`expected.json` holds every row of the five readable reports -- 88 rows --
checked by hand against the PDF text: name, value, unit, reference range,
flag. `test_every_value_unit_and_range_matches_the_printed_report` requires
an exact match, and `tests/test_mapping.py::REAL_ROWS` pins the LOINC code
for the rows most likely to be mapped wrong.

| | before the hand check | now |
|---|---|---|
| rows extracted | 85 | 88 (+ the "Component / Result / Component / Result" vitals pairs) |
| values that are wrong | **4** | 0 |
| rows with a unit | 58 (68%) | 75 (85%) |
| rows with a reference range | 34 (40%) | 48 (55%) |
| rows mapped to a LOINC code, offline | 30, **at least 8 wrong** | 81, **0 wrong** (7 left for review) |

The four wrong values were the reason to stop counting rows: a positive
SARS-CoV-2 result ("Detected Abnormal") and an indeterminate one read as
**NOT DETECTED** -- the reference column taken for the value -- and a
BUN/creatinine ratio ("NOT APPLICABLE NORMAL") read as its lab-site code
"01". The wrong mappings: creatinine to the *urine* code, five differential
percentages to absolute-count codes, HbA1c in % to a mass concentration,
nucleated RBCs to spinal fluid, and body weight to the weight of a kidney
stone. `app/data/common_tests.py` -- every code looked up in the seeded
LOINC table -- fixes those; vital signs, which that table doesn't cover,
go to review instead of being mapped to anything.

The remaining range gap is mostly `labcorp_607016` (a "Class 0"/"Class III"
severity grade, not a number) and Quest differential rows that print no
range.
