import { describe, expect, it } from "vitest";
import { idleDelayMs } from "./useIdleSignOut.js";

/**
 * Only the arithmetic. The listener wiring is a handful of addEventListener
 * calls and a cleanup that mirrors them, and the hook already keeps `onIdle` in
 * a ref so the effect does not re-run — pinning that would need a DOM and a
 * renderer to assert what reading the file already shows. The maths is the part
 * that can be quietly wrong.
 */

describe("idleDelayMs", () => {
  it("fires a minute before the server would, so the screen clears first", () => {
    // A 401 mid-click reads as a bug; a lock screen reads as a lock screen.
    expect(idleDelayMs(30)).toBe(29 * 60_000);
    expect(idleDelayMs(15)).toBe(14 * 60_000);
  });

  it("never returns zero or less, however short the server timeout", () => {
    // The bug the floor exists to stop: setTimeout treats <= 0 as "now", so a
    // one-minute timeout would lock the screen the instant it rendered, clear,
    // re-render, and lock again — unusable, and looking like a broken session
    // system rather than a short timeout.
    for (const minutes of [2, 1, 0.5]) {
      expect(idleDelayMs(minutes)).toBeGreaterThanOrEqual(60_000);
    }
  });

  it("gives the shortest usable timeouts a full minute of grace", () => {
    expect(idleDelayMs(1)).toBe(60_000);
    expect(idleDelayMs(2)).toBe(60_000);
    // Two minutes is where subtracting a minute and flooring at a minute agree;
    // above it the subtraction takes over.
    expect(idleDelayMs(3)).toBe(2 * 60_000);
  });
});
