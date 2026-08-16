import { useEffect, useRef } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Makes an element behave like the dialog it claims to be.
 *
 * `aria-modal="true"` is a promise to assistive technology: that focus is
 * contained, that Escape leaves, and that the page behind is out of reach.
 * Setting the attribute without honouring it is worse than not setting it,
 * because a screen reader announces a boundary the keyboard can walk straight
 * out of.
 *
 * Four things: move focus in, trap Tab, close on Escape, and return focus to
 * whatever opened it — that last one is what stops a keyboard user losing
 * their place in a long table.
 */
export default function useDialog(onClose) {
  const ref = useRef(null);

  // Held in a ref so the effect below can stay a mount effect.
  //
  // With `onClose` in the dependency array this ran again on every render, and
  // every caller passes a fresh arrow — so typing one character into a field
  // inside the dialog re-ran the whole thing and moved focus back to the first
  // focusable element. You could not type a second character without clicking
  // back. Opening is a one-time event; the close handler only has to be current
  // when Escape is actually pressed, which a ref gives without the re-run.
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const node = ref.current;
    const opener = document.activeElement;

    // The page behind must not scroll under the sheet.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const focusables = () => [...(node?.querySelectorAll(FOCUSABLE) ?? [])];
    (focusables()[0] ?? node)?.focus();

    function onKey(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        close.current();
        return;
      }
      if (e.key !== "Tab") return;

      const items = focusables();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      if (opener instanceof HTMLElement) opener.focus();
    };
    // Mount only. See the ref above: anything here re-running mid-dialog steals
    // focus from whatever the user is typing into.
  }, []);

  return ref;
}
