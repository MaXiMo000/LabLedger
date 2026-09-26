import { describe, expect, it } from "vitest";
import { MIN_SHARED, movedTogether, pearson, sharedDraws, stepStops } from "./river.js";

const track = (code, values, days = values.map((_, i) => `2026-0${i + 1}-15T08:0${i}:00`)) => ({
  loinc_code: code,
  points: values.map((v, i) => ({ value: v, collected_at: days[i], flag: "normal" })),
});

describe("pearson", () => {
  it("is 1 and -1 for perfectly paired series, 0 for a flat one", () => {
    expect(pearson([1, 2, 3], [2, 4, 6])).toBeCloseTo(1);
    expect(pearson([1, 2, 3], [6, 4, 2])).toBeCloseTo(-1);
    expect(pearson([1, 2, 3], [5, 5, 5])).toBe(0);
  });
});

describe("sharedDraws", () => {
  it("pairs results from the same day even when stamped minutes apart", () => {
    const a = track("A", [1, 2], ["2026-01-15T08:00:00", "2026-02-15T08:00:00"]);
    const b = track("B", [5, 6], ["2026-01-15T08:07:00", "2026-03-01T08:00:00"]);
    expect(sharedDraws(a, b).map((p) => p.day)).toEqual(["2026-01-15"]);
  });
});

describe("movedTogether", () => {
  it("finds strongly paired tracks over enough shared draws", () => {
    const found = movedTogether([track("LDL", [100, 120, 140, 160]), track("TC", [180, 200, 221, 240]),
                                 track("K", [4.1, 3.9, 4.2, 4.0])]);
    expect(found.map((f) => [f.i, f.j, f.n])).toEqual([[0, 1, 4]]);
    expect(found[0].r).toBeGreaterThan(0.99);
  });

  it("needs at least MIN_SHARED draws -- three points is close to noise", () => {
    const three = [100, 120, 140];
    expect(MIN_SHARED).toBe(4);
    expect(movedTogether([track("A", three), track("B", three)])).toEqual([]);
  });

  it("reports opposite movement too, with its sign", () => {
    const found = movedTogether([track("A", [1, 2, 3, 4]), track("B", [8, 6, 4, 2])]);
    expect(found[0].r).toBeCloseTo(-1);
  });
});

describe("stepStops", () => {
  it("holds each flag until the next reading, then switches sharply", () => {
    const pts = [{ flag: "normal", x: 0 }, { flag: "high", x: 0.5 }, { flag: "normal", x: 1 }];
    const stops = stepStops(pts, (p) => p.x);
    expect(stops.map((s) => s.flag)).toEqual(["normal", "normal", "high", "high", "normal"]);
    expect(stops[2].offset).toBe(0.5);
  });
});
