import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, messageFor } from "../api/client";
import Modal from "../components/Modal";
import { useAuth } from "../auth/AuthContext";
import "./Security.css";

/**
 * The account's own security: where it is signed in, and what a sign-in takes.
 *
 * Laid out as a panel of readings, like every other screen here — a count, a
 * state, and a flag when a state sits outside where this account should be.
 * Two-factor off on an account that reaches a record it did not create is
 * genuinely out of range, so it is marked, in the same quiet tone a high
 * result is marked in. Nothing on this screen shouts.
 */

function when(iso) {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? `today ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`
    : d.toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      });
}

/** Groups of four, the way an authenticator key is printed to be transcribed. */
function grouped(secret) {
  return (secret.match(/.{1,4}/g) ?? []).join(" ");
}

function Devices({ sessions, isPending }) {
  const [error, setError] = useState(null);
  const qc = useQueryClient();
  const { signOut } = useAuth();

  const revoke = useMutation({
    mutationFn: (id) => api.delete(`/auth/sessions/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
    onError: (e) => setError(messageFor(e)),
  });

  const revokeAll = useMutation({
    // Ends this device too, so there is nothing left to refetch — drop the
    // local session rather than letting the next request discover it.
    mutationFn: () => api.post("/auth/sessions/revoke-all"),
    onSuccess: () => signOut(),
    onError: (e) => setError(messageFor(e)),
  });

  return (
    <section className="sec__block">
      <div className="sec__head">
        <h2 className="sec__h">Where you are signed in</h2>
      </div>
      <p className="sec__note">
        Ending a session takes effect on that device&rsquo;s next request, not
        when its token expires.
      </p>

      {isPending ? (
        <p className="muted">Loading&hellip;</p>
      ) : (
        <ul className="devices">
          {sessions.map((s) => (
            <li key={s.id} className="device">
              <span className="device__name">
                <span className="device__label">{s.device ?? "Unknown device"}</span>
                {s.current && <span className="device__here">This device</span>}
              </span>
              <span className="device__ip num">{s.ip ?? ""}</span>
              <span className="device__seen num">Last used {when(s.last_seen)}</span>
              {/* Every row gets the same action, because a column that is
                  present on some rows and blank on others reads as a bug
                  rather than as a rule. Ending the current session is just
                  signing out, so it says so. */}
              <button
                className="device__end"
                onClick={() => (s.current ? signOut() : revoke.mutate(s.id))}
                disabled={revoke.isPending}
              >
                {s.current ? "Sign out" : "End session"}
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="field__error" role="alert">{error}</p>}

      <div className="sec__panic">
        <p>Signs out every device, this one included.</p>
        <button onClick={() => revokeAll.mutate()} disabled={revokeAll.isPending}>
          {revokeAll.isPending ? "Signing out…" : "Sign out everywhere"}
        </button>
      </div>
    </section>
  );
}

function TwoFactor() {
  const { user, setUser, refreshUser } = useAuth();
  const [setup, setSetup] = useState(null);   // { secret, uri, qr_svg } while enrolling
  const [manual, setManual] = useState(false); // typed key instead of the QR
  const [codes, setCodes] = useState(null);    // recovery codes, shown once
  const [code, setCode] = useState("");
  const [error, setError] = useState(null);

  const close = () => {
    setSetup(null);
    setManual(false);
    setCode("");
    setError(null);
  };

  const begin = useMutation({
    mutationFn: async () => (await api.post("/auth/mfa/setup")).data,
    onSuccess: (d) => { setError(null); setSetup(d); },
    onError: (e) => setError(messageFor(e)),
  });

  const enable = useMutation({
    mutationFn: async () => (await api.post("/auth/mfa/enable", { code })).data,
    onSuccess: async (d) => { close(); setCodes(d.codes); await refreshUser(); },
    onError: (e) => setError(messageFor(e)),
  });

  const reissue = useMutation({
    mutationFn: async () => (await api.post("/auth/mfa/recovery", { code })).data,
    onSuccess: async (d) => { close(); setCodes(d.codes); await refreshUser(); },
    onError: (e) => setError(messageFor(e)),
  });

  const disable = useMutation({
    mutationFn: async () => (await api.post("/auth/mfa/disable", { code })).data,
    onSuccess: (updated) => { close(); setUser(updated); },
    onError: (e) => setError(messageFor(e)),
  });

  const on = user?.mfa_enabled;
  const flagged = user?.mfa_recommended;
  const deadline = user?.mfa_deadline ? new Date(user.mfa_deadline) : null;

  const codeField = (label) => (
    <label className="field">
      <span className="field__label">{label}</span>
      <input
        className="field__input num sec__code"
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
        inputMode="numeric"
        autoComplete="one-time-code"
        placeholder="000000"
        required
      />
    </label>
  );

  return (
    <section className="sec__block">
      <div className="sec__head">
        <h2 className="sec__h">Two-factor authentication</h2>
        {/* One word. The tone carries "and it should not be", and the note
            below says so in full — a chip that spells out the whole
            recommendation wraps onto two lines on a phone. */}
        <span className={`sec__chip ${on ? "sec__chip--on" : flagged ? "sec__chip--flag" : ""}`}>
          {on ? "On" : "Off"}
        </span>
      </div>

      <p className="sec__note">
        {on
          ? "Signing in asks for a code from your authenticator app as well as your password."
          : flagged
            ? "This account can reach a record it did not create. Without a second factor, a password is the only thing between someone else and that record."
            : "Signing in needs only your password."}
      </p>

      {/* A wall nobody saw coming is an outage. Sign-in is never blocked, so
          this screen stays reachable even after the date passes. */}
      {deadline && (
        <p className={`sec__deadline ${deadline < new Date() ? "sec__deadline--past" : ""}`}>
          {deadline < new Date()
            ? "Records shared with you are closed until you turn this on. Your own records are unaffected."
            : `Required by ${deadline.toLocaleDateString(undefined, { day: "numeric", month: "long" })} to keep opening records shared with you.`}
        </p>
      )}

      {on ? (
        <>
          <p className="sec__note">
            <span className="num">{user.recovery_codes_left}</span> recovery
            code{user.recovery_codes_left === 1 ? "" : "s"} left — each signs
            you in once if you lose your phone.
          </p>
          <div className="sec__actions">
            <button className="btn btn--quiet" onClick={() => setSetup("reissue")}>
              New recovery codes
            </button>
            <button className="btn btn--quiet" onClick={() => setSetup("off")}>
              Turn off
            </button>
          </div>
        </>
      ) : (
        <div className="sec__actions">
          <button
            className="btn btn--primary"
            onClick={() => { setError(null); begin.mutate(); }}
            disabled={begin.isPending}
          >
            {begin.isPending ? "Preparing…" : "Set up"}
          </button>
        </div>
      )}

      {error && !setup && <p className="field__error" role="alert">{error}</p>}

      {/* --- enrolment, in a dialog ------------------------------------- */}
      {setup && typeof setup === "object" && (
        <Modal
          title="Scan this with your authenticator app"
          description="Google Authenticator, 1Password, Authy — any of them."
          onClose={close}
          footer={
            <>
              <button className="btn btn--quiet" onClick={close}>Cancel</button>
              <button
                className="btn btn--primary"
                form="mfa-enrol"
                disabled={enable.isPending || code.length < 6}
              >
                {enable.isPending ? "Checking…" : "Turn on"}
              </button>
            </>
          }
        >
          <div className="qr">
            {/* The SVG is built server-side from the same URI the key encodes;
                `currentColor` makes it take the page's ink. */}
            <div
              className="qr__code"
              role="img"
              aria-label="QR code for your authenticator app"
              dangerouslySetInnerHTML={{ __html: setup.qr_svg }}
            />
          </div>

          {/* A desktop authenticator has no camera, and some phones refuse to
              scan a screen. The key is the same secret, not a second path. */}
          {manual ? (
            <dl className="sec__key">
              <div>
                <dt>Account</dt>
                <dd className="num">{user?.email}</dd>
              </div>
              <div>
                <dt>Key</dt>
                <dd className="num sec__secret">{grouped(setup.secret)}</dd>
              </div>
            </dl>
          ) : (
            <button type="button" className="qr__manual" onClick={() => setManual(true)}>
              Can’t scan it? Enter the key by hand
            </button>
          )}

          <form
            id="mfa-enrol"
            onSubmit={(e) => { e.preventDefault(); setError(null); enable.mutate(); }}
          >
            {codeField("Then enter the code it shows")}
            {error && <p className="field__error" role="alert">{error}</p>}
          </form>
        </Modal>
      )}

      {/* --- reissue / turn off, both a code prompt ---------------------- */}
      {(setup === "reissue" || setup === "off") && (
        <Modal
          title={setup === "off" ? "Turn off two-factor authentication" : "New recovery codes"}
          description={
            setup === "off"
              ? "Your password alone will be enough to sign in, and your recovery codes are destroyed."
              : "The codes you have now stop working immediately."
          }
          onClose={close}
          tone={setup === "off" ? "danger" : "neutral"}
          footer={
            <>
              <button className="btn btn--quiet" onClick={close}>Cancel</button>
              <button
                className="btn btn--primary"
                form="mfa-confirm"
                disabled={code.length < 6 || disable.isPending || reissue.isPending}
              >
                {setup === "off" ? "Turn off" : "Replace codes"}
              </button>
            </>
          }
        >
          <form
            id="mfa-confirm"
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              (setup === "off" ? disable : reissue).mutate();
            }}
          >
            {codeField("Current code from your app")}
            {error && <p className="field__error" role="alert">{error}</p>}
          </form>
        </Modal>
      )}

      {/* --- the codes, shown exactly once ------------------------------- */}
      {codes && (
        <Modal
          title="Save your recovery codes"
          description="Each one signs you in once if you lose your phone. This is the only time they are shown."
          onClose={() => setCodes(null)}
          footer={
            <>
              <button
                className="btn btn--quiet"
                onClick={() => navigator.clipboard?.writeText(codes.join("\n"))}
              >
                Copy all
              </button>
              <button className="btn btn--primary" onClick={() => setCodes(null)}>
                I have saved them
              </button>
            </>
          }
        >
          <ul className="recovery">
            {codes.map((c) => <li key={c} className="num">{c}</li>)}
          </ul>
        </Modal>
      )}
    </section>
  );
}

export default function Security() {
  const { user } = useAuth();

  const { data: sessions = [], isPending } = useQuery({
    queryKey: ["sessions"],
    queryFn: async () => (await api.get("/auth/sessions")).data,
  });

  return (
    <>
      <header className="screen__head">
        <div>
          <p className="eyebrow">Account</p>
          <h1 className="screen__title">
            {isPending ? "Security" : `${sessions.length} device${sessions.length === 1 ? "" : "s"} signed in`}
          </h1>
        </div>
        <dl className="tally">
          <div>
            <dt>Second factor</dt>
            <dd className={`num ${user?.mfa_recommended ? "tally--flag" : ""}`}>
              {user?.mfa_enabled ? "On" : "Off"}
            </dd>
          </div>
          <div>
            <dt>Locks after</dt>
            <dd className="num">{user?.idle_timeout_min ?? 30} min</dd>
          </div>
        </dl>
      </header>

      <Devices sessions={sessions} isPending={isPending} />
      <TwoFactor />
      <Password />
      <YourData />
      {user?.role === "admin" && <AdminTools adminEmail={user.email} />}
    </>
  );
}

/**
 * The one thing the admin role can do, and the only screen it has.
 *
 * Shown to nobody else — but the hiding is courtesy, not security: the route
 * behind it checks the role itself, because a section that renders based on a
 * field the client was handed is a decoration, not a permission.
 *
 * Deliberately the last block on the page, under the account's own settings. An
 * operator acting on somebody else's account should have had to scroll past
 * their own.
 */
function AdminTools({ adminEmail }) {
  const [email, setEmail] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState(null);
  const [error, setError] = useState(null);

  const reset = useMutation({
    mutationFn: () => api.post("/auth/admin/mfa/reset", { email: email.trim().toLowerCase() }),
    onSuccess: () => {
      setDone(email.trim().toLowerCase());
      setEmail("");
      setConfirming(false);
    },
    onError: (e) => { setError(messageFor(e)); setConfirming(false); },
  });

  const self = email.trim().toLowerCase() === adminEmail.toLowerCase();

  return (
    <section className="sec__block sec__block--admin">
      <div className="sec__head">
        <h2 className="sec__h">Clear someone's second factor</h2>
        <span className="sec__badge">admin</span>
      </div>
      <p className="sec__note">
        For an account that has lost its authenticator <em>and</em> every
        recovery code. Anyone still holding a recovery code can do this
        themselves under their own Two-factor settings — check that first, because
        this ends every session they have open.
      </p>

      <form
        className="sec__enrol"
        onSubmit={(e) => { e.preventDefault(); setError(null); setDone(null); setConfirming(true); }}
      >
        <label className="field">
          <span className="field__label">Account email</span>
          <input
            className="field__input"
            type="email"
            required
            value={email}
            onChange={(e) => { setEmail(e.target.value); setDone(null); setError(null); }}
            placeholder="them@example.com"
          />
        </label>
        <button className="btn btn--quiet" disabled={!email.trim() || self || reset.isPending}>
          {reset.isPending ? "Clearing…" : "Clear second factor"}
        </button>
      </form>

      {/* Caught here as well as by the server, so the reason is visible before
          the click rather than as a rejection after it. */}
      {self && (
        <p className="sec__note sec__note--warn">
          That is your own account. Use Two-factor authentication above — it asks
          for a code, and this does not.
        </p>
      )}
      {error && <p className="field__error" role="alert">{error}</p>}
      {done && (
        <p className="sec__note" role="status">
          Cleared for {done}. They sign in with their password alone and can
          enrol again from their own Security screen.
        </p>
      )}

      {confirming && (
        <Modal
          title={`Clear two-factor for ${email.trim().toLowerCase()}?`}
          description="Their second factor is removed and every session they have open ends. They will be able to sign in with just their password until they enrol again."
          tone="danger"
          onClose={() => setConfirming(false)}
          footer={
            <>
              <button className="btn btn--quiet" onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button className="btn btn--danger" onClick={() => reset.mutate()}
                      disabled={reset.isPending}>
                {reset.isPending ? "Clearing…" : "Clear it"}
              </button>
            </>
          }
        />
      )}
    </section>
  );
}

function Password() {
  const { user, refreshUser } = useAuth();
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  const first = user && !user.has_password;

  const save = useMutation({
    mutationFn: () => api.post("/auth/password", {
      current_password: first ? undefined : form.current,
      new_password: form.next,
    }),
    onSuccess: async () => {
      setForm({ current: "", next: "", confirm: "" });
      setError(null);
      setDone(true);
      await refreshUser();
    },
    onError: (e) => { setDone(false); setError(messageFor(e)); },
  });

  const mismatch = form.confirm.length > 0 && form.next !== form.confirm;
  const ready = form.next.length >= 12 && !mismatch && (first || form.current);

  return (
    <section className="sec__block">
      <div className="sec__head">
        <h2 className="sec__h">{first ? "Set a password" : "Password"}</h2>
      </div>
      <p className="sec__note">
        {first
          ? "This account signs in with Google. Adding a password gives you a second way in."
          : "Changing it signs out every other device. This one stays signed in."}
      </p>

      <form
        className="sec__enrol"
        onSubmit={(e) => { e.preventDefault(); setError(null); save.mutate(); }}
      >
        {!first && (
          <label className="field">
            <span className="field__label">Current password</span>
            <input
              className="field__input" type="password" autoComplete="current-password"
              value={form.current}
              onChange={(e) => setForm({ ...form, current: e.target.value })}
            />
          </label>
        )}
        <label className="field">
          <span className="field__label">New password</span>
          <input
            className="field__input" type="password" autoComplete="new-password"
            minLength={12} value={form.next}
            onChange={(e) => setForm({ ...form, next: e.target.value })}
          />
          <span className="field__hint">At least 12 characters.</span>
        </label>
        <label className="field">
          <span className="field__label">Confirm new password</span>
          <input
            className="field__input" type="password" autoComplete="new-password"
            value={form.confirm}
            onChange={(e) => setForm({ ...form, confirm: e.target.value })}
          />
          {/* Caught here rather than by the server, which cannot see it. */}
          {mismatch && <span className="field__error">These do not match.</span>}
        </label>

        {error && <p className="field__error" role="alert">{error}</p>}
        {done && <p className="sec__note" role="status">Password changed.</p>}

        <div className="sec__actions">
          <button className="btn btn--primary" disabled={!ready || save.isPending}>
            {save.isPending ? "Saving…" : first ? "Set password" : "Change password"}
          </button>
        </div>
      </form>
    </section>
  );
}

function YourData() {
  const { user, signOut } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);

  const download = useMutation({
    mutationFn: async () => (await api.get("/auth/export")).data,
    onSuccess: (data) => {
      // Built in the browser from a response it already has: the alternative
      // is a second authenticated request from a plain link, which cannot
      // carry the token this app deliberately keeps out of storage.
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = `labledger-account-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    },
    onError: (e) => setError(messageFor(e)),
  });

  const remove = useMutation({
    mutationFn: () => api.request({
      method: "DELETE", url: "/auth/me", data: { password },
    }),
    onSuccess: () => signOut(),
    onError: (e) => setError(messageFor(e)),
  });

  return (
    <section className="sec__block">
      <div className="sec__head">
        <h2 className="sec__h">Your data</h2>
      </div>
      <p className="sec__note">
        The export covers this account — your profile, devices, the printed
        names you have confirmed, and which records you can reach. Results
        themselves are exported per record, by its owner, from the Record screen.
      </p>

      <div className="sec__actions">
        <button
          className="btn btn--quiet"
          onClick={() => download.mutate()}
          disabled={download.isPending}
        >
          {download.isPending ? "Preparing…" : "Download my data"}
        </button>
      </div>

      <div className="sec__panic">
        <p>
          Deleting this account also deletes any record only you can reach, and
          the results in it. It cannot be undone.
        </p>
        <button onClick={() => { setError(null); setConfirming(true); }}>
          Delete account
        </button>
      </div>

      {error && !confirming && <p className="field__error" role="alert">{error}</p>}

      {confirming && (
        <Modal
          title="Delete this account?"
          description="Records only you can reach are deleted with it, and the results in them. This cannot be undone."
          tone="danger"
          onClose={() => { setConfirming(false); setPassword(""); setError(null); }}
          footer={
            <>
              <button
                className="btn btn--quiet"
                onClick={() => { setConfirming(false); setPassword(""); }}
              >
                Keep my account
              </button>
              <button
                className="btn btn--primary"
                form="delete-account"
                disabled={remove.isPending || (user?.has_password && !password)}
              >
                {remove.isPending ? "Deleting…" : "Delete permanently"}
              </button>
            </>
          }
        >
          <form
            id="delete-account"
            onSubmit={(e) => { e.preventDefault(); setError(null); remove.mutate(); }}
          >
            {user?.has_password && (
              <label className="field">
                <span className="field__label">Your password, to confirm</span>
                <input
                  className="field__input" type="password" autoComplete="current-password"
                  value={password} onChange={(e) => setPassword(e.target.value)}
                />
              </label>
            )}
            {error && <p className="field__error" role="alert">{error}</p>}
          </form>
        </Modal>
      )}
    </section>
  );
}
