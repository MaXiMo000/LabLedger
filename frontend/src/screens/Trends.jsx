import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { usePatient } from "../patients/PatientContext";
import IntervalRail from "../components/IntervalRail";
import Provenance from "./Provenance";
import TrendChart from "./TrendChart";
import "./Trends.css";

/**
 * Everything the account has data for, as ruled rows rather than a card grid.
 *
 * Out-of-range analytes sort first, because that is what a person opens this
 * screen to find. Expanding a row plots it, and shows what was left out — a
 * series that quietly drops the points it could not convert is worse than one
 * that says so.
 */

const isFlagged = (p) => p.latest_flag === "high" || p.latest_flag === "low";

/**
 * The rows, under the headings a report prints them under — but ordered by
 * what needs attention rather than by the order a report prints.
 *
 * A lab report's own order is a filing convention: the blood count is first
 * because it has always been first, not because it is the interesting one
 * today. Sorting the *groups* by their worst result keeps the reason a person
 * opened this screen at the top, which is the same rule the flat list followed
 * and the one thing grouping could quietly have cost. Within a group the
 * server's ordering is kept as-is — it already sorts out-of-range first.
 */
function groupByPanel(panels) {
  const groups = new Map();
  for (const p of panels) {
    if (!groups.has(p.panel)) {
      groups.set(p.panel, { key: p.panel, label: p.panel_label, rows: [] });
    }
    groups.get(p.panel).rows.push(p);
  }

  const rank = (rows) =>
    rows.some((p) => p.latest_critical) ? 0 : rows.some(isFlagged) ? 1 : 2;

  return [...groups.values()]
    .map((g) => ({ ...g, rank: rank(g.rows), flagged: g.rows.filter(isFlagged).length }))
    // "Other results" means "everything the panel map had no heading for", so
    // it stays last however it ranks — a catch-all above named groups reads as
    // though it were one of them.
    .sort((a, b) => (a.key === "other") - (b.key === "other") || a.rank - b.rank);
}

function Series({ loinc, unit, onInspect, patientId }) {
  const { data, isPending, error } = useQuery({
    queryKey: ["series", patientId, loinc],
    queryFn: async () =>
      (await api.get(`/observations/${patientId}/series`, { params: { loinc } })).data,
  });

  if (isPending) return <p className="muted">Loading results…</p>;
  if (error) return <p className="muted">Could not load this series.</p>;

  const { points, excluded, insights = [], reference } = data;
  const ref = points.find((p) => p.ref_low != null || p.ref_high != null);

  return (
    <div className="series">
      {points.length > 1 ? (
        <TrendChart
          points={points}
          refLow={ref?.ref_low ?? null}
          refHigh={ref?.ref_high ?? null}
          unit={data.unit ?? unit}
        />
      ) : (
        <p className="muted">
          {points.length === 1
            ? "One result so far. A second gives you a trend."
            : "No chartable results yet."}
        </p>
      )}

      {/* Facts about these numbers. Everything here is arithmetic on the
          points above and can be checked against the chart. */}
      {insights.length > 0 && (
        <ul className="insights">
          {insights.map((i, n) => (
            <li key={n} className={`insight insight--${i.severity}`}>
              <span className="insight__kind num">{i.kind}</span>
              <span className="insight__text">{i.text}</span>
            </li>
          ))}
        </ul>
      )}

      <table className="readings">
        <caption className="sr-only">Individual results for this test</caption>
        <thead>
          <tr>
            <th scope="col">Collected</th>
            <th scope="col">Result</th>
            <th scope="col">Range</th>
            <th scope="col">Source</th>
            <th scope="col">Where from</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.observation_id}>
              <td className="num">
                {p.collected_at ? new Date(p.collected_at).toLocaleDateString() : "—"}
              </td>
              <td className="num">
                {p.operator}
                {p.value} <span className="unit">{p.unit}</span>
                {p.critical && (
                  /* The threshold is in the title, not just the word: a reader
                     who can check the basis is being given background, and one
                     handed only a verdict is being given a recommendation. */
                  <span
                    className="critical"
                    title={`At or beyond the ${p.critical.side} critical limit of ${p.critical.threshold} ${p.critical.unit} — ${p.critical.basis}`}
                  >
                    Critical {p.critical.side}
                  </span>
                )}
                {p.delta && (
                  <span
                    className="delta"
                    title={`${p.delta.percent > 0 ? "Up" : "Down"} from ${p.delta.from_value} ${p.unit} ${p.delta.days} day${p.delta.days === 1 ? "" : "s"} earlier. Flagged past ${p.delta.limit_percent}%.`}
                  >
                    {p.delta.percent > 0 ? "▲" : "▼"} {Math.abs(p.delta.percent)}%
                  </span>
                )}
              </td>
              <td className="num muted">
                {p.ref_low ?? "—"}–{p.ref_high ?? "—"}
              </td>
              <td className="muted">{p.ref_source === "pdf" ? "the report" : p.ref_source}</td>
              <td className="num muted">
                <button
                  className="trace"
                  onClick={() => onInspect(p.observation_id)}
                  aria-label={`Where the ${p.value} ${p.unit} result came from`}
                >
                  {p.stage} · {p.confidence.toFixed(2)}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Background on the analyte — general, not about this person. Set
          apart and labelled so it cannot be read as a finding. */}
      {reference && (
        <details className="ref">
          <summary className="ref__summary">
            About {reference.name} — general background
          </summary>
          <div className="ref__body">
            <p className="ref__measures">{reference.measures}</p>
            <dl className="ref__dirs">
              <div>
                <dt className="flag flag--low">Low</dt>
                <dd>{reference.low}</dd>
              </div>
              <div>
                <dt className="flag flag--high">High</dt>
                <dd>{reference.high}</dd>
              </div>
            </dl>
            <p className="ref__note">{reference.source_note}</p>
            <a className="ref__link" href={reference.link} target="_blank" rel="noreferrer">
              MedlinePlus reference for this test
            </a>
          </div>
        </details>
      )}

      {excluded.length > 0 && (
        <div className="excluded">
          <p className="eyebrow">Not charted · {excluded.length}</p>
          <ul>
            {excluded.map((e) => (
              <li key={e.observation_id}>
                <button className="trace" onClick={() => onInspect(e.observation_id)}>
                  <span className="num">
                    {e.raw_name} {e.raw_value}
                    {e.raw_unit ? ` ${e.raw_unit}` : ""}
                  </span>
                </button>
                <span className="excluded__why">{e.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * Values the laboratory never measured — eGFR, anion gap, and the rest.
 *
 * Kept in its own section rather than mixed into the panel list, because a
 * calculated number and a measured one are different kinds of claim and the
 * reader is entitled to know which they are looking at. Each carries its
 * formula and the values that went into it, so the arithmetic can be checked
 * rather than trusted.
 */
function Calculated({ data }) {
  if (!data || (!data.series.length && !data.unavailable.length)) return null;

  return (
    <section className="calc">
      <div className="calc__head">
        <h2 className="calc__h">Calculated</h2>
        <p className="calc__note">
          Not measured — worked out from the results above, on draws where every
          input was present in a known unit.
        </p>
      </div>

      {data.series.map((s) => {
        const latest = s.points[s.points.length - 1];
        return (
          <details key={s.display} className="calc__row">
            <summary className="calc__summary">
              <span className="calc__name">{s.display}</span>
              <span className="calc__value num">
                {latest.value} <span className="unit">{s.unit}</span>
              </span>
              <span className={`flag flag--${latest.flag}`}>{latest.flag}</span>
              <span className="calc__n num">
                {s.points.length} draw{s.points.length === 1 ? "" : "s"}
              </span>
            </summary>

            <div className="calc__body">
              <p className="calc__formula num">{s.formula}</p>
              <table className="readings">
                <thead>
                  <tr>
                    <th scope="col">Collected</th>
                    <th scope="col">Result</th>
                    <th scope="col">From</th>
                  </tr>
                </thead>
                <tbody>
                  {[...s.points].reverse().map((pt) => (
                    <tr key={pt.collected_at}>
                      <td className="num">
                        {new Date(pt.collected_at).toLocaleDateString()}
                      </td>
                      <td className="num">
                        {pt.value} <span className="unit">{s.unit}</span>
                      </td>
                      <td className="num muted">
                        {pt.inputs.map((i) => `${i.display} ${i.value} ${i.unit}`).join("  ·  ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {s.note && <p className="calc__caveat">{s.note}</p>}
            </div>
          </details>
        );
      })}

      {/* Named, not silent: "no eGFR because this record has no date of birth"
          is a gap somebody can close in thirty seconds. */}
      {data.unavailable.map((u) => (
        <p key={u} className="calc__missing">{u}</p>
      ))}
    </section>
  );
}

export default function Trends() {
  const [openCode, setOpenCode] = useState(null);
  const [inspecting, setInspecting] = useState(null);
  const { activeId } = usePatient();

  const { data: panels, isPending, error } = useQuery({
    queryKey: ["panels", activeId],
    queryFn: async () => (await api.get(`/observations/${activeId}/panels`)).data,
    enabled: Boolean(activeId),
  });

  const { data: derived } = useQuery({
    queryKey: ["derived", activeId],
    queryFn: async () => (await api.get(`/observations/${activeId}/derived`)).data,
    enabled: Boolean(activeId),
  });

  if (!activeId || isPending) return <p className="muted">Loading results…</p>;
  if (error) return <p className="muted">Could not load results.</p>;

  if (!panels.length) {
    return (
      <div className="empty">
        <h2 className="empty__title">No results yet</h2>
        <p className="empty__body">
          Add a lab report and LabLedger will pull the values out, resolve each
          test, and start a trend.
        </p>
        <Link className="btn btn--primary" to="/app/upload">Add a report</Link>
      </div>
    );
  }

  const flagged = panels.filter(isFlagged).length;
  const critical = panels.filter((p) => p.latest_critical).length;
  const pending = panels.reduce((n, p) => n + p.pending_review, 0);
  const groups = groupByPanel(panels);

  return (
    <>
      <header className="screen__head">
        <div>
          <p className="eyebrow">Results</p>
          <h1 className="screen__title">{panels.length} tests tracked</h1>
        </div>
        <dl className="tally">
          <div>
            <dt>Out of range</dt>
            <dd className="num">{flagged}</dd>
          </div>
          <div>
            <dt>Critical</dt>
            <dd className={`num ${critical ? "tally--flag" : ""}`}>{critical}</dd>
          </div>
          <div>
            <dt>Awaiting you</dt>
            <dd className="num">{pending}</dd>
          </div>
        </dl>
      </header>

      <div className="panels__head" aria-hidden="true">
        <span>Test</span>
        <span className="panels__head-val">Latest</span>
        <span>Against its range</span>
        <span />
        <span className="panels__head-n">Results</span>
      </div>

      {groups.map((g) => (
        <section key={g.key} className="pgroup">
          <h2 className="pgroup__h">
            <span className="pgroup__label">{g.label}</span>
            {/* Carries its own noun. Right-aligned and bare it would land under
                the "Results" column, where the row numbers below it mean
                something else entirely — how many results that one test has. */}
            <span className="pgroup__n num">
              {g.rows.length} test{g.rows.length === 1 ? "" : "s"}
            </span>
            {g.flagged > 0 && (
              <span className="pgroup__flagged num">{g.flagged} out of range</span>
            )}
          </h2>

          <ul className="panels">
            {g.rows.map((p) => {
              const open = openCode === p.loinc_code;
              return (
                <li key={p.loinc_code} className={`panel ${open ? "panel--open" : ""}`}>
                  <button
                    className="panel__row"
                    aria-expanded={open}
                    onClick={() => setOpenCode(open ? null : p.loinc_code)}
                  >
                    <span className="panel__name">
                      {p.display ?? p.loinc_code}
                      {p.pending_review > 0 && (
                        <span className="panel__pending num">{p.pending_review} to confirm</span>
                      )}
                    </span>

                    <span className="panel__value num">
                      {p.latest_value ?? "—"}
                      <span className="unit"> {p.unit ?? ""}</span>
                    </span>

                    <IntervalRail
                      value={p.latest_value}
                      low={p.ref_low}
                      high={p.ref_high}
                      flag={p.latest_flag}
                      size="panel"
                      label={`${p.display}: ${p.latest_value ?? "no value"} ${p.unit ?? ""}, ${p.latest_flag}`}
                    />

                    {p.latest_critical ? (
                      <span
                        className="flag flag--critical"
                        title={`At or beyond the ${p.latest_critical.side} critical limit of ${p.latest_critical.threshold} ${p.latest_critical.unit} — ${p.latest_critical.basis}`}
                      >
                        critical
                      </span>
                    ) : (
                      <span className={`flag flag--${p.latest_flag}`}>{p.latest_flag}</span>
                    )}
                    <span className="panel__n num">
                      {p.count}
                      <span className="sr-only"> results for this test</span>
                    </span>
                  </button>

                  {open && (
                    <Series
                      loinc={p.loinc_code}
                      unit={p.unit}
                      patientId={activeId}
                      onInspect={setInspecting}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      <Calculated data={derived} />

      {inspecting && (
        <Provenance observationId={inspecting} onClose={() => setInspecting(null)} />
      )}
    </>
  );
}
