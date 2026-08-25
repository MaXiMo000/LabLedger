/**
 * One keypress to one intent, for the review queue.
 *
 * Pure, and separate from the queue itself, for the same reason `idleDelayMs`
 * is separate from `useIdleSignOut`: the wiring is two `addEventListener`
 * calls and a cleanup that mirrors them, which reading the file shows. *Which
 * key means what, and when a key means nothing at all* is the part that can be
 * quietly wrong, and being wrong here is not cosmetic — a shortcut that fires
 * while somebody is typing into the LOINC search sends `x` to "not a lab
 * result" instead of into the field.
 *
 * Returning `null` is the common answer and the important one. This handler is
 * on `window`, so every keystroke anywhere on the screen passes through it.
 */

const TEXT_FIELD = /^(input|textarea|select)$/;

export function keyAction(e) {
  // A modifier means the browser or the OS is being addressed, not us.
  if (e.ctrlKey || e.metaKey || e.altKey) return null;

  const target = e.target ?? {};
  const tag = (target.tagName ?? "").toLowerCase();

  // Typing beats every shortcut. The search field accepts 58k codes' worth of
  // letters and every one of them is also an accelerator here.
  if (TEXT_FIELD.test(tag) || target.isContentEditable) {
    return e.key === "Escape" ? { type: "leaveSearch" } : null;
  }

  switch (e.key) {
    case "j":
    case "ArrowDown":
      return { type: "next" };
    case "k":
    case "ArrowUp":
      return { type: "prev" };
    case "x":
      return { type: "reject" };
    case "/":
      return { type: "search" };
    case "Enter":
      // A focused button already answers to Enter, and on this screen that
      // button is a candidate code. Claiming Enter as well would settle the
      // row on the keypress that only meant "choose this one".
      return tag === "button" ? null : { type: "confirm" };
    default: {
      // 1–9 pick the nth candidate as listed. Not 0: there is no tenth row
      // worth a shortcut, and a queue where the digits are off by one is
      // worse than a queue with no digits.
      const n = Number(e.key);
      return Number.isInteger(n) && n >= 1 && n <= 9
        ? { type: "pick", index: n - 1 }
        : null;
    }
  }
}
