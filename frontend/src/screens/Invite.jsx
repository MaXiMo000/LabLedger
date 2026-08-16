import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, messageFor } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import "./Invite.css";

/**
 * Accepting an invitation to somebody's record.
 *
 * The page does almost nothing on purpose. It cannot show whose record this is
 * before you have signed in — a link forwarded to the wrong person would
 * otherwise announce that a named patient exists — so an anonymous visitor is
 * told only that an invitation needs an account, and everything else waits.
 *
 * Signing in returns here rather than to the app, so accepting is one
 * uninterrupted move even when it starts with creating the account.
 */
export default function Invite() {
  const { token } = useParams();
  const { status, user } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const claim = useMutation({
    mutationFn: async () =>
      (await api.post("/auth/invites/claim", { token })).data,
    onSuccess: (data) => {
      // The new record has to exist in the switcher before we send them to it.
      qc.invalidateQueries({ queryKey: ["patients"] });
      setTimeout(() => navigate("/app", { replace: true }), 1200);
      return data;
    },
  });

  // Fires once, as soon as there is a session to attach the access to.
  useEffect(() => {
    if (status === "in" && claim.isIdle) claim.mutate();
  }, [status, claim]);

  if (status === "loading") {
    return <div className="boot" role="status">Checking your session…</div>;
  }

  return (
    <main className="inv">
      <div className="inv__panel">
        <Link to="/" className="inv__mark">LabLedger</Link>

        {status === "out" ? (
          <>
            <h1 className="inv__title">You have been invited to a record</h1>
            <p className="inv__body">
              Sign in to accept it, or create an account first. Use the address
              the invitation was sent to — it will not work with another.
            </p>
            <Link
              className="btn btn--primary inv__go"
              to="/signin"
              state={{ from: `/invite/${token}` }}
            >
              Sign in or create an account
            </Link>
          </>
        ) : claim.isSuccess ? (
          <>
            <p className="eyebrow">Accepted</p>
            <h1 className="inv__title">{claim.data.display_name}</h1>
            <p className="inv__body">
              You now have <strong>{claim.data.role}</strong> access to this
              record. Opening it&hellip;
            </p>
          </>
        ) : claim.isError ? (
          <>
            <h1 className="inv__title">This invitation cannot be used</h1>
            <p className="inv__body">{messageFor(claim.error)}</p>
            <p className="inv__body inv__body--faint">
              You are signed in as <span className="num">{user?.email}</span>.
              An invitation only works for the address it was sent to, and it
              stops working once it has been accepted or has expired.
            </p>
            <Link className="btn btn--quiet inv__go" to="/app">Go to your records</Link>
          </>
        ) : (
          <div className="boot" role="status">Accepting the invitation…</div>
        )}
      </div>
    </main>
  );
}
