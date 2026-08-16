import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, messageFor } from "../api/client";
import Modal from "../components/Modal";
import Select from "../components/Select";
import { usePatient } from "./PatientContext";
import "./PatientSwitcher.css";

const SEX_OPTIONS = [
  { value: "", label: "Not stated" },
  { value: "F", label: "Female" },
  { value: "M", label: "Male" },
  { value: "X", label: "Other" },
];

/**
 * Whose record is open, and how to change it.
 *
 * Rendered in the shell on every screen rather than only on a list page,
 * because "which patient am I looking at" must never require remembering. The
 * name is set at body weight in the header — the same size as the product
 * mark — since it is the most important word on the page.
 */
export default function PatientSwitcher() {
  const { patients, active, select } = usePatient();
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ display_name: "", dob: "", sex_at_birth: "" });
  const [error, setError] = useState(null);
  const wrap = useRef(null);
  const qc = useQueryClient();

  useEffect(() => {
    if (!open) return;
    const away = (e) => !wrap.current?.contains(e.target) && setOpen(false);
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const create = useMutation({
    mutationFn: async () =>
      (await api.post("/patients", {
        display_name: form.display_name,
        dob: form.dob || null,
        sex_at_birth: form.sex_at_birth || null,
      })).data,
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["patients"] });
      select(p.id);
      setAdding(false);
      setOpen(false);
      setForm({ display_name: "", dob: "", sex_at_birth: "" });
    },
    onError: (err) => setError(messageFor(err)),
  });

  if (!active) return null;

  return (
    <div className="psw" ref={wrap}>
      <button
        className="psw__current"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        <span className="psw__label eyebrow">Record</span>
        <span className="psw__name">{active.display_name}</span>
        <span className="psw__caret" aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>

      {open && (
        <div className="psw__menu">
          <ul className="psw__list" role="listbox" aria-label="Choose a record">
            {patients.map((p) => (
              <li key={p.id}>
                <button
                  role="option"
                  aria-selected={p.id === active.id}
                  className={`psw__item ${p.id === active.id ? "psw__item--on" : ""}`}
                  onClick={() => { select(p.id); setOpen(false); }}
                >
                  <span className="psw__item-name">{p.display_name}</span>
                  <span className="psw__item-meta num">
                    {p.dob ? `b. ${p.dob}` : "no date of birth"} · {p.role}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <button className="psw__new" onClick={() => { setAdding(true); setOpen(false); }}>
            Add a record
          </button>
        </div>
      )}

      {adding && (
        <Modal
          title="Add a record"
          description="A record is one person whose results you are tracking."
          onClose={() => { setAdding(false); setError(null); }}
          footer={
            <>
              <button className="btn btn--quiet" onClick={() => setAdding(false)}>
                Cancel
              </button>
              <button
                className="btn btn--primary"
                form="add-record"
                disabled={create.isPending || !form.display_name.trim()}
              >
                {create.isPending ? "Adding…" : "Add record"}
              </button>
            </>
          }
        >
          <form
            id="add-record"
            className="stack"
            onSubmit={(e) => { e.preventDefault(); setError(null); create.mutate(); }}
          >
            <label className="field">
              <span className="field__label">Name</span>
              <input
                className="field__input"
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder="How you will recognise this person"
                required
              />
            </label>

            <div className="grid-2">
              <label className="field">
                <span className="field__label">Date of birth</span>
                <input
                  className="field__input"
                  type="date"
                  value={form.dob}
                  onChange={(e) => setForm({ ...form, dob: e.target.value })}
                />
              </label>
              {/* A div, not a label: the custom select is a button, and a
                  wrapping label would toggle it twice per click. */}
              <div className="field">
                <label className="field__label" htmlFor="add-sex">Sex at birth</label>
                <Select
                  id="add-sex"
                  ariaLabel="Sex at birth"
                  value={form.sex_at_birth}
                  onChange={(v) => setForm({ ...form, sex_at_birth: v })}
                  options={SEX_OPTIONS}
                />
              </div>
            </div>

            {/* Not decoration: without these, age- and sex-specific reference
                ranges fall back to whatever each report happened to print. */}
            <p className="field__hint">
              Both are used to choose reference ranges when a report does not
              print its own. They are encrypted at rest.
            </p>

            {error && <p className="field__error" role="alert">{error}</p>}
          </form>
        </Modal>
      )}
    </div>
  );
}
