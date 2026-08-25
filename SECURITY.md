# Security

## Reporting a vulnerability

Please open a private security advisory on this repository rather than a
public issue. If you would rather email, use the address on the GitHub profile
of the maintainer and say "LabLedger" in the subject.

Expect an acknowledgement within a week. Please give a reasonable window to
ship a fix before disclosing publicly.

## What this system holds

LabLedger stores laboratory reports: PDF bytes, extracted text, dates of
birth, sex, medical record numbers, and results. Treat any finding that
touches those as high severity by default.

The controls the design depends on, all covered by the test suite:

| Concern | How |
|---|---|
| Access token | 15 minutes, held in a JS closure — never localStorage, never a cookie |
| Session restore | httpOnly + SameSite refresh cookie, rotated on every use |
| Revocation | the token names a `Session`, resolved on every request |
| Replayed refresh token | ends the session and is logged |
| Idle | 30 minutes server-side |
| Second factor | TOTP (RFC 6238), recovery codes, enforced after a grace period |
| Ownership | enforced in `repo.py` at the query layer, never per-handler |
| Wrong owner | **404, not 403** — a refusal never confirms a record exists |
| At rest | PDF bytes, extracted text, DOB, sex, MRN and TOTP secret are AES-256-GCM |
| Logs | ids only, never filenames — `oncology_panel.pdf` is itself a diagnosis |
| Caching | every response carries `Cache-Control: private, no-store` |
| Audit | every read and write of clinical data, append-only, survives account deletion |

Two rules shape most of it. **Access and audit live at one choke point**,
because thirty handlers means twenty-nine correct ones and one that forgot —
which is either an IDOR or a hole in the log. And **a refusal never confirms
existence**.

## Clinical safety

This is not a medical device and must not be used to make clinical decisions.
Reports of *incorrect results* — a bad unit conversion, a wrong reference
interval, a missed critical value — are treated as security-grade issues, not
ordinary bugs, because being confidently wrong is the failure mode that
matters here.

## Scope

Out of scope: findings that require a compromised host or browser, rate-limit
observations without a demonstrated impact, and automated scanner output with
no working proof of exploitability.
