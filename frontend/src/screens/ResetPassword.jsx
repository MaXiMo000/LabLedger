import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, messageFor } from "../api/client";
import "./Invite.css";

/**
 * Setting a new password from an emailed link.
 *
 * Reuses the invitation panel's layout: both are a single decision arrived at
 * from outside the app, with nothing to compare against, so the split sign-in
 * layout would be two-thirds empty.
 *
 * Succeeding here signs every device out, including whoever is holding the
 * account right now — a reset is what somebody does when they have lost
 * control of it, and leaving the existing sessions alive would hand the new
 * password to a stranger and change nothing for the person who took it.
 */
export default function ResetPassword() {
  const { token } = useParams();
  const navigate = useNavigate();
  const [form, setForm] = useState({ next: "", confirm: "" });
  const [error, setError] = useState(null);

  const submit = useMutation({
    mutationFn: () =>
      api.post("/auth/password/reset/confirm", {
        token, new_password: form.next,
      }),
    onSuccess: () => setTimeout(() => navigate("/signin", { replace: true }), 1500),
    onError: (e) => setError(messageFor(e)),
  });

  const mismatch = form.confirm.length > 0 && form.next !== form.confirm;
  const ready = form.next.length >= 12 && !mismatch;

  return (
    <main className="inv">
      <div className="inv__panel">
        <Link to="/" className="inv__mark">LabLedger</Link>

        {submit.isSuccess ? (
          <>
            <p className="eyebrow">Done</p>
            <h1 className="inv__title">Password changed</h1>
            <p className="inv__body">
              Every device has been signed out. Taking you to sign in&hellip;
            </p>
          </>
        ) : (
          <>
            <h1 className="inv__title">Choose a new password</h1>
            <p className="inv__body">
              This signs out every device currently using the account.
            </p>

            <form
              className="stack"
              onSubmit={(e) => { e.preventDefault(); setError(null); submit.mutate(); }}
            >
              <label className="field">
                <span className="field__label">New password</span>
                <input
                  className="field__input" type="password" autoComplete="new-password"
                  minLength={12} value={form.next}
                  onChange={(e) => setForm({ ...form, next: e.target.value })}
                  // eslint-disable-next-line jsx-a11y/no-autofocus
                  autoFocus required
                />
                <span className="field__hint">At least 12 characters.</span>
              </label>
              <label className="field">
                <span className="field__label">Confirm new password</span>
                <input
                  className="field__input" type="password" autoComplete="new-password"
                  value={form.confirm}
                  onChange={(e) => setForm({ ...form, confirm: e.target.value })}
                  required
                />
                {mismatch && <span className="field__error">These do not match.</span>}
              </label>

              {error && (
                <>
                  <p className="field__error" role="alert">{error}</p>
                  <Link className="inv__go btn btn--quiet" to="/signin">
                    Back to sign in
                  </Link>
                </>
              )}

              <button
                className="btn btn--primary inv__go"
                disabled={!ready || submit.isPending}
              >
                {submit.isPending ? "Saving…" : "Set password"}
              </button>
            </form>
          </>
        )}
      </div>
    </main>
  );
}
