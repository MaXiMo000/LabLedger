from datetime import UTC, datetime
from typing import Annotated, Literal

from beanie import Document, Indexed
from pydantic import EmailStr, Field


def utcnow() -> datetime:
    """Timezone-aware now(). Naive datetimes must never enter the database."""
    return datetime.now(UTC)


class User(Document):
    """An account. Demographics are encrypted; nothing clinical lives here."""

    email: Annotated[EmailStr, Indexed(unique=True)]
    name: str
    password_hash: str | None = None  # None for Google-only accounts
    google_sub: str | None = None
    role: Literal["user", "admin"] = "user"

    # Demographics drive age/sex-specific reference ranges. Encrypted at rest,
    # never indexed, never logged.
    dob_enc: bytes | None = None
    sex_at_birth_enc: bytes | None = None

    # TOTP second factor. The secret is encrypted at rest for the same reason
    # the demographics are: a leaked row must not be a usable one. `mfa_enabled`
    # only flips once a code has been verified, so a half-finished enrolment
    # cannot lock anybody out.
    mfa_secret_enc: bytes | None = None
    mfa_enabled: bool = False
    # Hashes only, and spent on use. Without these a lost phone is an account
    # that only a database edit can reach again.
    recovery_hashes: list[str] = Field(default_factory=list)

    # When this account first reached a record it did not create — the moment
    # the second factor became required rather than merely advisable. Stored
    # rather than derived from the grant, so introducing the policy does not
    # retroactively lock out everyone whose grant predates it.
    mfa_required_since: datetime | None = None

    # Wrong six-digit codes in a row, and the cooldown they earned. Counted per
    # account because the per-IP limiter is distributed around trivially, and
    # the thing under attack is the account. See `app/throttle.py`.
    code_failures: int = 0
    code_locked_until: datetime | None = None

    # A live password-reset link, hashed like every other bearer token here.
    # One at a time: requesting another replaces it, so a link forwarded or
    # left in a mailbox stops working the moment a newer one is asked for.
    reset_token_hash: str | None = None
    reset_expires_at: datetime | None = None

    created_at: datetime = Field(default_factory=utcnow)
    last_login: datetime | None = None

    class Settings:
        name = "users"
