import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, messageFor } from "../api/client";
import Modal from "../components/Modal";
import "./Aliases.css";

/**
 * What this account has taught the system, and how to take it back.
 *
 * Confirming a mapping writes a rule that decides the same printed name
 * forever, at stage zero, with no further review — which is the point, and
 * also the danger. Three wrong ones were found sitting in a real account
 * (`FERRTN SER` read as Serine, `HCT` as Reticulocyte production index,
 * `VIT B-12` as Thiamine), each a plausible pick from a correctly-widened
 * candidate list, and none of them visible anywhere afterwards.
 *
 * Correcting is separated from forgetting because they are different
 * admissions. "This is wrong and I know the right answer" re-codes the history
 * behind it; "this is wrong and I do not" sends those rows back to the queue
 * rather than guessing a second time.
 */

function LoincPicker({ onPick, busy }) {
  const [query, setQuery] = useState("");
  // Searched a beat after typing stops, not on every keystroke. The query is a
  // regex walk over 58k rows — cheap enough to feel instant when it runs, and
  // not something to run five times on the way to a word.
  const [settled, setSettled] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setSettled(query), 220);
    return () => clearTimeout(t);
  }, [query]);

  const { data: hits = [], isFetching } = useQuery({
    queryKey: ["loinc-search", settled],
    queryFn: async () =>
      (await api.get("/review/loinc/search", { params: { q: settled } })).data,
    enabled: settled.trim().length >= 3,
    // Results for a given string do not change while the dialog is open.
    staleTime: 5 * 60_000,
  });

  return (
    <>
      <label className="field">
        <span className="field__label">Search for the right test</span>
        <input
          className="field__input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="ferritin, haematocrit, cobalamin…"
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus
        />
        <span className="field__hint">
          {query.trim().length < 3
            ? "Three letters or more."
            : isFetching ? "Searching…" : `${hits.length} matches`}
        </span>
      </label>

      <ul className="alias__hits scroller">
        {hits.map((h) => (
          <li key={h.loinc_code}>
            <button
              className="alias__hit"
              onClick={() => onPick(h.loinc_code)}
              disabled={busy}
            >
              <span className="alias__hit-name">{h.display}</span>
              <span className="alias__hit-meta num">
                {h.loinc_code} · {h.system}
                {!h.auto_matchable && " · search only"}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

export default function Aliases() {
  const [correcting, setCorrecting] = useState(null);
  const [forgetting, setForgetting] = useState(null);
  const [error, setError] = useState(null);
  const qc = useQueryClient();

  const { data: aliases = [], isPending } = useQuery({
    queryKey: ["aliases"],
    queryFn: async () => (await api.get("/review/aliases")).data,
  });

  const done = () => {
    // The corrected rows change code, unit and interval, so everything that
    // reads them is stale — not just this list.
    qc.invalidateQueries();
    setCorrecting(null);
    setForgetting(null);
    setError(null);
  };

  const correct = useMutation({
    mutationFn: (loinc_code) =>
      api.patch(`/review/aliases/${correcting.id}`, { loinc_code }),
    onSuccess: done,
    onError: (e) => setError(messageFor(e)),
  });

  const forget = useMutation({
    mutationFn: () => api.delete(`/review/aliases/${forgetting.id}`),
    onSuccess: done,
    onError: (e) => setError(messageFor(e)),
  });

  return (
    <>
      <header className="screen__head">
        <div>
          <p className="eyebrow">Review</p>
          <h1 className="screen__title">
            {isPending ? "Learned mappings" : `${aliases.length} learned mapping${aliases.length === 1 ? "" : "s"}`}
          </h1>
        </div>
      </header>

      <p className="alias__note">
        Each of these was confirmed once and now decides that printed name every
        time it appears, without asking again. Correcting one also re-codes the
        results it already decided.
      </p>

      {error && <p className="field__error" role="alert">{error}</p>}

      {isPending ? (
        <p className="muted">Loading…</p>
      ) : aliases.length === 0 ? (
        <div className="empty">
          <h2 className="empty__title">Nothing learned yet</h2>
          <p className="empty__body">
            Confirming a mapping in the review queue teaches the system how this
            lab prints that test. Those rules will appear here.
          </p>
          {/* An empty state that explains itself and offers nothing to do
              leaves the reader where they started. The queue is where these
              come from, so that is the door. */}
          <Link className="btn btn--primary" to="/app/review">Go to the review queue</Link>
        </div>
      ) : (
        <ul className="aliases">
          {aliases.map((a) => (
            <li key={a.id} className="alias">
              <span className="alias__printed num">
                {a.printed_name}
                {a.specimen && <span className="alias__spec"> · {a.specimen}</span>}
              </span>
              <span className="alias__arrow" aria-hidden="true">→</span>
              <span className="alias__means">
                {a.loinc_display ?? "(unknown code)"}
                <span className="alias__code num"> {a.loinc_code}</span>
              </span>
              <span className="alias__uses num">
                {a.uses} result{a.uses === 1 ? "" : "s"}
              </span>
              <span className="alias__actions">
                <button className="alias__fix" onClick={() => { setError(null); setCorrecting(a); }}>
                  Correct
                </button>
                <button className="alias__forget" onClick={() => { setError(null); setForgetting(a); }}>
                  Forget
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {correcting && (
        <Modal
          title={`What does “${correcting.printed_name}” actually mean?`}
          description={`Currently read as ${correcting.loinc_display ?? correcting.loinc_code}. Changing it also re-codes ${correcting.uses} stored result${correcting.uses === 1 ? "" : "s"}, recomputing units and reference intervals.`}
          onClose={() => setCorrecting(null)}
          footer={
            <button className="btn btn--quiet" onClick={() => setCorrecting(null)}>
              Cancel
            </button>
          }
        >
          <LoincPicker onPick={(code) => correct.mutate(code)} busy={correct.isPending} />
          {error && <p className="field__error" role="alert">{error}</p>}
        </Modal>
      )}

      {forgetting && (
        <Modal
          title={`Forget “${forgetting.printed_name}”?`}
          description={`The ${forgetting.uses} result${forgetting.uses === 1 ? "" : "s"} it decided go back to the review queue, unmapped. Use this when you know the mapping is wrong but not what it should be.`}
          tone="danger"
          onClose={() => setForgetting(null)}
          footer={
            <>
              <button className="btn btn--quiet" onClick={() => setForgetting(null)}>
                Keep it
              </button>
              <button
                className="btn btn--primary"
                onClick={() => forget.mutate()}
                disabled={forget.isPending}
              >
                {forget.isPending ? "Forgetting…" : "Forget and re-review"}
              </button>
            </>
          }
        />
      )}
    </>
  );
}
