import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, messageFor } from "../api/client";
import { usePatient } from "../patients/PatientContext";
import { keyAction } from "./reviewKeys";
import "./Review.css";

/**
 * The review queue.
 *
 * Each row shows the printed line exactly as the lab set it, next to the codes
 * it might be, with the reason each candidate surfaced. Confirming writes an
 * alias, so the same printed name resolves without asking again — the counter
 * at the top is there so that convergence is visible rather than claimed.
 *
 * It is also the one screen somebody works *down*. Forty-four rows at eight tab
 * stops each is where a queue stops being reviewed and starts being dismissed,
 * so the whole loop — move, choose, confirm — has keys. The queue keeps a
 * cursor; the row under it owns the keys that settle a row, the list owns the
 * keys that move between them.
 */

function Candidate({ c, chosen, onPick, ordinal }) {
  return (
    <li>
      <button
        className={`cand ${chosen ? "cand--on" : ""}`}
        onClick={onPick}
        aria-pressed={chosen}
      >
        {/* The digit that picks this one. Only the first nine get one, and a
            row with more candidates than that still reads correctly — the
            unnumbered ones are the ones the mouse is for. */}
        <span className="cand__key num" aria-hidden="true">
          {ordinal <= 9 ? ordinal : ""}
        </span>
        <span className="cand__code num">{c.loinc_code}</span>
        <span className="cand__name">{c.display}</span>
        <span className="cand__why num">{c.why}</span>
      </button>
    </li>
  );
}

function Item({ item, focused, takeFocus, onDone }) {
  const [picked, setPicked] = useState(item.proposed_loinc ?? null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState(null);
  const row = useRef(null);
  const search = useRef(null);
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
    onSuccess: (_res, action) => {
      ["review", "panels", "documents"].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k] })
      );
      // Which one it was, because only a confirm writes an alias — the reject
      // endpoint answers `alias_written: false` and means it.
      onDone(action);
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

  // Held in a ref, like `useDialog` holds its close handler, so the effect
  // below stays keyed on `focused` alone. Everything this reads — the pick,
  // the current options, whether a save is in flight — changes on renders that
  // have no business tearing down and re-attaching a window listener.
  const handle = useRef(null);
  handle.current = (e) => {
    const action = keyAction(e);
    if (!action) return;

    if (action.type === "leaveSearch") {
      // Back to the queue rather than out of the screen: the row is still the
      // row, and the next keystroke should move the cursor, not the caret.
      e.target.blur();
      row.current?.focus();
      return;
    }
    if (action.type === "search") {
      // Or the "/" lands in the field as its first character.
      e.preventDefault();
      search.current?.focus();
      return;
    }
    if (action.type === "pick") {
      const chosen = options[action.index];
      if (chosen) setPicked(chosen.loinc_code);
      return;
    }
    if (settle.isPending) return;
    if (action.type === "confirm" && picked) settle.mutate("confirm");
    if (action.type === "reject") settle.mutate("reject");
  };

  useEffect(() => {
    if (!focused) return undefined;
    const onKey = (e) => handle.current(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focused]);

  // Real focus, not a class, so a screen reader is told the cursor moved and
  // the row is scrolled into view without asking. Only once the keyboard is
  // actually in use — moving focus on mount would yank the page for somebody
  // who arrived with a mouse and has not pressed anything yet.
  useEffect(() => {
    if (takeFocus) row.current?.focus();
  }, [takeFocus]);

  return (
    <li
      ref={row}
      tabIndex={-1}
      className={`ritem ${focused ? "ritem--on" : ""}`}
    >
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
          ref={search}
          className="field__input"
          placeholder="Search all 58,252 codes…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>

      <ul className="cands">
        {options.map((c, i) => (
          <Candidate
            key={c.loinc_code}
            c={c}
            ordinal={i + 1}
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
  const [cursor, setCursor] = useState(0);
  const [byKeyboard, setByKeyboard] = useState(false);
  const { activeId } = usePatient();

  const { data: items, isPending, error } = useQuery({
    queryKey: ["review", activeId],
    queryFn: async () => (await api.get(`/review/${activeId}`)).data,
    enabled: Boolean(activeId),
  });

  // Before the early returns: hooks cannot be conditional, and an empty queue
  // is a perfectly ordinary state for this to run against.
  const groups = groupQueue(items ?? []);
  const order = groups.flatMap((g) => g.rows);
  // The cursor is an index into a list that shrinks underneath it, and that is
  // how it advances: settle the row it points at, the row after it takes that
  // position, and the cursor is already on the next thing to do. Clamped
  // rather than corrected, so settling the last row lands on the new last row
  // instead of running off the end.
  const at = Math.min(cursor, Math.max(order.length - 1, 0));
  const focusedId = order[at]?.observation_id;

  const step = useRef(null);
  step.current = (e) => {
    const action = keyAction(e);
    if (action?.type !== "next" && action?.type !== "prev") return;
    // Or ArrowDown scrolls the page out from under the row it just moved to.
    e.preventDefault();
    setByKeyboard(true);
    setCursor(
      Math.max(0, Math.min(at + (action.type === "next" ? 1 : -1), order.length - 1))
    );
  };

  useEffect(() => {
    const onKey = (e) => step.current(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
            {items.length === 1
              ? "1 result needs you"
              : `${items.length} results need you`}
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

      {/* Hidden where there is no keyboard to describe. A phone showing a row
          of key caps it cannot press is telling the reader about somebody
          else's screen. */}
      <p className="review__keys">
        <kbd>J</kbd>
        <kbd>K</kbd> move
        <span className="review__keys-sep">·</span>
        <kbd>1</kbd>–<kbd>9</kbd> choose
        <span className="review__keys-sep">·</span>
        <kbd>↵</kbd> confirm
        <span className="review__keys-sep">·</span>
        <kbd>X</kbd> not a result
        <span className="review__keys-sep">·</span>
        <kbd>/</kbd> search
      </p>

      {groups.map((g) => (
        <section key={g.key} className="rgroup">
          <h2 className="rgroup__h">
            <span className="rgroup__label">{g.label}</span>
            <span className="rgroup__n num">
              {g.rows.length} to confirm
            </span>
          </h2>
          <ul className="ritems">
            {g.rows.map((item) => (
              <Item
                key={item.observation_id}
                item={item}
                focused={item.observation_id === focusedId}
                takeFocus={byKeyboard && item.observation_id === focusedId}
                onDone={(action) => {
                  // Rejecting teaches nothing — the endpoint writes no alias —
                  // so counting it here made the tally overstate exactly what
                  // it exists to keep honest.
                  if (action === "confirm") setConfirmed((n) => n + 1);
                  // Whatever settled this row — a key or a click — focus was
                  // inside a row that is about to be unmounted. Parking it on
                  // the row that takes its place beats dropping it to <body>,
                  // which is a keyboard user back at the top of the document.
                  setByKeyboard(true);
                }}
              />
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

/**
 * The queue under the headings a report prints, in a report's own order.
 *
 * No triage sort here, unlike the results screen. Every row in this list needs
 * the same thing — a human to look at it — so there is nothing to rank by, and
 * inventing an order would only make the list less predictable to work down.
 * Reviewing a blood count as a blood count also beats meeting its analytes
 * alphabetically between a lipid and a thyroid test.
 *
 * "Not recognised yet" sits last: those rows have no proposal, so they need a
 * search rather than a yes or no, and they are the slowest work in the list.
 */
function groupQueue(items) {
  const groups = new Map();
  for (const item of items) {
    if (!groups.has(item.panel)) {
      groups.set(item.panel, { key: item.panel, label: item.panel_label, rows: [] });
    }
    groups.get(item.panel).rows.push(item);
  }
  // Named panels, then the results whose panel is unknown, then the rows we
  // cannot name at all. Ordered by how much work each needs: a proposed code
  // is a yes or no, "Other results" is still a yes or no, and "Not recognised
  // yet" is a search.
  const rank = (k) => (k === "unmatched" ? 2 : k === "other" ? 1 : 0);
  return [...groups.values()].sort((a, b) => rank(a.key) - rank(b.key));
}
