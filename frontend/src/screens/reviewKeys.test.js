import { describe, expect, it } from "vitest";
import { keyAction } from "./reviewKeys.js";

/**
 * Only the mapping. The listeners are two `addEventListener` calls and a
 * cleanup that mirrors them; what can be quietly wrong is which keystrokes
 * this claims, and the expensive failure is claiming one it should not have.
 */

const press = (key, extra = {}) => ({
  key,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  target: { tagName: "LI" },
  ...extra,
});

const inField = (key, tagName = "INPUT") =>
  press(key, { target: { tagName } });

describe("keyAction", () => {
  it("moves down and up on both the letters and the arrows", () => {
    for (const k of ["j", "ArrowDown"]) {
      expect(keyAction(press(k))).toEqual({ type: "next" });
    }
    for (const k of ["k", "ArrowUp"]) {
      expect(keyAction(press(k))).toEqual({ type: "prev" });
    }
  });

  it("maps 1-9 to a zero-based candidate index", () => {
    expect(keyAction(press("1"))).toEqual({ type: "pick", index: 0 });
    expect(keyAction(press("9"))).toEqual({ type: "pick", index: 8 });
    // 0 is not a tenth candidate, and an off-by-one queue is worse than none.
    expect(keyAction(press("0"))).toBeNull();
  });

  it("keeps its hands off a text field", () => {
    // The bug this exists to stop: typing "flux" into the LOINC search
    // rejecting the row on the x, and jumping the cursor on the j.
    for (const k of ["x", "j", "k", "1", "/", "Enter"]) {
      expect(keyAction(inField(k))).toBeNull();
      expect(keyAction(inField(k, "TEXTAREA"))).toBeNull();
      expect(keyAction(inField(k, "SELECT"))).toBeNull();
    }
    expect(
      keyAction(press("x", { target: { tagName: "DIV", isContentEditable: true } }))
    ).toBeNull();
  });

  it("leaves a field on Escape, and only on Escape", () => {
    expect(keyAction(inField("Escape"))).toEqual({ type: "leaveSearch" });
    expect(keyAction(press("Escape"))).toBeNull();
  });

  it("lets a focused button keep Enter", () => {
    // Enter on a candidate means "choose this one". If it also confirmed, the
    // row would settle on the keypress that was only meant to select.
    expect(keyAction(press("Enter", { target: { tagName: "BUTTON" } }))).toBeNull();
    expect(keyAction(press("Enter"))).toEqual({ type: "confirm" });
  });

  it("yields every combination to the browser and the OS", () => {
    for (const mod of ["ctrlKey", "metaKey", "altKey"]) {
      // ⌘K is a browser search bar, not a cursor move.
      expect(keyAction(press("k", { [mod]: true }))).toBeNull();
      expect(keyAction(press("Enter", { [mod]: true }))).toBeNull();
    }
  });

  it("ignores everything it was not given a meaning for", () => {
    for (const k of [" ", "q", "Tab", "F5", "Shift", "ArrowLeft"]) {
      expect(keyAction(press(k))).toBeNull();
    }
  });
});
