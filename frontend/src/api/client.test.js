/**
 * The refresh interceptor, against a real HTTP server.
 *
 * No mock adapter: the thing worth testing here is axios's own retry and
 * header handling interacting with our interceptor, and a mock that stands in
 * for axios tests the mock. A throwaway node server is smaller than the
 * library that would replace it, and it fails for the right reasons.
 *
 * `baseURL` is overridden per test because the client ships a relative "/api",
 * which is the whole point in a browser and unusable in node.
 */

import { createServer } from "node:http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, getAccessToken, onSessionEnd, setAccessToken } from "./client.js";

/** Start a server that answers from a queue of [status, headers] per path. */
function serve(handler) {
  return new Promise((resolve) => {
    const calls = [];
    const server = createServer((req, res) => {
      calls.push({ url: req.url, method: req.method, auth: req.headers.authorization });
      handler(req, res, calls);
    });
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        calls,
        url: `http://127.0.0.1:${port}/api`,
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

const json = (res, status, body, headers = {}) => {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(JSON.stringify(body));
};

let ctx;
let sessionLost;

beforeEach(() => {
  sessionLost = vi.fn();
  onSessionEnd(sessionLost);
  setAccessToken(null);
});

afterEach(async () => {
  await ctx?.close();
  ctx = null;
  onSessionEnd(() => {});
});

describe("401 handling", () => {
  it("refreshes once and replays the original request", async () => {
    let first = true;
    ctx = await serve((req, res) => {
      if (req.url === "/api/auth/refresh") return json(res, 200, { access_token: "fresh" });
      if (first) {
        first = false;
        return json(res, 401, { detail: "expired" });
      }
      json(res, 200, { ok: true });
    });
    api.defaults.baseURL = ctx.url;
    setAccessToken("stale");

    const r = await api.get("/patients");

    expect(r.data).toEqual({ ok: true });
    expect(getAccessToken()).toBe("fresh");
    // The replay must carry the NEW token, not the one that just 401'd.
    expect(ctx.calls.at(-1).auth).toBe("Bearer fresh");
    expect(sessionLost).not.toHaveBeenCalled();
  });

  it("shares one refresh across requests that 401 together", async () => {
    // Four queries on mount is the real case. Four refreshes would rotate the
    // refresh token four times and the session would invalidate itself.
    ctx = await serve((req, res, calls) => {
      if (req.url === "/api/auth/refresh") {
        return setTimeout(() => json(res, 200, { access_token: "fresh" }), 25);
      }
      const retried = req.headers.authorization === "Bearer fresh";
      json(res, retried ? 200 : 401, retried ? { ok: true } : { detail: "expired" });
      void calls;
    });
    api.defaults.baseURL = ctx.url;
    setAccessToken("stale");

    await Promise.all([
      api.get("/a"), api.get("/b"), api.get("/c"), api.get("/d"),
    ]);

    const refreshes = ctx.calls.filter((c) => c.url === "/api/auth/refresh");
    expect(refreshes).toHaveLength(1);
  });

  it("gives up and ends the session when the refresh itself 401s", async () => {
    ctx = await serve((req, res) => json(res, 401, { detail: "no" }));
    api.defaults.baseURL = ctx.url;
    setAccessToken("stale");

    await expect(api.get("/patients")).rejects.toThrow();
    expect(getAccessToken()).toBeNull();
    expect(sessionLost).toHaveBeenCalled();
  });

  it("retries a request only once", async () => {
    ctx = await serve((req, res) => {
      if (req.url === "/api/auth/refresh") return json(res, 200, { access_token: "fresh" });
      json(res, 401, { detail: "still expired" });
    });
    api.defaults.baseURL = ctx.url;
    setAccessToken("stale");

    await expect(api.get("/patients")).rejects.toThrow();
    // One original + one replay. A second retry would loop forever.
    expect(ctx.calls.filter((c) => c.url === "/api/patients")).toHaveLength(2);
  });
});

describe("403 handling", () => {
  it("ends the session for a token that is not usable", async () => {
    ctx = await serve((req, res) => json(res, 403, { detail: "Invalid token" }));
    api.defaults.baseURL = ctx.url;
    setAccessToken("bad");

    await expect(api.get("/patients")).rejects.toThrow();
    expect(getAccessToken()).toBeNull();
    expect(sessionLost).toHaveBeenCalled();
  });

  it("keeps the session when the refusal is about the action, not the credential", async () => {
    // The MFA wall. Signing this caller out would clear the Security screen the
    // message tells them to open — the lockout the grace period exists to avoid.
    ctx = await serve((req, res) =>
      json(res, 403, { detail: "Two-factor authentication is required." },
        { "X-Credential-Valid": "1" }));
    api.defaults.baseURL = ctx.url;
    setAccessToken("good");

    await expect(api.get("/observations/x/panels")).rejects.toThrow();
    expect(getAccessToken()).toBe("good");
    expect(sessionLost).not.toHaveBeenCalled();
  });

  it("never retries a 403", async () => {
    ctx = await serve((req, res) => json(res, 403, { detail: "Invalid token" }));
    api.defaults.baseURL = ctx.url;
    setAccessToken("bad");

    await expect(api.get("/patients")).rejects.toThrow();
    expect(ctx.calls.filter((c) => c.url === "/api/auth/refresh")).toHaveLength(0);
  });
});

describe("the refresh call itself", () => {
  it("is never retried, so a failure cannot recurse", async () => {
    ctx = await serve((req, res) => json(res, 401, { detail: "no" }));
    api.defaults.baseURL = ctx.url;

    await expect(api.post("/auth/refresh", null, { _skipRetry: true })).rejects.toThrow();
    expect(ctx.calls).toHaveLength(1);
  });
});
