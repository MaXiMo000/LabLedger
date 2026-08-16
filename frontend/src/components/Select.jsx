import { useEffect, useId, useRef, useState } from "react";
import "./Select.css";

/**
 * A themed replacement for `<select>`.
 *
 * Built rather than styled because a native select's option list is drawn by
 * the operating system, not the page: on a dark-mode Mac it renders a grey
 * system menu in the middle of a warm paper-white report, and no stylesheet
 * can reach it. Everything a lab report shows has to look like the report.
 *
 * What is kept from the native element, because losing it would be a
 * regression rather than a simplification: arrow keys move the highlight,
 * Enter and Space commit, Escape closes without changing anything, Home and
 * End jump to the ends, clicking away closes, and the trigger reports its
 * state to a screen reader. Typeahead is the one thing not carried over —
 * these lists are four items long.
 */

export default function Select({ value, onChange, options, id, ariaLabel }) {
  const [open, setOpen] = useState(false);
  // Where the keyboard is, which is not where the value is until Enter.
  const [cursor, setCursor] = useState(0);
  const wrap = useRef(null);
  const listRef = useRef(null);
  const generated = useId();
  const listId = `${id ?? generated}-list`;

  const index = Math.max(options.findIndex((o) => o.value === value), 0);
  const current = options[index];

  useEffect(() => {
    if (!open) return undefined;
    setCursor(index);
    const away = (e) => !wrap.current?.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
    // `index` deliberately absent: reopening should start from the current
    // value, but changing the value while open should not yank the highlight.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Keep the highlighted row in view when arrowing past the fold.
  //
  // Done with arithmetic on the menu's own scrollTop rather than
  // `scrollIntoView`, which walks up the ancestor chain and scrolls the window
  // as well — opening the menu near the bottom of a long page jumped the whole
  // screen out from under the pointer.
  useEffect(() => {
    const list = listRef.current;
    const row = list?.children[cursor];
    if (!open || !row) return;
    const top = row.offsetTop - list.offsetTop;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (top + row.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = top + row.offsetHeight - list.clientHeight;
    }
  }, [open, cursor]);

  const commit = (i) => {
    onChange(options[i].value);
    setOpen(false);
  };

  const onKeyDown = (e) => {
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];

    if (!open) {
      // A closed select opens on any key that would move within an open one.
      if (step || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }

    if (step) {
      e.preventDefault();
      // Clamped, not wrapped: wrapping past the last item lands somewhere the
      // eye is not, which reads as a jump rather than a move.
      setCursor((c) => Math.min(Math.max(c + step, 0), options.length - 1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setCursor(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setCursor(options.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      commit(cursor);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "Tab") {
      setOpen(false);  // let focus leave, but never leave a menu hanging
    }
  };

  return (
    <div className="sel" ref={wrap}>
      <button
        type="button"
        id={id}
        className={`sel__trigger ${open ? "sel__trigger--open" : ""}`}
        onClick={() => setOpen(!open)}
        onKeyDown={onKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
      >
        <span className="sel__value">
          {current?.label}
          {current?.hint && <span className="sel__hint"> — {current.hint}</span>}
        </span>
        <span className="sel__caret" aria-hidden="true" />
      </button>

      {open && (
        <ul
          className="sel__menu"
          id={listId}
          ref={listRef}
          role="listbox"
          aria-label={ariaLabel}
          aria-activedescendant={`${listId}-${cursor}`}
        >
          {options.map((o, i) => (
            <li
              key={o.value}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={o.value === value}
              className={[
                "sel__opt",
                i === cursor ? "sel__opt--cursor" : "",
                o.value === value ? "sel__opt--on" : "",
              ].join(" ")}
              // Pointer, not click: the button keeps focus, so the keyboard
              // still works after the mouse has been used.
              onMouseEnter={() => setCursor(i)}
              onMouseDown={(e) => { e.preventDefault(); commit(i); }}
            >
              {/* Always rendered, so the label column does not shift by a
                  tick's width when the chosen row changes. */}
              <span className="sel__tick" aria-hidden="true">
                {o.value === value ? "✓" : ""}
              </span>
              <span className="sel__opt-label">{o.label}</span>
              {o.hint && <span className="sel__opt-hint">{o.hint}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
