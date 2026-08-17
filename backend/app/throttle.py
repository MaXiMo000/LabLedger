"""Slowing down guesses at a six-digit secret.

The per-IP limiter in `routers/auth.py` is the first line and the weaker one:
it buckets by address, so anything distributed across a handful of hosts walks
straight past it. This is the second line, and it counts against the *account*,
which is the thing actually under attack.

**Why this guards codes and not passwords.** Locking an account after failed
passwords hands anyone who knows a clinician's address a way to lock them out
of a ward terminal — a denial of service dressed as a security control, and on
a ward that is a patient safety problem rather than an inconvenience. A code
attempt is different: every route that checks one already requires a live
session, so an attacker who can trip this lock is someone who already holds the
account's session. There is nothing left to deny them.

Password attempts stay on the per-IP limiter and in the audit log, where a run
of `sign_in_failed` against one account is visible to a human without locking
anybody out of anything.
"""

from datetime import timedelta

from fastapi import HTTPException, status

from app.models.user import User, utcnow

# Five is enough to absorb a mistyped code and a stale one; six digits at five
# tries per quarter hour is 480 guesses a day against a million, which is not
# an attack any more.
CODE_ATTEMPT_LIMIT = 5
CODE_LOCKOUT = timedelta(minutes=15)


async def guard_code_attempts(user: User) -> None:
    """Refuse a code check while the account is cooling off."""
    if user.code_locked_until and user.code_locked_until > utcnow():
        remaining = max(int((user.code_locked_until - utcnow()).total_seconds() // 60), 1)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many incorrect codes. Try again in {remaining} minute"
            f"{'s' if remaining != 1 else ''}.",
        )


async def note_code_failure(user: User) -> None:
    """Count a wrong code, and start the cooldown once there have been enough."""
    user.code_failures += 1
    if user.code_failures >= CODE_ATTEMPT_LIMIT:
        user.code_locked_until = utcnow() + CODE_LOCKOUT
        # Reset the count with the lock, so the next lockout needs a fresh run
        # of failures rather than tripping on the very next wrong digit.
        user.code_failures = 0
    await user.save()


async def clear_code_failures(user: User) -> None:
    """Forget the failures after a correct code. Saves only when there is a change."""
    if user.code_failures or user.code_locked_until:
        user.code_failures = 0
        user.code_locked_until = None
        await user.save()


# --- extraction work in flight ----------------------------------------------

# Extraction is the most expensive thing this system does, and both paths to it
# — uploading and reprocessing — enqueue the same job. On Render the worker runs
# *inside* the API process (`RUN_WORKER_IN_API`), so saturating the queue does
# not just run up a bill: it competes with request handling on the same event
# loop and takes the API down with it.
#
# The per-IP limiter in `routers/documents.py` is the first line and the weaker
# one, for the reason at the top of this file — it buckets by address. This
# counts against the account, which is what the work is done on behalf of.
#
# **A concurrency cap, not a rate limit.** What needs bounding is how much work
# is in flight at once, and that is a thing the database already knows: a
# document sitting in `queued`, `extracting` or `mapping` *is* the queued work.
# Counting rows needs no new collection, no counters to keep in step and no
# window to expire. It also never punishes steady use — somebody working
# through a folder of reports is only ever blocked while the previous ones are
# still running, which is the honest answer to "why is this slow" anyway.
IN_FLIGHT = ("queued", "extracting", "mapping")
MAX_IN_FLIGHT = 5


async def guard_queue_depth(user) -> None:
    """Refuse to queue more extraction while this account already has plenty."""
    from app.models.document import LabDocument

    depth = await LabDocument.find(
        LabDocument.uploaded_by == user.id,
        {"status": {"$in": list(IN_FLIGHT)}},
    ).count()
    if depth >= MAX_IN_FLIGHT:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"{depth} of your documents are still being processed. "
            "They will finish on their own — try again in a minute.",
        )


# --- stored bytes ------------------------------------------------------------

# Documents are stored as encrypted blobs in Mongo, capped at 25 MB each with
# no ceiling on how many. One account could therefore fill the database, and a
# full cluster on the free Atlas tier does not degrade gracefully: it blocks
# writes for *everything* on it. This project has already done that to itself
# once, running the test suite in parallel.
#
# Bounds one actor, not the cluster. Ten accounts at the cap still fill a small
# tier, and nothing here pretends otherwise — the deployment's real protection
# is the storage it pays for. What this stops is a single account, or a single
# stolen credential, taking the whole system down by uploading.


async def storage_used(user) -> int:
    """Total stored bytes across every document this account uploaded."""
    from app.models.document import LabDocument

    # Only what is still held. `size_bytes` survives disposal as a record of
    # what the document was, but the cap is about storage in use — counting
    # bytes already reclaimed would leave somebody blocked from uploading by
    # files that no longer exist.
    return await LabDocument.find(
        LabDocument.uploaded_by == user.id,
        {"blob_enc": {"$ne": None}},
    ).sum(LabDocument.size_bytes) or 0


def guard_storage(used: int, adding: int = 0) -> None:
    """Refuse an upload that would take the account past its storage cap.

    Called twice by design: once before the request body is read, so an account
    already at the cap is refused without spending the bandwidth, and once with
    the real size, because only then is it known whether this file fits.

    507 rather than 413. The payload may be perfectly small; what is full is the
    account, and saying "request too large" about a 2 MB file would send someone
    off shrinking a PDF that was never the problem.
    """
    from app.config import settings

    cap = settings.max_account_bytes
    if used + adding <= cap:
        return
    mb = lambda n: f"{n / (1024 * 1024):.0f} MB"  # noqa: E731 - local formatting only
    raise HTTPException(
        status.HTTP_507_INSUFFICIENT_STORAGE,
        f"This account is storing {mb(used)} of its {mb(cap)} limit. "
        "Delete a report you no longer need, then try again.",
    )
