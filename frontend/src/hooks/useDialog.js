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
        onClose();
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
  }, [onClose]);

  return ref;
}
