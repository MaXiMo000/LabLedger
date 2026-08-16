import "./IntervalRail.css";

/**
 * The reference interval, drawn.
 *
 * A lab value is meaningless without the range it is judged against, so the
 * range is the primitive and the value is a mark on it — not the other way
 * round. The rail spans the reference interval; the tick sits where the value
 * falls. Out-of-range values push the tick into the overflow zone past the
 * rail's end, which is what draws the eye.
 *
 * Three sizes, one geometry: `inline` in a report row, `panel` in the grid,
 * `full` behind a trend line.
 *
 * Position encodes the meaning. Colour only reinforces it, and every use ships
 * with a text label beside it, so the component is legible in greyscale.
 */

const PAD = 0.18; // share of the rail reserved for out-of-range overflow

function positionOf(value, low, high) {
  // One-sided intervals ("> 39") have no opposite bound to scale against, so
  // the value is placed by which side of the bound it falls on rather than
  // by a fabricated distance.
  if (low == null && high == null) return null;
  if (low == null) return value > high ? 1 - PAD / 2 : 0.5;
  if (high == null) return value < low ? PAD / 2 : 0.5;
  if (high === low) return 0.5;

  const t = (value - low) / (high - low);
  if (t < 0) return Math.max(PAD * 0.15, PAD + t * PAD); // clamp inside overflow
  if (t > 1) return Math.min(1 - PAD * 0.15, 1 - PAD + (t - 1) * PAD);
  return PAD + t * (1 - 2 * PAD);
}

export default function IntervalRail({
  value,
  low,
  high,
  flag = "unknown",
  size = "inline",
  label,
}) {
  const pos = value == null ? null : positionOf(value, low, high);

  if (pos == null) {
    return (
      <div className={`rail rail--${size} rail--empty`} aria-hidden="true">
        <span className="rail__line rail__line--absent" />
      </div>
    );
  }

  const bandStart = low == null ? 0 : PAD;
  const bandEnd = high == null ? 1 : 1 - PAD;

  return (
    <div
      className={`rail rail--${size} rail--${flag}`}
      role="img"
      aria-label={
        label ??
        `${value}${low != null || high != null
          ? `, reference ${low ?? "any"} to ${high ?? "any"}`
          : ""}, ${flag}`
      }
    >
      <span className="rail__line" />
      <span
        className="rail__band"
        style={{
          left: `${bandStart * 100}%`,
          right: `${(1 - bandEnd) * 100}%`,
        }}
      />
      <span className="rail__tick" style={{ left: `${pos * 100}%` }} />
    </div>
  );
}
