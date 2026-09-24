"""Tidewatch metrics add-on: window math, p95, auth, and nothing identifying in the output.

No database: a bare Starlette app is wrapped in the middleware, so these run without mongo.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import tidewatch_metrics as tm

TOKEN = "test-token-not-a-secret-0123456789"
T = 1_000_000_000.0


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(tm, "_http", tm.Series())
    monkeypatch.setattr(tm, "_deps", {})


def test_rolling_window_drops_old_seconds_and_reuses_slots():
    s = tm.Series()
    s.record(10, True, T)
    s.record(10, False, T + 30)
    assert (s.totals(T + 30)["count"], s.totals(T + 30)["errors"]) == (2, 1)
    assert s.totals(T + 61)["count"] == 1  # the first second has left the window
    s.record(10, True, T + 60)  # same slot as T, one minute later: reset, not added
    assert s.totals(T + 60)["count"] == 2
    assert s.totals(T + 200) == {"count": 0, "errors": 0, "p95_ms": 0}


def test_p95_is_within_one_histogram_bucket():
    fast = tm.Series()
    for _ in range(95):
        fast.record(10, True, T)
    for _ in range(5):
        fast.record(5000, True, T)
    assert 10 <= fast.totals(T)["p95_ms"] < 14.5

    slow = tm.Series()
    for _ in range(94):
        slow.record(10, True, T)
    for _ in range(6):
        slow.record(5000, True, T)
    assert 5000 <= slow.totals(T)["p95_ms"] < 7200

    extremes = tm.Series()
    extremes.record(0, True, T)
    extremes.record(10 * 60_000, True, T)  # beyond the top bucket: clamped, not lost
    assert extremes.totals(T) == {"count": 2, "errors": 0, "p95_ms": 60_000}


async def test_dependencies_track_listener_and_limits():
    async with tm.track("queue", "queue"):
        pass
    with pytest.raises(RuntimeError):
        async with tm.track("queue", "queue"):
            raise RuntimeError("boom")
    async with tm.track("ai", "service") as call:
        call.fail()  # e.g. a 5xx that did not raise
    async with tm.track("ai", "service"):
        pass

    listener = tm.MongoListener()
    listener.succeeded(SimpleNamespace(duration_micros=4000, command_name="find", reply={"x": 1}))
    listener.failed(SimpleNamespace(duration_micros=9000, command_name="insert", failure="dup"))

    tm.record_dep("Bad Id!", "service", 1, True)
    tm.record_dep("é", "service", 1, True)
    tm.record_dep("x", "gateway", 1, True)
    for i in range(20):
        tm.record_dep(f"extra-{i}", "cache", 1, True)

    deps = tm.snapshot()["deps"]
    assert len(deps) == 8
    by_id = {d["id"]: d for d in deps}
    assert (by_id["queue"]["count"], by_id["queue"]["errors"], by_id["queue"]["kind"]) == (2, 1, "queue")
    assert (by_id["ai"]["count"], by_id["ai"]["errors"]) == (2, 1)
    assert (by_id["db"]["count"], by_id["db"]["errors"], by_id["db"]["kind"]) == (2, 1, "database")
    assert {"Bad Id!", "é", "x"}.isdisjoint(by_id)
    text = json.dumps(deps)
    for leak in ("find", "insert", "dup"):
        assert leak not in text


def test_series_is_thread_safe_enough_for_pymongo_threads():
    s = tm.Series()

    async def hammer():
        await asyncio.gather(*(asyncio.to_thread(lambda: [s.record(1, True) for _ in range(500)])
                               for _ in range(8)))

    asyncio.run(hammer())
    assert s.totals()["count"] == 4000


def _app():
    async def user(request):
        return JSONResponse({}, status_code=500 if "fail" in request.query_params else 404)

    async def crash(request):
        raise RuntimeError("unhandled")

    inner = Starlette(routes=[Route("/api/users/{name}", user), Route("/crash", crash)])
    return tm.TidewatchMetrics(inner, token=TOKEN)


def test_middleware_refuses_an_empty_token():
    with pytest.raises(ValueError, match="token"):
        tm.TidewatchMetrics(Starlette(), token="")


async def test_auth_no_store_5xx_only_errors_and_nothing_identifying():
    async with AsyncClient(transport=ASGITransport(app=_app(), raise_app_exceptions=False),
                           base_url="http://test") as c:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
            r = await c.get(tm.PATH, headers=headers)
            assert r.status_code == 401
            assert r.headers["cache-control"] == "no-store"
            assert r.headers["www-authenticate"] == "Bearer"
        r = await c.post(tm.PATH, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 405

        await c.get("/api/users/alice-secret?email=alice%40example.com")  # 404
        await c.get("/api/users/bob?fail=1")  # 500
        await c.get("/crash")  # exception -> counted as a 5xx

        r = await c.get(tm.PATH, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        body = r.json()
        assert list(body) == ["v", "window_s", "uptime_s", "http", "deps"]
        assert list(body["http"]) == ["count", "errors", "p95_ms"]
        # Polls of the metrics route are never counted; the three app requests are.
        assert (body["http"]["count"], body["http"]["errors"]) == (3, 2)
        for leak in ("alice", "bob", "example.com", "/api", "users", "crash", TOKEN):
            assert leak not in r.text

        head = await c.head(tm.PATH, headers={"Authorization": f"Bearer {TOKEN}"})
        assert head.status_code == 200
        assert head.content == b""
