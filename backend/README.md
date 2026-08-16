# LabLedger backend

FastAPI + MongoDB (Beanie) + Redis (arq). Design rationale lives in [../PLAN.md](../PLAN.md).

## Run

Two terminals, both from `backend/`:

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
.venv/bin/arq app.worker.WorkerSettings
```

Interactive API docs at http://localhost:8000/docs. The API stays up without the
worker: uploads are stored and can be replayed with `POST /api/documents/{id}/reprocess`.

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/ruff check app scripts tests
```

## Pipeline

```
PDF ─┬─ extract.py ──► raw rows (name, value, unit, ref range, specimen)
     │                 pdfplumber tables → text layer + token classifier
     │
     ├─ mapping.py ──► LOINC code
     │   stage 0  alias            confirmed lookup           conf 1.00  auto
     │   stage 1  exact            primary LOINC name         conf 0.95  auto
     │   stage 1b related_corrob.  related name + specimen + unit  0.85  REVIEW
     │   stage 2  narrowing        specimen → SYSTEM axis     (not a decision)
     │   stage 3  narrowed_fuzzy   rapidfuzz over narrowed set      f(s)  auto
     │   stage 4  llm.py           multiple choice, never generation 0.85 REVIEW
     │            unmapped         → review queue
     │
     ├─ units.py ───► canonical value (audited table; unknown unit → None)
     ├─ ranges.py ──► printed range > builtin demographic > none, + flag
     └─ review.py ──► human confirms → writes Alias → next time it is stage 0
```

Three invariants the tests pin:

1. **Narrowing never excludes the correct answer.** Every filter widens rather
   than returning empty.
2. **A related name is never identity.** `RELATEDNAMES2` is associative — "HCT"
   is a related name of *Reticulocyte production index*. It widens the candidate
   pool but can never auto-accept, at any stage.
3. **Unit conversion is deterministic or absent.** An unrecognised unit stores
   the raw value and flags for review. It never assumes a factor of 1.0.

## API

```
POST   /api/auth/register              5/hour     POST /api/auth/login   10/min
POST   /api/auth/refresh    rotates    POST /api/auth/logout
GET    /api/auth/google     GET /api/auth/google/callback
GET    /api/auth/me         PATCH /api/auth/me     (dob, sex — encrypted at rest)

POST   /api/documents                  30/hour, multipart → 202
GET    /api/documents       GET /api/documents/{id}    GET /api/documents/{id}/file
POST   /api/documents/{id}/reprocess   DELETE /api/documents/{id}

GET    /api/observations?loinc=2276-4  one analyte's trend + `excluded` with reasons
GET    /api/observations/panels        what is chartable, abnormals first

GET    /api/review                     pending rows + ranked candidates
GET    /api/review/search?q=           the whole LOINC table, not just auto-matchable
POST   /api/review/{id}/confirm        writes an Alias, recomputes units + ranges
POST   /api/review/{id}/reject
```

`/api/observations` returns a point only when it has a resolved code, a
successful unit conversion, and is not awaiting review. Everything else appears
in `excluded` with a reason — never silently dropped.

## Security

| | |
|---|---|
| Access token | 15 min, `aud=labledger`, client holds it in memory only |
| Refresh | httpOnly + SameSite=Lax cookie scoped to `/api/auth`, rotated on every use |
| Ownership | enforced in `repo.py`, never per-handler; 404 not 403 |
| At rest | PDF bytes, extracted text, DOB and sex — AES-256-GCM |
| Uploads | magic bytes (not extension), 25 MB, 100 pages, SHA-256 dedupe |
| To Google | test name, unit, specimen. Never the value, date, or patient |
| Logs | ids only — never filenames (`oncology_panel.pdf` is a diagnosis) |

`JWT_SECRET` and `FIELD_ENCRYPTION_KEY` are **not** shared with Quiz-App. The
`aud` claim means a Quiz-App token is rejected here even if the secrets were
ever accidentally aligned.

## Environment

`.env` is chmod 600 and gitignored; `.env.example` lists every key.

Shared with Quiz-App: `MONGO_URI` (separate database `labledger`),
`GEMINI_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `REDIS_URL`
(db 0, namespaced by `ARQ_QUEUE_NAME` — the free tier has no db 1).

Google OAuth uses the "quiz app user" client with a second redirect URI:
`http://localhost:8000/api/auth/google/callback`.

`GEMINI_MODEL=gemini-3.5-flash`, pinned rather than `-latest` because the model
version is recorded in every mapping's provenance. Chosen by measurement, not
assumption — see `scripts/bench_llm_hard.py`. On 40 real lab abbreviations
`gemini-2.5-pro` was not more accurate and was 2.3× slower; flash is the only
model that abstained correctly when the right code was absent from the list.

## LOINC

`data/loinc_lab.csv.gz` — 58,252 codes distilled from the 925 MB LOINC 2.82
release (`ACTIVE` + laboratory class + orderable/observable). 18,178 carry a
`COMMON_TEST_RANK` and are auto-matchable; the other 40,074 are reachable only
through `/api/review/search`, kept because a backstop that cannot reach a valid
code is not a backstop.

```bash
.venv/bin/python scripts/build_loinc_subset.py ~/Downloads/Loinc_2.82
.venv/bin/python scripts/seed_loinc.py --drop
```

LOINC is a registered trademark of Regenstrief Institute, Inc., used under the
LOINC License v5.8 (`data/LOINC_LICENSE.txt`).

## Tests

`ENV=test` is set in `conftest.py` before any `app.*` import — it disables the
per-IP rate limits, which would otherwise put every test in one 127.0.0.1
bucket. The LOINC table is copied into `labledger_test` once per run; without
it the cascade matches nothing and mapping assertions pass for the wrong reason.

`tests/fixtures/*.pdf` are synthetic, carrying the same analytes in **opposite
column orders** — that is what pins the extractor's order-independence. Drop
real de-identified reports alongside them and extend `test_extract.py`.
