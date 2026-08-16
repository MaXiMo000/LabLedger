import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, onSessionEnd, setAccessToken } from "../api/client";
import { useIdleSignOut } from "../hooks/useIdleSignOut";

const AuthContext = createContext(null);

/**
 * Session state.
 *
 * On mount the app has no access token — it was never persisted. It asks the
 * server to mint one from the refresh cookie. That request either succeeds
 * (signed in) or 401s (signed out), and until it settles the app is in a third
 * state, `loading`, which the router must respect. Treating "no token yet" as
 * "signed out" would bounce a returning user to the login screen on every
 * reload.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState("loading"); // loading | in | out

  const signOutLocal = useCallback(() => {
    setAccessToken(null);
    setUser(null);
    setStatus("out");
  }, []);

  useEffect(() => {
    onSessionEnd(signOutLocal);
  }, [signOutLocal]);

  // Restore the session once, on mount.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { data } = await api.post("/auth/refresh", null, { _skipRetry: true });
        setAccessToken(data.access_token);
        const me = await api.get("/auth/me");
        if (!alive) return;
        setUser(me.data);
        setStatus("in");
      } catch {
        if (alive) signOutLocal();
      }
    })();
    return () => {
      alive = false;
    };
  }, [signOutLocal]);

  // Re-read the account after something changes it server-side (enrolling in
  // MFA changes three fields at once, and setUser with a partial would drift).
  const refreshUser = useCallback(async () => {
    const me = await api.get("/auth/me");
    setUser(me.data);
    return me.data;
  }, []);

  const establish = useCallback(async (token) => {
    setAccessToken(token);
    const me = await api.get("/auth/me");
    setUser(me.data);
    setStatus("in");
    return me.data;
  }, []);

  const signIn = useCallback(
    async (email, password, code) => {
      const { data } = await api.post("/auth/login", { email, password, code });
      return establish(data.access_token);
    },
    [establish]
  );

  const register = useCallback(
    async (email, name, password) => {
      const { data } = await api.post("/auth/register", { email, name, password });
      return establish(data.access_token);
    },
    [establish]
  );

  const signOut = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      signOutLocal();
    }
  }, [signOutLocal]);

  // Clears results off an unattended screen a minute before the server ends
  // the session. Zero while signed out, so the timer never runs on the landing
  // page or the sign-in form.
  useIdleSignOut(status === "in" ? user?.idle_timeout_min : 0, signOut);

  const value = useMemo(
    () => ({ user, status, signIn, register, signOut, establish, setUser, refreshUser }),
    [user, status, signIn, register, signOut, establish, refreshUser]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
