import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { niceBounds } from "./TrendChart";
import "./PanelTrends.css";

/**
 * Every analyte in one panel, over one shared time axis.
 *
 * **Small multiples, not one overlaid plot.** The obvious design is to
 * normalise each analyte to its reference interval so a whole panel fits on
 * one axis. It is the wrong design: it invents a common scale for quantities
 * that have none, and stacking potassium against cholesterol invites reading
 * the height of one as if it meant something about the other. The data here
 * refuses it anyway — a lipid panel holds an HDL interval of `39–` with no
 * upper bound to normalise against, and analytes still awaiting review have no
 * interval at all.
 *
 * What these analytes genuinely share is *when they were drawn*. So that is
 * the only thing shared: one x scale across every track, each keeping its own
 * values, its own unit and its own band. "Did anything move after that change"
 * is answered by reading down a date, which is what a shared x axis is for.
 *
 * No combined line is drawn, ever. A line through two different quantities is
 * not a trend, it is an artefact of the drawing.
 */

const ROW_H = 46;
const PAD = { l: 4, r: 4, t: 8, b: 8 };

function Track({ track, x0, x1 }) {
  const pts = track.points.filter((p) => p.collected_at);

  if (!pts.length) {
    return (
      <li className="ptrack ptrack--empty">
        <div className="ptrack__name">
          {track.display ?? track.loinc_code}
          <span className="ptrack__unit">{track.unit ?? ""}</span>
        </div>
        {/* Named, not omitted. A panel that quietly shrinks to the analytes
            that happened to convert is the silent omission this whole
            pipeline exists to prevent. */}
        <p className="ptrack__none">
          nothing chartable
          {track.excluded > 0 && ` — ${track.excluded} result${track.excluded === 1 ? "" : "s"} left out`}
        </p>
      </li>
    );
  }

  const W = 100; // viewBox units; the SVG scales to whatever width it is given
  const span = Math.max(x1 - x0, 1);
  const bounds = niceBounds(pts.map((p) => p.value), track.ref_low, track.ref_high);
  const sx = (iso) =>
    PAD.l + ((new Date(iso).getTime() - x0) / span) * (W - PAD.l - PAD.r);
  const sy = (v) =>
    ROW_H - PAD.b -
    ((v - bounds.min) / (bounds.max - bounds.min || 1)) * (ROW_H - PAD.t - PAD.b);

  const d = pts.map((p, i) => `${i ? "L" : "M"}${sx(p.collected_at)} ${sy(p.value)}`).join(" ");
  const last = pts[pts.length - 1];

  // Only when both bounds exist and both are on the chart: half a band drawn
  // as a whole one would claim a limit the lab never published.
  const bandable = track.ref_low != null && track.ref_high != null;
  const bandTop = bandable ? sy(Math.min(track.ref_high, bounds.max)) : 0;
  const bandBot = bandable ? sy(Math.max(track.ref_low, bounds.min)) : 0;

  return (
    <li className="ptrack">
      <div className="ptrack__name">
        {track.display ?? track.loinc_code}
        <span className="ptrack__unit">{track.unit ?? ""}</span>
      </div>

      <svg
        className="ptrack__plot"
        viewBox={`0 0 ${W} ${ROW_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={
          `${track.display}: ${pts.length} results, ` +
          `${pts[0].value} to ${last.value} ${track.unit ?? ""}, latest ${last.flag}`
        }
      >
        {bandable && (
          <rect className="ptrack__band" x={PAD.l} width={W - PAD.l - PAD.r}
                y={bandTop} height={Math.max(bandBot - bandTop, 0.5)} />
        )}
        <path className="ptrack__line" d={d} vectorEffect="non-scaling-stroke" />
        {/* Ticks, not dots. The plot stretches to whatever width the column
            gives it (`preserveAspectRatio="none"`), which turns a circle into a
            smear; a vertical line with a non-scaling stroke stays crisp at any
            aspect, and still carries the flag colour. */}
        {pts.map((p) => (
          <line
            key={p.observation_id}
            className={`ptrack__tick ptrack__tick--${p.flag}`}
            x1={sx(p.collected_at)} x2={sx(p.collected_at)}
            y1={sy(p.value) - 2.5} y2={sy(p.value) + 2.5}
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>

      <div className={`ptrack__last num flag--${last.flag}`}>
        {last.value}
        {track.excluded > 0 && (
          <span className="ptrack__excl" title={`${track.excluded} result(s) could not join this line`}>
            −{track.excluded}
          </span>
        )}
      </div>
    </li>
  );
}

export default function PanelTrends({ panel, patientId }) {
  const { data, isPending, error } = useQuery({
    queryKey: ["panel-trends", patientId, panel],
    queryFn: async () =>
      (await api.get(`/observations/${patientId}/panel-trends`, { params: { panel } })).data,
  });

  if (isPending) return <p className="muted">Loading the panel…</p>;
  if (error) return <p className="muted">Could not load this panel.</p>;
  if (!data.tracks.length) return <p className="muted">Nothing in this panel yet.</p>;

  const x0 = data.first_at ? new Date(data.first_at).getTime() : 0;
  const x1 = data.last_at ? new Date(data.last_at).getTime() : x0 + 1;
  const fmt = (iso) => new Date(iso).toLocaleDateString(undefined, { month: "short", year: "numeric" });

  return (
    <div className="ptrends">
      <p className="ptrends__note">
        One time axis, shared. Each test keeps its own scale and its own
        reference range — the values are not comparable with each other, only
        with their own range.
      </p>

      <ul className="ptracks">
        {data.tracks.map((t) => (
          <Track key={t.loinc_code} track={t} x0={x0} x1={x1} />
        ))}
      </ul>

      {data.first_at && (
        <div className="ptrends__axis num" aria-hidden="true">
          <span>{fmt(data.first_at)}</span>
          <span>{fmt(data.last_at)}</span>
        </div>
      )}
    </div>
  );
}
