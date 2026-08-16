/**
 * One analyte over time.
 *
 * Hand-drawn SVG rather than a chart library, for one reason: the reference
 * band is the subject of this chart, not an annotation on it. Every library
 * treats a shaded region as decoration layered over a plot; here the band is
 * the first thing drawn and the line is read against it.
 *
 * Censored values ("<0.5") are drawn as an open marker with a bar, because a
 * value at an assay's floor is a bound and must not read as a measurement.
 */

const W = 720;
const H = 220;
const PAD = { t: 16, r: 16, b: 28, l: 48 };

/** A round step near `range / count`, from the 1 / 2 / 5 series. */
function niceStep(range, count) {
  const raw = range / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}

/**
 * The value axis.
 *
 * Three rules, each of which this got wrong before.
 *
 * **The data sets the scale, not the reference interval.** A one-sided
 * interval is printed as `0–99`, and folding that 0 into the pool dragged the
 * floor to zero: LDL results of 92, 97 and 118 were charted on an axis running
 * to −21, with the readings crushed into the top third. A reference bound
 * joins the domain only when it is near enough to the data to be worth seeing.
 *
 * **Concentrations do not go negative.** Padding below zero draws an axis that
 * implies a lab could report −21 mg/dL.
 *
 * **Ticks land on round numbers**, because an axis labelled 88.1 and 121.9 is
 * arithmetic showing through rather than a reading aid.
 */
export function niceBounds(values, low, high) {
  const data = values.filter((v) => v != null);
  let min = Math.min(...data);
  let max = Math.max(...data);

  const span = max - min || Math.abs(max) || 1;
  for (const bound of [low, high]) {
    if (bound == null) continue;
    // Within one-and-a-half spans of the data: close enough that seeing the
    // threshold is worth the room it costs.
    if (bound >= min - span * 1.5 && bound <= max + span * 1.5) {
      min = Math.min(min, bound);
      max = Math.max(max, bound);
    }
  }

  if (min === max) {
    const nudge = Math.abs(min) * 0.05 || 1;
    min -= nudge;
    max += nudge;
  }

  const pad = (max - min) * 0.15;
  min -= pad;
  max += pad;
  if (Math.min(...data) >= 0 && min < 0) min = 0;

  const step = niceStep(max - min, 4);
  return {
    min: Math.floor(min / step) * step,
    max: Math.ceil(max / step) * step,
    step,
  };
}

export default function TrendChart({ points, refLow, refHigh, unit }) {
  if (!points.length) return null;

  const values = points.map((p) => p.value);
  const { min, max, step } = niceBounds(values, refLow, refHigh);

  const times = points.map((p) => (p.collected_at ? new Date(p.collected_at).getTime() : 0));
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);

  const x = (t) =>
    PAD.l + (t1 === t0 ? (W - PAD.l - PAD.r) / 2 : ((t - t0) / (t1 - t0)) * (W - PAD.l - PAD.r));
  const y = (v) => PAD.t + (1 - (v - min) / (max - min)) * (H - PAD.t - PAD.b);

  // Clamped to the plot area. The domain no longer necessarily contains the
  // reference bounds — a one-sided 0 is deliberately left off the axis — so an
  // unclamped band would draw past the bottom of the chart.
  const clamp = (v) => Math.min(Math.max(v, PAD.t), H - PAD.b);
  const bandTop = clamp(refHigh != null ? y(refHigh) : PAD.t);
  const bandBottom = clamp(refLow != null ? y(refLow) : H - PAD.b);

  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(times[i]).toFixed(1)} ${y(p.value).toFixed(1)}`)
    .join(" ");

  // Every round step across the axis, thinned if that would crowd the labels.
  const all = [];
  for (let v = min; v <= max + step / 2; v += step) all.push(Number(v.toFixed(6)));
  const every = Math.ceil(all.length / 5);
  const ticks = all.filter((_, i) => i % every === 0 || i === all.length - 1);
  // Decimals from the *step*, not the range: a 0.05 step over a 0.2 range
  // rounded to one place prints 0.9, 1.0, 1.0 — two of them the same label.
  const decimals = step >= 1 ? 0 : Math.min(4, Math.ceil(-Math.log10(step)));

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`${points.length} results between ${Math.min(...values)} and ${Math.max(
        ...values
      )} ${unit}, reference ${refLow ?? "any"} to ${refHigh ?? "any"}`}
    >
      {/* The reference interval, first and underneath. */}
      {(refLow != null || refHigh != null) && (
        <rect
          className="chart__band"
          x={PAD.l}
          y={bandTop}
          width={W - PAD.l - PAD.r}
          height={Math.max(1, bandBottom - bandTop)}
        />
      )}

      {ticks.map((v) => (
        <g key={v}>
          <line className="chart__grid" x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} />
          <text className="chart__tick num" x={PAD.l - 8} y={y(v) + 3} textAnchor="end">
            {v.toFixed(decimals)}
          </text>
        </g>
      ))}

      <path className="chart__line" d={path} />

      {points.map((p, i) => (
        <g key={p.observation_id}>
          {p.operator && (
            <line
              className="chart__censor"
              x1={x(times[i]) - 5}
              x2={x(times[i]) + 5}
              y1={y(p.value)}
              y2={y(p.value)}
            />
          )}
          <circle
            className={`chart__pt chart__pt--${p.flag}${p.operator ? " chart__pt--censored" : ""}`}
            cx={x(times[i])}
            cy={y(p.value)}
            r="4"
          >
            <title>
              {p.operator ?? ""}
              {p.value} {unit}
              {p.collected_at ? ` · ${new Date(p.collected_at).toLocaleDateString()}` : ""}
              {` · ${p.flag} · resolved at stage ${p.stage}`}
            </title>
          </circle>
        </g>
      ))}

      {[t0, t1].map((t, i) => (
        <text
          key={t}
          className="chart__tick num"
          x={x(t)}
          y={H - 8}
          textAnchor={i === 0 ? "start" : "end"}
        >
          {new Date(t).toLocaleDateString(undefined, { month: "short", year: "numeric" })}
        </text>
      ))}
    </svg>
  );
}
