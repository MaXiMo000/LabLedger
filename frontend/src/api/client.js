import axios from "axios";

/**
 * The API client.
 *
 * The access token lives in this module's closure and nowhere else — not in
 * localStorage, not in sessionStorage, not on window. Any XSS that runs on
 * this page can read those stores; it cannot read a closure variable it has no
 * reference to. Reloading therefore loses the token on purpose: the httpOnly
 * refresh cookie restores the session on mount, at the cost of one request.
 *
 * Requests go to a same-origin /api path, proxied to :8000 in dev, so the
 * refresh cookie stays SameSite in development exactly as it will in
 * production behind one domain.
 */

let accessToken = null;
let onSessionLost = () => {};

export function setAccessToken(token) {
  accessToken = token;
}

export function getAccessToken() {
  return accessToken;
}

export function onSessionEnd(fn) {
  onSessionLost = fn;
}

export const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
  withCredentials: true, // the refresh cookie
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
  return config;
});

// A single in-flight refresh, shared by every request that 401s at once.
// Without this, a screen that fires four queries on mount would rotate the
// refresh token four times and invalidate its own session.
let refreshing = null;

async function refresh() {
  refreshing ??= api
    .post("/auth/refresh", null, { _skipRetry: true })
    .then((r) => {
      setAccessToken(r.data.access_token);
      return r.data.access_token;
    })
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const { response, config } = error;

    // 401 means "your token expired, refresh it". 403 means "this token is not
    // valid here" — a different situation, and not one a retry fixes. The
    // backend splits these deliberately; the client has to honour the split or
    // the short token lifetime becomes a logout every fifteen minutes.
    if (response?.status === 401 && config && !config._retried && !config._skipRetry) {
      config._retried = true;
      try {
        const token = await refresh();
        config.headers.Authorization = `Bearer ${token}`;
        return api(config);
      } catch {
        setAccessToken(null);
        onSessionLost();
      }
    }

    // `deps` raises 403 for a token that cannot be used at all, so dropping the
    // session is right by default. But two refusals are about the *action*
    // rather than the credential — the MFA wall, and an invitation addressed to
    // someone else — and both mark themselves. Signing those callers out would
    // clear the screen the message just told them to open, which for the MFA
    // wall is precisely the lockout the backend's grace period exists to avoid.
    if (response?.status === 403 && response.headers?.["x-credential-valid"] !== "1") {
      setAccessToken(null);
      onSessionLost();
    }

    return Promise.reject(error);
  }
);

/** Human-readable message from a FastAPI error, without leaking internals. */
export function messageFor(error) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  if (error?.response?.status === 429) return "Too many attempts. Wait a minute and try again.";
  if (!error?.response) return "Cannot reach the server. Is the API running on :8000?";
  return "Something went wrong. Try again.";
}
