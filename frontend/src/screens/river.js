/**
 * The arithmetic behind the river view, kept apart from the drawing so it
 * can be tested. (Ported from vitaline, the standalone timeline app this
 * view replaces.)
 */

/** Two tests "moved together" only past this |r|, and only over at least
 *  MIN_SHARED draws. With three shared points a correlation coefficient is
 *  close to noise, so it takes four. Not a significance test -- a personal
 *  lab history is far too small for one -- and never a claim of cause. */
export const CORRELATION_THRESHOLD = 0.8;
export const MIN_SHARED = 4;

export function pearson(xs, ys) {
  const n = xs.length;
  if (n < 2) return 0;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let dx2 = 0;
  let dy2 = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - mx;
    const dy = ys[i] - my;
    num += dx * dy;
    dx2 += dx * dx;
    dy2 += dy * dy;
  }
  const den = Math.sqrt(dx2 * dy2);
  return den === 0 ? 0 : num / den;
}

/** The calendar day a sample was drawn -- two tests from one blood draw share
 *  it even when the lab stamps them a few minutes apart. */
const dayOf = (iso) => iso.slice(0, 10);

/** Values of two tracks on the days both were measured. */
export function sharedDraws(a, b) {
  const byDay = new Map(b.points.filter((p) => p.collected_at).map((p) => [dayOf(p.collected_at), p]));
  const pairs = [];
  for (const p of a.points) {
    if (!p.collected_at) continue;
    const q = byDay.get(dayOf(p.collected_at));
    if (q) pairs.push({ day: dayOf(p.collected_at), a: p, b: q });
  }
  return pairs;
}

/** Every pair of tracks that moved together (or opposite), strongest first. */
export function movedTogether(tracks) {
  const found = [];
  for (let i = 0; i < tracks.length; i++) {
    for (let j = i + 1; j < tracks.length; j++) {
      const pairs = sharedDraws(tracks[i], tracks[j]);
      if (pairs.length < MIN_SHARED) continue;
      const r = pearson(pairs.map((p) => p.a.value), pairs.map((p) => p.b.value));
      if (Math.abs(r) >= CORRELATION_THRESHOLD) found.push({ i, j, r, n: pairs.length, pairs });
    }
  }
  return found.sort((x, y) => Math.abs(y.r) - Math.abs(x.r));
}

/** Gradient stops that hold each reading's flag colour until the next
 *  reading, then change sharply at that reading -- "this is what the result
 *  was at each draw", not a blend between two states. `frac` maps a point to
 *  0..1 along the track. */
export function stepStops(points, frac) {
  const stops = [];
  points.forEach((p, i) => {
    const f = Math.min(Math.max(frac(p), 0), 1);
    if (i === 0) {
      stops.push({ offset: 0, flag: p.flag });
    } else {
      stops.push({ offset: Math.max(f - 0.0005, 0), flag: points[i - 1].flag });
      stops.push({ offset: f, flag: p.flag });
    }
  });
  return stops;
}
