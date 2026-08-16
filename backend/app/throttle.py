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
