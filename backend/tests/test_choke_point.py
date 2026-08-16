"""The choke point, checked rather than trusted.

`repo.py` carries access and audit for every clinical route, and that is the
right design: thirty handlers means twenty-nine correct ones and one that
forgot. But nothing stopped the thirty-first from being the one that forgot —
the guarantee was an intention held up by review, and review is exactly what
misses the route added in a hurry.

These tests read the running app's route table and the source of each handler.
No database, no fixtures: it is introspection, and it should cost nothing to
run on every save.

**The allowlist is the point.** A new route either reaches the choke point or
its author has to come here and say, in writing, why it does not. That is the
mechanism — not the assertions, which only hold today's routes still.
"""

import inspect

import pytest
from fastapi.routing import APIRoute

from app.main import app

# Every way a handler can legitimately scope what it touches.
#
# `repo.` and `access.require` narrow to one record. `live_grants` and
# `reachable_patient_ids` narrow to the set this account may reach, which is the
# same guarantee for a list endpoint. `actor_id == user.id` scopes the audit
# trail to the reader's own history.
SCOPING = (
    "repo.",
    "access.require",
    "access.live_grants",
    "access.reachable_patient_ids",
    "AuditEntry.actor_id == user.id",
)

# Routes that reach no patient's clinical data, each with the reason.
#
# Adding an entry here is a decision, and it should be an uncomfortable one:
# everything below is either about the caller's own account, about reference
# data that belongs to nobody, or about the sign-in machinery itself.
NO_RECORD_DATA = {
    ("GET", "/api/health"): "liveness only, and deliberately free of detail",
    ("GET", "/api/review/loinc/search"): "the LOINC table is reference data, not anyone's",
    # Aliases are per-user, not per-patient: they are how a lab prints things,
    # belonging to whoever made that judgement. The handlers scope by
    # `alias.user_id != user.id` and the rows they re-code go through
    # `_rows_for_alias`, which does use `access.reachable_patient_ids`.
    # The one route that creates the thing everything else scopes against.
    # There is no existing record to check reach on; it calls `access.grant` to
    # make the creator its owner, which is the first grant rather than a check
    # of one.
    ("POST", "/api/patients"): "creates the record and grants its first owner",
    ("GET", "/api/review/aliases"): "user-scoped; rows re-coded via _rows_for_alias",
    ("PATCH", "/api/review/aliases/{alias_id}"): "user-scoped; re-codes via _rows_for_alias",
    ("DELETE", "/api/review/aliases/{alias_id}"): "user-scoped; re-codes via _rows_for_alias",
}


def routes():
    for r in app.routes:
        if isinstance(r, APIRoute):
            for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield method, r.path, r.endpoint


def source(endpoint) -> str:
    try:
        return inspect.getsource(endpoint)
    except (OSError, TypeError):  # pragma: no cover - C-level or generated
        return ""


def is_scoped(endpoint) -> bool:
    return any(marker in source(endpoint) for marker in SCOPING)


def clinical(method, path):
    """Whether this route can reach a patient's record at all."""
    if (method, path) in NO_RECORD_DATA:
        return False
    # Account and session management. These reach the caller's own account,
    # which `current_user` already resolved from their own token.
    return not path.startswith("/api/auth/")


def test_every_route_naming_a_patient_scopes_to_it():
    """The narrowest and least arguable version: if the path says `patient_id`,
    the handler must have asked whether this caller may reach that patient."""
    unscoped = [
        f"{m} {p}" for m, p, e in routes()
        if "{patient_id}" in p and not is_scoped(e)
    ]
    assert not unscoped, (
        "these take a patient id without going through access/repo: "
        f"{unscoped}"
    )


def test_no_route_reaches_a_record_without_the_choke_point():
    """The one that catches tomorrow's route.

    A handler that queries `Observation` or `LabDocument` directly is fine —
    most do, downstream of a scoping call. What is not fine is doing it without
    that call, and this is what makes that a build failure rather than a review
    miss.
    """
    unscoped = [
        f"{m} {p}" for m, p, e in routes()
        if clinical(m, p) and not is_scoped(e)
    ]
    assert not unscoped, (
        "these reach records without access/repo scoping. Either route them "
        "through the choke point, or add them to NO_RECORD_DATA with the "
        f"reason they need no scoping: {unscoped}"
    )


def test_every_change_to_a_record_is_recorded():
    """An audit trail with holes is worse than none: it invites the belief that
    what is not in it did not happen."""
    silent = [
        f"{m} {p}" for m, p, e in routes()
        if clinical(m, p) and m in ("POST", "PATCH", "DELETE")
        and "record(" not in source(e)
    ]
    assert not silent, f"these change a record without writing an audit entry: {silent}"


@pytest.mark.parametrize("method,path", sorted(NO_RECORD_DATA))
def test_the_allowlist_has_no_stale_entries(method, path):
    """An exemption for a route that no longer exists, or that has since been
    scoped properly, is a lie left where the next reader will trust it."""
    live = {(m, p) for m, p, _ in routes()}
    assert (method, path) in live, f"{method} {path} is exempted but does not exist"


def test_the_allowlist_stays_small_enough_to_read():
    """Not a real limit, a tripwire. If this needs raising, the choke point has
    stopped being a choke point and that is worth noticing deliberately."""
    assert len(NO_RECORD_DATA) <= 8
