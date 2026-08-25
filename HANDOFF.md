# LabLedger — handoff

Everything a fresh session needs. Read this first, then [README.md](README.md)
for what the product does and [ARCHITECTURE.md](ARCHITECTURE.md) for how.

> **This file was reconstructed.** The original `HANDOFF.md` and
> `PLAN-CLINICAL.md` disappeared from the working tree partway through the
> session that wrote most of what follows, before the first git commit, and
> could not be recovered — not in the index, no commits, no loose objects. What
> is here was rebuilt from that session. Phase 1–6 detail is thinner than it
> was. The repository now has commits, so there is a floor under it; there was
> none while the above happened.

---

## Do these first

Nothing. The three that were here are done: the EmailJS non-browser API is
enabled, the leaked keys are rotated, and the three wrong aliases on
`ritishsaini1995@gmail.com` were corrected through `/app/review/learned` — so
the results they had mis-coded were re-coded too, not just the rules.

**Mail is verified working end to end.** EmailJS returned 200 on a live reset
and a live invitation. If it stops, check the obvious thing first: `.env`
changes do not restart `uvicorn --reload`, which watches `.py` only. A process
started before a key rotation keeps using the revoked keys and every send fails
silently, because a failed send never fails the operation it belongs to.

---

## Current state

| | |
|---|---|
| Backend tests | ~290 passing (`cd backend && .venv/bin/python -m pytest -q`) — **~20 min**, see below |
| Lint | `ruff check app scripts` clean; `tests` has 8 pre-existing RUF059 warnings |
| Frontend | builds clean |
| LOINC | 58,252 codes in Mongo `labledger.loinc` |

**Free-tier latency can look exactly like a hang.** A file whose tests each
pass in 4-17 seconds on their own ran for over ten minutes as a file, and so
did an untouched one that had taken thirty seconds an hour earlier. Storage was
59 MB of the 512 MB limit, so it was not the quota — the shared tier was simply
throttling. Before debugging a "hang", re-run a file you have not touched: if
that is slow too, it is the cluster, not the change.

**The suite is slow and cannot be parallelised on the current tier.** Every
round-trip goes to Atlas. `pytest-xdist` needs one database per worker, each
needs its own copy of the LOINC table, and four of those exceeded the 512 MB
free-tier quota and *blocked writes on the whole cluster*. Tried it; that is
what happened. A local `mongod` or a paid tier is the fix — a flag is not. Run
the affected files, not the suite, while iterating.

---

## Run it

```bash
npm run api      # FastAPI :8000
```
```bash
npm run worker   # arq — extraction and mapping
```
```bash
npm run dev      # Vite :5173, proxies /api to :8000
```

**Test account:** `demo@labledger.dev` — password held out of band, ask the
owner. It is deliberately not written here: **this repository is public**, and
the account is live on the deployed instance, so a password in this file is a
working key to a running system rather than a convenience for the next
session. The one that used to sit here has been rotated and is dead; it remains
in git history, which is harmless now and is why rotating beat redacting.

Its record carries **synthetic demographics** for the same reason. A public
demo that shows a real name and date of birth is publishing exactly the thing
the rest of this system encrypts at rest — and the date of birth is not
decoration, it selects the reference interval for every result.

Also real: `ritishsaini1995@gmail.com` (Google OAuth, real reports). Reachable
only through that Google account or its mailbox — no other account can see it,
since a new account holds nothing until granted.

**A stale API process cost an hour once.** `npm run api` carries `--reload`; a
manually started `uvicorn` without it serves whatever code was loaded at boot
and 500s on everything after a model change. If logins fail inexplicably, check
`ps` for a uvicorn started by hand.

---

## Deployed

| | |
|---|---|
| App | <https://labledger-web.onrender.com> |
| API | <https://labledger-api.onrender.com> |

Verified live: sign-in, patient decryption, panel grouping, derived values, and
an upload processed end to end (`queued → mapping → needs_review`, 55s).

**There is no worker service.** Render has no Free instance type for
`type: worker`, so the API sets `RUN_WORKER_IN_API=true` and runs arq inside
its own process. Free web services sleep after 15 minutes idle and the worker
sleeps with them, so an external pinger against `/api/health` is what keeps
uploads processing — at the cost of 744 of the 750 monthly Free instance hours,
which is enough for exactly one always-on Free web service and no more.

Google sign-in and mail from Render are both confirmed working, including that
reset links carry the right host. That took two fixes worth remembering:
`fromService ... property: host` is the **private network** hostname, not a
URL — bare, no scheme, and unsupported for static sites — so `FRONTEND_URL` and
`BACKEND_URL` are literal values in `render.yaml`. And the OAuth callback must
sit on the *frontend* origin, because the sign-in link is relative, which means
the OAuth state cookie is stored there and a callback on the API host never
receives it.

## Deploying to Render

[render.yaml](render.yaml) is a blueprint: **New → Blueprint → this repo.** It
creates the API, a Key Value instance and the static frontend, and prompts
once for the eleven secrets marked `sync: false`. **There is no worker
service** — Render has no Free instance type for one, so the API runs arq
in-process; see *Deployed* above.

**Paste these when prompted** (from `backend/.env`): `MONGO_URI`,
`FIELD_ENCRYPTION_KEY`, `GEMINI_API_KEY`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_CALLBACK_URL`, and the five `EMAILJS_*` values.
`JWT_SECRET` is generated by Render.

Three things that are not obvious:

- **`FIELD_ENCRYPTION_KEY` must be the same value as dev.** It is not
  generated, because anything already encrypted is unreadable without it. A
  mismatch shows up as every document failing.
- **The frontend proxies `/api` to the backend; it does not call it
  cross-origin.** The refresh cookie is httpOnly, SameSite=lax, scoped to
  `/api/auth` — a browser will not send that to a different site. A static site
  pointed straight at `labledger-api.onrender.com` would sign in once and then
  fail every refresh. The rewrite in `render.yaml` keeps one origin, exactly as
  the Vite proxy does locally. Changing that means changing the session model
  too (SameSite=none, Secure, CORS with credentials).
- **`GOOGLE_CALLBACK_URL` sits on the *frontend* origin**, not the API's:
  `https://labledger-web.onrender.com/api/auth/google/callback`, registered in
  Google Cloud Console exactly. The sign-in button is a relative link, so the
  browser is on the frontend when SessionMiddleware stores the OAuth state
  cookie; a callback on the API host is a different site and never receives it,
  and the user lands on `?error=oauth`. The rewrite destination in `render.yaml`
  assumes the API hostname; if Render assigns different names, update both.

Free-tier services sleep after inactivity: the first request after a while
takes ~30s, and the worker only processes uploads while it is awake.

---

## Email

EmailJS, called **server-side with the private key** — `app/mailer.py`.

Its usual mode is client-side with the *public* key, which is fine for a
contact form and wrong here twice over: the public key is readable in any
browser's devtools, and a reset link composed in a browser is worthless because
whoever controls the browser can mint one for any address. The frontend
therefore holds **no EmailJS keys at all**.

| Template | ID | Used by |
|---|---|---|
| Password Reset | `template_pdo0y5q` | `POST /api/auth/password/reset` |
| Welcome (invitation) | `template_6g2zb5r` | `POST /api/patients/{id}/invites` |

Service `service_elkjfhk`. Variables: reset takes `to_email`, `reset_url`,
`expires_minutes`; invitation takes `to_email`, `inviter_email`, `role`,
`invite_url`, `expires_on`.

**The patient's name is deliberately not in the invitation email.** It would
put clinical identity through EmailJS and Gmail — neither under any agreement
here — and into a mailbox that may be shared or forwarded. The recipient learns
whose record it is after authenticating, which is already how `/invite/:token`
behaves.

**A failed send never fails the operation.** The invitation still exists and
its link is still returned; the reset still answers 204. Losing an email is bad;
a 500 on the reset endpoint would tell an attacker which addresses are
registered.

**The invitation says which of those happened.** `InviteOut.emailed` carries
the send result, and the screen reads "Emailed to them" or "the email could not
be sent" accordingly. The link is shown either way — a silent failure whose
only copy of the link was never displayed leaves an invitation that exists and
can never be delivered.

---

## Decisions that must not be re-litigated

Each was arrived at by hitting the failure first.

**A related name is never identity.** LOINC's `RELATEDNAMES2` is associative —
`HCT` is listed under *Reticulocyte production index* because RPI is calculated
from the hematocrit. Treating those as identity produced confident wrong
mappings at conf 0.95. Related names widen the candidate pool and can never
auto-accept.

**Unit conversion is deterministic or absent.** `pipeline/units.py` is
hand-audited. An unrecognised unit returns `(None, None, None)` — never 1.0,
never the LLM. A silently wrong conversion is off by 10× and looks plausible.

**Narrowing never excludes the correct answer.** Every filter widens.

**The model picks an index, never generates a code.**

**Critical analytes always reach a human** when a probabilistic stage decided.

**A critical *value* is a different statement from a high one.**
`pipeline/flags.py` keys limits by LOINC **and unit**, like the conversion
table, and for the same reason: a limit in a unit the pipeline never emits is
never compared, so the analyte is *silently never assessed*. Two entries were
wrong that way when written. A test pins `CRITICAL`/`DELTA` against
`units.CANONICAL` — do not delete it. Three states are reported, not two: no
limit published, checked and within limits, or crossed.

**Flags report, they do not advise.** Every flag carries the threshold it used
and where it came from. **The shipped limits are illustrative adult values and
must be replaced with an institution's own.**

**Derived values are computed on read, never stored.** `pipeline/derived.py` —
eGFR (CKD-EPI 2021, race-free), anion gap, albumin-corrected calcium, non-HDL,
transferrin saturation. Every input must be present, in the *exact* canonical
unit the formula is written in, from the same draw. A creatinine in µmol/L fed
to CKD-EPI is wrong by 88× and charts fine. A stored derivation would outlive
the correction that invalidated it.

**The eGFR test carries a longhand transcription, not remembered numbers.** The
first version had four expected values written from memory; three were wrong,
and following them would have meant "fixing" correct arithmetic about kidney
function. Do not replace `ckd_epi_2021()` in the test with constants.

**A confirmation is permanent, so it needs an undo.** `/app/review/learned`.
**Correcting** re-codes the results the rule already decided — fixing the rule
alone leaves the mistake on the chart while making the settings screen look
fixed. **Forgetting** sends those rows back to the queue, because "wrong, and I
know the right answer" and "wrong, and I do not" are different admissions.

**Audit and access both live in `repo.py`**, the single choke point. Thirty
handlers means twenty-nine correct ones and one that forgot.

**404, never 403** for anything the caller cannot reach — *except* the MFA wall,
which is 403 deliberately: that caller holds a live grant and has been reading
the record for days, so hiding it reads as data loss.

**MFA is enforced, never at the sign-in door.** Past `MFA_GRACE_DAYS` (7) an
account without a second factor is refused *records it did not create*. Sign-in,
`/auth/me`, sessions and enrolment keep working, or the requirement would lock
people out of the screen that satisfies it. The clock starts at first access to
a shared record, not at the grant.

**A recovery code counts wherever a TOTP code does.** Sign-in always took
either, but `/mfa/disable` and `/mfa/recovery` took only TOTP — so somebody who
had lost their authenticator could get *in* and never get *out*, spending one
code per sign-in until none were left. That is the lockout recovery codes exist
to prevent, reached by using them exactly as intended. `_spend_second_factor`
is now the single check, and it spends the code like sign-in does.

**An admin can clear an enrolment, and nothing else.** `deps.admin_user` is
finally used, by one route, for the case where the authenticator *and* every
code are gone. Never on yourself — that would skip the code `/mfa/disable`
demands, so a borrowed admin session could strip its own second factor. It ends
the target's sessions, for the reason a password reset does, and it does *not*
reset the grace clock: a fresh week of reaching shared records unprotected is
not an admin's to hand out. The role grants nothing over clinical data; reach
still comes only from `Access`. Granted by `scripts/make_admin.py`, deliberately
not by a screen — the first admin has to come from outside the application.

**Wrong codes are throttled per account; passwords are not.** `app/throttle.py`
— five wrong codes earn a fifteen-minute cooldown, shared across sign-in and
every MFA route, and it holds against a *correct* code too. Passwords are
excluded: locking on failed passwords lets anyone who knows a clinician's
address shut them out of a ward terminal. A code attempt always needs a live
session already.

**A record must always have a live owner, enforced in `access.grant`.** It was
guarded only on *revocation*, so a record could be orphaned by **demoting** its
last owner — which happened to a real record. An owner also cannot grant to
themselves at all: typing your own address into "give someone access" reads as
adding a person but lands on your own grant.

**An invitation needs the link *and* the invited address.** Registration does
not verify email, so "the invited address signed up, give them the access"
would be an account takeover.

**A password reset ends every session, and leaves MFA on.** A reset is what
somebody does when they have lost control; leaving sessions alive hands the new
password to a stranger. It proves control of a mailbox, which is exactly what
MFA exists to not be sufficient on its own.

**A Google-only account can reset too — deliberately reversed.** It used to be
refused, on the grounds that mailing a link which *sets* a password lets anyone
with mailbox access past the identity provider. That is still true and is the
price: signing up through Google can no longer be a one-way door, so control of
the mailbox now reaches an account that previously required Google. The
password is *added*, not swapped in — Google sign-in keeps working — and the
second factor surviving the reset is what stands behind it. If this is ever
reconsidered, reconsider it against that, not against the enumeration rule,
which is untouched: the endpoint still always answers 204.

**A replayed refresh token ends the session for everybody.** `previous_hash`
remembers one step back. Inside `ROTATION_GRACE` (10s) it is two tabs racing on
load, not a theft — refusing that signed people out for having two tabs open.
Outside it, the session ends rather than guessing which party is the thief.

**Signing in again on a device rotates its session, it does not add one.**
Note for tests: one httpx client is one cookie jar, so
`sign_in(new_device=True)` is how a test says "a second browser".

**The session list reaps before it lists.** The idle timeout only fires when a
session is *used*, so an abandoned one is never closed and sat in the list for
nine hours looking live. A list nobody can trust is worse than none.

**An export is owner-only because it outlives revocation.** A viewer can read
today and lose access tomorrow; JSON on their laptop does not expire.

**Aliases are per-user, not per-patient.**

**PATCH distinguishes "not sent" from "sent as null"** via `model_fields_set`.
Testing for None makes a field impossible to *clear* — a wrong date of birth
would be permanent, and it selects the reference range for every result.

**Motor is opened with `tz_aware=True`.** BSON returns naive datetimes;
`db.py` and `conftest.py` must agree or the idle timeout breaks only in tests.

**Route order: literal paths before path params.** `GET /aliases` declared after
`GET /{patient_id}` resolves as a patient named "aliases". Caught twice.

**Route sections animate with `backwards`, never `both`.** A filling animation
keeps a **stacking context**, so each section became one and a popover was
painted over by the next section regardless of `z-index` — which looks exactly
like a transparent dropdown.

**Panels are grouped by what needs attention, not by the order a report
prints.** `data/panels.py` holds the LOINC→heading map; `Trends.jsx` orders the
*groups* by their worst result. A lab report leads with the blood count because
it has always led with the blood count, and adopting that order would have put
a critical value below a screen of normals — losing the one thing the flat list
got right. Membership is one-to-one (a code under two headings renders the row
twice and the reader counts it twice), and anything unmapped falls to a visible
**Other results** group, pinned last. Urine glucose and serum glucose are
different analytes with different codes and must never share a heading, which
is the case that makes one-to-one membership feel restrictive and is exactly
why it is not.

`panels.UNITLESS` excuses dipstick analytes from the unit-table check below.
A result of "Trace" is not a quantity: there is nothing to convert, and adding
entries to `units.CANONICAL` to satisfy a test would be inventing a conversion
that does not exist. A typo'd code in that map is invisible —
the row just sits under the wrong heading forever — so `tests/test_panels.py`
pins every entry against `units.CANONICAL`. It needs no database and runs in
10ms; keep it that way.

**Charts scale to the data, not to the reference interval.** A one-sided `0–99`
dragged an LDL axis to −21. Reference bounds join the domain only when near the
data; never below zero; ticks on round numbers.

**`components/Select.jsx` replaces every `<select>`.** A native option list is
drawn by the OS and cannot be themed.

**A dialog's focus effect is a mount effect.** `useDialog` keeps `onClose` in a
ref. With it in the dependency array the effect re-ran on every render — every
caller passes a fresh arrow — so typing one character into any field inside any
modal moved focus back to the dialog's first focusable element and the next
character went nowhere. Fixed in the hook, not in the callers: every dialog with
an input had it, and one guard where they all route through is smaller than five
`useCallback`s the next dialog would forget.

**`gemini-3.5-flash`, pinned**, chosen by measurement
(`scripts/bench_llm_hard.py`). Rerun the bench before changing it.

**`react@19.0.0`, pinned.** `@react-three/fiber` reaches React internals via
`its-fine`, which broke on 19.2 despite the peer range.

---

## What to build next

Ordered by what the system is currently worst at, not by what is most fun. Each
says why it matters and what "done" looks like, so a session can start on one
without re-deriving the case for it.

### 1. ~~LOINC search~~ — done

Left here because the diagnosis in it was wrong in a way worth remembering.

It looked like a ranking failure: `hema` returned F8 gene mutation panels. The
ranking was fine. Mongo's `$text` index matches **whole words only**, so `hema`
never matched Haematocrit at all, and the F8 rows came back because their
synonym blob literally contains the token "hema". Full words always worked —
`ferritin`, `tsh`, `hematocrit` each ranked correctly. What was missing was
prefix matching, which is what someone typing towards a name is doing.

Now: whitespace tokens ANDed, each matched as a word-prefix, ordered by walking
the `common_rank` index so Mongo stops as soon as it has enough. That last part
is what keeps a regex over 58k rows affordable *and* fixes a second bug — the
old code applied `limit` and then sorted, so the twenty rows it ranked were an
arbitrary twenty. Rank 0 means "never observed", so it is a separate second
pass rather than the front of the first. The client debounces 220ms.

**The lesson:** the symptom named the wrong cause, and only measuring the
endpoint directly separated them. `scripts/` has no bench for search; the tests
in `test_review.py` pin top hits instead, which is the cheaper version of the
same discipline.

### 2. ~~Panel grouping for the review queue~~ — done

The queue groups by the panel of the *proposed* code, so it moves if the
proposal is wrong. That is honest: the heading is a claim about what the cascade
currently thinks, not about what the test is.

Two leftover groups, not one, and the distinction is the point. **Other
results** means "a result whose panel is not in the map"; **Not recognised yet**
means "no proposed code at all". Collapsing them would file the rows needing the
most work under a heading that reads like a tidy leftovers drawer. Ordered by
how much work each needs — a proposal is a yes or no, an unmatched row is a
search — so *Not recognised yet* sits last.

No triage sort, unlike the results screen: every row here needs the same thing,
so there is nothing to rank by and inventing an order would only make the list
less predictable to work down.

**Reports is still ungrouped** — it lists documents rather than analytes, so the
map does not apply to it unchanged. Left out deliberately rather than forgotten.

### 3. ~~Trends across a panel~~ — done

**The plan in this slot was wrong and was not built.** It said to overlay a
panel on one axis, normalised to each analyte's reference interval. That
invents a common scale for quantities that have none: it puts potassium and
cholesterol on one axis and invites reading the height of one against the
other. The data refuses it too — a lipid panel here holds an HDL interval of
`39–` with no upper bound to normalise against, and analytes still awaiting
review have no interval at all.

What these analytes genuinely share is *when they were drawn*, so that is the
only thing shared. `GET /observations/{id}/panel-trends?panel=cbc` returns one
track per analyte, each keeping its own values, unit and band; the client
stacks them on one x scale. No combined line is drawn, ever — a line through
two different quantities is an artefact of the drawing, not a trend.

Tracks with nothing chartable come back anyway, carrying a count of what was
excluded. An analyte that vanished from a panel is a finding.

Both this and `/series` go through `_charted`, extracted so the two cannot
drift on which points are comparable. That extraction immediately earned
itself: the test that compares them caught the two endpoints returning the same
points in *opposite orders*, because results sharing a `collected_at` sort equal
and Mongo returned each query's ties in whatever order its plan produced. Not
cosmetic — `_charted` measures each delta against the previous *charted* point,
so an unstable order can change a reported change. `_CHART_ORDER` adds `_id` as
a stable tiebreak.

### 4. ~~Reference intervals a deployment can own~~ — done

`REFERENCE_CONFIG_PATH` points at a JSON file; `reference-config.example.json`
is the shape. It replaces the shipped tables in `ranges.py` and `flags.py`, and
its provenance block becomes the `basis` string shown next to every critical
flag — so the UI names who approved the threshold and for which population,
instead of our illustrative note.

**A file, not a database table.** A lab director signs a document, and a file in
version control *is* that document: who changed what, when, and what it was
before, in a form an assessor can read. Runtime-editable thresholds would need
their own audit, permissions and screen, and still be a worse record than
`git log`.

**Invalid config is fatal.** A deployment believing its own limits are live
while the illustrative ones actually run has the confidence without the
substance. The check that matters: a unit must equal the canonical unit for
that analyte, because a limit in a unit the pipeline never emits is never
compared and the analyte is *silently never assessed*. That is now a boot
failure naming the entry.

**Each section replaces wholesale, never merges.** A half-replaced table leaves
nobody able to tell which analytes their file governs and which fell through to
values nobody there approved.

Still open, and the reason this does not close the clinical gap: paediatric
intervals need age bands the shipped table has no data for, and nothing
validates that a configured interval is *clinically* sensible — only that it is
structurally sound and in the right unit.

### 5. Hardening, in rough order of exposure

- ~~**Rate-limit the upload endpoint.**~~ Done — and the entry was wrong. Upload
  already carried `@limiter.limit("30/hour")`; **`/reprocess` carried nothing**,
  and it queues the same extraction job for less effort — no file body, just an
  id. That was the cheap way to load the worker, and on Render the worker shares
  a process with the API.

  Both now also pass `throttle.guard_queue_depth`, a **concurrency cap rather
  than a rate limit**: what needs bounding is how much work is in flight, and
  the documents collection already knows that — `queued`/`extracting`/`mapping`
  rows *are* the queue. No new collection, no counters to keep in step, no
  window to expire, and steady use is never punished because the block lifts as
  the backlog drains. Per-account, because the per-IP limiter buckets by
  address and anything spread over a few hosts walks past it — the same
  argument `throttle.py` already makes about codes.
- ~~**Cap total stored bytes per account.**~~ Done — `MAX_ACCOUNT_BYTES`,
  default 200 MB. 25 MB a file with no ceiling on the count let one account
  fill Mongo, and a full cluster on the free Atlas tier does not degrade: it
  **blocks writes for everything on it**, which this project has already done
  to itself once running the suite in parallel. Bounds one actor, not the
  cluster — ten accounts at the cap still fill a small tier, and the real
  protection is the storage a deployment pays for. 507, not 413: the payload
  may be small, what is full is the account.
- ~~**A retention policy that does something.**~~ Partly — `DOCUMENT_RETENTION_DAYS`
  (0, off by default) disposes of the **stored PDF only**, nightly, from the
  worker. The results, their printed names and the audit trail all survive it,
  which is the point: the blob is very nearly all of a document's bytes and the
  numbers are the clinical value.

  **Call it what it is: disposal of source documents, not a retention policy.**
  A retention policy says how long clinical records live and who may end them.
  This says how long the scanned page lives. What is lost is real — a number
  can still be traced to its printed name, value and page number, but not back
  to the image it was read from — so the file endpoint answers **410 Gone**, not
  404, and reprocess refuses rather than queueing work that would fail a minute
  later.

  It also made `storage_used` count only what is still held. It summed
  `size_bytes`, which survives disposal, so an account whose files had been
  reclaimed stayed blocked from uploading by bytes nobody was storing.
- ~~**Audit the audit log.**~~ Done — `tests/test_choke_point.py` reads the
  running app's route table and each handler's source. Three properties: a route
  naming a `patient_id` must scope to it, no route may reach records without
  `repo.`/`access.`, and every POST/PATCH/DELETE on a record must write an audit
  entry. No database; 0.02s.

  **The allowlist is the mechanism**, not the assertions. A new route either
  reaches the choke point or its author comes to `NO_RECORD_DATA` and writes
  down why it need not — six entries today, with a tripwire if it ever exceeds
  eight, because an allowlist that keeps growing means the choke point has
  stopped being one. Verified by adding a deliberately unscoped route: both
  assertions fire and name it.
- **Structured error codes.** `X-Credential-Valid` works but is a one-off. A
  small `code` field on error bodies would let the client branch on meaning
  rather than on status plus header plus prose.

### 6. Polish that is worth the time

- **Empty states with a next action.** "0 learned mappings" explains itself well
  but offers nothing to do.
- **The upload screen does not say what happens next.** A dropped file goes
  `queued → extracting → mapping → needs_review`, and only the last one is
  actionable. Say so on the screen rather than in status labels.
- ~~**Keyboard path through the review queue.**~~ Done. `j`/`k` move a cursor,
  `1`–`9` choose a candidate, `Enter` confirms, `x` rejects, `/` reaches the
  search field and `Escape` leaves it. There is no skip key: moving the cursor
  *is* skipping, and a second key for it would only be a second way to lose
  your place.

  **The cursor is an index into a list that shrinks underneath it**, which is
  how it advances — settle the row it points at, the row after it takes that
  position, and the cursor is already on the next thing to do. Clamped rather
  than corrected, so settling the last row lands on the new last row instead of
  running off the end. Focus follows it for real (`tabIndex={-1}` on the row),
  not as a class, so a screen reader is told the cursor moved and the row is
  scrolled into view without asking — but only once a key has been pressed,
  because moving focus on mount yanks the page for somebody who arrived with a
  mouse.

  **`keyAction` is pure and tested; the listeners are not.** Same split as
  `idleDelayMs`: two `addEventListener` calls and a cleanup that mirrors them
  is what reading the file shows, while *which keystrokes this claims* is what
  can be quietly wrong. The expensive failure is claiming one it should not
  have — a `window` handler sees every keystroke on the screen, so typing
  "flux" into a LOINC search must not reject the row on the `x`. Text fields,
  modifier combinations, and Enter on an already-focused button are all
  returned as "not ours", and each has a test.

  Two bugs the screen had all along surfaced while driving it: the tally
  counted rejections as **rules learned**, when `/reject` answers
  `alias_written: false` and means it — the one counter whose job is to make
  convergence visible rather than claimed was overstating it. And a queue of
  one read "1 result need you".
- **`prefers-reduced-motion`.** The cascade scene animates unconditionally.
- **A real 404 route.** Unknown `/app/*` paths currently render the shell empty.

### Deliberately still not in scope

EHR/FHIR, clinical validation against an adjudicated gold set, a signed BAA,
KMS, breach procedure. Nothing above changes that, and nothing above should be
read as moving towards certification.

---

## Data model

```
User ──Access(role, expires_at)──► Patient ──► Documents ──► Observations
                     │                            └─ DOB, sex, MRN (encrypted)
User ──► Aliases            per-user: how a lab prints things
User ──► Sessions           one per device; the access token names one
Invite(email, role) ──► Patient      becomes an Access when accepted
AuditEntry ──► actor + patient + action      survives account deletion
```

Roles: `viewer` < `nurse` < `clinician` < `owner`. A nurse cannot confirm
mappings — deciding what a number *is* is clinical.

**A new account holds nothing.** `User.role` is `user` and no route checks it
(`deps.admin_user` is unused). Reach comes only from `Access`.

---

## Still outstanding

- **Provenance sheet on mobile** is still verified by DOM only. `/app/security`,
  `/app/review/learned` (populated, and its correction dialog) and a real
  drag-and-drop file drop have now all been seen by eye and behave.
- **Granting the admin role is still a script**, by design —
  `scripts/make_admin.py`. The reset itself now has a screen, shown only to
  admins at the foot of `/app/security`. **No account holds the role right now**;
  the demo account was promoted to verify the screen and demoted immediately,
  because a publicly-reachable demo must never carry it.
- **Client-side role gating is one boolean** (`active.role === "owner"`, hiding
  owner-only sections on the Record screen) and is not worth a component test
  harness — the enforcement is server-side and `test_access.py` covers it. The
  handoff used to call this "real branching"; having read it, that was
  overstated.
- **Reports is the one list still ungrouped.** It lists documents rather than
  analytes, so `data/panels.py` does not apply to it unchanged. Deliberate.
- **Nothing checks that a configured reference interval is clinically
  sensible** — only that it is structurally sound and in the canonical unit.
  Paediatric bands are the obvious gap; the shipped table has no data for them.

## Explicitly out of scope

EHR/FHIR integration, clinical validation against an adjudicated gold set, a
signed BAA and hosting inside it, KMS key management, retention and disposal
policy, breach procedure. The architecture is defensible; it is not certified,
and nothing in the interface should suggest otherwise.
