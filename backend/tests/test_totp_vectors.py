"""RFC 6238 / RFC 4226 published test vectors against `totp_at`/`verify_totp`.

Every other MFA test (test_mfa.py) generates a code with `totp_at` and then
checks it with `verify_totp` -- which proves the two functions agree with
each other, not that either is correct. A subtly wrong HMAC key length, byte
order, truncation offset, or off-by-one on the time step would pass every one
of those tests while producing codes no real authenticator app would ever
generate. This file is the independent source of truth: RFC 6238 Appendix B's
published time/code pairs, and RFC 4226 Appendix D's counter/code pairs, both
using the RFC's own 20-byte ASCII test key -- never a value this codebase
invented.

No `client` fixture, no MongoDB: `totp_at` and `verify_totp` are pure
functions, and this suite proves that by never asking for one.
"""

import base64
import time

import pytest

from app.security import TOTP_STEP, totp_at, totp_qr_svg, totp_uri, verify_totp

# RFC 6238 Appendix B / RFC 4226 Appendix D both use this exact 20-byte ASCII
# string as the SHA1 secret. totp_at() takes a base32 secret (what this
# codebase stores and what an authenticator app scans), so it's re-encoded
# here rather than hand-transcribed as base32 -- one less place to transpose
# a character against the RFC text.
_RFC_ASCII_KEY = b"12345678901234567890"
RFC_SECRET = base64.b32encode(_RFC_ASCII_KEY).decode().rstrip("=")

# RFC 6238 Appendix B, SHA1 column: (unix time, published 8-digit TOTP code).
# https://www.rfc-editor.org/rfc/rfc6238#appendix-B
RFC6238_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]

# RFC 4226 Appendix D: HOTP(secret, count) for count 0..9, 6 digits.
# https://www.rfc-editor.org/rfc/rfc4226#appendix-D
RFC4226_VECTORS = [
    (0, "755224"), (1, "287082"), (2, "359152"), (3, "969429"), (4, "338314"),
    (5, "254676"), (6, "287922"), (7, "162583"), (8, "399871"), (9, "520489"),
]


@pytest.mark.parametrize("counter,expected", RFC4226_VECTORS)
def test_hotp_matches_rfc4226_appendix_d(counter, expected):
    """TOTP is HOTP with the counter derived from time; if the HOTP core is
    wrong, no amount of correct time-stepping saves it. `totp_at` doubles as
    HOTP: RFC 6238 defines T as an integer counter no differently from RFC
    4226's `count`, so calling it directly with a raw counter is exactly what
    the RFC's own relationship between the two documents describes."""
    assert totp_at(RFC_SECRET, counter) == expected


@pytest.mark.parametrize("unix_time,published_8_digit", RFC6238_VECTORS)
def test_totp_matches_rfc6238_appendix_b(unix_time, published_8_digit):
    """This codebase's TOTP is 6 digits; RFC 6238's published vectors are 8.
    The two share one underlying integer before either truncates to its own
    digit count (RFC 4226 SS5.3's DT/Truncate, then `mod 10^Digit`), so the
    correct 6-digit code is that same integer's last 6 digits -- not a
    different computation, just a shorter modulus applied to the identical
    HMAC-SHA1 output the 8-digit vector already pins down."""
    counter = unix_time // TOTP_STEP
    expected_6_digit = published_8_digit[-6:]
    assert totp_at(RFC_SECRET, counter) == expected_6_digit


def test_verify_totp_accepts_a_real_code_through_the_real_entry_point():
    """The vectors above prove totp_at's math against the RFC; this exercises
    verify_totp itself -- the digit/format check, the window, the
    constant-time comparison -- which none of them touch. verify_totp reads
    the real wall clock with no way to inject a historical one, so this uses
    `now`, not an RFC timestamp; that's fine, because totp_at is independently
    proven correct by every test above it in this file."""
    now = int(time.time()) // TOTP_STEP
    assert verify_totp(RFC_SECRET, totp_at(RFC_SECRET, now), window=0) is True
    assert verify_totp(RFC_SECRET, "000000", window=0) is False


def test_verify_totp_window_covers_clock_drift_but_not_further():
    """The whole reason a window exists: a phone's clock can be a step off.
    It must not be two steps off, or the codes an attacker needs to guess
    grows from 1 million to 3 million for a bracket most authenticator apps
    never actually drift into."""
    now = int(time.time()) // TOTP_STEP
    one_step_off = totp_at(RFC_SECRET, now + 1)
    two_steps_off = totp_at(RFC_SECRET, now + 2)
    assert verify_totp(RFC_SECRET, one_step_off, window=1) is True
    assert verify_totp(RFC_SECRET, two_steps_off, window=1) is False


def test_verify_totp_rejects_malformed_input_without_touching_totp_at():
    """A non-digit or wrong-length code is refused by shape alone -- it
    should never reach totp_at (which would coerce or crash on it)."""
    assert verify_totp(RFC_SECRET, "12345", window=1) is False   # too short
    assert verify_totp(RFC_SECRET, "1234567", window=1) is False  # too long
    assert verify_totp(RFC_SECRET, "12a456", window=1) is False   # not digits
    assert verify_totp(RFC_SECRET, "", window=1) is False
    assert verify_totp(RFC_SECRET, None, window=1) is False


# ---------- QR rendering ----------
#
# test_mfa.py's own test already asserts "currentColor" appears somewhere in
# the output; it does not catch the ways this specific transform can regress
# silently. Both of these are real risks named in totp_qr_svg's own
# docstring, not hypothetical: the sentinel colour must be gone everywhere
# (not just replaced somewhere), and the width/height -> viewBox rewrite is a
# single regex matched against a specific segno output shape -- a segno
# version bump that changes its SVG slightly would make that regex quietly
# match zero times, leaving a fixed-pixel-size code with no test failure.

def _qr(secret=RFC_SECRET):
    return totp_qr_svg(totp_uri(secret, "test@example.com"))


def test_qr_svg_never_leaks_the_sentinel_ink_colour():
    svg = _qr()
    assert "#010203" not in svg, "the sentinel must be fully replaced, not just present alongside currentColor"
    assert svg.count("currentColor") >= 1


def test_qr_svg_has_a_viewbox_and_no_leftover_fixed_size_on_the_root_svg():
    svg = _qr()
    root = svg[:svg.index(">") + 1]  # the opening <svg ...> tag only
    assert "viewBox=" in root
    assert "width=" not in root and "height=" not in root, (
        "the width/height on the root <svg> should have become a viewBox -- "
        "if this fails, the rewrite regex stopped matching segno's output "
        "and the code is back to a fixed pixel size"
    )
