# How LabLedger works

A walkthrough of the whole system, from dropping a PDF to seeing a trend line.
[PLAN.md](PLAN.md) has the original design rationale; this is what actually got
built and why each piece behaves the way it does.

---

## The problem, precisely

You have blood work from Quest, LabCorp, and a hospital portal. All three
measured ferritin. They printed it as:

```
FERRITIN, SERUM      18   ng/mL     24-336
Ferritin (S)         22   ng/mL     11-307
FERRTN SER           40   µg/L      24-336
```

Three names, two units, three reference ranges. To chart your ferritin over
five years, something has to decide those are the same test, put them in the
same unit, and know which range applies to which point. That decision is the
entire product.

Getting it wrong is not a cosmetic bug. A ferritin of 40 µg/L charted as if it
were 40 ng/mL happens to be correct (they are the same), but a creatinine of
88 µmol/L charted as 88 mg/dL is off by a factor of 88 and looks completely
plausible. So the architecture is built around one rule: **be certain or say
you are not.**

---

## The journey of one PDF

```
  you drop a file
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ 1  UPLOAD          POST /api/documents                       │
│    magic bytes checked, SHA-256 dedupe, AES-256-GCM          │
│    encrypted, stored, job queued.  Returns 202 immediately.  │
└──────────────────────────────────────────────────────────────┘
        │  (arq job on Redis — the API is free again)
        ▼
┌──────────────────────────────────────────────────────────────┐
│ 2  EXTRACT         pipeline/extract.py                       │
│    tables first, then text layer + token classifier.         │
│    Out: raw rows — name, value, unit, range, specimen, page  │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ 3  MAP             pipeline/mapping.py → a LOINC code        │
│    five stages, cheapest first, LLM last                     │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ 4  CONVERT         pipeline/units.py, pipeline/ranges.py     │
│    canonical unit, reference interval, high/low flag         │
└──────────────────────────────────────────────────────────────┘
        │
        ├── confident ──────────────► charted
        └── not confident ──────────► review queue → you confirm
                                             │
                                             └──► writes an alias,
                                                  so next time it is
                                                  stage 0 and instant
```

---

## 1. Upload

`POST /api/documents` does four things before it will accept a file.

**Magic bytes, not the filename.** A file called `report.pdf` with a
`application/pdf` content type can still be a GIF — both of those are supplied
by whoever is uploading. Only the leading `%PDF-` is evidence.

**Size and page caps.** 25 MB, 100 pages. PDF parsers are a known denial-of-
service surface.

**SHA-256 dedupe, per user.** Upload the same file twice and you get the same
document back rather than a duplicate set of results.

**Encrypt before storing.** The PDF bytes are encrypted with AES-256-GCM before
they touch the database, as is the extracted text.

Then it returns `202 Accepted` and hands the work to a background queue. It does
not process the file in the request, because extraction can take tens of seconds
on a large scan and a crash mid-parse must not lose your upload.

---

## 2. Extraction — reading the page

Two strategies, cheapest first.

1. **Ruled tables** via `pdfplumber.extract_tables()`. Most digital lab PDFs
   have real table structure.
2. **Text layer + token classifier** for borderless layouts.

The second one is where the interesting decision is. Column order is *not*
stable across labs:

```
Quest     NAME   VALUE   FLAG   UNIT    REF
LabCorp   NAME   VALUE   FLAG   REF     UNIT
```

A parser that reads by position silently swaps the unit and the reference range
on exactly one of those — and produces a chart that looks perfectly reasonable
and is wrong. So each token is classified **by its own shape** instead: a range
looks like `24-336` or `>39`, a flag is a lone `H`/`L`, a number is a number, a
unit has a slash or a known suffix. Order cannot matter.

Two fixtures in `backend/tests/fixtures/` carry identical analytes in opposite
column orders, and a test asserts the two parses come out byte-identical.

**Specimen sections are tracked.** A header like `CHEMISTRY (SERUM)` scopes
every row beneath it. This matters enormously later — serum glucose and urine
glucose are different LOINC codes, and losing the specimen would merge two valid
trends into one meaningless one.

**Collection date beats report date.** A trend plotted on the day the lab
printed the page rather than the day blood was drawn is subtly wrong.

---

## 3. Mapping — the part that is actually hard

Each raw name has to become a LOINC code. LOINC is the international standard
for identifying lab tests; the local table holds **58,252** of them.

Five stages run in order. A row stops at the first one that holds it.

| # | Stage | What it does | Confidence | Accepted? |
|---|---|---|---|---|
| 0 | **Alias** | Looks up names you have already confirmed | 1.00 | yes, instantly |
| 1 | **Exact** | Exact match on a code's own primary names | 0.95 | yes |
| 2 | **Related** | Match on an *associated* name, only if specimen and unit both agree | 0.85 | **no — you confirm** |
| 3 | **Fuzzy** | Specimen narrows the field, then similarity ranks it | ~0.85 | yes, if it clears the margin |
| 4 | **Model** | Gemini picks from a numbered list | 0.85 | **no — you confirm** |
| — | *unresolved* | nothing matched | 0 | goes to you with a search box |

### Why stage 2 exists, and why it never decides on its own

LOINC ships a field of "related names". It is tempting to treat a hit there as
identity. That produced confidently wrong answers:

- `HCT` is listed under **Reticulocyte production index** — because that index
  is *calculated from* the hematocrit. Meanwhile the actual Hematocrit code does
  not carry `HCT` as a name at all.
- `PHOS` is listed under **Alkaline phosphatase**, not just Phosphate.

Both mapped at confidence 0.95 and looked identical to a correct answer. So the
rule now holds everywhere: **a related name is evidence for considering a code,
never for accepting one.** It widens the candidate pool at every stage, and at
no stage can it auto-accept.

### Why the model cannot invent a code

Stage 4 never asks Gemini to *produce* a LOINC code. It hands over a numbered
list that stage 3 built from the LOINC table and asks for one index, or `NONE`:

```
Observed test name: HGB
Unit as printed:    g/dL
Specimen:           WHOLE BLOOD

Candidates:
0. MCHC [Entitic Mass/volume] in Red Blood Cells
1. Hemoglobin [Mass/volume] in Blood
...
Reply with ONLY the number of the best match, or NONE.
```

Anything that is not an index into that list is discarded. The failure mode
degrades from "invented a code" to "picked the wrong option" — which the
confidence floor and the review queue already handle.

**What is sent to Google:** the test name, the unit, the specimen. Never your
name, your date of birth, the date, or the measured value. A test asserts this.

**Which model, and why:** `gemini-3.5-flash`, chosen by measurement rather than
assumption. On 40 real lab abbreviations, `gemini-2.5-pro` was *not* more
accurate and was 2.3× slower — and flash was the only one that correctly said
`NONE` when the right code was not on the list. Abstaining is the skill that
matters here, not raw capability. Rerun it yourself with
`scripts/bench_llm_hard.py`.

### The critical-analyte override

```python
CRITICAL_COMPONENTS = {"potassium", "sodium", "troponin i.cardiac", "inr",
                       "glucose", "creatinine", "hemoglobin", ...}
```

If the resolved test is on this list and the deciding stage was probabilistic
(fuzzy or model), it goes to review **no matter what the confidence says**.
Confidence is a statistical instrument; these failures are categorical. A wrong
potassium changes dosing, so the cost of asking you to click once is nothing
against the cost of being wrong.

---

## 4. Units and reference ranges

### Conversion is a table, not a computation

`pint` and similar libraries do dimensional conversion (mg/dL → g/L) but not
mass↔molar (mg/dL → µmol/L), which is the conversion that actually matters in a
lab and depends on the analyte's molar mass. So `pipeline/units.py` is a
hand-audited table keyed by LOINC code.

**The rule: deterministic or absent.** An unrecognised unit stores the raw value,
leaves the canonical value null, and flags for review. It never assumes a factor
of 1.0 and it never asks the model. A silently wrong conversion is off by 10×
and still looks plausible — it is the one bug that could ship unnoticed.

### The lab's own range wins

Priority: **the range printed on that PDF** → a built-in demographic table →
nothing. Reference intervals are instrument- and assay-specific; LabCorp's
ferritin range legitimately differs from Quest's. The printed range is the one
that lab's pathologist signed off on. `ref_source` records which applied, so the
interface can tell you where a band came from.

Censored values keep their operator: `<0.5` is a bound, not a measurement of 0.5.

---

## 5. The review queue — how the system gets smarter

Everything uncertain lands in `/app/review`. Each row shows the printed line
verbatim, why it is uncertain, and ranked candidates with the reason each
surfaced. You can also search all 58,252 codes — deliberately including the
40,074 that the cascade will never auto-match, because a backstop that cannot
reach a valid code is not a backstop.

Confirming writes an **alias**: `normalized name + specimen → LOINC code`,
scoped to your account. The next time that name appears it resolves at stage 0 —
a dictionary lookup, confidence 1.00, no fuzzy matching and no model call.

Measured on the fixtures: first pass was `exact 11 · related 6 · llm 4`. After
working the queue and re-processing, `exact 11 · alias 7 · llm 3`. The model's
share falls every time you use the app. That is the whole design.

---

## 6. Trends

`GET /api/observations?loinc=2276-4` returns a point only if it has a resolved
code **and** a successful unit conversion **and** is not awaiting review.

Everything else comes back in `excluded` with a reason:

```json
{ "raw_value": "NEGATIVE", "reason": "non-numeric result 'NEGATIVE'" }
```

A series that quietly drops what it could not handle is worse than one that
says so. Silence is how a missing point becomes an invisible error.

---

## Security

| Concern | How |
|---|---|
| Access token | 15 min, held in a **JS closure** — never localStorage, never a cookie |
| Session restore | httpOnly + SameSite refresh cookie, rotated on every use |
| Revocation | the token carries `sid`; `deps.current_user` resolves that `Session` **every request** |
| Idle | 30 min server-side (configurable); the client locks a minute earlier |
| Second factor | TOTP, RFC 6238, secret encrypted at rest |
| Cross-app replay | tokens carry `aud=labledger`; a Quiz-App token is rejected here |
| Ownership | enforced in `repo.py` at the query layer, never per-handler |
| Wrong owner | **404, not 403** — never confirm someone else's id exists |
| At rest | PDF bytes, extracted text, DOB and sex, TOTP secret — AES-256-GCM |
| Logs | ids only. Never filenames: `oncology_panel.pdf` is a diagnosis |
| Brute force | 5 registrations/hour, 10 logins/minute, 30 uploads/hour |

The access token living in a closure is why **"Open PDF" has to fetch the file
and hand the browser a blob** rather than being a plain link — a browser
navigation carries no Authorization header, so the API correctly refuses it.

**Sessions are per-device.** A `Session` row holds the rotating refresh hash,
so signing in on a second device no longer ends the first — and because the
access token names its session, ending one is felt on that device's next
request rather than up to fifteen minutes later. Reach is a separate question,
answered per request from the `Access` grant, so revoking a grant removes
reach without signing the holder out of their other patients.

---

## Running it

```bash
npm run api      # FastAPI on :8000
```

```bash
npm run worker   # arq — does the extraction and mapping
```

```bash
npm run dev      # Vite on :5173, proxies /api to :8000
```

Both the API and the worker must be running for uploads to process. The API
stays up without the worker — files are stored safely and can be replayed with
**Re-read** on the Reports screen.

Sample reports to test with:

```bash
cd backend && .venv/bin/python scripts/make_sample_reports.py
```

Six reports, three labs, three years, one fictional person. Ferritin falls from
96 to 18 ng/mL across them, and one lab prints it as `FERRTN SER` in µg/L — so
you can watch names and units reconcile onto a single line.
