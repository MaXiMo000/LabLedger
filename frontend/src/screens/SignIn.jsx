import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, messageFor } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import IntervalRail from "../components/IntervalRail";
import "./SignIn.css";

/**
 * Sign in, or create an account.
 *
 * Split rather than centred: the form takes the left column, and the right
 * carries a single resolved reading — the same components the app uses, with
 * real values. It states what you are signing into instead of asserting it,
 * and it costs nothing, because every piece is already built.
 *
 * Errors never say which half of the credentials was wrong. The API refuses to
 * distinguish an unknown email from a wrong password, and the interface must
 * not undo that.
 */

const PROOF = [
  { lab: "Quest Diagnostics", printed: "FERRITIN, SERUM", value: 18, unit: "ng/mL",
    low: 24, high: 336, flag: "low" },
  { lab: "LabCorp", printed: "HGB", value: 14.6, unit: "g/dL",
    low: 13.0, high: 17.7, flag: "normal" },
  { lab: "Sutter Health", printed: "FERRTN SER", value: 40, unit: "µg/L",
    low: 24, high: 336, flag: "normal" },
];

// The API's word for "the password was right, now prove the second factor".
// Matched rather than inferred from the status: a plain 401 here means the
// credentials were wrong, and the two must not look the same to the user.
const MFA_REQUIRED = "Verification code required";

export default function SignIn() {
  const [mode, setMode] = useState("in"); // in | up
  const [form, setForm] = useState({ email: "", name: "", password: "", code: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [needsCode, setNeedsCode] = useState(false);
  const [forgot, setForgot] = useState(null);  // null | "asking" | "sent"

  const { signIn, register } = useAuth();
  const navigate = useNavigate();
  // Where the user was going before they were sent here. An invitation link
  // lands on /signin, and dropping them at /app afterwards would strand them
  // one step short of the thing they clicked.
  const from = useLocation().state?.from ?? "/app";

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const creating = mode === "up";

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (creating) await register(form.email, form.name, form.password);
      else await signIn(form.email, form.password, form.code || undefined);
      navigate(from, { replace: true });
    } catch (err) {
      const message = messageFor(err);
      if (message === MFA_REQUIRED) {
        // Not an error the first time: the password was accepted, and the form
        // is simply asking for the rest of it.
        setNeedsCode(true);
        setError(null);
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="signin">
      <div className="signin__grid">
        <div className="signin__panel">
          <Link to="/" className="signin__mark">LabLedger</Link>

          <h1 className="signin__title">
            {creating ? "Create your account" : "Sign in"}
          </h1>
          <p className="signin__sub">
            {creating
              ? "Your reports are encrypted before they are stored. Only accounts you grant access to can read them."
              : "Your reports are encrypted at rest and private to the records you can reach."}
          </p>

          <form onSubmit={submit} noValidate className="stack signin__form">
            {creating && (
              <label className="field">
                <span className="field__label">Name</span>
                <input
                  className="field__input"
                  value={form.name}
                  onChange={set("name")}
                  autoComplete="name"
                  required
                />
              </label>
            )}

            <label className="field">
              <span className="field__label">Email</span>
              <input
                className="field__input"
                type="email"
                value={form.email}
                onChange={set("email")}
                autoComplete="email"
                required
              />
            </label>

            <label className="field">
              <span className="field__label">Password</span>
              <input
                className="field__input"
                type="password"
                value={form.password}
                onChange={set("password")}
                autoComplete={creating ? "new-password" : "current-password"}
                minLength={creating ? 12 : undefined}
                required
              />
              {creating && <span className="field__hint">At least 12 characters.</span>}
            </label>

            {needsCode && (
              <label className="field">
                <span className="field__label">Verification code</span>
                <input
                  className="field__input num"
                  value={form.code}
                  onChange={set("code")}
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  // Appears mid-flow, in response to a submit the user just
                  // made — the cursor belongs in it.
                  autoFocus
                  required
                />
                <span className="field__hint">
                  The six digits from your authenticator app.
                </span>
              </label>
            )}

            {error && <p className="field__error" role="alert">{error}</p>}

            <button className="btn btn--primary signin__submit" disabled={busy}>
              {busy ? "Working…" : creating ? "Create account" : "Sign in"}
            </button>
          </form>

          <div className="signin__or"><span>or</span></div>

          {/* A full navigation, not fetch: the OAuth handshake ends in a
              redirect that sets the refresh cookie. */}
          <a className="btn btn--quiet signin__google" href="/api/auth/google">
            Continue with Google
          </a>

          {/* Always the same answer, sent or not: telling somebody their
              address is unknown would turn this into a way to find out who
              holds a record here. */}
          {!creating && (
            <p className="signin__switch">
              {forgot === "sent" ? (
                <span className="field__hint">
                  If that address has an account, a reset link is on its way.
                  It expires in 30 minutes. This works for accounts created
                  through Google too — it adds a password rather than
                  replacing the Google sign-in.
                </span>
              ) : (
                <button
                  type="button"
                  className="signin__toggle"
                  disabled={forgot === "asking"}
                  onClick={async () => {
                    if (!form.email) { setError("Enter your email first."); return; }
                    setForgot("asking");
                    setError(null);
                    try {
                      await api.post("/auth/password/reset", { email: form.email });
                    } catch { /* the answer is the same either way */ }
                    setForgot("sent");
                  }}
                >
                  {forgot === "asking" ? "Sending…" : "Forgot your password?"}
                </button>
              )}
            </p>
          )}

          <p className="signin__switch">
            {creating ? "Already have an account?" : "No account yet?"}{" "}
            <button
              type="button"
              className="signin__toggle"
              onClick={() => { setMode(creating ? "in" : "up"); setError(null); }}
            >
              {creating ? "Sign in" : "Create one"}
            </button>
          </p>
        </div>

        {/* The right column is the product, not an illustration of it: three
            labs' spellings of two tests, resolved, with their intervals. */}
        <aside className="signin__proof" aria-hidden="true">
          <p className="eyebrow">What you are signing into</p>
          <ul className="proof">
            {PROOF.map((p) => (
              <li key={p.printed} className="proof__row">
                <div className="proof__head">
                  <span className="proof__printed num">{p.printed}</span>
                  <span className="proof__lab">{p.lab}</span>
                </div>
                <div className="proof__reading">
                  <span className="proof__value num">{p.value}</span>
                  <span className="proof__unit num">{p.unit}</span>
                  <IntervalRail
                    value={p.value}
                    low={p.low}
                    high={p.high}
                    flag={p.flag}
                    size="panel"
                  />
                </div>
              </li>
            ))}
          </ul>
          <p className="proof__note">
            Three spellings, two units, one chart. Every number traceable to the
            page it came from.
          </p>
        </aside>
      </div>
    </div>
  );
}
