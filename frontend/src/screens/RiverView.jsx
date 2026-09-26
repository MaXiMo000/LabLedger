import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { niceBounds } from "./TrendChart";
import { movedTogether, stepStops } from "./river.js";
import "./RiverView.css";

/**
 * Every test on one timeline -- the river. (It replaces vitaline, the
 * standalone app this view came from.)
 *
 * The same rule as a panel's small multiples: the only thing the rows share
 * is when the samples were drawn. Each test keeps its own scale, unit and
 * reference band. Two differences from the panel view:
 *
 * - Each ribbon takes its colour from the reading it starts at, and changes
 *   sharply at the next one -- so a stretch out of range reads as a stretch
 *   of colour. Segments are straight: a smoothed curve between two draws
 *   would draw values nobody measured.
 * - Pairs of tests that rose and fell together over at least four shared
 *   draws are named, with how many draws that rests on. That is arithmetic
 *   on these numbers, not a claim that one causes the other.
 */

const W = 100;
const ROW_H = 54;
const PAD = { l: 2, r: 2, t: 10, b: 10 };
const FLAG_VAR = {
  high: "var(--flag-high)", low: "var(--flag-low)", abnormal: "var(--flag-high)",
};
const colour = (flag) => FLAG_VAR[flag] ?? "var(--flag-normal)";

function Ribbon({ track, x0, span, scrubDay, highlighted }) {
  const pts = track.points.filter((p) => p.collected_at);
  const bounds = niceBounds(pts.map((p) => p.value), track.ref_low, track.ref_high);
  const frac = (p) => (new Date(p.collected_at).getTime() - x0) / span;
  const sx = (p) => PAD.l + frac(p) * (W - PAD.l - PAD.r);
  const sy = (v) => ROW_H - PAD.b - ((v - bounds.min) / (bounds.max - bounds.min || 1)) * (ROW_H - PAD.t - PAD.b);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${sx(p)} ${sy(p.value)}`).join(" ");
  const gid = `river-${track.loinc_code.replace(/[^A-Za-z0-9]/g, "")}`;
  const bandable = track.ref_low != null && track.ref_high != null;
  const today = scrubDay && pts.find((p) => p.collected_at.slice(0, 10) === scrubDay);
  const last = pts[pts.length - 1];

  return (
    <li className={`river__row${highlighted ? " river__row--hl" : ""}`}>
      <div className="river__name">
        {track.display ?? track.loinc_code}
        <span className="river__unit">{track.unit ?? ""}</span>
      </div>
      <svg
        className="river__plot"
        viewBox={`0 0 ${W} ${ROW_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${track.display}: ${pts.length} results from ${pts[0].value} to ${last.value} ${track.unit ?? ""}, latest ${last.flag}`}
      >
        <defs>
          <linearGradient id={gid} gradientUnits="userSpaceOnUse" x1={PAD.l} x2={W - PAD.r} y1="0" y2="0">
            {stepStops(pts, frac).map((s, i) => (
              <stop key={i} offset={s.offset} stopColor={colour(s.flag)} />
            ))}
          </linearGradient>
        </defs>
        {bandable && (
          <rect className="river__band" x={PAD.l} width={W - PAD.l - PAD.r}
                y={sy(Math.min(track.ref_high, bounds.max))}
                height={Math.max(sy(Math.max(track.ref_low, bounds.min)) - sy(Math.min(track.ref_high, bounds.max)), 0.5)} />
        )}
        <path className="river__ribbon" d={d} stroke={`url(#${gid})`} vectorEffect="non-scaling-stroke" />
        {today && (
          <line className="river__scrub" x1={sx(today)} x2={sx(today)} y1={0} y2={ROW_H}
                vectorEffect="non-scaling-stroke" />
        )}
      </svg>
      <div className={`river__value num flag--${(today ?? last).flag}`}>
        {today ? today.value : scrubDay ? "—" : last.value}
      </div>
    </li>
  );
}

export default function RiverView({ patientId }) {
  const { data, isPending, error } = useQuery({
    queryKey: ["panel-trends", patientId, "all"],
    queryFn: async () =>
      (await api.get(`/observations/${patientId}/panel-trends`, { params: { panel: "all" } })).data,
  });
  const [scrub, setScrub] = useState(null);
  const [focus, setFocus] = useState(null);

  const tracks = useMemo(
    () => (data?.tracks ?? []).filter((t) => t.points.filter((p) => p.collected_at).length >= 2),
    [data],
  );
  const days = useMemo(
    () => [...new Set(tracks.flatMap((t) => t.points.filter((p) => p.collected_at)
      .map((p) => p.collected_at.slice(0, 10))))].sort(),
    [tracks],
  );
  const pairs = useMemo(() => movedTogether(tracks), [tracks]);

  if (isPending) return <p className="muted">Loading every test…</p>;
  if (error) return <p className="muted">Could not load the timeline.</p>;
  if (!tracks.length) {
    return <p className="muted">A test needs at least two dated results to appear on the timeline.</p>;
  }

  const x0 = new Date(data.first_at).getTime();
  const span = Math.max(new Date(data.last_at).getTime() - x0, 1);
  const scrubDay = scrub == null ? null : days[scrub];
  const hl = focus == null ? new Set() : new Set([pairs[focus].i, pairs[focus].j]);
  const fmt = (day) => new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

  return (
    <div className="river">
      <p className="river__note">
        Every test with two or more results, on one shared time axis. Each keeps
        its own scale and reference range; colour shows whether each result was
        in range, low or high.
      </p>

      <label className="river__scrubber">
        <span className="river__scrubber-label">
          {scrubDay ? `Draw of ${fmt(scrubDay)}` : "Latest results"}
        </span>
        <input
          type="range" min={-1} max={days.length - 1} step={1}
          value={scrub ?? -1}
          onChange={(e) => setScrub(Number(e.target.value) < 0 ? null : Number(e.target.value))}
          aria-valuetext={scrubDay ? `Draw of ${fmt(scrubDay)}` : "Latest results"}
        />
      </label>

      <ul className="river__rows">
        {tracks.map((t, i) => (
          <Ribbon key={t.loinc_code} track={t} x0={x0} span={span} scrubDay={scrubDay} highlighted={hl.has(i)} />
        ))}
      </ul>
      <div className="river__axis num" aria-hidden="true">
        <span />
        <div className="river__axis-dates">
          <span>{fmt(days[0])}</span>
          <span>{fmt(days[days.length - 1])}</span>
        </div>
        <span />
      </div>

      {pairs.length > 0 && (
        <section className="river__pairs">
          <h3 className="river__pairs-h">Moved together</h3>
          <p className="river__pairs-note">
            Rose and fell together across the draws they share. That is all it
            says: not that one causes the other.
          </p>
          <ul>
            {pairs.map((p, k) => (
              <li key={`${p.i}-${p.j}`}>
                <button
                  className="river__pair"
                  aria-pressed={focus === k}
                  onClick={() => setFocus(focus === k ? null : k)}
                >
                  {tracks[p.i].display} {p.r > 0 ? "and" : "against"} {tracks[p.j].display}
                  <span className="river__pair-n num">
                    {p.n} shared draws · r = {p.r.toFixed(2)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
