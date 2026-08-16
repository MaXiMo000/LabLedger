import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

ALGORITHM = "HS256"
# Even with a distinct JWT_SECRET, the audience claim makes a cross-app token
# replay structurally impossible rather than merely unlikely.
AUDIENCE = "labledger"

# bcrypt silently ignores bytes past 72; reject instead of truncating.
MAX_PASSWORD_BYTES = 72


# ---------- passwords (hashes are interoperable with Quiz-App's bcryptjs) ----------

def hash_password(password: str) -> str:
    """Hash a password with bcrypt. Rejects input over bcrypt's 72-byte limit."""
    pw = password.encode()
    if len(pw) > MAX_PASSWORD_BYTES:
        raise ValueError("Password must be at most 72 bytes")
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored bcrypt hash."""
    pw = password.encode()
    if len(pw) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(pw, password_hash.encode())
    except ValueError:
        return False


# ---------- access tokens ----------

def create_access_token(user_id: str, role: str, sid: str) -> str:
    """Mint a short-lived access token carrying the labledger audience.

    `sid` names the Session row this token belongs to. It is what makes the
    token revocable: without it the only way to end a live session is to wait
    out the expiry.
    """
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "sid": sid,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_access_ttl_min),
        },
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    """Decode and verify a token.

    Raises jwt.ExpiredSignatureError (-> 401) or jwt.PyJWTError (-> 403).
    """
    return jwt.decode(
        token, settings.jwt_secret, algorithms=[ALGORITHM], audience=AUDIENCE
    )


# ---------- refresh tokens (opaque, hashed at rest, rotated on every use) ----------

def new_refresh_token() -> tuple[str, str]:
    """Generate a refresh token, returning (token_for_cookie, hash_for_db)."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage. Plaintext is never persisted."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- field encryption (AES-256-GCM, envelope-ready) ----------

def _key() -> bytes:
    key = base64.b64decode(settings.field_encryption_key)
    if len(key) != 32:
        raise ValueError("FIELD_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def encrypt_field(plaintext: str | bytes | None) -> bytes | None:
    """Encrypt a field with AES-256-GCM. None passes through."""
    if plaintext is None:
        return None
    data = plaintext.encode() if isinstance(plaintext, str) else plaintext
    nonce = os.urandom(12)
    return nonce + AESGCM(_key()).encrypt(nonce, data, None)


def decrypt_field(blob: bytes | None) -> bytes | None:
    """Decrypt a field written by encrypt_field."""
    if blob is None:
        return None
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], None)


def decrypt_str(blob: bytes | None) -> str | None:
    """Decrypt a field and decode it as UTF-8."""
    out = decrypt_field(blob)
    return out.decode() if out is not None else None


# ---------- TOTP (RFC 6238, the defaults every authenticator app assumes) ----------
#
# Hand-rolled rather than a dependency: the algorithm is HMAC-SHA1 over a time
# counter plus RFC 4226's dynamic truncation, and it is shorter than the import
# line's worth of supply chain. The parameters are not adjustable on purpose —
# SHA1 / 30s / 6 digits is what Google Authenticator, 1Password and Authy read
# from a QR code, and anything else silently fails to enrol.

TOTP_STEP = 30
TOTP_DIGITS = 6


def new_totp_secret() -> str:
    """Generate a base32 TOTP secret. 160 bits, RFC 4226's recommendation."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_uri(secret: str, email: str, issuer: str = "LabLedger") -> str:
    """Build the otpauth:// URI an authenticator app scans."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{email}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"


# A sentinel, not a design decision — it never reaches the browser.
_QR_INK = "#010203"


def totp_qr_svg(uri: str) -> str:
    """Render an otpauth URI as an inline SVG QR code.

    Inline rather than a PNG data URI: it is under 2kB, it scales without
    blurring, and drawn in `currentColor` it follows the page's ink instead of
    being a black rectangle pasted onto warm paper.

    A library, not hand-rolled. QR is Reed-Solomon error correction plus mask
    selection, and a subtly wrong encoder produces codes that scan on the
    phone you tested and fail on the one the nurse owns.
    """
    import io

    import segno

    buf = io.BytesIO()
    segno.make(uri, error="m").save(
        buf, kind="svg", scale=1, border=2,
        dark=_QR_INK, light=None,  # light=None keeps the paper showing through
        xmldecl=False, svgversion=None, svgclass=None, lineclass=None,
    )
    svg = buf.getvalue().decode()
    # segno validates colours against real ones and will not emit a keyword, so
    # the swap happens here. It appears once, in the single path's stroke.
    svg = svg.replace(_QR_INK, "currentColor")
    # segno sizes the root in module units and emits no viewBox, so CSS width
    # scales the box while leaving the code its original 37px in one corner.
    # Trading the fixed size for a viewBox is what makes it resolution-free.
    return re.sub(
        r'<svg([^>]*?)\s+width="(\d+)"\s+height="(\d+)"',
        r'<svg\1 viewBox="0 0 \2 \3"',
        svg,
        count=1,
    )



# ---------- recovery codes ----------
#
# The answer to a lost phone. Without them a second factor turns a mislaid
# device into an account that only a database edit can reach again.

RECOVERY_CODE_COUNT = 10
# Crockford-ish: no I, L, O, U — the characters people transcribe wrongly from
# a printout, and these get written down by definition.
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> tuple[list[str], list[str]]:
    """Generate recovery codes, returning (plaintext_to_show, hashes_to_store)."""
    codes = [
        "-".join(
            "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4))
            for _ in range(2)
        )
        for _ in range(count)
    ]
    return codes, [hash_recovery_code(c) for c in codes]


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code for storage.

    SHA-256 rather than bcrypt: these are 40 bits of uniform randomness that
    the server generated, not a human-chosen password, so there is nothing for
    a dictionary to attack and no reason to pay bcrypt's cost ten times per
    sign-in attempt.
    """
    return hashlib.sha256(code.strip().upper().replace(" ", "").encode()).hexdigest()


def take_recovery_code(code: str, hashes: list[str]) -> list[str] | None:
    """Spend a recovery code, returning the remaining hashes, or None if unknown.

    Compared against every stored hash rather than short-circuiting, so the
    time taken does not reveal how far down the list a guess matched.
    """
    wanted = hash_recovery_code(code)
    found = False
    kept = []
    for h in hashes:
        if hmac.compare_digest(h, wanted) and not found:
            found = True   # consumed: a recovery code is good exactly once
        else:
            kept.append(h)
    return kept if found else None


def totp_at(secret: str, counter: int) -> str:
    """Compute the code for one 30-second step."""
    # b32decode is strict about padding; the stored secret has it stripped.
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(code % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """Check a code, allowing one step either side for clock drift.

    A phone's clock is a real clock and drifts; without the window a correct
    code fails a few seconds before or after the boundary and the user
    concludes MFA is broken. Compared in constant time — a code is a secret
    small enough that timing tells you the digits.
    """
    code = (code or "").strip()
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return False
    now = int(datetime.now(UTC).timestamp()) // TOTP_STEP
    return any(
        hmac.compare_digest(totp_at(secret, now + drift), code)
        for drift in range(-window, window + 1)
    )
