"""Tidewatch metrics add-on (vendored, v1, no dependencies beyond the stdlib and pymongo).

Source of truth: github.com/MaXiMo000/tidewatch addons/tidewatch_metrics.py

Serves aggregate health numbers at GET /tidewatch/metrics for the Tidewatch dashboard:
    {"v": 1, "window_s": 60, "uptime_s": ..., "http": {count, errors, p95_ms}, "deps": [...]}

- Only installed when TIDEWATCH_METRICS_TOKEN is set; requires `Authorization: Bearer <token>`
  (constant-time compare). Add it as the outermost middleware so rate limits, sessions and CORS
  never see the dashboard's polls.
- Records per request ONLY (duration, 5xx or not) - never URLs, routes, query strings, bodies,
  headers, user ids or PHI. errors = 5xx responses (4xx are the client's fault).
- Rolling 60 s window of 1 s buckets; p95 from a fixed log-spaced histogram (1 ms..60 s).
  Memory is constant: the http series plus at most 8 dependency series. Thread-safe, because
  pymongo reports command events from its own threads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from typing import Any

from pymongo import monitoring

WINDOW_S = 60
BUCKETS = 32
MIN_MS = 1.0
MAX_MS = 60_000.0
STEP = math.log(MAX_MS / MIN_MS) / (BUCKETS - 1)  # each bucket ~1.43x the previous
MAX_DEPS = 8
KINDS = frozenset({"service", "cache", "database", "queue", "worker"})
PATH = "/tidewatch/metrics"

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _bucket_of(ms: float) -> int:
    if ms <= MIN_MS:
        return 0
    return min(BUCKETS - 1, math.ceil(math.log(ms / MIN_MS) / STEP))


_DEP_ID = re.compile(r"[a-z0-9-]{1,16}")


class Series:
    """Request count, 5xx count and a latency histogram over the last 60 seconds."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stamp = [-1] * WINDOW_S  # which second each slot currently holds
        self._count = [0] * WINDOW_S
        self._errors = [0] * WINDOW_S
        self._hist = [[0] * BUCKETS for _ in range(WINDOW_S)]

    def record(self, ms: float, ok: bool, now: float | None = None) -> None:
        """Add one observation (duration in ms, and whether it succeeded)."""
        sec = int(time.time() if now is None else now)
        i = sec % WINDOW_S
        with self._lock:
            if self._stamp[i] != sec:
                self._stamp[i] = sec
                self._count[i] = 0
                self._errors[i] = 0
                self._hist[i] = [0] * BUCKETS
            self._count[i] += 1
            if not ok:
                self._errors[i] += 1
            self._hist[i][_bucket_of(ms)] += 1

    def totals(self, now: float | None = None) -> dict[str, float]:
        """Count, errors and p95 (upper edge of its bucket) over the window."""
        oldest = int(time.time() if now is None else now) - WINDOW_S
        merged = [0] * BUCKETS
        count = errors = 0
        with self._lock:
            for i in range(WINDOW_S):
                if self._stamp[i] <= oldest:
                    continue
                count += self._count[i]
                errors += self._errors[i]
                for b, n in enumerate(self._hist[i]):
                    merged[b] += n
        p95 = 0.0
        seen, target = 0, math.ceil(count * 0.95)
        for b, n in enumerate(merged if count else []):
            seen += n
            if seen >= target:
                p95 = MIN_MS * math.exp(b * STEP)
                break
        return {"count": count, "errors": errors, "p95_ms": round(p95, 1)}


_started = time.time()
_http = Series()
_deps: dict[str, tuple[str, Series]] = {}
_deps_lock = threading.Lock()


def record_dep(dep_id: str, kind: str, ms: float, ok: bool) -> None:
    """Record one dependency call. Unknown ids beyond 8, bad ids and bad kinds are ignored."""
    dep = _deps.get(dep_id)
    if dep is None:
        if not _DEP_ID.fullmatch(dep_id) or kind not in KINDS:
            return
        with _deps_lock:
            dep = _deps.get(dep_id)
            if dep is None:
                if len(_deps) >= MAX_DEPS:
                    return
                dep = _deps[dep_id] = (kind, Series())
    dep[1].record(ms, ok)


class Call:
    """Handle yielded by `track()`: call `fail()` for an error that did not raise (e.g. a 5xx)."""

    def __init__(self) -> None:
        self.ok = True

    def fail(self) -> None:
        """Count this call as an error."""
        self.ok = False


@asynccontextmanager
async def track(dep_id: str, kind: str) -> AsyncIterator[Call]:
    """Time one dependency call; a raise, or `call.fail()`, counts as an error.

    Usage: `async with track("ai", "service") as call: ...`
    """
    call = Call()
    t0 = time.perf_counter()
    try:
        yield call
    except BaseException:
        call.fail()
        raise
    finally:
        record_dep(dep_id, kind, (time.perf_counter() - t0) * 1000, call.ok)


class MongoListener(monitoring.CommandListener):
    """Times every MongoDB command for the "db" island (durations only, never the command)."""

    def __init__(self, dep_id: str = "db") -> None:
        self._id = dep_id

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        """Nothing to do until the command finishes."""

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:
        """Record a successful command."""
        record_dep(self._id, "database", event.duration_micros / 1000, True)

    def failed(self, event: monitoring.CommandFailedEvent) -> None:
        """Record a failed command."""
        record_dep(self._id, "database", event.duration_micros / 1000, False)


def snapshot(now: float | None = None) -> dict[str, Any]:
    """Return the JSON body served to Tidewatch."""
    now = time.time() if now is None else now
    return {
        "v": 1,
        "window_s": WINDOW_S,
        "uptime_s": int(now - _started),
        "http": _http.totals(now),
        "deps": [{"id": i, "kind": k, **s.totals(now)} for i, (k, s) in list(_deps.items())],
    }


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


class TidewatchMetrics:
    """Pure ASGI middleware: times every HTTP request and answers GET /tidewatch/metrics."""

    def __init__(self, app: ASGIApp, token: str, path: str = PATH) -> None:
        if not token:
            raise ValueError("TidewatchMetrics needs a token")
        self.app = app
        self.path = path
        self._expected = _digest(token.encode())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve the metrics path; time everything else."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] == self.path:
            await self._serve(scope, send)
            return

        status = 500  # an exception before the response starts is a server error
        t0 = time.perf_counter()

        async def send_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        finally:
            _http.record((time.perf_counter() - t0) * 1000, status < 500)

    async def _serve(self, scope: Scope, send: Send) -> None:
        headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store"),
                   (b"x-content-type-options", b"nosniff")]
        auth = b""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                auth = value
                break
        given = _digest(auth[7:] if auth.startswith(b"Bearer ") else b"")
        if scope["method"] not in ("GET", "HEAD"):
            status, body = 405, {"error": "method not allowed"}
            headers.append((b"allow", b"GET, HEAD"))
        elif not auth.startswith(b"Bearer ") or not hmac.compare_digest(given, self._expected):
            status, body = 401, {"error": "unauthorized"}
            headers.append((b"www-authenticate", b"Bearer"))
        else:
            status, body = 200, snapshot()
        payload = json.dumps(body, separators=(",", ":")).encode()
        headers.append((b"content-length", str(len(payload)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body",
                    "body": b"" if scope["method"] == "HEAD" else payload})
