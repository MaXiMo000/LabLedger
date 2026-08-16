# LabLedger

Upload a lab PDF from any laboratory. LabLedger reads the rows, resolves each
test to a **LOINC** code, converts the units, resolves the reference interval,
and charts the trend. Anything it is not certain about goes to a human, and
every confirmation makes the system more deterministic than it was.

The hard part is not the chart. It is that `FERRTN SER`, `Ferritin, Serum` and
`FERRITIN` are the same test printed three ways, that one lab reports it in
µg/L and another in ng/mL, and that being confidently wrong about either is
worse than admitting you do not know.

---

## What it does

### Reading a report

| | |
|---|---|
| **Extract** | `pdfplumber` over the page, table-aware, falling back to line parsing |
| **Map** | five-stage cascade from exact match to an LLM that may only *pick from a list* |
| **Convert** | hand-audited unit table per LOINC — deterministic or absent, never guessed |
| **Range** | the report's own interval when printed; an age- and sex-aware table when not |
| **Flag** | low / normal / high, plus critical limits and delta checks (below) |
| **Review** | anything uncertain queues for a human, and confirming teaches the system |

### The mapping cascade

Each stage is tried in order, and each **widens** the candidate pool rather
than narrowing to empty:

1. **Alias** — this user already confirmed what this printed name means. Free,
   deterministic, no network.
2. **Exact** — the printed name matches a LOINC component outright.
3. **Related, corroborated** — LOINC's associative `RELATEDNAMES2`, but only
   when a second signal agrees. A related name is *never* identity on its own.
4. **Narrowed fuzzy** — `rapidfuzz` over a pool narrowed by specimen and unit.
5. **LLM** — `gemini-3.5-flash`, offered a numbered list and required to return
   an index. It cannot invent a code, and it is expected to abstain.

Nothing auto-accepts below the confidence floor, and **critical analytes always
reach a human** when a probabilistic stage decided. Confidence is statistical;
those failures are categorical.

### Beyond the reference interval

- **Critical values.** A potassium of 7.0 is not "high", it is a phone call.
  Limits are a hand-audited table keyed by LOINC **and unit**, and every flag
  carries the threshold it used and where that threshold came from.
- **Delta checks.** A result inside its interval that doubled since the last
  one is often the more important finding. Percent change against the patient's
  own previous charted point, inside a per-analyte time window.

Three states, never two: *no limit published for this analyte*, *checked and
within limits*, and *crossed*. An unassessed result must never read as
reassurance.

### Provenance

Every number traces back to the page it came from — the row exactly as printed,
the stage that mapped it, the confidence, the conversion factor, and where the
reference interval came from. A resolved value the user cannot trace is not
finished work.

---

## Who can see what

One account is not one body. A nurse with twelve patients and a parent tracking
a child are both ordinary, so the **subject** of the data is separate from the
**accessor** of it.

```
User ──Access(role, expires_at)──► Patient ──► Documents ──► Observations
                     │                            │
                     │                            └─ DOB, sex, MRN (encrypted)
User ──► Aliases (per-user: how a lab prints things, not whose body it is)
User ──► Sessions (one per device; the access token names one)
Invite(email, role) ──► Patient        becomes an Access when accepted
AuditEntry ──► actor + patient + action
```

| Role | Read | Upload | Confirm mappings | Manage access |
|---|---|---|---|---|
| `owner` | ✓ | ✓ | ✓ | ✓ |
| `clinician` | ✓ | ✓ | ✓ | — |
| `nurse` | ✓ | ✓ | — | — |
| `viewer` | ✓ | — | — | — |

A nurse cannot confirm a mapping on purpose. Deciding what a number *is* is a
clinical judgement.

**Sharing.** Grant by email to an existing account, or send an invitation link
to somebody who has none — accepting needs the link *and* the invited address,
because registration does not verify email. Grants can carry an expiry, for a
locum covering a shift. Ownership can be transferred.

---

## Security

| Concern | How |
|---|---|
| Access token | 15 min, in a **JS closure** — never localStorage, never a cookie |
| Session restore | httpOnly + SameSite refresh cookie, rotated on every use |
| Revocation | the token names a `Session`, resolved on **every request** |
| Stolen cookie | a replayed refresh token ends the session and is logged |
| Idle | 30 min server-side; the client clears the screen a minute earlier |
| Second factor | TOTP (RFC 6238), QR or typed key, recovery codes, enforced after a grace period |
| Guessing | per-IP limits *and* a per-account cooldown on wrong codes |
| Ownership | enforced in `repo.py` at the query layer, never per-handler |
| Wrong owner | **404, not 403** — never confirm someone else's record exists |
| At rest | PDF bytes, extracted text, DOB, sex, MRN, TOTP secret — AES-256-GCM |
| Logs | ids only. Never a filename: `oncology_panel.pdf` is a diagnosis |
| Audit | every read and write of clinical data, append-only, survives account deletion |

Two rules shape most of it. **Access and audit both live at one choke point**,
because thirty handlers means twenty-nine correct ones and one that forgot —
which is either an IDOR or a hole in the log. And **a refusal never confirms
existence**: an unreachable record answers exactly as a non-existent one does.

---

## Running it

```bash
npm run api      # FastAPI on :8000
```
```bash
npm run worker   # arq — extraction and mapping
```
```bash
npm run dev      # Vite on :5173, proxying /api to :8000
```

Both API and worker are needed for uploads to process. The API runs fine
without the worker — files are stored and can be replayed with **Re-read**.

**First run**

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in MONGO_URI, JWT_SECRET, FIELD_ENCRYPTION_KEY
.venv/bin/python scripts/seed_loinc.py
cd ../frontend && npm install
```

`FIELD_ENCRYPTION_KEY` must be 32 bytes, base64:
`python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`

---

## Layout

```
backend/app/
  main.py          middleware: security headers, audit context
  deps.py          the only sanctioned way a handler gets a User
  access.py        who may reach which patient, and the MFA requirement
  repo.py          the choke point: resolve access, record the access, return data
  audit.py         append-only trail, written from repo.py not from handlers
  throttle.py      per-account cooldown on wrong codes
  security.py      passwords, tokens, field encryption, TOTP, recovery codes
  worker.py        arq job: extract -> map -> finalise
  pipeline/
    extract.py     PDF -> rows
    mapping.py     the five-stage cascade
    units.py       hand-audited conversion table
    ranges.py      age- and sex-aware reference intervals
    flags.py       critical limits and delta checks
    insights.py    statements about a series
    llm.py         Gemini, index-only answers
  routers/         auth, patients, documents, observations, review, audit
frontend/src/
  api/client.js    token in a closure, single-flight refresh
  auth/            session state, idle lock
  screens/         Trends, Reports, Upload, Review, Record, Security, Invite
  components/      Select, Modal, IntervalRail
  styles/tokens.css   the design system
```

---

## Testing

```bash
cd backend && .venv/bin/python -m pytest -q
```

~250 tests. They run against a real MongoDB (`labledger_test`) rather than
mocks, because the things worth testing here are queries and indexes.

The suite takes around twenty minutes against a remote Atlas cluster, and
**cannot be parallelised on a free tier** — each `pytest-xdist` worker needs
its own database, each needs its own copy of the 58k-row LOINC table, and four
of those exceeds 512 MB and blocks writes on the whole cluster. A local `mongod`
or a paid tier is the fix; a flag is not.

The LLM is stubbed by an autouse fixture. Opt out with `@pytest.mark.live_llm`,
and expect a paid API call.

---

## What this is not

Deployable in a hospital. The gap is not a to-do list, it is a different
category of work:

| | What it needs |
|---|---|
| **EHR integration** | HL7 v2 / FHIR against an interface engine. Nobody uploads PDFs on a ward. |
| **Clinical validation** | Measured sensitivity and specificity per analyte against a clinician-adjudicated gold set. |
| **Critical limits** | The shipped table is *illustrative adult values*. A deployment replaces it with limits its own laboratory director has signed. |
| **Regulatory** | The reviewable-basis boundary must be re-checked whenever interface copy changes. |
| **BAA and hosting** | A signed business associate agreement, and infrastructure inside its scope. |
| **Key management** | `FIELD_ENCRYPTION_KEY` lives in `.env`. Real deployment needs a KMS and rotation. |
| **Retention** | Documented retention periods and verified destruction, per jurisdiction. |
| **Breach procedure** | Detection, assessment and notification within statutory deadlines. |

The architecture is correct and defensible. It is not certified, and nothing in
the interface should ever suggest otherwise.

---

## Reading further

- [ARCHITECTURE.md](ARCHITECTURE.md) — how the pieces fit and why
- [PLAN-CLINICAL.md](PLAN-CLINICAL.md) — the phase plan, including what was
  deliberately not built
- [HANDOFF.md](HANDOFF.md) — decisions that must not be re-litigated, and the
  traps that cost real time
