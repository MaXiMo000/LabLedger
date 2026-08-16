import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import useDialog from "../hooks/useDialog";
import IntervalRail from "../components/IntervalRail";
import "./Provenance.css";

/**
 * Where one number came from.
 *
 * Read top to bottom it is a chain of custody: the line as the lab printed it,
 * then each decision made about it and the evidence that decision rested on,
 * ending at the point you see on the chart. Every step names what it did — a
 * resolved value the reader cannot trace is not finished work.
 *
 * Deliberately not a card grid. The steps are a sequence and the numbering
 * carries that, because the order is the argument: each step is only valid
 * because the one above it was.
 */

const STAGE_STORY = {
  alias: {
    title: "Matched a name you confirmed",
    body: "You have confirmed this printed name before, so it resolved by lookup — no matching, no model.",
  },
  exact: {
    title: "Matched a LOINC name exactly",
    body: "The printed name is one of this code's own names, character for character after normalising case and punctuation.",
  },
  related_corroborated: {
    title: "Matched an associated name",
    body: "The name appears in LOINC's related-names field, which is associative rather than identifying. It was accepted as a proposal only because the specimen and the unit independently agreed — and it still needed your confirmation.",
  },
  narrowed_fuzzy: {
    title: "Matched by similarity, within the specimen",
    body: "The specimen narrowed the field first, then string similarity ranked what remained. Accepted only because it cleared both the score threshold and the margin over the runner-up.",
  },
  llm: {
    title: "Chosen by the model, from a fixed list",
    body: "The earlier stages could not decide, so the candidates were handed to the model as a numbered list. It returned an index — it cannot emit a code that was not offered.",
  },
  unmapped: {
    title: "Not resolved",
    body: "No stage could identify this test with enough confidence, so it was sent to you rather than guessed at.",
  },
};

function Step({ n, label, children, muted }) {
  return (
    <li className={`step ${muted ? "step--muted" : ""}`}>
      <span className="step__n num">{n}</span>
      <div className="step__body">
        <h3 className="step__label">{label}</h3>
        {children}
      </div>
    </li>
  );
}

function Row({ k, v }) {
  if (v === null || v === undefined || v === "") return null;
  return (
    <div className="fact">
      <dt>{k}</dt>
      <dd className="num">{v}</dd>
    </div>
  );
}

export default function Provenance({ observationId, onClose }) {
  const panelRef = useDialog(onClose);

  const { data: p, isPending, error } = useQuery({
    queryKey: ["provenance", observationId],
    queryFn: async () => (await api.get(`/observations/item/${observationId}`)).data,
    enabled: Boolean(observationId),
  });

  const story = p ? STAGE_STORY[p.stage] ?? STAGE_STORY.unmapped : null;

  return (
    <div className="prov__scrim" onClick={onClose}>
      <aside
        className="prov"
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Where this result came from"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="prov__head">
          <p className="eyebrow">Where this came from</p>
          <button className="prov__close" onClick={onClose} aria-label="Close">
            Close
          </button>
        </header>

        {isPending && <p className="muted prov__pad">Loading…</p>}
        {error && <p className="muted prov__pad">Could not load this result.</p>}

        {p && (
          <>
            {/* The evidence, set as it appeared on the page. */}
            <figure className="printed">
              <figcaption className="printed__cap eyebrow">
                As printed · {p.lab_name ?? "lab not identified"} · page {p.page}
              </figcaption>
              <div className="printed__line num">
                <span className="printed__name">{p.raw_name}</span>
                <span className="printed__val">{p.raw_value}</span>
                <span className="printed__unit">{p.raw_unit ?? ""}</span>
                <span className="printed__flag">{p.raw_flag ?? ""}</span>
                <span className="printed__ref">{p.raw_ref_range ?? ""}</span>
              </div>
              <p className="printed__meta num">
                {p.raw_specimen ?? "specimen not stated"} ·{" "}
                {p.collected_at
                  ? `collected ${new Date(p.collected_at).toLocaleDateString()}`
                  : "no collection date"}
                {p.date_source === "reported" && " (report date)"}
              </p>
            </figure>

            <ol className="steps">
              <Step n="01" label="Read from the page">
                <p className="step__text">
                  Found by {p.extraction_method === "tables"
                    ? "the ruled table on the page"
                    : "reading the text layer and classifying each column by its shape"}.
                  The line above is stored exactly as it was printed and is never
                  altered — every value below is derived from it.
                </p>
              </Step>

              <Step n="02" label={story.title} muted={!p.loinc_code}>
                <p className="step__text">{story.body}</p>
                {p.loinc_code && (
                  <div className="resolved-to">
                    <span className="resolved-to__code num">{p.loinc_code}</span>
                    <span className="resolved-to__name">{p.loinc_display}</span>
                  </div>
                )}
                <dl className="facts">
                  <Row k="Stage" v={p.stage} />
                  <Row k="Confidence" v={p.confidence.toFixed(2)} />
                  <Row
                    k="Candidates considered"
                    v={p.candidates_considered > 0 ? p.candidates_considered : null}
                  />
                  <Row k="Model" v={p.llm_model} />
                  <Row k="LOINC component" v={p.loinc_component} />
                  <Row k="LOINC system" v={p.loinc_system} />
                  <Row k="LOINC property" v={p.loinc_property} />
                  <Row
                    k="Confirmed by you"
                    v={p.confirmed_by_user_at
                      ? new Date(p.confirmed_by_user_at).toLocaleString()
                      : null}
                  />
                </dl>
              </Step>

              <Step n="03" label="Converted to a comparable unit" muted={!p.canonical_value}>
                {p.canonical_value != null ? (
                  <>
                    <p className="step__text">
                      {p.unit_conversion_factor === 1
                        ? `Already in the canonical unit for this test, so the value is unchanged.`
                        : `Multiplied by ${p.unit_conversion_factor} to convert ${p.raw_unit} into ${p.canonical_unit}, the unit every result for this test is stored in.`}{" "}
                      The factor comes from a hand-audited table — an unrecognised
                      unit is left unconverted rather than assumed.
                    </p>
                    <div className="conv num">
                      <span>{p.value_num} {p.raw_unit}</span>
                      <span className="conv__arrow" aria-hidden="true">→</span>
                      <span className="conv__out">{p.canonical_value} {p.canonical_unit}</span>
                    </div>
                  </>
                ) : (
                  <p className="step__text">
                    {p.value_text
                      ? `This is a qualitative result (“${p.value_text}”), so there is no number to convert and it is not charted.`
                      : `No audited conversion exists for ${p.raw_unit ?? "this unit"} on this test, so the value is kept as printed and left off the chart rather than guessed at.`}
                  </p>
                )}
              </Step>

              <Step n="04" label="Judged against a reference range" muted={p.ref_source === "none"}>
                {p.ref_source === "none" ? (
                  <p className="step__text">
                    No range was printed and none is held for this test, so it is
                    shown without a judgement rather than compared to an invented one.
                  </p>
                ) : (
                  <>
                    <p className="step__text">
                      {p.ref_source === "pdf"
                        ? "Using the range printed on this report. Reference intervals are specific to a lab's own instruments and assay, so the lab's own range is preferred over any built-in table."
                        : "This report printed no range, so a built-in range for your age and sex was used instead."}
                      {p.raw_flag && " The lab's own marker was also present and takes precedence over a numeric comparison."}
                    </p>
                    <div className="judge">
                      <IntervalRail
                        value={p.value_num}
                        low={p.ref_low}
                        high={p.ref_high}
                        flag={p.flag}
                        size="full"
                      />
                      <div className="judge__legend num">
                        <span>{p.ref_low ?? "any"}</span>
                        <span className={`flag flag--${p.flag}`}>{p.flag}</span>
                        <span>{p.ref_high ?? "any"}</span>
                      </div>
                    </div>
                  </>
                )}
              </Step>
            </ol>

            <footer className="prov__foot">
              <span className="num prov__status">
                {p.review_status === "confirmed"
                  ? "Confirmed by you"
                  : p.review_status === "pending"
                    ? "Awaiting your confirmation"
                    : p.review_status === "rejected"
                      ? "You marked this as not a lab result"
                      : "Accepted automatically"}
              </span>
              <span className="num prov__file">{p.document_filename}</span>
            </footer>
          </>
        )}
      </aside>
    </div>
  );
}
