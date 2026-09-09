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

## Measured coverage (this corpus, current extractor)

Run `pytest tests/test_real_world.py -v` to reproduce. As of the last
extraction-accuracy pass: 85 rows extracted across the 6 files, 100% with a
value, 74% with a flag, 68% with a unit, 40% with a reference range. The
reference-range gap is mostly `labcorp_607016` (a "Class 0"/"Class III"
severity label, not a number, so there is nothing to extract) and a few
computed/ratio rows Quest prints without republishing a range. See
`ARCHITECTURE.md` for what's still a known gap rather than a bug.
