import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, messageFor } from "../api/client";
import { usePatient } from "../patients/PatientContext";
import "./Review.css";

/**
 * The review queue.
 *
 * Each row shows the printed line exactly as the lab set it, next to the codes
 * it might be, with the reason each candidate surfaced. Confirming writes an
 * alias, so the same printed name resolves without asking again — the counter
 * at the top is there so that convergence is visible rather than claimed.
 */

function Candidate({ c, chosen, onPick }) {
  return (
    <li>
      <button
        className={`cand ${chosen ? "cand--on" : ""}`}
        onClick={onPick}
        aria-pressed={chosen}
      >
        <span className="cand__code num">{c.loinc_code}</span>
        <span className="cand__name">{c.display}</span>
        <span className="cand__why num">{c.why}</span>
      </button>
    </li>
  );
}

function Item({ item, onDone }) {
  const [picked, setPicked] = useState(item.proposed_loinc ?? null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState(null);
  const qc = useQueryClient();

  const { data: hits } = useQuery({
    queryKey: ["loinc-search", query],
    queryFn: async () =>
      (await api.get("/review/loinc/search", { params: { q: query } })).data,
    enabled: query.trim().length >= 2,
  });

  const settle = useMutation({
    mutationFn: async (action) =>
      action === "reject"
        ? api.post(`/review/item/${item.observation_id}/reject`)
        : api.post(`/review/item/${item.observation_id}/confirm`, { loinc_code: picked }),
    onSuccess: () => {
      ["review", "panels", "documents"].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k] })
      );
      onDone();
    },
    onError: (err) => setError(messageFor(err)),
  });

  const options = query.trim().length >= 2
    ? (hits ?? []).map((h) => ({
        loinc_code: h.loinc_code,
        display: h.display,
        why: h.auto_matchable ? `rank ${h.common_rank}` : "search only",
      }))
    : item.candidates;

  return (
    <li className="ritem">
      {/* The printed line, verbatim. This is the thing being judged, so it is
          set as it appeared on the page, not reformatted. */}
      <div className="ritem__printed">
        <span className="ritem__name num">{item.raw_name}</span>
        <span className="ritem__value num">
          {item.raw_value}
          {item.raw_unit ? ` ${item.raw_unit}` : ""}
        </span>
        <span className="ritem__meta num">
          {item.raw_specimen ?? "specimen not stated"}
          {item.raw_ref_range ? ` · ref ${item.raw_ref_range}` : ""}
          {` · p${item.page}`}
        </span>
      </div>

      <p className="ritem__reason">{item.reason}</p>

      <label className="ritem__search">
        <span className="sr-only">Search all LOINC codes</span>
        <input
          className="field__input"
          placeholder="Search all 58,252 codes…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>

      <ul className="cands">
        {options.map((c) => (
          <Candidate
            key={c.loinc_code}
            c={c}
            chosen={picked === c.loinc_code}
            onPick={() => setPicked(c.loinc_code)}
          />
        ))}
        {options.length === 0 && (
          <li className="muted cands__none">
            {query.trim().length >= 2
              ? "Nothing matches that. Try the component name, like “bilirubin”."
              : "No candidates. Search above to find the right code."}
          </li>
        )}
      </ul>

      {error && <p className="field__error" role="alert">{error}</p>}

      <div className="ritem__actions">
        <button
          className="btn btn--primary"
          disabled={!picked || settle.isPending}
          onClick={() => settle.mutate("confirm")}
        >
          {settle.isPending ? "Saving…" : "Confirm and remember"}
        </button>
        <button
          className="btn btn--quiet"
          disabled={settle.isPending}
          onClick={() => settle.mutate("reject")}
        >
          Not a lab result
        </button>
      </div>
    </li>
  );
}

export default function Review() {
  const [confirmed, setConfirmed] = useState(0);
  const { activeId } = usePatient();

  const { data: items, isPending, error } = useQuery({
    queryKey: ["review", activeId],
    queryFn: async () => (await api.get(`/review/${activeId}`)).data,
    enabled: Boolean(activeId),
  });

  if (!activeId || isPending) return <p className="muted">Loading the queue…</p>;
  if (error) return <p className="muted">Could not load the review queue.</p>;

  if (!items.length) {
    return (
      <div className="empty">
        <h2 className="empty__title">Nothing to confirm</h2>
        <p className="empty__body">
          {confirmed > 0
            ? `You confirmed ${confirmed}. Each one is now a rule — those names will resolve on their own next time.`
            : "Every result resolved on its own. Anything uncertain will appear here."}
        </p>
        <Link className="btn btn--primary" to="/app">See your results</Link>
      </div>
    );
  }

  return (
    <>
      <header className="screen__head">
        <div>
          <p className="eyebrow">Review</p>
          <h1 className="screen__title">
            {items.length} result{items.length > 1 ? "s" : ""} need you
          </h1>
        </div>
        {confirmed > 0 && (
          <dl className="tally">
            <div>
              <dt>Rules learned</dt>
              <dd className="num">{confirmed}</dd>
            </div>
          </dl>
        )}
      </header>

      <p className="review__lede">
        These are the rows LabLedger would not accept on its own — a name matched
        only loosely, a model chose it, or the test is one where a wrong answer
        matters. Confirming teaches it: the same printed name resolves by itself
        from then on.
      </p>

      <ul className="ritems">
        {items.map((item) => (
          <Item
            key={item.observation_id}
            item={item}
            onDone={() => setConfirmed((n) => n + 1)}
          />
        ))}
      </ul>
    </>
  );
}
