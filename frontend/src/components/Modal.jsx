import { createPortal } from "react-dom";
import useDialog from "../hooks/useDialog";
import "./Modal.css";

/**
 * A dialog that behaves like one.
 *
 * Rendered through a portal so it escapes whatever overflow or stacking
 * context it was called from — a modal that clips inside a scrolling panel is
 * the classic symptom of not doing this.
 *
 * `useDialog` supplies the parts that make `aria-modal` honest: focus moves in,
 * Tab is trapped, Escape closes, the page behind stops scrolling, and focus
 * returns to whatever opened it.
 */
export default function Modal({ title, description, onClose, children, footer, tone = "neutral" }) {
  const ref = useDialog(onClose);

  return createPortal(
    <div className="modal__scrim" onClick={onClose}>
      <div
        className={`modal modal--${tone}`}
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal__head">
          <h2 className="modal__title" id="modal-title">{title}</h2>
          {description && <p className="modal__desc">{description}</p>}
        </header>

        {children && <div className="modal__body">{children}</div>}

        <footer className="modal__foot">{footer}</footer>
      </div>
    </div>,
    document.body
  );
}

/**
 * Confirmation before something irreversible.
 *
 * The confirming button names the action and its scale — "Delete 18 results",
 * not "OK". A native `confirm()` cannot do that, cannot be styled to signal
 * severity, and reads as though the page has broken.
 */
export function ConfirmDialog({ title, description, confirmLabel, onConfirm, onClose, busy }) {
  return (
    <Modal
      title={title}
      description={description}
      onClose={onClose}
      tone="danger"
      footer={
        <>
          {/* Cancel first in the DOM so it takes focus on open: the safe
              choice should be the one a stray Enter lands on. */}
          <button className="btn btn--quiet" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn--danger" onClick={onConfirm} disabled={busy}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    />
  );
}
