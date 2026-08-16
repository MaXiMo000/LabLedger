import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, messageFor } from "../api/client";
import Modal from "../components/Modal";
import Select from "../components/Select";
import { useAuth } from "../auth/AuthContext";
import { usePatient } from "../patients/PatientContext";
import "./Record.css";

/**
 * Everything about the record rather than its results: who it describes, who
 * can reach it, and who has.
 *
 * The three sit on one page in that order because they answer one question in
 * sequence — whose data, who may see it, who did.
 */

const ROLES = [
  { value: "viewer", label: "Viewer", can: "Read results" },
  { value: "nurse", label: "Nurse", can: "Read and add reports" },
  { value: "clinician", label: "Clinician", can: "Read, add, and confirm mappings" },
  { value: "owner", label: "Owner", can: "Everything, including managing access" },
];

const ROLE_OPTIONS = ROLES.map((r) => ({ value: r.value, label: r.label, hint: r.can }));

const SEX_OPTIONS = [
  { value: "", label: "Not set" },
  { value: "F", label: "Female" },
  { value: "M", label: "Male" },
  { value: "X", label: "Other" },
];

const ACTION_WORDS = {
  read: "opened", list: "listed", download: "downloaded a PDF from",
  create: "added to", update: "changed", delete: "removed from",
  confirm: "confirmed a result in", reject: "rejected a result in",
  reprocess: "re-read a report in", sign_in: "signed in",
  sign_out: "signed out", sign_in_failed: "failed to sign in",
};

function when(iso) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function Demographics({ patient, canEdit }) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState(null);
  const qc = useQueryClient();

  // Seeded from the patient each time the form opens, so cancelling and
  // reopening shows what is stored rather than the abandoned edit.
  const [form, setForm] = useState(null);
  const open = () => {
    setForm({
      display_name: patient.display_name,
      dob: patient.dob ?? "",
      sex_at_birth: patient.sex_at_birth ?? "",
      mrn: patient.mrn ?? "",
    });
    setError(null);
    setEditing(true);
  };

  const save = useMutation({
    // Empty strings are sent as null, not dropped: the API distinguishes
    // "not sent" from "sent as null", and clearing a wrong date of birth has
    // to be possible — it selects the reference range for every result.
    mutationFn: () => api.patch(`/patients/${patient.id}`, {
      display_name: form.display_name.trim(),
      dob: form.dob || null,
      sex_at_birth: form.sex_at_birth || null,
      mrn: form.mrn.trim() || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["patients"] });
      setEditing(false);
    },
    onError: (e) => setError(messageFor(e)),
  });

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  return (
    <section className="rec__block">
      <div className="rec__head">
        <h2 className="rec__h">Who this record describes</h2>
        {canEdit && !editing && (
          <button className="rec__edit" onClick={open}>Edit</button>
        )}
      </div>

      {editing ? (
        <form
          className="rec__form"
          onSubmit={(e) => { e.preventDefault(); setError(null); save.mutate(); }}
        >
          <label className="field">
            <span className="field__label">Name</span>
            <input
              className="field__input"
              value={form.display_name}
              onChange={set("display_name")}
              required
            />
          </label>

          <div className="grid-2">
            <label className="field">
              <span className="field__label">Date of birth</span>
              <input
                className="field__input num"
                type="date"
                value={form.dob}
                max={new Date().toISOString().slice(0, 10)}
                onChange={set("dob")}
              />
            </label>
            {/* A label cannot wrap the custom select — it is a button, and
                clicking the label would toggle it twice. Associated by id. */}
            <div className="field">
              <label className="field__label" htmlFor="rec-sex">Sex at birth</label>
              <Select
                id="rec-sex"
                ariaLabel="Sex at birth"
                value={form.sex_at_birth}
                onChange={(v) => setForm({ ...form, sex_at_birth: v })}
                options={SEX_OPTIONS}
              />
            </div>
          </div>

          <label className="field">
            <span className="field__label">MRN</span>
            <input
              className="field__input num"
              value={form.mrn}
              onChange={set("mrn")}
              placeholder="Medical record number, if the lab prints one"
            />
          </label>

          <p className="field__hint">
            Date of birth and sex at birth choose the reference range when a
            report does not print its own. All three are encrypted at rest.
          </p>

          {error && <p className="field__error" role="alert">{error}</p>}

          <div className="rec__actions">
            <button className="btn btn--primary" disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save changes"}
            </button>
            <button
              type="button"
              className="btn btn--quiet"
              onClick={() => { setEditing(false); setError(null); }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <dl className="rec__facts">
            <div><dt>Name</dt><dd>{patient.display_name}</dd></div>
            <div><dt>Date of birth</dt><dd className="num">{patient.dob ?? "Not set"}</dd></div>
            <div><dt>Sex at birth</dt><dd className="num">{patient.sex_at_birth ?? "Not set"}</dd></div>
            <div><dt>MRN</dt><dd className="num">{patient.mrn ?? "Not set"}</dd></div>
          </dl>
          {/* Not a nag: without these, age- and sex-specific ranges fall back
              to whatever each report happened to print. */}
          {(!patient.dob || !patient.sex_at_birth) && (
            <p className="rec__hint">
              Date of birth and sex at birth are used to choose reference ranges
              when a report does not print its own. Both are encrypted.
            </p>
          )}
        </>
      )}
    </section>
  );
}

function AccessList({ patientId }) {
  const me = useAuth().user?.email?.toLowerCase();
  const [form, setForm] = useState({ email: "", role: "viewer" });
  const [error, setError] = useState(null);
  // Set when the email has no account yet: the grant cannot proceed, but an
  // invitation can. Holds the address so the offer is one click, not a retype.
  const [noAccount, setNoAccount] = useState(null);
  const [link, setLink] = useState(null);
  const [handover, setHandover] = useState(false);
  const [newOwner, setNewOwner] = useState("");
  const qc = useQueryClient();

  const { data: grants = [], isPending } = useQuery({
    queryKey: ["access", patientId],
    queryFn: async () => (await api.get(`/patients/${patientId}/access`)).data,
  });

  const { data: invites = [] } = useQuery({
    queryKey: ["invites", patientId],
    queryFn: async () => (await api.get(`/patients/${patientId}/invites`)).data,
  });

  const done = () => {
    qc.invalidateQueries({ queryKey: ["access", patientId] });
    qc.invalidateQueries({ queryKey: ["invites", patientId] });
    setError(null);
  };

  const add = useMutation({
    mutationFn: () => api.post(`/patients/${patientId}/access`, form),
    onSuccess: () => { setForm({ email: "", role: "viewer" }); setNoAccount(null); done(); },
    onError: (e) => {
      // 404 here means "no account with that email" — the one failure that has
      // a next step rather than a message.
      if (e?.response?.status === 404) {
        setNoAccount(form.email);
        setError(null);
      } else setError(messageFor(e));
    },
  });

  const sendInvite = useMutation({
    mutationFn: async () =>
      (await api.post(`/patients/${patientId}/invites`,
        { email: noAccount, role: form.role })).data,
    onSuccess: (data) => {
      setLink(data);
      setForm({ email: "", role: "viewer" });
      setNoAccount(null);
      done();
    },
    onError: (e) => setError(messageFor(e)),
  });

  const transfer = useMutation({
    mutationFn: () => api.post(`/patients/${patientId}/transfer`, { email: newOwner }),
    onSuccess: () => {
      // The caller is a clinician now, so this screen's owner-only sections
      // disappear — the patient list is what carries the new role.
      qc.invalidateQueries({ queryKey: ["patients"] });
      setHandover(false);
      setNewOwner("");
    },
    onError: (e) => setError(messageFor(e)),
  });

  const cancelInvite = useMutation({
    mutationFn: (id) => api.delete(`/patients/${patientId}/invites/${id}`),
    onSuccess: done,
    onError: (e) => setError(messageFor(e)),
  });

  const revoke = useMutation({
    mutationFn: (userId) => api.delete(`/patients/${patientId}/access/${userId}`),
    onSuccess: done,
    onError: (e) => setError(messageFor(e)),
  });

  const live = grants.filter((g) => !g.revoked_at);
  const past = grants.filter((g) => g.revoked_at);

  return (
    <section className="rec__block">
      <h2 className="rec__h">Who can reach it</h2>

      {isPending ? (
        <p className="muted">Loading…</p>
      ) : (
        <ul className="grants">
          {live.map((g) => (
            <li key={g.user_id} className="grant">
              <span className="grant__who num">
                {g.email}
                {/* Which row is you. Without it, "grant access" and "change my
                    own role" look like the same control from the outside. */}
                {g.email === me && <span className="grant__you">You</span>}
              </span>
              <span className="grant__role">
                {ROLES.find((r) => r.value === g.role)?.label ?? g.role}
              </span>
              <span className="grant__can">
                {ROLES.find((r) => r.value === g.role)?.can}
              </span>
              <button
                className="grant__revoke"
                onClick={() => revoke.mutate(g.user_id)}
                disabled={revoke.isPending}
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}

      <form
        className="rec__grant"
        onSubmit={(e) => { e.preventDefault(); setError(null); add.mutate(); }}
      >
        <label className="field">
          <span className="field__label">Email</span>
          <input
            className="field__input"
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            required
          />
        </label>
        <div className="field">
          <label className="field__label" htmlFor="grant-role">Role</label>
          <Select
            id="grant-role"
            ariaLabel="Role"
            value={form.role}
            onChange={(v) => setForm({ ...form, role: v })}
            options={ROLE_OPTIONS}
          />
        </div>
        <button
          className="btn btn--primary"
          disabled={add.isPending || form.email.toLowerCase() === me}
        >
          {add.isPending ? "Granting…" : "Grant access"}
        </button>

        {/* Its own row, spanning the width. Inside the email field it made that
            cell taller than the other two, and `align-items: end` then pushed
            the role picker and the button down to meet its bottom edge. */}
        <p className="rec__granthint">
          {form.email && form.email.toLowerCase() === me
            ? "That is you — you already have access to this record."
            : "If they have no account yet, you will get an invitation link to send them."}
        </p>
      </form>

      {/* Not an error: they simply have not signed up yet, and the way
          forward is an invitation rather than a retry. */}
      {noAccount && (
        <div className="rec__offer">
          <p>
            <span className="num">{noAccount}</span> has no account yet. Send an
            invitation and they will get {ROLES.find((r) => r.value === form.role)?.label.toLowerCase()} access
            when they accept.
          </p>
          <div className="rec__actions">
            <button
              className="btn btn--primary"
              onClick={() => sendInvite.mutate()}
              disabled={sendInvite.isPending}
            >
              {sendInvite.isPending ? "Creating…" : "Create invitation"}
            </button>
            <button className="btn btn--quiet" onClick={() => setNoAccount(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Shown once. The API stores only a hash, so this cannot be recovered
          from the list later — which is the point. */}
      {link && (
        <div className="rec__offer rec__offer--link">
          <h3 className="rec__sub">Invitation for {link.email}</h3>
          {/* The link is shown either way. A send that failed silently, with
              the only copy of the link never displayed, would leave an
              invitation that exists and can never be delivered. */}
          <p>
            {link.emailed
              ? "Emailed to them. Here is the link as well, in case it does not arrive."
              : "Send this link to them yourself — the email could not be sent."}{" "}
            It works once, expires{" "}
            {new Date(link.expires_at).toLocaleDateString()}, and only for that
            address.
          </p>
          <div className="rec__linkrow">
            <input className="field__input num" value={link.link} readOnly
                   onFocus={(e) => e.target.select()} />
            <button
              className="btn btn--quiet"
              onClick={() => navigator.clipboard?.writeText(link.link)}
            >
              Copy
            </button>
          </div>
          <button className="rec__edit" onClick={() => setLink(null)}>Done</button>
        </div>
      )}

      {invites.length > 0 && (
        <>
          <h3 className="rec__sub">Invited, not yet accepted</h3>
          <ul className="grants">
            {invites.map((i) => (
              <li key={i.id} className="grant">
                <span className="grant__who num">{i.email}</span>
                <span className="grant__role">
                  {ROLES.find((r) => r.value === i.role)?.label ?? i.role}
                </span>
                <span className="grant__can num">
                  expires {new Date(i.expires_at).toLocaleDateString()}
                </span>
                <button
                  className="grant__revoke"
                  onClick={() => cancelInvite.mutate(i.id)}
                  disabled={cancelInvite.isPending}
                >
                  Cancel
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {error && <p className="field__error" role="alert">{error}</p>}

      {past.length > 0 && (
        <>
          {/* Shown rather than hidden: that somebody used to have access is
              what an investigation actually asks about. */}
          <h3 className="rec__sub">Previously had access</h3>
          <ul className="grants grants--past">
            {past.map((g) => (
              <li key={`${g.user_id}-${g.revoked_at}`} className="grant">
                <span className="grant__who num">{g.email}</span>
                <span className="grant__role">{g.role}</span>
                <span className="grant__can num">
                  revoked {when(g.revoked_at)}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="rec__handover">
        <div>
          <h3 className="rec__sub">Hand this record over</h3>
          <p className="rec__hint">
            Makes someone else the owner. You stay on as a clinician, so you
            keep clinical access without managing it — and can remove yourself
            entirely afterwards if you want to.
          </p>
        </div>
        <button className="rec__edit" onClick={() => setHandover(true)}>
          Transfer ownership
        </button>
      </div>

      {handover && (
        <Modal
          title="Transfer ownership"
          description="The new owner can manage access, edit demographics, and remove you. You become a clinician on this record."
          onClose={() => { setHandover(false); setError(null); }}
          footer={
            <>
              <button className="btn btn--quiet" onClick={() => setHandover(false)}>
                Cancel
              </button>
              <button
                className="btn btn--primary"
                form="transfer"
                disabled={transfer.isPending || !newOwner}
              >
                {transfer.isPending ? "Transferring…" : "Transfer"}
              </button>
            </>
          }
        >
          <form
            id="transfer"
            onSubmit={(e) => { e.preventDefault(); setError(null); transfer.mutate(); }}
          >
            <label className="field">
              <span className="field__label">Email of the new owner</span>
              <input
                className="field__input" type="email" required
                value={newOwner} onChange={(e) => setNewOwner(e.target.value)}
              />
              <span className="field__hint">They must already have an account.</span>
            </label>
            {error && <p className="field__error" role="alert">{error}</p>}
          </form>
        </Modal>
      )}
    </section>
  );
}

// Matches the API's own default, so "did I hit the ceiling" is answerable
// from the row count instead of guessed at.
const TRAIL_CAP = 200;

function Activity({ patientId }) {
  const { data: rows = [], isPending, error } = useQuery({
    queryKey: ["patient-audit", patientId],
    queryFn: async () =>
      (await api.get(`/audit/patient/${patientId}`, { params: { limit: TRAIL_CAP } })).data,
  });

  if (isPending) return <p className="muted">Loading activity…</p>;
  if (error) return <p className="muted">Could not load activity.</p>;

  return (
    <section className="rec__block">
      <div className="rec__head">
        <h2 className="rec__h">Who has looked</h2>
        {rows.length > 0 && (
          <span className="rec__count num">
            {rows.length}{rows.length === TRAIL_CAP ? "+" : ""} entries
          </span>
        )}
      </div>
      <p className="rec__hint">
        Every time this record is opened, downloaded or changed, it is recorded
        here. The log cannot be edited or deleted.
      </p>

      {rows.length === 0 ? (
        <p className="muted">Nothing recorded yet.</p>
      ) : (
        // Scrolls in its own box rather than running the page down forever.
        // A busy record accumulates an entry per screen opened, so the trail
        // is the one list here with no natural end — and pushing "who can
        // reach it" a thousand rows up the page hides the section that
        // actually gets acted on.
        <ol className="trail scroller" tabIndex={0} aria-label="Access log">
          {rows.map((r, i) => (
            <li key={i} className={`trailrow ${r.action === "download" ? "trailrow--notable" : ""}`}>
              <span className="trailrow__at num">{when(r.at)}</span>
              <span className="trailrow__who num">{r.actor_email}</span>
              <span className="trailrow__what">
                {ACTION_WORDS[r.action] ?? r.action} {r.resource}
              </span>
              <span className="trailrow__ip num">{r.ip ?? ""}</span>
            </li>
          ))}
        </ol>
      )}

      {rows.length === TRAIL_CAP && (
        <p className="rec__hint">
          Showing the {TRAIL_CAP} most recent. Older entries are kept, and are
          not reachable from this screen.
        </p>
      )}
    </section>
  );
}

export default function Record() {
  const { active } = usePatient();

  if (!active) return <p className="muted">Loading record…</p>;

  const isOwner = active.role === "owner";

  return (
    <>
      <header className="screen__head">
        <div>
          <p className="eyebrow">Record</p>
          <h1 className="screen__title">{active.display_name}</h1>
        </div>
        <span className="rec__role num">your role: {active.role}</span>
      </header>

      <Demographics patient={active} canEdit={isOwner} />

      {isOwner ? (
        <>
          <AccessList patientId={active.id} />
          <Activity patientId={active.id} />
        </>
      ) : (
        <p className="rec__locked">
          Only an owner of this record can see who has access to it and who has
          opened it.
        </p>
      )}
    </>
  );
}
